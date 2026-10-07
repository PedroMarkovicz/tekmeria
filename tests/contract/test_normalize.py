from hypothesis import given
from hypothesis import strategies as st

from medcite.contract.normalize import (
    closest_match,
    fold_for_compare,
    normalize_text,
    quote_in_passage,
)

# Printable text without combining marks: realistic for abstracts and keeps
# substring properties meaningful (a substring may not cut a combining sequence).
TEXT = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs", "Mn", "Me", "Mc"), blacklist_characters="\x00"
    ),
    max_size=200,
)


def test_normalize_collapses_whitespace_and_trims() -> None:
    assert normalize_text("  a\n\tb\u00a0 c  ") == "a b c"


def test_normalize_applies_nfc() -> None:
    assert normalize_text("e\u0301") == "\u00e9"


def test_fold_maps_typography() -> None:
    assert fold_for_compare("\u201cHR\u201d \u2013 0.93\u2026") == '"HR" - 0.93...'


def test_quote_matches_across_typographic_variants() -> None:
    passage = "The adjusted hazard ratio was 0.93 (95% CI, 0.85\u20131.01)."
    assert quote_in_passage("95% CI, 0.85-1.01", passage)


def test_quote_mismatch_on_changed_number() -> None:
    passage = "The adjusted hazard ratio was 0.93 (95% CI, 0.85-1.01)."
    assert not quote_in_passage("hazard ratio was 1.93", passage)


def test_empty_quote_never_matches() -> None:
    assert not quote_in_passage("   ", "anything")


def test_closest_match_finds_the_near_window() -> None:
    passage = "Among 657,461 children, MMR vaccination did not increase the risk for autism."
    window, similarity = closest_match("MMR vaccination did increase the risk", passage)
    assert window.startswith("MMR vaccination did")
    assert 0.8 < similarity < 1.0


@given(TEXT)
def test_normalize_is_idempotent(text: str) -> None:
    once = normalize_text(text)
    assert normalize_text(once) == once


@given(TEXT)
def test_fold_is_idempotent(text: str) -> None:
    once = fold_for_compare(text)
    assert fold_for_compare(once) == once


@given(TEXT, st.data())
def test_every_substring_of_a_passage_matches(text: str, data: st.DataObject) -> None:
    passage = normalize_text(text)
    if not passage.strip():
        return
    start = data.draw(st.integers(min_value=0, max_value=len(passage) - 1))
    end = data.draw(st.integers(min_value=start + 1, max_value=len(passage)))
    quote = passage[start:end]
    if quote.strip():
        assert quote_in_passage(quote, passage)
