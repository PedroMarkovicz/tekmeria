import pytest
from hypothesis import given
from hypothesis import strategies as st

from tekmerion.contract.handles import (
    CROCKFORD_ALPHABET,
    ForgedHandleError,
    HandleCodec,
    Locator,
    MalformedHandleError,
    RecordRef,
    parse_handle,
    parse_record_id,
)

KEY = bytes(range(32))
OTHER_KEY = bytes(range(1, 33))

locators = st.builds(
    Locator,
    scheme=st.sampled_from(["pmid", "pmcid", "ppr", "nct", "setid"]),
    id=st.from_regex(r"[A-Za-z0-9-]{1,40}", fullmatch=True),
    version=st.none() | st.from_regex(r"[A-Za-z0-9.-]{1,12}", fullmatch=True),
    path=st.from_regex(r"[a-z0-9_]{1,12}(\.[a-z0-9_]{1,12}){0,3}", fullmatch=True),
)


def test_issue_produces_grammar_conforming_handle() -> None:
    codec = HandleCodec(KEY)
    handle = codec.issue(Locator("pmid", "38421676", None, "abstract.2"))
    assert handle.startswith("pmid:38421676/abstract.2~")
    check = handle.rsplit("~", 1)[1]
    assert len(check) == 5 and all(ch in CROCKFORD_ALPHABET for ch in check)


def test_versioned_locator_renders_version() -> None:
    locator = RecordRef("nct", "NCT04368728", "2025-11-02").at("outcome.primary.1")
    assert str(locator) == "nct:NCT04368728@2025-11-02/outcome.primary.1"


def test_check_code_depends_on_key() -> None:
    locator = Locator("pmid", "1", None, "title")
    assert HandleCodec(KEY).check_code(locator) != HandleCodec(OTHER_KEY).check_code(locator)


def test_short_key_rejected() -> None:
    with pytest.raises(ValueError):
        HandleCodec(b"short")


def test_verify_rejects_foreign_handle_as_forged() -> None:
    handle = HandleCodec(OTHER_KEY).issue(Locator("pmid", "1", None, "title"))
    with pytest.raises(ForgedHandleError):
        HandleCodec(KEY).verify(handle)


@pytest.mark.parametrize(
    "text",
    [
        "pmid:1/title",  # no check code
        "PMID:1/title~abcde",  # uppercase scheme
        "pmid:1/Title~abcde",  # uppercase path
        "pmid:1/title~abcdi",  # i is not Crockford
        "doi:10.1/x/title~abcde",  # unknown scheme
        "pmid:30831578-abstract",
        "pmid:1/title~abcde ",
    ],
)
def test_parse_rejects_malformed(text: str) -> None:
    with pytest.raises(MalformedHandleError):
        parse_handle(text)


def test_parse_record_id() -> None:
    assert parse_record_id("setid:1d2e-aa@14") == RecordRef("setid", "1d2e-aa", "14")
    with pytest.raises(ValueError):
        parse_record_id("pmid:1/title")


@given(locators)
def test_issue_then_verify_round_trips(locator: Locator) -> None:
    codec = HandleCodec(KEY)
    assert codec.verify(codec.issue(locator)) == locator


@given(locators, st.data())
def test_any_single_character_change_is_caught(locator: Locator, data: st.DataObject) -> None:
    codec = HandleCodec(KEY)
    handle = codec.issue(locator)
    position = data.draw(st.integers(min_value=0, max_value=len(handle) - 1))
    replacement = data.draw(st.characters(codec="ascii").filter(lambda c: c != handle[position]))
    mutated = handle[:position] + replacement + handle[position + 1 :]
    with pytest.raises((MalformedHandleError, ForgedHandleError)):
        codec.verify(mutated)
