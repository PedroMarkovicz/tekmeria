from collections.abc import Mapping, Sequence

import pytest

from tekmerion.contract.audit import PassageResolver, Resolution, Unavailable
from tekmerion.contract.audit_models import ClaimIn
from tekmerion.contract.citations import parse_answer_text
from tekmerion.contract.handles import HandleCodec, Locator
from tekmerion.evaluation import run
from tekmerion.evaluation.cases import generate_cases
from tekmerion.evaluation.run import EvaluationReport, render_report, run_evaluation
from tekmerion.evaluation.snapshot import Snapshot, SnapshotResolver
from tekmerion.infra.keys import DEMO_KEY

pytestmark = pytest.mark.anyio

CODEC = HandleCodec(DEMO_KEY)


class EverythingDown:
    """A resolver whose sources never answer: a stand-in for a broken audit."""

    async def resolve(self, locators: Sequence[Locator]) -> Mapping[Locator, Resolution]:
        return {locator: Unavailable("down") for locator in locators}


async def _evaluate(snapshot: Snapshot, resolver: PassageResolver) -> EvaluationReport:
    cases = generate_cases(snapshot, CODEC, seed=0, per_category=5)
    return await run_evaluation(
        cases,
        codec=CODEC,
        resolver=resolver,
        seed=0,
        per_category=5,
        snapshot_records=sorted(snapshot.records),
        snapshot_passages=len(snapshot.passages()),
    )


async def test_the_audit_gives_every_planted_case_its_verdict(snapshot: Snapshot) -> None:
    report = await _evaluate(snapshot, SnapshotResolver(snapshot))
    assert report.cases == 5 * (16 * 2 + 1)  # hidden_in_quote runs on answer text only
    assert (report.correct, report.failures) == (report.cases, [])
    assert all(score.precision == score.recall == 1.0 for score in report.verdicts)


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
    assert lines[0].startswith("planted cases: 165/165 correct (seed 0, 5 per category;")
    assert "| `hidden_in_quote` | - | 5/5 |" in lines
    assert "| `forged` | 35 | 35 | 1.000 | 1.000 |" in lines
