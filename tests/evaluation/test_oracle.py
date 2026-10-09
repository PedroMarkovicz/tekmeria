"""The evaluation's reference oracle agrees with the spec and the documented examples."""

import re
import shutil
import subprocess
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


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl command")
def test_the_check_code_matches_an_independent_hmac() -> None:
    # OpenSSL computes the HMAC; the first 25 bits are then read as five base-32 digits.
    for locator, check in EXAMPLES:
        digest = subprocess.run(
            ["openssl", "dgst", "-sha256", "-hmac", DEMO_KEY.decode("ascii")],
            input=locator.encode("utf-8"),
            capture_output=True,
            check=True,
        ).stdout.split()[-1]
        bits = int(digest[:8], 16) >> 7
        code = "".join(oracle.ALPHABET[(bits >> shift) & 31] for shift in (20, 15, 10, 5, 0))
        assert code == check == oracle.check_code(DEMO_KEY, locator)


# docs/CONTRACT.md sections 7 to 9, the rules whole answers are scored by.


def test_claim_status_follows_section_7() -> None:
    assert oracle.claim_status([]) == "uncited"
    assert oracle.claim_status(["forged", "ok"]) == "cited"
    assert oracle.claim_status(["forged", "quote_mismatch"]) == "invalid_only"


@pytest.mark.parametrize(
    ("statuses", "verdicts", "retracted_ok", "expected"),
    [
        (["cited"], ["ok"], 0, "pass"),
        (["cited", "uncited"], ["ok"], 0, "warn"),
        (["cited"], ["ok"], 1, "warn"),
        (["cited", "uncited"], ["ok", "unavailable"], 1, "incomplete"),
        (["invalid_only"], ["quote_mismatch", "withdrawn"], 0, "warn"),
        (["cited", "invalid_only"], ["ok", "quote_mismatch"], 0, "warn"),
        (["cited", "uncited"], ["ok", "unavailable", "malformed"], 0, "fail"),
        (["cited"], ["ok", "forged"], 0, "fail"),
    ],
)
def test_the_overall_verdict_follows_section_9(
    statuses: list[str], verdicts: list[str], retracted_ok: int, expected: str
) -> None:
    assert oracle.overall_verdict(statuses, verdicts, retracted_ok) == expected


def test_metrics_follow_section_8() -> None:
    statuses = ["cited", "uncited", "invalid_only", "cited"]
    citations = [
        ("ok", True, False),
        ("ok", False, True),
        ("quote_mismatch", True, False),
        ("forged", True, False),  # a quote that never reaches the quote check
        ("unavailable", False, False),
    ]
    assert oracle.metrics(statuses, citations) == {
        "citation_validity": 2 / 4,
        "sentence_coverage": 2 / 4,
        "quote_accuracy": 1 / 2,
        "fabricated": 1,
        "retracted_cited": 1,
    }
    assert oracle.metrics([], []) == {
        "citation_validity": None,
        "sentence_coverage": None,
        "quote_accuracy": None,
        "fabricated": 0,
        "retracted_cited": 0,
    }


def test_fold_removes_invisible_characters_and_folds_the_rarer_variants() -> None:
    text = "a" + chr(0x00AD) + "b" + chr(0x200B) + "c" + chr(0x2012) + chr(0x2032)
    assert oracle.fold(text) == "abc-'"
    assert frozenset(map(chr, (0x00AD, 0x200B, 0x2060, 0xFEFF))) == oracle.REMOVED
