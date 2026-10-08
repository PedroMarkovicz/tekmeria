"""Run the planted cases through the audit and score the verdicts (spec section 16.9)."""

import math
import random
import re
from collections import Counter
from collections.abc import Sequence

from pydantic import BaseModel

from tekmerion.contract.audit import PassageResolver, audit
from tekmerion.contract.audit_models import VERDICTS, CitationIn, ClaimIn, Verdict
from tekmerion.contract.citations import parse_answer_text
from tekmerion.contract.handles import HandleCodec
from tekmerion.evaluation.answers import Answer, compose_answers, expected_result
from tekmerion.evaluation.cases import CATEGORIES, MODES, Case, Mode, render_text

MAX_FAILURES_SHOWN = 20
MISSED, UNPLANTED = "missed", "unplanted"
OUTSIDE = "outside the snapshot"
_RECORD = re.compile(r"^[a-z]+:[A-Za-z0-9-]+")  # a handle's record, without any version


def _record_of(handle: str, snapshot_records: Sequence[str]) -> str:
    match = _RECORD.match(handle)
    record = match.group(0) if match else ""
    return record if record in snapshot_records else OUTSIDE


class CategoryScore(BaseModel):
    category: str
    mode: Mode
    cases: int
    correct: int
    distinct: int  # different cases: a small pool is drawn from more than once


class Failure(BaseModel):
    category: str
    mode: Mode
    answer: str
    expected: list[str]
    got: list[str]


class VerdictScore(BaseModel):
    verdict: Verdict
    expected: int
    predicted: int
    correct: int
    precision: float | None
    recall: float | None


class AnswerScore(BaseModel):
    kind: str  # "mixed" or "clean"
    answers: int
    correct: int
    # the overall verdict planted in these answers -> how many answers
    planted: dict[str, int]


class EvaluationReport(BaseModel):
    seed: int
    per_category: int
    snapshot_records: list[str]
    snapshot_passages: int
    cases: int
    correct: int
    categories: list[CategoryScore]
    verdicts: list[VerdictScore]
    # planted verdict -> the audit's verdict -> citations. Column "missed": a planted citation
    # the audit never reported; row "unplanted": a citation the audit found but none was planted.
    confusion: dict[str, dict[str, int]]
    # planted citations per record, once per case (each case is audited in up to two modes)
    records: dict[str, int]
    # whole answers: correct only when every sentence's status, every citation's verdict and
    # flags, every metric and the overall verdict are as planted
    answers: list[AnswerScore]
    failures: list[Failure]

    def passed(self) -> bool:
        """Every planted case and every whole answer came out as planted."""
        return self.correct == self.cases and all(a.correct == a.answers for a in self.answers)


def _label(verdict: str, flags: Sequence[str]) -> str:
    return verdict + "".join(f"+{flag}" for flag in sorted(flags))


async def _audit_case(
    case: Case, mode: Mode, *, codec: HandleCodec, resolver: PassageResolver, rng: random.Random
) -> tuple[str, list[tuple[str, list[str]]]]:
    if mode == "claims":
        citations = [CitationIn(handle=p.handle, quote=p.quote) for p in case.citations]
        claims = [ClaimIn(sentence="The cited source reports this finding.", citations=citations)]
        answer = " ".join(p.handle + (f' "{p.quote}"' if p.quote else "") for p in case.citations)
    else:
        answer = render_text(case, rng)
        claims = parse_answer_text(answer)
    report = await audit(claims, codec=codec, resolver=resolver)
    got = [
        (c.verdict, [f.name for f in c.flags]) for claim in report.claims for c in claim.citations
    ]
    return answer, [(verdict, flags) for verdict, flags in got]


async def _score_answer(
    answer: Answer, *, codec: HandleCodec, resolver: PassageResolver
) -> tuple[bool, list[str], list[str]]:
    want = expected_result(answer)
    report = await audit(parse_answer_text(answer.text), codec=codec, resolver=resolver)
    summary = report.summary
    got_metrics: dict[str, float | int | None] = {
        name: getattr(summary, name) for name in want.metrics
    }
    got = [c for claim in report.claims for c in claim.citations]
    ok = (
        [claim.status for claim in report.claims] == want.statuses
        and [(c.verdict, sorted(f.name for f in c.flags)) for c in got] == want.citations
        and report.verdict == want.verdict
        and all(_same(got_metrics[name], value) for name, value in want.metrics.items())
    )
    expected = [want.verdict, *want.statuses, *(_label(v, f) for v, f in want.citations)]
    actual = [
        report.verdict,
        *(claim.status for claim in report.claims),
        *(_label(c.verdict, [f.name for f in c.flags]) for c in got),
    ]
    if ok or expected != actual:
        return ok, expected, actual
    return ok, [*expected, str(want.metrics)], [*actual, str(got_metrics)]  # a metric differs


def _same(got: float | int | None, want: float | int | None) -> bool:
    if got is None or want is None:
        return got is want
    return math.isclose(got, want)


