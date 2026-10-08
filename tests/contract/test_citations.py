import pytest
from pydantic import ValidationError

from medcite.contract.citations import parse_answer_text

H1 = "pmid:30831578/abstract.7~7hq3k"
H2 = "pmid:9500320/abstract.3~p0c8m"


def test_citation_group_attaches_to_its_sentence() -> None:
    claims = parse_answer_text(f"MMR vaccination did not raise autism risk [{H1}]. It is safe.")
    assert [c.sentence for c in claims] == [
        "MMR vaccination did not raise autism risk.",
        "It is safe.",
    ]
    assert [c.handle for c in claims[0].citations] == [H1]
    assert claims[1].citations == []


def test_multiple_handles_and_quotes() -> None:
    text = f'Risk was unchanged [{H1} "hazard ratio, 0.93"; {H2}].'
    (claim,) = parse_answer_text(text)
    assert [(c.handle, c.quote) for c in claim.citations] == [
        (H1, "hazard ratio, 0.93"),
        (H2, None),
    ]


def test_curly_quotes_and_semicolon_inside_quote() -> None:
    text = f"Risk was unchanged [{H1} \u201cdesign; setting\u201d]."
    (claim,) = parse_answer_text(text)
    assert claim.citations[0].quote == "design; setting"


def test_numeric_brackets_are_plain_text() -> None:
    (claim,) = parse_answer_text("Prior work [1] found nothing.")
    assert claim.sentence == "Prior work [1] found nothing."
    assert claim.citations == []


def test_citation_after_full_stop_belongs_to_previous_sentence() -> None:
    claims = parse_answer_text(f"First claim. [{H1}] Second claim.")
    assert [c.sentence for c in claims] == ["First claim.", "Second claim."]
    assert [c.handle for c in claims[0].citations] == [H1]
    assert claims[1].citations == []


def test_trailing_group_after_last_sentence() -> None:
    claims = parse_answer_text(f"Only claim. [{H1}]")
    assert len(claims) == 1
    assert [c.handle for c in claims[0].citations] == [H1]


def test_biomedical_abbreviations_do_not_split() -> None:
    text = f"Smith et al. gave 2.5 mg vs. placebo [{H1}]. Nausea, e.g. mild, occurred [{H2}]."
    claims = parse_answer_text(text)
    assert len(claims) == 2


def test_malformed_entry_is_kept_verbatim() -> None:
    (claim,) = parse_answer_text("Claim [pmid:30831578-abstract].")
    assert claim.citations[0].handle == "pmid:30831578-abstract"


def test_empty_text() -> None:
    assert parse_answer_text("") == []


def test_comma_separated_handles() -> None:
    (claim,) = parse_answer_text(f"Risk was unchanged [{H1}, {H2}].")
    assert [c.handle for c in claim.citations] == [H1, H2]


def test_markdown_wrapped_handles_are_unwrapped() -> None:
    (claim,) = parse_answer_text(f"Risk was unchanged [`{H1}`; **{H2}**].")
    assert [c.handle for c in claim.citations] == [H1, H2]


def test_repeated_sentences_stay_separate_claims() -> None:
    claims = parse_answer_text(f"Risk was unchanged [{H1}]. Risk was unchanged [{H2}].")
    assert [[c.handle for c in claim.citations] for claim in claims] == [[H1], [H2]]


def test_quote_containing_brackets_keeps_its_citation() -> None:
    text = f'Mean age was six [{H1} "12 children (mean age 6 years [range 3-10], 11 boys)"].'
    (claim,) = parse_answer_text(text)
    assert [(c.handle, c.quote) for c in claim.citations] == [
        (H1, "12 children (mean age 6 years [range 3-10], 11 boys)")
    ]


def test_bare_handle_outside_brackets_is_still_a_citation() -> None:
    (claim,) = parse_answer_text(f"Risk was unchanged {H1}.")
    assert [c.handle for c in claim.citations] == [H1]


def test_straight_quotes_inside_a_quote() -> None:
    (claim,) = parse_answer_text(f'Risk was unchanged [{H1} "a "short" quote"].')
    assert [(c.handle, c.quote) for c in claim.citations] == [(H1, 'a "short" quote')]


def test_backticks_with_a_quote() -> None:
    (claim,) = parse_answer_text(f'Risk was unchanged [`{H1}` "hazard ratio, 0.93"].')
    assert [(c.handle, c.quote) for c in claim.citations] == [(H1, "hazard ratio, 0.93")]


