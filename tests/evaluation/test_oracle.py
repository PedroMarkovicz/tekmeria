"""The evaluation's reference oracle agrees with the spec and the documented examples."""

import re
from pathlib import Path

import pytest

from tekmerion.evaluation import oracle
from tekmerion.infra.keys import DEMO_KEY

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = re.findall(
    r"^((?:pmid|pmcid|nct|setid):\S+)~([0-9a-z]{5})$",
    (ROOT / "docs" / "CONTRACT.md").read_text(encoding="utf-8"),
    flags=re.MULTILINE,
)


def test_the_oracle_issues_the_documented_demo_handles() -> None:
    assert len(EXAMPLES) == 4
    for locator, check in EXAMPLES:
        assert oracle.issue(DEMO_KEY, locator) == f"{locator}~{check}"
        assert oracle.is_genuine(DEMO_KEY, f"{locator}~{check}")


def test_a_changed_code_path_or_key_is_not_genuine() -> None:
    locator, check = EXAMPLES[0]
    other = "a" if check[0] != "a" else "b"
    assert not oracle.is_genuine(DEMO_KEY, f"{locator}~{other}{check[1:]}")
    assert not oracle.is_genuine(DEMO_KEY, f"{locator}9~{check}")
    assert not oracle.is_genuine(b"another key, at least 32 bytes long", f"{locator}~{check}")


@pytest.mark.parametrize(
    ("handle", "expected"),
    [
        ("pmid:1/abstract.2~abcde", True),
        ("nct:NCT04368728@2025-11-02/arm.1~00000", True),
        ("pmid:1/abstract.2-abcde", False),  # wrong mark before the check code
        ("pmid:1/abstract.2~abcd", False),  # four characters
        ("pmid:1/abstract.2~abcdi", False),  # "i" is not in the alphabet
        ("pmid:1~abcde", False),  # no path
        ("pmid:1/Abstract.2~abcde", False),  # path in capitals
        ("PMID:1/abstract.2~abcde", False),  # scheme in capitals
        ("doi:10.1/x/abstract.1~abcde", False),  # not a scheme
    ],
)
def test_well_formed_follows_the_grammar(handle: str, expected: bool) -> None:
    assert oracle.well_formed(handle) is expected


def test_fold_follows_the_spec() -> None:
    text = "  A" + chr(0xA0) + "b  " + chr(0x201C) + "c" + chr(0x201D) + " " + chr(0x2013)
    text += " d" + chr(0x2026) + chr(0x2019) + "e" + chr(0x2212) + "1 "
    assert oracle.fold(text) == 'A b "c" - d...\'e-1'


def test_a_quote_matches_only_a_non_empty_folded_part() -> None:
    assert oracle.quote_matches("risk  ratio", "The risk ratio was 0.93.")
    assert oracle.quote_matches("ratio " + chr(0x2013) + " 0.93", "the ratio - 0.93")
    assert not oracle.quote_matches("risk rate", "The risk ratio was 0.93.")
    assert not oracle.quote_matches("   ", "anything")
