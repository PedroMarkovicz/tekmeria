import pytest

from tekmerion.demo.scenario import DemoError, require_complete, wanted


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