def test_unparseable_long_entry_does_not_raise() -> None:
    (claim,) = parse_answer_text("Claim [pmid:1 " + "x" * 400 + "].")
    assert len(claim.citations) == 1 and len(claim.citations[0].handle) <= 300


H1 = "pmid:1/title~abcde"
H2 = "pmid:2/title~fghjk"


def _claims(text: str) -> list[tuple[str, list[str]]]:
    return [(c.sentence, [x.handle for x in c.citations]) for c in parse_answer_text(text)]


def test_a_bold_lead_in_is_a_sentence_of_its_own() -> None:
    text = f"**Danish cohort (2019).** This study followed 657 children [{H1}]."
    assert _claims(text) == [
        ("Danish cohort (2019).", []),
        ("This study followed 657 children.", [H1]),
    ]


def test_headings_and_bold_only_lines_are_sentences_of_their_own() -> None:
    text = f"## Results\n\nRisk was unchanged [{H1}].\n\n**Safety**\n\nSeizures were rare [{H2}]."
    assert _claims(text) == [
        ("Results", []),
        ("Risk was unchanged.", [H1]),
        ("Safety", []),
        ("Seizures were rare.", [H2]),
    ]


def test_an_uncited_bold_claim_is_still_audited() -> None:
    # Dropping "labels" would let a model hide an uncited claim behind bold text.
    text = f"**MMR causes autism.** Other text [{H1}]."
    assert _claims(text) == [("MMR causes autism.", []), ("Other text.", [H1])]


def test_a_label_with_a_citation_stays_a_claim() -> None:
    text = f"**MMR is safe [{H1}].** More text [{H2}]."
    assert _claims(text) == [("MMR is safe.", [H1]), ("More text.", [H2])]


def test_emphasis_markers_are_removed_from_sentences() -> None:
    assert _claims(f"Risk was **not** raised [{H1}].") == [("Risk was not raised.", [H1])]


def test_a_bracketed_url_is_plain_text() -> None:
    (claim,) = parse_answer_text("See the registry [https://clinicaltrials.gov/study/NCT1].")
    assert claim.citations == []
    assert claim.sentence == "See the registry [https://clinicaltrials.gov/study/NCT1]."


@pytest.mark.parametrize("closing", [".", ",", ";", ":"])
def test_punctuation_after_a_handle_is_not_part_of_it(closing: str) -> None:
    (claim,) = parse_answer_text(f"Risk was unchanged [{H1}{closing}]")
    assert [c.handle for c in claim.citations] == [H1]


@pytest.mark.parametrize(
    "entry",
    [
        f"{H1} 'hazard ratio 0.93'",
        f"{H1} \u2018hazard ratio 0.93\u2019",
        f'{H1}"hazard ratio 0.93"',
        f"`{H1}`'hazard ratio 0.93'",
    ],
)
def test_quote_forms_models_write(entry: str) -> None:
    (claim,) = parse_answer_text(f"Risk was unchanged [{entry}].")
    assert [(c.handle, c.quote) for c in claim.citations] == [(H1, "hazard ratio 0.93")]


def test_an_apostrophe_inside_a_single_quoted_quote() -> None:
    (claim,) = parse_answer_text(f"Risk was unchanged [{H1} 'the children's cohort'].")
    assert claim.citations[0].quote == "the children's cohort"


@pytest.mark.parametrize("separator", [",", ";"])
def test_a_single_quoted_quote_may_contain_a_separator(separator: str) -> None:
    text = f"Risk was unchanged [{H1} 'hazard ratio{separator} 0.93', {H2}]."
    (claim,) = parse_answer_text(text)
    assert [(c.handle, c.quote) for c in claim.citations] == [
        (H1, f"hazard ratio{separator} 0.93"),
        (H2, None),
    ]


def test_a_handle_whose_quote_holds_brackets_is_still_audited() -> None:
    (claim,) = parse_answer_text(f"Risk was unchanged [{H1} 'range [3-10]'].")
    assert H1 in [c.handle for c in claim.citations]


def test_citations_moved_to_the_previous_sentence_obey_the_limit() -> None:
    ten = ", ".join([H2] * 10)
    with pytest.raises(ValidationError):
        parse_answer_text(f"First claim [{H1}]. [{ten}] Second claim.")
