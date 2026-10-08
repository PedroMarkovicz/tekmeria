import random
from collections import Counter

import pytest

from tekmerion.contract.citations import parse_answer_text
from tekmerion.evaluation import cases, oracle
from tekmerion.evaluation.cases import CATEGORIES, Planted, generate_cases, render_text
from tekmerion.evaluation.snapshot import Snapshot, record_key
from tekmerion.infra.keys import DEMO_KEY

pytestmark = pytest.mark.anyio


def _text_of(snapshot: Snapshot, handle: str) -> str | None:
    locator = handle.rsplit("~", 1)[0]
    record_id, path = locator.split("/", 1)
    item = snapshot.records.get(record_key(record_id))
    passage = next((p for p in item.passages if p.path == path), None) if item else None
    return passage.text if passage else None


def _check(snapshot: Snapshot, planted: Planted) -> None:
    """The planted citation really is what its verdict says, by the reference oracle: code
    written from the spec that shares nothing with the audit under test."""
    if planted.verdict == "malformed":
        assert not oracle.well_formed(planted.handle)
        return
    assert oracle.well_formed(planted.handle)
    if planted.verdict == "forged":
        assert not oracle.is_genuine(DEMO_KEY, planted.handle)
        return
    assert oracle.is_genuine(DEMO_KEY, planted.handle)
    text = _text_of(snapshot, planted.handle)
    if planted.verdict in ("withdrawn", "unavailable"):
        assert text is None
        return
    assert text is not None
    if planted.quote is not None:
        assert oracle.quote_matches(planted.quote, text) == (planted.verdict == "ok")


async def test_the_same_seed_gives_the_same_cases(snapshot: Snapshot) -> None:
    first = generate_cases(snapshot, DEMO_KEY, seed=7, per_category=3)
    assert first == generate_cases(snapshot, DEMO_KEY, seed=7, per_category=3)
    assert first != generate_cases(snapshot, DEMO_KEY, seed=8, per_category=3)


async def test_every_category_gets_its_share(snapshot: Snapshot) -> None:
    cases = generate_cases(snapshot, DEMO_KEY, seed=0, per_category=4)
    assert Counter(case.category for case in cases) == dict.fromkeys(CATEGORIES, 4)


async def test_cases_plant_what_their_category_says(snapshot: Snapshot) -> None:
    for case in generate_cases(snapshot, DEMO_KEY, seed=0, per_category=10):
        for planted in case.citations:
            _check(snapshot, planted)


async def test_a_rendered_answer_cites_exactly_the_planted_citations(snapshot: Snapshot) -> None:
    rng = random.Random(0)
    for case in generate_cases(snapshot, DEMO_KEY, seed=0, per_category=10):
        if "text" not in case.modes:
            continue
        claims = parse_answer_text(render_text(case, rng))
        cited = [(c.handle, c.quote) for claim in claims for c in claim.citations]
        assert cited == [(p.handle, p.quote) for p in case.citations], case.category


async def test_a_category_that_cannot_be_planted_fails_fast(
    snapshot: Snapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    # As if the check code ignored the path: every path swap would then be genuine.
    monkeypatch.setattr(cases._Builder, "_genuine", lambda self, handle: True)
    with pytest.raises(RuntimeError, match="forged_path_swap: no case found"):
        generate_cases(snapshot, DEMO_KEY, seed=0, per_category=1)
