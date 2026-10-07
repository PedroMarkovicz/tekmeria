import pytest

from medcite.demo.scenario import DemoError, require_complete


def test_recording_refuses_a_degraded_result() -> None:
    with pytest.raises(DemoError, match="pubmed"):
        require_complete({"source_status": {"pubmed": "unavailable", "europepmc": "ok"}})
    with pytest.raises(DemoError, match="crossref"):
        require_complete({"source_status": {"crossref": "degraded"}})


def test_recording_accepts_complete_results() -> None:
    data = {"source_status": {"pubmed": "ok", "europepmc": "ok"}, "hits": []}
    assert require_complete(data) is data
    assert require_complete({"verdict": "fail"}) == {"verdict": "fail"}
