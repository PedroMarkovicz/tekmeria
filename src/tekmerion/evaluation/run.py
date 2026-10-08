"""Run the planted cases through the audit and score the verdicts (spec section 16.9)."""

import random
from collections.abc import Sequence

from pydantic import BaseModel

from tekmerion.contract.audit import PassageResolver, audit
from tekmerion.contract.audit_models import VERDICTS, CitationIn, ClaimIn, Verdict
from tekmerion.contract.citations import parse_answer_text
from tekmerion.contract.handles import HandleCodec
from tekmerion.evaluation.cases import CATEGORIES, MODES, Case, Mode, render_text

MAX_FAILURES_SHOWN = 20
MISSED, UNPLANTED = "missed", "unplanted"


class CategoryScore(BaseModel):
    category: str
    mode: Mode
    cases: int
    correct: int


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
    failures: list[Failure]


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
    total = correct = 0
    for case in cases:
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
            CategoryScore(category=c, mode=m, cases=n, correct=k)
            for (c, m), (n, k) in scores.items()
            if n
        ],
        verdicts=verdicts,
        confusion=confusion,
        failures=failures,
    )


def _ratio(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def render_report(report: EvaluationReport) -> str:
    """The report as Markdown: per-category results, then per-verdict precision and recall."""
    by_category: dict[str, dict[str, CategoryScore]] = {}
    for score in report.categories:
        by_category.setdefault(score.category, {})[score.mode] = score

    def cell(score: CategoryScore | None) -> str:
        return "-" if score is None else f"{score.correct}/{score.cases}"

    lines = [
        f"planted cases: {report.correct}/{report.cases} correct "
        f"(seed {report.seed}, {report.per_category} per category; "
        f"{len(report.snapshot_records)} records, {report.snapshot_passages} passages)",
        "",
        "| Category | Structured claims | Answer text |",
        "|---|---|---|",
    ]
    for category, modes in by_category.items():
        lines.append(f"| `{category}` | {cell(modes.get('claims'))} | {cell(modes.get('text'))} |")
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
    for failure in report.failures:
        lines.append(
            f"FAILED {failure.category} ({failure.mode}): expected {failure.expected}, "
            f"got {failure.got}: {failure.answer}"
        )
    return "\n".join(lines)
