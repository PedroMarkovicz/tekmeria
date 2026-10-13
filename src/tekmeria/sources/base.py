"""What every source adapter returns, before handles are minted."""

from dataclasses import dataclass, field

from tekmeria.contract.models import Entity, Record, Signal


@dataclass(frozen=True)
class PassageDraft:
    path: str
    text: str  # already normalized
    section_label: str | None = None
    entities: Signal[list[Entity]] | None = None  # None: entities were not looked up


@dataclass(frozen=True)
class RecordBundle:
    record: Record
    passages: list[PassageDraft]


@dataclass
class FetchOutcome:
    bundles: dict[str, RecordBundle] = field(default_factory=dict[str, RecordBundle])
    missing: list[str] = field(default_factory=list[str])
