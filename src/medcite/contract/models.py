"""Evidence model: records, passages and signals (spec section 5)."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue

SignalState = Literal["known", "unknown", "not_checked"]
StudyDesign = Literal[
    "meta_analysis",
    "systematic_review",
    "randomized_controlled_trial",
    "clinical_trial",
    "observational_study",
    "case_report",
    "review",
    "guideline",
    "other",
]
RecordKind = Literal["journal_article", "preprint", "trial_registration", "drug_label"]
LinkRel = Literal[
    "results_of", "has_results_in", "retracted_by", "preprint_of", "published_as", "erratum_in"
]
EntityType = Literal["gene", "disease", "chemical", "species", "mutation", "cell_line"]
DatePrecision = Literal["day", "month", "year"]
SourceStatusValue = Literal["ok", "degraded", "unavailable"]
ErrorCode = Literal[
    "invalid_input",
    "not_found",
    "upstream_unavailable",
    "upstream_error",
    "rate_limited",
    "deadline_exceeded",
    "internal",
]


class Signal[T](BaseModel):
    """A value that may be missing, with the reason it is missing (spec 5.1)."""

    model_config = ConfigDict(frozen=True)

    state: SignalState
    value: T | None = None
    sources: list[str] = Field(default_factory=list)

    @classmethod
    def known(cls, value: T, *sources: str) -> Self:
        return cls(state="known", value=value, sources=list(sources))

    @classmethod
    def unknown(cls, *sources: str) -> Self:
        return cls(state="unknown", sources=list(sources))

    @classmethod
    def not_checked(cls) -> Self:
        return cls(state="not_checked")


class PublishedDate(BaseModel):
    date: str
    precision: DatePrecision


class Provenance(BaseModel):
    source: str
    endpoint: str
    retrieved_at: datetime
    upstream_version: str | None = None


class Retraction(BaseModel):
    retracted: bool
    notice_doi: str | None = None


class OpenAccess(BaseModel):
    available: bool
    license: str | None = None


class Quality(BaseModel):
    study_designs: Signal[list[StudyDesign]] = Field(
        default_factory=lambda: Signal[list[StudyDesign]].not_checked()
    )
    publication_types: Signal[list[str]] = Field(
        default_factory=lambda: Signal[list[str]].not_checked()
    )
    peer_reviewed: Signal[bool] = Field(default_factory=lambda: Signal[bool].not_checked())
    retraction: Signal[Retraction] = Field(default_factory=lambda: Signal[Retraction].not_checked())
    medline_indexed: Signal[bool] = Field(default_factory=lambda: Signal[bool].not_checked())
    open_access_full_text: Signal[OpenAccess] = Field(
        default_factory=lambda: Signal[OpenAccess].not_checked()
    )


class Link(BaseModel):
    rel: LinkRel
    target: str


class RecordIds(BaseModel):
    pmid: str | None = None
    pmcid: str | None = None
    doi: str | None = None
    ppr: str | None = None
    nct: str | None = None
    setid: str | None = None


class Record(BaseModel):
    record_id: str
    kind: RecordKind
    ids: RecordIds
    title: str
    published: PublishedDate | None = None
    venue: str | None = None
    provenance: Provenance
    quality: Quality = Field(default_factory=Quality)
    links: list[Link] = Field(default_factory=list[Link])
    extras: dict[str, JsonValue] = Field(default_factory=dict)


class Entity(BaseModel):
    id: str
    type: EntityType
    text: str
    span: tuple[int, int]


class Passage(BaseModel):
    handle: str
    record_id: str
    path: str
    section_label: str | None = None
    text: str
    entities: Signal[list[Entity]] = Field(
        default_factory=lambda: Signal[list[Entity]].not_checked()
    )


class ToolError(BaseModel):
    code: ErrorCode
    message: str
    retryable: bool
    hint: str | None = None
