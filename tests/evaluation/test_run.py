from collections.abc import Mapping, Sequence

import pytest

from tekmeria.contract import audit as audit_module
from tekmeria.contract import normalize
from tekmeria.contract.audit import PassageResolver, Resolution, Unavailable
from tekmeria.contract.audit_models import (
    AuditSummary,
    CitationResult,
    ClaimIn,
    ClaimResult,
    ClaimStatus,
    FailOn,
    OverallVerdict,
)
from tekmeria.contract.citations import parse_answer_text
from tekmeria.contract.handles import HandleCodec, Locator
from tekmeria.evaluation import cases as cases_module
from tekmeria.evaluation import run
from tekmeria.evaluation.cases import generate_cases
from tekmeria.evaluation.run import EvaluationReport, render_report, run_evaluation
from tekmeria.evaluation.snapshot import Snapshot, SnapshotResolver
from tekmeria.infra.keys import DEMO_KEY

pytestmark = pytest.mark.anyio

CODEC = HandleCodec(DEMO_KEY)


class EverythingDown:
    """A resolver whose sources never answer."""

    async def resolve(self, locators: Sequence[Locator]) -> Mapping[Locator, Resolution]:
        return {locator: Unavailable("down") for locator in locators}


async def _evaluate(
    snapshot: Snapshot, resolver: PassageResolver, *, per_category: int = 5
) -> EvaluationReport:
    cases = generate_cases(snapshot, DEMO_KEY, seed=0, per_category=per_category)
    return await run_evaluation(
        cases,
        codec=CODEC,
        resolver=resolver,
        seed=0,
        per_category=per_category,
        snapshot_records=sorted(snapshot.records),
        snapshot_passages=len(snapshot.passages()),
    )


async def test_the_audit_gives_every_planted_case_its_verdict(snapshot: Snapshot) -> None:
    report = await _evaluate(snapshot, SnapshotResolver(snapshot))
    # ok_quote_double_marks and ok_quote_folded_marks run on structured claims only;
    # forged_bare and hidden_in_quote on answer text only
    assert report.cases == 5 * (30 * 2 + 4)
    assert (report.correct, report.failures) == (report.cases, [])
    assert all(score.precision == score.recall == 1.0 for score in report.verdicts)
    assert report.passed()


async def test_whole_answers_reach_every_overall_verdict(snapshot: Snapshot) -> None:
    report = await _evaluate(snapshot, SnapshotResolver(snapshot))
    assert all(score.correct == score.answers for score in report.answers)
    planted = {verdict for score in report.answers for verdict in score.planted}
    assert planted == {"fail", "incomplete", "warn", "pass"}


async def test_a_broken_audit_is_caught(snapshot: Snapshot) -> None:
    report = await _evaluate(snapshot, EverythingDown())
    assert report.correct < report.cases and report.failures
    ok = next(score for score in report.verdicts if score.verdict == "ok")
    assert ok.recall == 0.0


