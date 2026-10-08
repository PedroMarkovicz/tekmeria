"""Structured outputs of the MCP tools (spec section 6.2)."""

from pydantic import BaseModel, Field

from tekmerion.contract.audit_models import HandleCheck
from tekmerion.contract.models import (
    OpenAccess,
    Passage,
    PublishedDate,
    Record,
    RecordIds,
    RecordKind,
    Retraction,
    Signal,
    SourceStatusValue,
    StudyDesign,
)


class LiteratureHit(BaseModel):
    record_id: str
    kind: RecordKind
    title: str
    title_handle: str | None
    preview: str | None = Field(description="First abstract passage, at most 300 characters")
    published: PublishedDate | None
    venue: str | None
    study_designs: Signal[list[StudyDesign]]
    peer_reviewed: Signal[bool]
    retraction: Signal[Retraction]
    open_access_full_text: Signal[OpenAccess]
    ids: RecordIds


class LiteratureSearchResult(BaseModel):
    hits: list[LiteratureHit]
    total: int = Field(description="Largest single-source match count (sources overlap)")
    totals: dict[str, int] = Field(
        default_factory=dict[str, int], description="Match count per source"
    )
    next_cursor: str | None = None
    source_status: dict[str, SourceStatusValue]


class TrialHit(BaseModel):
    record_id: str = Field(description="nct:<NCT ID>@<last update post date>")
    title: str
    status: str | None = Field(description="Overall recruitment status, e.g. COMPLETED")
    phases: list[str] = Field(description="e.g. ['PHASE2', 'PHASE3']; empty when not given")
    enrollment: int | None
    conditions: list[str]
    interventions: list[str]
    has_results: bool = Field(description="Results are posted on ClinicalTrials.gov")
    linked_pmids: list[str] = Field(description="PMIDs the registry lists as results publications")


class TrialSearchResult(BaseModel):
    hits: list[TrialHit]
    total: int
    next_cursor: str | None = None
    source_status: dict[str, SourceStatusValue]


class LabelHit(BaseModel):
    record_id: str = Field(description="setid:<SPL set ID>@<label version>")
    title: str
    brand_names: list[str]
    generic_names: list[str]
    manufacturer: str | None
    effective_date: str | None = Field(description="YYYY-MM-DD")
    routes: list[str]
    rxcui: list[str]
    has_boxed_warning: bool


class LabelSearchResult(BaseModel):
    hits: list[LabelHit]
    total: int
    next_cursor: str | None = None
    source_status: dict[str, SourceStatusValue]


class RecordPassages(BaseModel):
    record: Record
    passages: list[Passage]


class FetchResult(BaseModel):
    records: list[RecordPassages]
    not_found: list[str] = Field(default_factory=list[str])
    unavailable: list[str] = Field(
        default_factory=list[str], description="Requested records whose source did not answer"
    )
    next_cursor: str | None = None
    source_status: dict[str, SourceStatusValue]


class ResolveResult(BaseModel):
    results: list[HandleCheck]


class TermInfo(BaseModel):
    name: str
    meaning: str


class ContractDescription(BaseModel):
    contract_version: str
    handle_pattern: str
    schemes: list[str]
    passage_paths: list[TermInfo]
    verdicts: list[TermInfo] = Field(description="In precedence order: first failure wins")
    flags: list[TermInfo]
    metrics: list[TermInfo]
    instructions: str
