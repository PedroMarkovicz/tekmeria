"""Acceptance criterion 2: the offline demo catches all six planted cases."""

from pathlib import Path

import pytest
from mcp import Client

from medcite.__main__ import main
from medcite.demo.scenario import run_demo
from tests.conftest import structured

pytestmark = pytest.mark.anyio


async def test_demo_detects_every_planted_case(client: Client) -> None:
    run = await run_demo(client)
    report = structured(run.audit)
    claims = report["claims"]
    assert [c["status"] for c in claims] == [
        "cited",
        "invalid_only",
        "invalid_only",
        "invalid_only",
        "cited",
        "uncited",
    ]
    verdicts = [[x["verdict"] for x in c["citations"]] for c in claims]
    assert verdicts == [["ok"], ["quote_mismatch"], ["forged"], ["malformed"], ["ok"], []]
    assert [f["name"] for f in claims[4]["citations"][0]["flags"]] == ["retracted"]
    assert "does not increase the risk" in claims[1]["citations"][0]["closest_match"]

    s = report["summary"]
    assert (
        s["sentences"],
        s["cited_sentences"],
        s["uncited_sentences"],
        s["invalid_only_sentences"],
    ) == (6, 2, 1, 3)
    assert s["citations"] == 5
    assert s["by_verdict"] == {
        "malformed": 1,
        "forged": 1,
        "unavailable": 0,
        "withdrawn": 0,
        "quote_mismatch": 1,
        "ok": 2,
    }
    assert s["citation_validity"] == pytest.approx(2 / 5)
    assert s["sentence_coverage"] == pytest.approx(2 / 6)
    assert (s["quotes_checked"], s["quotes_ok"], s["quote_accuracy"]) == (2, 1, 0.5)
    assert (s["fabricated"], s["retracted_cited"]) == (1, 1)
    assert report["verdict"] == "fail"


def test_cli_demo_prints_report_and_writes_json(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    out = tmp_path / "report.json"
    main(["demo", "--json-out", str(out)])
    printed = capsys.readouterr().out
    assert "verdict: FAIL" in printed and "forged" in printed
    assert '"verdict": "fail"' in out.read_text(encoding="utf-8")
