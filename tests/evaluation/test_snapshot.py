from pathlib import Path

import pytest

from tekmerion.contract.audit import Resolved, Unavailable, Withdrawn
from tekmerion.contract.handles import HandleCodec, Locator, parse_record_id
from tekmerion.evaluation.snapshot import (
    OFFLINE_RECORD,
    Snapshot,
    SnapshotResolver,
    record_key,
)
from tekmerion.infra.keys import DEMO_KEY

pytestmark = pytest.mark.anyio

CODEC = HandleCodec(DEMO_KEY)
CONTRACT = (Path(__file__).resolve().parents[2] / "docs" / "CONTRACT.md").read_text(
    encoding="utf-8"
)


async def test_the_snapshot_holds_the_recorded_records(snapshot: Snapshot) -> None:
    assert sorted(snapshot.records) == [
        "nct:NCT04363697",
        "pmcid:PMC12585062",
        "pmid:30831578",
        "pmid:41187138",
        "pmid:9500320",
        "ppr:PPR1297067",
        "setid:7c4deb97-9966-4c8e-9ba6-bb260b71e130",
    ]
    assert len(snapshot.passages()) == 213


async def test_the_contract_examples_name_passages_of_the_snapshot(snapshot: Snapshot) -> None:
    # docs/CONTRACT.md says its example handles resolve with the demo.
    block = CONTRACT.split("Examples", 1)[1].split("```", 2)[1]
    examples = [line.strip() for line in block.splitlines() if "~" in line]
    assert len(examples) == 4
    for handle in examples:
        record_id, path = handle.rsplit("~", 1)[0].split("/", 1)
        item = snapshot.records[record_key(record_id)]
        assert path in {p.path for p in item.passages}, handle


async def test_the_resolver_answers_as_the_service_would(snapshot: Snapshot) -> None:
    _, passage = snapshot.passages()[0]
    genuine = CODEC.verify(passage.handle)
    missing_path = genuine.record.at("abstract.999")
    unknown_record = Locator("pmid", "99999999", None, "abstract.1")
    offline = parse_record_id(OFFLINE_RECORD).at("abstract.1")
    got = await SnapshotResolver(snapshot).resolve([genuine, missing_path, unknown_record, offline])
    resolved = got[genuine]
    assert isinstance(resolved, Resolved) and resolved.passage == passage
    assert isinstance(got[missing_path], Withdrawn)
    assert isinstance(got[unknown_record], Withdrawn)
    assert isinstance(got[offline], Unavailable)
