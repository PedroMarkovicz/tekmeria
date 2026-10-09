import random
import re
import unicodedata
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


async def test_a_category_uses_every_passage_of_its_pool_before_repeating_one(
    snapshot: Snapshot,
) -> None:
    generated = generate_cases(snapshot, DEMO_KEY, seed=0, per_category=20)
    plain = [case.citations[0].handle for case in generated if case.category == "ok_plain"]
    assert len(set(plain)) == 20  # the pool has far more than 20 passages
    preprints = Counter(c.citations[0].handle for c in generated if c.category == "preprint_cited")
    assert sorted(preprints.values()) == [10, 10]  # two passages, used in turn


PER_MILLE = chr(0x2030)


def _units(text: str) -> str:
    return re.sub(
        r"(?<=\d) ?(mg/kg|mg/dL|mcg|mg|kg|mmol|mL|IU|g|L|%)(?![A-Za-z])",
        " U",
        text.replace(PER_MILLE, "%"),
    )


# Each edit changes one detail and keeps the rest: undoing the detail on both sides finds the
# quote in the passage again.
UNDO_THE_EDIT = {
    "quote_decimal_moved": lambda text: re.sub(r"[\d.]+", "#", text),
    "quote_comparison_flipped": lambda text: re.sub(
        "[<>" + chr(0x2264) + chr(0x2265) + "]", "<", text
    ),
    "quote_unit_changed": _units,
    "quote_case_changed": str.lower,
    "quote_accent_added": lambda text: "".join(
        c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c)
    ),
    "quote_compatibility_form": lambda text: unicodedata.normalize("NFKC", text),
    "quote_space_removed": lambda text: text.replace(" ", ""),
}


def _planted(snapshot: Snapshot, categories: set[str]) -> list[tuple[Planted, str]]:
    """The citations planted in `categories`, each with its passage's text; never empty."""
    found: list[tuple[Planted, str]] = []
    for case in generate_cases(snapshot, DEMO_KEY, seed=0, per_category=10):
        if case.category in categories:
            for planted in case.citations:
                text = _text_of(snapshot, planted.handle.split(" ", 1)[0])
                assert text is not None
                found.append((planted, text))
    assert found, categories
    return found


@pytest.mark.parametrize("category", sorted(UNDO_THE_EDIT))
async def test_a_meaning_edit_changes_one_detail(snapshot: Snapshot, category: str) -> None:
    undo = UNDO_THE_EDIT[category]
    for planted, text in _planted(snapshot, {category}):
        assert planted.quote is not None
        assert not oracle.quote_matches(planted.quote, text)
        assert undo(oracle.fold(planted.quote)) in undo(oracle.fold(text)), planted.quote


async def test_a_sign_edit_adds_one_minus_before_a_number(snapshot: Snapshot) -> None:
    for planted, text in _planted(snapshot, {"quote_sign_changed"}):
        assert planted.quote is not None
        minus = [i for i, ch in enumerate(planted.quote) if ch == "-"]
        unsigned = [planted.quote[:i] + planted.quote[i + 1 :] for i in minus]
        assert any(oracle.quote_matches(quote, text) for quote in unsigned), planted.quote


async def test_a_typographic_variant_still_differs_from_the_passage(snapshot: Snapshot) -> None:
    # The variant categories must exercise the folding: the quote is not the passage's own text.
    variants = {
        "ok_quote_apostrophe",
        "ok_quote_decomposed",
        "ok_quote_double_marks",
        "ok_quote_folded",
        "ok_quote_folded_marks",
    }
    for planted, text in _planted(snapshot, variants):
        assert planted.quote is not None and planted.quote not in text, planted.quote


async def test_an_invalid_citation_shares_its_sentence_with_a_genuine_one(
    snapshot: Snapshot,
) -> None:
    rng = random.Random(0)
    found = [
        case
        for case in generate_cases(snapshot, DEMO_KEY, seed=0, per_category=4)
        if case.category == "invalid_beside_valid"
    ]
    kinds = [sorted(p.verdict for p in case.citations) for case in found]
    assert kinds == [["ok", "quote_mismatch"], ["ok", "withdrawn"]] * 2  # in turn
    for case in found:
        (claim,) = parse_answer_text(render_text(case, rng))  # one sentence, both citations
        assert len(claim.citations) == 2


async def test_a_note_follows_a_genuine_handle(snapshot: Snapshot) -> None:
    for planted, _ in _planted(snapshot, {"malformed_with_note"}):
        handle, note = planted.handle.split(" ", 1)
        assert oracle.is_genuine(DEMO_KEY, handle) and note


async def test_every_fold_but_the_ellipsis_and_every_removal_is_written_into_a_quote(
    snapshot: Snapshot,
) -> None:
    # Each fold and each removed character of the spec appears in some planted quote, so dropping
    # any one of them fails a case. No passage of the snapshot holds an ellipsis, so its fold
    # cannot be planted.
    verbatim = {category for category in CATEGORIES if category.startswith("ok_quote")}
    written = {char for planted, _ in _planted(snapshot, verbatim) for char in planted.quote or ""}
    expected = (set(oracle.FOLDS) | oracle.REMOVED) - {chr(0x2026)}
    assert expected - written == set()
