"""What every source adapter returns, before handles are minted."""

from dataclasses import dataclass, field

from medcite.contract.models import Record


@dataclass(frozen=True)
class PassageDraft:
    path: str
    text: str  # already normalized
    section_label: str | None = None


@dataclass(frozen=True)
class RecordBundle:
    record: Record
    passages: list[PassageDraft]


@dataclass
class FetchOutcome:
    bundles: dict[str, RecordBundle] = field(default_factory=dict[str, RecordBundle])
    missing: list[str] = field(default_factory=list[str])
