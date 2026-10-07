from datetime import UTC, datetime

import pytest

from medcite.contract.audit_models import (
    AuditReport,
    AuditSummary,
    CitationResult,
    ClaimResult,
    Flag,
)
from medcite.contract.handles import HandleCodec
from medcite.contract.models import ToolError
from medcite.contract.tool_models import (
    LabelHit,
    LabelSearchResult,
    TrialHit,
    TrialSearchResult,
)
from medcite.server.cursor import decode_cursor, decode_offset, encode_cursor
from medcite.server.render import render_audit, render_error, render_labels, render_trials
from medcite.sources.errors import InvalidInputError

CODEC = HandleCodec(bytes(range(32)))
ARGS = {"query": "mmr", "limit": None}


def test_cursor_round_trip() -> None:
    cursor = encode_cursor(CODEC, "search_literature", ARGS, 20)
    assert decode_cursor(CODEC, "search_literature", ARGS, cursor) == 20


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c[:-1] + ("0" if c[-1] != "0" else "1"),  # signature edited
        lambda c: "x" + c,  # body edited
        lambda c: "not-a-cursor",
    ],
)
def test_tampered_cursor_is_rejected(mutate: object) -> None:
    cursor = encode_cursor(CODEC, "search_literature", ARGS, 20)
    with pytest.raises(InvalidInputError):
        decode_cursor(CODEC, "search_literature", ARGS, mutate(cursor))  # type: ignore[operator]


def test_cursor_is_bound_to_tool_and_arguments() -> None:
    cursor = encode_cursor(CODEC, "search_literature", ARGS, 20)
    with pytest.raises(InvalidInputError):
        decode_cursor(CODEC, "fetch_passages", ARGS, cursor)
    with pytest.raises(InvalidInputError):
        decode_cursor(CODEC, "search_literature", {**ARGS, "query": "other"}, cursor)


def test_cursor_from_another_key_is_rejected() -> None:
    cursor = encode_cursor(HandleCodec(bytes(range(1, 33))), "search_literature", ARGS, 20)
    with pytest.raises(InvalidInputError):
        decode_cursor(CODEC, "search_literature", ARGS, cursor)


def test_render_audit_lists_verdicts_flags_and_closest_match() -> None:
    summary = AuditSummary(
        sentences=1,
        cited_sentences=0,
        uncited_sentences=0,
        invalid_only_sentences=1,
        citations=1,
        by_verdict={
            "malformed": 0,
            "forged": 0,
            "unavailable": 0,
            "withdrawn": 0,
            "quote_mismatch": 1,
            "ok": 0,
        },
        quotes_checked=1,
        quotes_ok=0,
        citation_validity=0.0,
        sentence_coverage=0.0,
        quote_accuracy=0.0,
        fabricated=0,
        retracted_cited=0,
    )
    citation = CitationResult(
        handle="pmid:1/title~abcde",
        quote="x",
        verdict="quote_mismatch",
        flags=[Flag(name="retracted")],
        closest_match="y",
        similarity=0.5,
    )
    report = AuditReport(
        contract_version="0.1.0",
        audited_at=datetime(2026, 1, 1, tzinfo=UTC),
        claims=[ClaimResult(index=0, sentence="S.", status="invalid_only", citations=[citation])],
        summary=summary,
        verdict="warn",
        fail_on=["forged", "malformed"],
    )
    text = render_audit(report)
    assert text.splitlines()[0].startswith("verdict: WARN")
    assert "quote_mismatch pmid:1/title~abcde" in text and "[retracted]" in text
    assert 'closest: "y" (0.5)' in text


def test_render_trials_and_labels_lead_with_the_record_id() -> None:
    trial = TrialHit(
        record_id="nct:NCT01234567@2025-11-02",
        title="Drug X in HF",
        status="COMPLETED",
        phases=["PHASE3"],
        enrollment=300,
        conditions=[],
        interventions=[],
        has_results=True,
        linked_pmids=["33301246"],
    )
    text = render_trials(
        TrialSearchResult(
            hits=[trial], total=9, next_cursor="c", source_status={"clinicaltrials": "ok"}
        )
    )
    assert text.splitlines() == [
        "1 of 9 trials",
        "nct:NCT01234567@2025-11-02  Drug X in HF (COMPLETED, PHASE3, n=300, results posted)"
        "  results in: 33301246",
        "next_cursor: c",
    ]
    label = LabelHit(
        record_id="setid:abc@3",
        title="Coumadin (WARFARIN SODIUM)",
        brand_names=[],
        generic_names=[],
        manufacturer=None,
        effective_date=None,
        routes=[],
        rxcui=[],
        has_boxed_warning=True,
    )
    text = render_labels(LabelSearchResult(hits=[label], total=1, source_status={"openfda": "ok"}))
    assert text.splitlines() == [
        "1 of 1 labels",
        "setid:abc@3  Coumadin (WARFARIN SODIUM) (unknown manufacturer, n.d., route n/a)"
        "  BOXED WARNING",
    ]


def test_render_error_includes_hint() -> None:
    error = ToolError(code="invalid_input", message="bad", retryable=False, hint="do this")
    assert render_error(error) == "error invalid_input: bad\nhint: do this"


def test_cursor_carries_structured_state() -> None:
    state = {"p": 10, "m": "AoIIQ", "s": 3, "pd": False, "ed": True}
    cursor = encode_cursor(CODEC, "search_literature", ARGS, state)
    assert decode_cursor(CODEC, "search_literature", ARGS, cursor) == state


@pytest.mark.parametrize("state", [{"p": 1}, -1, True, "5"])
def test_decode_offset_rejects_anything_but_a_non_negative_integer(state: object) -> None:
    cursor = encode_cursor(CODEC, "fetch_passages", ARGS, state)  # type: ignore[arg-type]
    with pytest.raises(InvalidInputError):
        decode_offset(CODEC, "fetch_passages", ARGS, cursor)
