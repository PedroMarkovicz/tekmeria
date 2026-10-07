from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from medcite.contract.audit_models import AuditReport, CitationIn
from medcite.contract.models import Passage, Provenance, Quality, Record, RecordIds, Signal
from medcite.contract.tool_models import FetchResult, LiteratureSearchResult


def test_signal_constructors() -> None:
    assert Signal[bool].known(False, "pubmed").model_dump() == {
        "state": "known",
        "value": False,
        "sources": ["pubmed"],
    }
    assert Signal[bool].unknown("pubmed").value is None
    assert Signal[bool].not_checked().sources == []


def test_quality_defaults_to_not_checked_never_to_empty_facts() -> None:
    quality = Quality()
    for name in Quality.model_fields:
        assert getattr(quality, name).state == "not_checked", name


def test_record_and_passage_round_trip_through_json() -> None:
    record = Record(
        record_id="pmid:1",
        kind="journal_article",
        ids=RecordIds(pmid="1"),
        title="t",
        provenance=Provenance(source="pubmed", endpoint="efetch", retrieved_at=datetime.now(UTC)),
    )
    assert Record.model_validate_json(record.model_dump_json()) == record
    passage = Passage(handle="pmid:1/title~abcde", record_id="pmid:1", path="title", text="t")
    assert passage.entities.state == "not_checked"


def test_blank_quote_becomes_none() -> None:
    assert CitationIn(handle="h", quote="   ").quote is None


def test_unknown_record_kind_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Record.model_validate(
            {
                "record_id": "x:1",
                "kind": "blog_post",
                "ids": {},
                "title": "t",
                "provenance": {
                    "source": "s",
                    "endpoint": "e",
                    "retrieved_at": "2026-01-01T00:00:00Z",
                },
            }
        )


@pytest.mark.parametrize("model", [AuditReport, FetchResult, LiteratureSearchResult])
def test_output_models_produce_json_schemas(model: type) -> None:
    schema = model.model_json_schema()  # type: ignore[attr-defined]
    assert schema["type"] == "object"
