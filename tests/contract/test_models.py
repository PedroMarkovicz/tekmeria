from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from medcite.contract.audit_models import AuditReport, CitationIn
from medcite.contract.models import Passage, Provenance, Quality, Record, RecordIds, Signal
from medcite.contract.tool_models import (
    FetchResult,
    LabelHit,
    LabelSearchResult,
    LiteratureSearchResult,
    TrialHit,
    TrialSearchResult,
)


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


def test_trial_and_label_hits_round_trip() -> None:
    trial = TrialHit(
        record_id="nct:NCT04368728@2026-03-10",
        title="A trial",
        status="COMPLETED",
        phases=["PHASE2", "PHASE3"],
        enrollment=46969,
        conditions=["COVID-19"],
        interventions=["BNT162b2"],
        has_results=True,
        linked_pmids=["33301246"],
    )
    label = LabelHit(
        record_id="setid:0cbce382-9c88-4f58-ae0f-532a841e8f95@20",
        title="Warfarin Sodium",
        brand_names=["Warfarin Sodium"],
        generic_names=["WARFARIN SODIUM"],
        manufacturer="Teva Pharmaceuticals USA, Inc.",
        effective_date="2025-06-17",
        routes=["ORAL"],
        rxcui=["855288"],
        has_boxed_warning=True,
    )
    for model in (
        TrialSearchResult(hits=[trial], total=1, source_status={"clinicaltrials": "ok"}),
        LabelSearchResult(hits=[label], total=1, source_status={"openfda": "ok"}),
    ):
        assert type(model).model_validate_json(model.model_dump_json()) == model
