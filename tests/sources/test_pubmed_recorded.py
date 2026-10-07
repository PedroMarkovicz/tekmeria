"""Parser checks against real recorded PubMed records."""

import json
from datetime import UTC, datetime

from medcite.demo.scenario import COHORT_PMID, FIXTURES_DIR, RETRACTED_PMID
from medcite.sources.base import RecordBundle
from medcite.sources.pubmed_parse import parse_efetch

NOW = datetime(2026, 10, 7, tzinfo=UTC)


def _fixture_bundles() -> dict[str, RecordBundle]:
    bundles: dict[str, RecordBundle] = {}
    for path in FIXTURES_DIR.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if "efetch.fcgi" in data.get("url", ""):
            for bundle in parse_efetch(data["body"], NOW):
                bundles[bundle.record.record_id] = bundle
    return bundles


def test_recorded_retracted_article() -> None:
    record = _fixture_bundles()[f"pmid:{RETRACTED_PMID}"].record
    retraction = record.quality.retraction
    assert retraction.state == "known" and retraction.value is not None
    assert retraction.value.retracted is True
    assert retraction.value.notice_doi == "10.1016/S0140-6736(10)60175-4"
    assert ("retracted_by", "pmid:20137807") in [(link.rel, link.target) for link in record.links]


def test_recorded_structured_abstract() -> None:
    bundle = _fixture_bundles()[f"pmid:{COHORT_PMID}"]
    labels = [p.section_label for p in bundle.passages if p.path.startswith("abstract.")]
    assert labels[:2] == ["BACKGROUND", "OBJECTIVE"]
    assert "RESULTS" in labels and "CONCLUSION" in labels
    assert bundle.record.quality.study_designs.value == ["other"]
    assert bundle.record.published is not None
    assert bundle.record.published.date == "2019-04-16"
