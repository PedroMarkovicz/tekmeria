"""`medcite demo` writes the report and the audit view, and can open the view."""

import json
from pathlib import Path

import pytest

from medcite.__main__ import main
from medcite.contract.audit_models import AuditReport
from medcite.ui import REPORT_SLOT


def _embedded(page: str) -> dict[str, object]:
    start = page.index('id="medcite-report">') + len('id="medcite-report">')
    return json.loads(page[start : page.index("</script>", start)])


def test_demo_writes_the_report_and_the_view(tmp_path: Path) -> None:
    report_path, page_path = tmp_path / "demo-report.json", tmp_path / "site" / "index.html"
    main(["demo", "--json-out", str(report_path), "--html-out", str(page_path)])
    report = AuditReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    assert report.verdict == "fail"
    page = page_path.read_text(encoding="utf-8")
    assert REPORT_SLOT not in page
    assert _embedded(page) == json.loads(report_path.read_text(encoding="utf-8"))


def test_demo_open_shows_the_view_in_a_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    page_path = tmp_path / "view.html"
    main(["demo", "--open", "--html-out", str(page_path)])
    assert opened == [page_path.resolve().as_uri()]


def test_demo_open_without_a_path_uses_a_temporary_file(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    main(["demo", "--open"])
    (url,) = opened
    assert url.startswith("file:") and url.endswith("/demo-report.html")