async def test_a_citation_the_audit_misses_counts_against_recall(
    snapshot: Snapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    def drops_second_citations(text: str) -> list[ClaimIn]:
        claims = parse_answer_text(text)
        return [claim.model_copy(update={"citations": claim.citations[:1]}) for claim in claims]

    monkeypatch.setattr(run, "parse_answer_text", drops_second_citations)
    report = await _evaluate(snapshot, SnapshotResolver(snapshot))
    forged = next(score for score in report.verdicts if score.verdict == "forged")
    assert report.confusion["forged"]["missed"] == 5
    assert forged.recall is not None and forged.recall < 1.0


async def test_the_report_reads_as_markdown_tables(snapshot: Snapshot) -> None:
    text = render_report(await _evaluate(snapshot, SnapshotResolver(snapshot)))
    lines = text.splitlines()
    assert lines[0].startswith("planted cases: 320/320 correct (seed 0, 5 per category;")
    assert lines[1] == "whole answers: 77/77 correct"
    assert "| `hidden_in_quote` | 5 | - | 5/5 |" in lines
    assert "| `ok_quote_double_marks` | 5 | 5/5 | - |" in lines
    assert "| `preprint_cited` | 2 | 5/5 | 5/5 |" in lines
    assert "| `forged` | 40 | 40 | 1.000 | 1.000 |" in lines
    assert "| clean | 5/5 | `pass` 5 |" in lines
    assert "| paired | 32/32 | `fail` 7, `incomplete` 1, `pass` 9, `warn` 15 |" in lines
    assert "| outside the snapshot | 17 |" in lines


async def test_the_report_counts_distinct_cases_and_citations_per_record(
    snapshot: Snapshot,
) -> None:
    report = await _evaluate(snapshot, SnapshotResolver(snapshot))
    distinct = {score.category: score.distinct for score in report.categories}
    assert distinct["preprint_cited"] == 2 and distinct["ok_plain"] == 5
    assert sum(report.records.values()) == 5 * 36  # every planted citation, once per case
    assert report.records["outside the snapshot"] == 17  # withdrawn records and the offline one


# Each test below breaks one decision of the audit, as a real bug would, and the evaluation
# must notice. The bug is also put wherever the generator could share it.


async def test_a_comparator_bug_shared_with_the_generator_is_still_caught(
    snapshot: Snapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = normalize.quote_in_passage

    def truncating(quote: str, passage: str) -> bool:  # compares only the first 40 characters
        return real(quote[:40], passage)

    monkeypatch.setattr(audit_module, "quote_in_passage", truncating)
    monkeypatch.setattr(normalize, "quote_in_passage", truncating)
    monkeypatch.setattr(cases_module, "quote_in_passage", truncating, raising=False)
    # At the README's size: a truncation shows only on edits past the 40th character.
    report = await _evaluate(snapshot, SnapshotResolver(snapshot), per_category=25)
    assert report.correct < report.cases


async def test_a_check_code_that_ignores_the_path_is_caught(
    snapshot: Snapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = HandleCodec.check_code

    def path_blind(self: HandleCodec, locator: Locator) -> str:
        return real(self, locator.record.at("title"))

    monkeypatch.setattr(HandleCodec, "check_code", path_blind)
    report = await _evaluate(snapshot, SnapshotResolver(snapshot))
    assert report.correct < report.cases


async def test_a_case_blind_comparator_is_caught(
    snapshot: Snapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = normalize.quote_in_passage

    def case_blind(quote: str, passage: str) -> bool:
        return real(quote.lower(), passage.lower())

    monkeypatch.setattr(audit_module, "quote_in_passage", case_blind)
    report = await _evaluate(snapshot, SnapshotResolver(snapshot))
    failed = {score.category for score in report.categories if score.correct < score.cases}
    assert failed == {"quote_case_changed"}


def test_metrics_compare_by_value_and_a_missing_one_only_matches_a_missing_one() -> None:
    assert run._same(0.1 + 0.2, 0.3) and run._same(2, 2)
    assert not run._same(None, 0.0) and not run._same(0, None)
    assert run._same(None, None)


# Faults in claim status, metrics and the overall verdict leave every citation's verdict
# right, so only whole answers can catch them.


async def test_an_overall_verdict_that_never_warns_is_caught_by_whole_answers(
    snapshot: Snapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = audit_module.overall_verdict

    def never_warns(summary: AuditSummary, fail_on: Sequence[FailOn]) -> OverallVerdict:
        verdict = real(summary, fail_on)
        return "pass" if verdict == "warn" else verdict

    monkeypatch.setattr(audit_module, "overall_verdict", never_warns)
    report = await _evaluate(snapshot, SnapshotResolver(snapshot))
    assert report.correct == report.cases
    assert not report.passed()


async def test_a_wrong_claim_status_is_caught_by_whole_answers(
    snapshot: Snapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = audit_module._claim_status

    def invalid_as_uncited(citations: Sequence[CitationResult]) -> ClaimStatus:
        status = real(citations)
        return "uncited" if status == "invalid_only" else status

    monkeypatch.setattr(audit_module, "_claim_status", invalid_as_uncited)
    report = await _evaluate(snapshot, SnapshotResolver(snapshot))
    assert report.correct == report.cases
    assert not report.passed()


async def test_a_wrong_metric_is_caught_by_whole_answers(
    snapshot: Snapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = audit_module.summarize

    def counts_unavailable(claims: Sequence[ClaimResult]) -> AuditSummary:
        summary = real(claims)
        ok = summary.by_verdict["ok"]
        validity = ok / summary.citations if summary.citations else None
        return summary.model_copy(update={"citation_validity": validity})

    monkeypatch.setattr(audit_module, "summarize", counts_unavailable)
    report = await _evaluate(snapshot, SnapshotResolver(snapshot))
    assert report.correct == report.cases
    assert not report.passed()
    assert any("citation_validity" in " ".join(f.expected) for f in report.failures)


async def test_a_dropped_flag_is_caught(
    snapshot: Snapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(audit_module, "_flags", lambda locator, record: [])
    report = await _evaluate(snapshot, SnapshotResolver(snapshot))
    failed = {score.category for score in report.categories if score.correct < score.cases}
    assert {"retracted_cited", "preprint_cited", "version_changed"} <= failed
