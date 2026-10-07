"""Audit input and report types (spec section 7)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from medcite.contract.models import Passage

Verdict = Literal["malformed", "forged", "unavailable", "withdrawn", "quote_mismatch", "ok"]
VERDICTS: tuple[Verdict, ...] = (
    "malformed",
    "forged",
    "unavailable",
    "withdrawn",
    "quote_mismatch",
    "ok",
)
HandleStatus = Literal["malformed", "forged", "unavailable", "withdrawn", "ok"]
FlagName = Literal["retracted", "preprint", "version_changed"]
FLAG_NAMES: tuple[FlagName, ...] = ("retracted", "preprint", "version_changed")
ClaimStatus = Literal["cited", "uncited", "invalid_only"]
FailOn = Literal[
    "malformed", "forged", "unavailable", "withdrawn", "quote_mismatch", "uncited", "retracted"
]
OverallVerdict = Literal["pass", "warn", "fail", "incomplete"]
CheckedAgainst = Literal["issued", "current"]
DEFAULT_FAIL_ON: tuple[FailOn, ...] = ("forged", "malformed")


class CitationIn(BaseModel):
    handle: str = Field(min_length=1, max_length=300)
    quote: str | None = Field(default=None, max_length=1000)

    @field_validator("quote")
    @classmethod
    def _blank_quote_is_none(cls, value: str | None) -> str | None:
        return value if value is not None and value.strip() else None


class ClaimIn(BaseModel):
    sentence: str = Field(max_length=4000)
    citations: list[CitationIn] = Field(default_factory=list[CitationIn], max_length=10)


class Flag(BaseModel):
    name: FlagName
    detail: str | None = None


class CitationResult(BaseModel):
    handle: str
    quote: str | None = None
    verdict: Verdict
    flags: list[Flag] = Field(default_factory=list[Flag])
    checked_against: CheckedAgainst | None = None
    resolved_version: str | None = None
    closest_match: str | None = None
    similarity: float | None = None
    passage: Passage | None = None
    support: Literal["not_checked"] = "not_checked"


class ClaimResult(BaseModel):
    index: int
    sentence: str
    status: ClaimStatus
    citations: list[CitationResult]


class AuditSummary(BaseModel):
    sentences: int
    cited_sentences: int
    uncited_sentences: int
    invalid_only_sentences: int
    citations: int
    by_verdict: dict[Verdict, int]
    quotes_checked: int
    quotes_ok: int
    citation_validity: float | None
    sentence_coverage: float | None
    quote_accuracy: float | None
    fabricated: int
    retracted_cited: int


class AuditReport(BaseModel):
    contract_version: str
    audited_at: datetime
    claims: list[ClaimResult]
    summary: AuditSummary
    verdict: OverallVerdict
    fail_on: list[FailOn]


class HandleCheck(BaseModel):
    handle: str
    status: HandleStatus
    passage: Passage | None = None
    flags: list[Flag] = Field(default_factory=list[Flag])
    checked_against: CheckedAgainst | None = None
    resolved_version: str | None = None
