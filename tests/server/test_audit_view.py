"""The audit view as an MCP App (spec 10) and as a standalone page."""

import json
from pathlib import Path

import pytest
from mcp import Client
from mcp.server.apps import APP_MIME_TYPE, EXTENSION_ID
from mcp.types import TextResourceContents

from tekmerion.contract.handles import HandleCodec, Locator
from tekmerion.infra.fixtures import FixtureTransport
from tekmerion.server.app import create_server
from tekmerion.server.runtime import RuntimeConfig
from tekmerion.ui import AUDIT_VIEW_URI, REPORT_SLOT, audit_view_html, standalone_page
from tests.conftest import structured

pytestmark = pytest.mark.anyio

KEY = bytes(range(32))


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
async def offline(tmp_path: Path):  # type: ignore[no-untyped-def]
    config = RuntimeConfig(
        handle_key=KEY,
        cache_path=":memory:",
        transport=FixtureTransport(tmp_path),
        sleep=_no_sleep,
        rate_override=1000.0,
    )
    async with Client(create_server(config)) as client:
        yield client


async def test_audit_answer_declares_the_audit_view(offline: Client) -> None:
    tools = {tool.name: tool for tool in (await offline.list_tools()).tools}
    meta = tools["audit_answer"].meta or {}
    assert meta["ui"] == {"resourceUri": AUDIT_VIEW_URI}
    assert meta["ui/resourceUri"] == AUDIT_VIEW_URI
    assert all(
        not (tool.meta or {}).get("ui") for name, tool in tools.items() if name != "audit_answer"
    )


async def test_the_view_is_served_as_an_mcp_app(offline: Client) -> None:
    (content,) = (await offline.read_resource(AUDIT_VIEW_URI)).contents
    assert isinstance(content, TextResourceContents)
    assert content.mime_type == APP_MIME_TYPE
    assert REPORT_SLOT in content.text
    listed = {str(r.uri): r for r in (await offline.list_resources()).resources}
    assert listed[AUDIT_VIEW_URI].mime_type == APP_MIME_TYPE


async def test_the_server_advertises_the_apps_extension(offline: Client) -> None:
    extensions = offline.server_capabilities.extensions or {}
    assert EXTENSION_ID in extensions


async def test_hosts_without_apps_still_get_text_and_structured_content(offline: Client) -> None:
    handle = HandleCodec(KEY).issue(Locator("pmid", "1", None, "title"))
    result = await offline.call_tool("audit_answer", {"answer_text": f"Claim [{handle}x]."})
    report = structured(result)
    assert report["claims"][0]["citations"][0]["verdict"] == "malformed"
    assert result.content[0].type == "text" and "verdict:" in result.content[0].text


def test_the_packaged_view_has_one_report_slot() -> None:
    html = audit_view_html()
    assert html.startswith("<!doctype html>")
    assert html.count(REPORT_SLOT) == 1


def test_a_standalone_page_embeds_the_report_safely() -> None:
    report = {"claims": [], "summary": {}, "note": "</script><script>alert(1)</script>"}
    page = standalone_page(json.dumps(report))
    assert REPORT_SLOT not in page
    start = page.index('id="tekmerion-report">') + len('id="tekmerion-report">')
    embedded = page[start : page.index("</script>", start)]
    assert json.loads(embedded) == report
    assert "<script>alert(1)" not in page


def test_a_view_without_a_slot_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tekmerion.ui.audit_view_html", lambda: "<html></html>")
    with pytest.raises(RuntimeError, match="rebuild"):
        standalone_page("{}")