async def run_evaluation(
    cases: Sequence[Case],
    *,
    codec: HandleCodec,
    resolver: PassageResolver,
    seed: int,
    per_category: int,
    snapshot_records: Sequence[str],
    snapshot_passages: int,
) -> EvaluationReport:
    """Audit every case in each of its modes; a case is correct only when every citation
    gets its planted verdict and exactly its planted flags."""
    rng = random.Random(seed)  # text styles; independent of the case generator
    scores: dict[tuple[str, Mode], list[int]] = {(c, m): [0, 0] for c in CATEGORIES for m in MODES}
    confusion: dict[str, dict[str, int]] = {
        row: dict.fromkeys((*VERDICTS, MISSED), 0) for row in (*VERDICTS, UNPLANTED)
    }
    failures: list[Failure] = []
    distinct: dict[str, set[Case]] = {}
    records: Counter[str] = Counter()
    total = correct = 0
    for case in cases:
        distinct.setdefault(case.category, set()).add(case)
        records.update(_record_of(p.handle, snapshot_records) for p in case.citations)
        expected = [(p.verdict, sorted(p.flags)) for p in case.citations]
        for mode in case.modes:
            answer, got = await _audit_case(case, mode, codec=codec, resolver=resolver, rng=rng)
            got_sorted = [(verdict, sorted(flags)) for verdict, flags in got]
            ok = got_sorted == expected
            total += 1
            correct += ok
            scores[(case.category, mode)][0] += 1
            scores[(case.category, mode)][1] += ok
            for index in range(max(len(expected), len(got_sorted))):
                want = expected[index][0] if index < len(expected) else UNPLANTED
                have = got_sorted[index][0] if index < len(got_sorted) else MISSED
                confusion[want][have] += 1
            if not ok and len(failures) < MAX_FAILURES_SHOWN:
                failures.append(
                    Failure(
                        category=case.category,
                        mode=mode,
                        answer=answer,
                        expected=[_label(v, f) for v, f in expected],
                        got=[_label(v, f) for v, f in got_sorted],
                    )
                )
    answer_scores: dict[str, AnswerScore] = {}
    for answer in compose_answers(cases, seed):
        ok, expected_items, got_items = await _score_answer(answer, codec=codec, resolver=resolver)
        score = answer_scores.setdefault(
            answer.kind, AnswerScore(kind=answer.kind, answers=0, correct=0, planted={})
        )
        score.answers += 1
        score.correct += ok
        verdict = expected_items[0]
        score.planted[verdict] = score.planted.get(verdict, 0) + 1
        if not ok and len(failures) < MAX_FAILURES_SHOWN:
            failures.append(
                Failure(
                    category=f"whole answer ({answer.kind})",
                    mode="text",
                    answer=answer.text,
                    expected=expected_items,
                    got=got_items,
                )
            )
    verdicts = []
    for verdict in VERDICTS:
        expected_n = sum(confusion[verdict].values())
        predicted_n = sum(row[verdict] for row in confusion.values())
        hits = confusion[verdict][verdict]
        verdicts.append(
            VerdictScore(
                verdict=verdict,
                expected=expected_n,
                predicted=predicted_n,
                correct=hits,
                precision=hits / predicted_n if predicted_n else None,
                recall=hits / expected_n if expected_n else None,
            )
        )
    return EvaluationReport(
        seed=seed,
        per_category=per_category,
        snapshot_records=list(snapshot_records),
        snapshot_passages=snapshot_passages,
        cases=total,
        correct=correct,
        categories=[
            CategoryScore(category=c, mode=m, cases=n, correct=k, distinct=len(distinct[c]))
            for (c, m), (n, k) in scores.items()
            if n
        ],
        verdicts=verdicts,
        confusion=confusion,
        records=dict(sorted(records.items())),
        answers=list(answer_scores.values()),
        failures=failures,
    )


def _ratio(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def render_report(report: EvaluationReport) -> str:
    """The report as Markdown: per-category results, per-verdict precision and recall, and
    how the planted citations spread over the records."""
    by_category: dict[str, dict[str, CategoryScore]] = {}
    for score in report.categories:
        by_category.setdefault(score.category, {})[score.mode] = score

    def cell(score: CategoryScore | None) -> str:
        return "-" if score is None else f"{score.correct}/{score.cases}"

    lines = [
        f"planted cases: {report.correct}/{report.cases} correct "
        f"(seed {report.seed}, {report.per_category} per category; "
        f"{len(report.snapshot_records)} records, {report.snapshot_passages} passages)",
        f"whole answers: {sum(a.correct for a in report.answers)}/"
        f"{sum(a.answers for a in report.answers)} correct",
        "",
        "| Category | Distinct cases | Structured claims | Answer text |",
        "|---|---|---|---|",
    ]
    for category, modes in by_category.items():
        first = next(iter(modes.values()))
        lines.append(
            f"| `{category}` | {first.distinct} | {cell(modes.get('claims'))} | "
            f"{cell(modes.get('text'))} |"
        )
    lines += [
        "",
        "| Verdict | Planted | Given | Precision | Recall |",
        "|---|---|---|---|---|",
    ]
    for score in report.verdicts:
        lines.append(
            f"| `{score.verdict}` | {score.expected} | {score.predicted} | "
            f"{_ratio(score.precision)} | {_ratio(score.recall)} |"
        )
    lines += [
        "",
        "| Whole answers | Correct | Planted overall verdicts |",
        "|---|---|---|",
    ]
    for answer in report.answers:
        planted = ", ".join(f"`{v}` {n}" for v, n in sorted(answer.planted.items()))
        lines.append(f"| {answer.kind} | {answer.correct}/{answer.answers} | {planted} |")
    lines += ["", "| Record | Planted citations |", "|---|---|"]
    lines += [f"| {record} | {count} |" for record, count in report.records.items()]
    for failure in report.failures:
        lines.append(
            f"FAILED {failure.category} ({failure.mode}): expected {failure.expected}, "
            f"got {failure.got}: {failure.answer}"
        )
    return "\n".join(lines)
