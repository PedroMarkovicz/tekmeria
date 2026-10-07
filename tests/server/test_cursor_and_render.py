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
from medcite.server.cursor import decode_cursor, encode_cursor
from medcite.server.render import render_audit, render_error
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


def test_render_error_includes_hint() -> None:
    error = ToolError(code="invalid_input", message="bad", retryable=False, hint="do this")
    assert render_error(error) == "error invalid_input: bad\nhint: do this"
