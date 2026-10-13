import json
from pathlib import Path

import pytest

from tekmeria.demo.scenario import (
    COHORT_PMID,
    RETRACTED_PMID,
    DemoError,
    abstract_pmids,
    fetched_pmids,
    require_complete,
    wanted,
)


def test_recording_refuses_a_degraded_result() -> None:
    with pytest.raises(DemoError, match="pubmed"):
        require_complete({"source_status": {"pubmed": "unavailable", "europepmc": "ok"}})
    with pytest.raises(DemoError, match="crossref"):
        require_complete({"source_status": {"crossref": "degraded"}})


def test_recording_accepts_complete_results() -> None:
    data = {"source_status": {"pubmed": "ok", "europepmc": "ok"}, "hits": []}
    assert require_complete(data) is data
    assert require_complete({"verdict": "fail"}) == {"verdict": "fail"}


def _article(license: str | None) -> dict[str, object]:
    return {
        "kind": "journal_article",
        "ids": {"pmid": "1", "pmcid": "PMC1"},
        "open_access_full_text": {"value": {"available": True, "license": license}},
    }


def test_only_a_cc_by_article_is_recorded_as_open_access() -> None:
    # Its full text is redistributed with the fixtures, so its licence must allow it.
    assert wanted(_article("cc by"), "open_access")
    for restricted in ("cc by-nc-nd", "cc by-nc", None):
        assert not wanted(_article(restricted), "open_access"), restricted


def test_the_fetched_records_are_the_demo_papers_and_the_recorded_finds() -> None:
    scenario = {"in_process_pmid": "3", "open_access_pmid": "4", "preprint_id": "PPR5"}
    assert fetched_pmids(scenario) == {COHORT_PMID, RETRACTED_PMID, "3", "4"}


def test_abstracts_are_kept_for_fetched_records_and_open_licence_search_results(
    tmp_path: Path,
) -> None:
    body = {"resultList": {"result": [{"pmid": "5", "license": "cc by"}]}}
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=x"
    (tmp_path / "s.json").write_text(
        json.dumps({"url": url, "body": json.dumps(body)}), encoding="utf-8"
    )
    scenario = {"in_process_pmid": "3", "open_access_pmid": "4", "preprint_id": "PPR5"}
    assert abstract_pmids(scenario, tmp_path) == {COHORT_PMID, RETRACTED_PMID, "3", "4", "5"}
