from collections import Counter

import pytest

from tekmeria.contract.citations import parse_answer_text
from tekmeria.evaluation.answers import Answer, compose_answers, expected_result
from tekmeria.evaluation.cases import Planted, generate_cases
from tekmeria.evaluation.snapshot import Snapshot
from tekmeria.infra.keys import DEMO_KEY

pytestmark = pytest.mark.anyio


async def test_every_text_case_goes_into_one_mixed_answer(snapshot: Snapshot) -> None:
    cases = generate_cases(snapshot, DEMO_KEY, seed=0, per_category=3)
    mixed = [a for a in compose_answers(cases, seed=0) if a.kind == "mixed"]
    planted = Counter(p for a in mixed for cited in a.sentences for p in cited)
    expected = Counter(p for case in cases if "text" in case.modes for p in case.citations)
    assert planted == expected


async def test_an_answer_parses_into_its_planned_sentences(snapshot: Snapshot) -> None:
    cases = generate_cases(snapshot, DEMO_KEY, seed=0, per_category=3)
    for answer in compose_answers(cases, seed=0):
        claims = parse_answer_text(answer.text)
        got = [[(c.handle, c.quote) for c in claim.citations] for claim in claims]
        planned = [[(p.handle, p.quote) for p in cited] for cited in answer.sentences]
        assert got == planned, answer.text


async def test_answers_use_headings_lead_ins_and_uncited_sentences(snapshot: Snapshot) -> None:
    cases = generate_cases(snapshot, DEMO_KEY, seed=0, per_category=3)
    text = "\n".join(a.text for a in compose_answers(cases, seed=0) if a.kind == "mixed")
    assert "## Findings" in text and "**Point" in text and "Further studies are needed." in text


async def test_clean_answers_carry_only_genuine_citations(snapshot: Snapshot) -> None:
    cases = generate_cases(snapshot, DEMO_KEY, seed=0, per_category=3)
    clean = [a for a in compose_answers(cases, seed=0) if a.kind == "clean"]
    assert clean
    for answer in clean:
        assert expected_result(answer).verdict == "pass"


def test_the_expected_result_follows_the_contract() -> None:
    ok = Planted("pmid:1/abstract.1~aaaaa", "some words", "ok")
    forged = Planted("pmid:1/abstract.2~bbbbb", None, "forged")
    retracted = Planted("pmid:2/abstract.1~ccccc", None, "ok", ("retracted",))
    answer = Answer(kind="mixed", text="", sentences=((), (ok, forged), (forged,), (retracted,)))
    expected = expected_result(answer)
    assert expected.statuses == ["uncited", "cited", "invalid_only", "cited"]
    assert expected.citations == [("ok", []), ("forged", []), ("forged", []), ("ok", ["retracted"])]
    assert expected.verdict == "fail"
    assert expected.metrics == {
        "citation_validity": 2 / 4,
        "sentence_coverage": 2 / 4,
        "quote_accuracy": 1.0,
        "fabricated": 2,
        "retracted_cited": 1,
    }


# A category's own effect on the overall verdict, with the default fail_on: forged and
# malformed citations fail; an unavailable source makes the answer incomplete; a misquoted or
# withdrawn citation, alone or beside a genuine one, or a retracted source, warns; a genuine
# citation, even with a flag, passes.
FAILS = {
    "forged_check_code",
    "forged_path_swap",
    "forged_record_id",
    "forged_bare",
    "hidden_in_quote",
    "malformed",
    "malformed_with_note",
}


def _paired_verdict(category: str) -> str:
    if category in FAILS:
        return "fail"
    if category == "unavailable":
        return "incomplete"
    if category.startswith(("quote_", "withdrawn_")) or category in (
        "retracted_cited",
        "invalid_beside_valid",
    ):
        return "warn"
    return "pass"


async def test_a_paired_answer_shows_its_category_on_its_own(snapshot: Snapshot) -> None:
    cases = generate_cases(snapshot, DEMO_KEY, seed=0, per_category=2)
    paired = [a for a in compose_answers(cases, seed=0) if a.kind == "paired"]
    text_categories = {case.category for case in cases if "text" in case.modes}
    assert len(paired) == len(text_categories)
    for answer in paired:
        (category,) = {
            case.category
            for case in cases
            if case.citations == answer.sentences[1] and "text" in case.modes
        }
        assert expected_result(answer).verdict == _paired_verdict(category), category
