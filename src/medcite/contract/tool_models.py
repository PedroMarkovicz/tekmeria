"""Structured outputs of the MCP tools (spec section 6.2)."""

from pydantic import BaseModel, Field

from medcite.contract.audit_models import HandleCheck
from medcite.contract.models import (
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
    total: int = Field(description="Total matches upstream, not just this page")
    next_cursor: str | None = None
    source_status: dict[str, SourceStatusValue]


class RecordPassages(BaseModel):
    record: Record
    passages: list[Passage]


class FetchResult(BaseModel):
    records: list[RecordPassages]
    not_found: list[str] = Field(default_factory=list[str])
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
