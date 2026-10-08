"""The real records the evaluation audits against (spec section 16.9).

They are fetched once through the evidence service, from the recorded fixtures, and then
served from memory: every audit in the evaluation sees the same records, and no case
depends on the network or on how the audit batches its lookups.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tekmerion.contract.audit import Resolution, Resolved, Unavailable, Withdrawn
from tekmerion.contract.handles import Locator
from tekmerion.contract.models import Passage, Record
from tekmerion.contract.tool_models import RecordPassages
from tekmerion.sources.service import EvidenceService

# Record IDs fetched together, and whether with full text: these are the recorded fetches.
SNAPSHOT_FETCHES: tuple[tuple[tuple[str, ...], bool], ...] = (
    (("pmid:30831578", "pmid:9500320"), False),  # a cohort study, and a retracted paper
    (("pmid:42530928",), True),  # an open-access article with its full text
    (("ppr:PPR1297067",), False),  # a preprint
    (("nct:NCT04363697",), False),  # a trial with posted results
    (("setid:7c4deb97-9966-4c8e-9ba6-bb260b71e130",), False),  # a drug label
)
# Genuine handles to this record are "unavailable": its source is down for the whole run.
OFFLINE_RECORD = "pmid:40881072"


def record_key(record_id: str) -> str:
    """'nct:NCT04363697@2026-06-16' -> 'nct:NCT04363697'."""
    return record_id.split("@", 1)[0]


@dataclass(frozen=True)
class Snapshot:
    records: Mapping[str, RecordPassages]  # by record key, without version

    def passages(self) -> list[tuple[Record, Passage]]:
        return [(item.record, p) for item in self.records.values() for p in item.passages]


async def load_snapshot(service: EvidenceService) -> Snapshot:
    """Fetch the snapshot records; fail loudly if any is missing, so no case is built
    on a record the run could not see."""
    records: dict[str, RecordPassages] = {}
    for record_ids, full_text in SNAPSHOT_FETCHES:
        page = await service.fetch_passages(
            list(record_ids), sections=None, max_passages=1000, offset=0, full_text=full_text
        )
        if page.not_found or page.unavailable:
            missing = [*page.not_found, *page.unavailable]
            raise RuntimeError(f"snapshot records missing: {', '.join(missing)}")
        for item in page.records:
            records[record_key(item.record.record_id)] = item
    return Snapshot(records=records)


class SnapshotResolver:
    """Resolves handles against the snapshot, as the evidence service would upstream."""

    def __init__(
        self, snapshot: Snapshot, *, offline: frozenset[str] = frozenset({OFFLINE_RECORD})
    ) -> None:
        self._snapshot = snapshot
        self._offline = offline

    async def resolve(self, locators: Sequence[Locator]) -> Mapping[Locator, Resolution]:
        results: dict[Locator, Resolution] = {}
        for locator in locators:
            key = locator.record.key
            if key in self._offline:
                results[locator] = Unavailable("source down for this run")
                continue
            item = self._snapshot.records.get(key)
            if item is None:
                results[locator] = Withdrawn("record not found upstream")
                continue
            passage = next((p for p in item.passages if p.path == locator.path), None)
            if passage is None:
                results[locator] = Withdrawn(f"passage {locator.path} not found in record")
                continue
            results[locator] = Resolved(passage=passage, record=item.record)
        return results
