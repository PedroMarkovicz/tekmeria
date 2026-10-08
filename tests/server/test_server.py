"""Server behaviour against recorded PubMed responses (src/tekmerion/demo/fixtures)."""

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp.types import TextResourceContents

from tekmerion.contract.handles import HandleCodec, Locator
from tekmerion.demo.scenario import COHORT_PMID, DEMO_QUERY, RETRACTED_PMID, TEST_SEARCH_ARGS, forge
from tekmerion.infra.keys import DEMO_KEY
from tests.conftest import structured

pytestmark = pytest.mark.anyio

CODEC = HandleCodec(DEMO_KEY)


async def _call(client: Client, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call a tool, check its structured output against its schema, and return it."""
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    result = await client.call_tool(name, args)
    data = structured(result)
    Draft202012Validator(tools[name].output_schema or {}).validate(data)
    assert result.content and result.content[0].type == "text" and result.content[0].text
    return data


async def _read_json(client: Client, uri: str) -> dict[str, Any]:
    (content,) = (await client.read_resource(uri)).contents
    assert isinstance(content, TextResourceContents)
    return json.loads(content.text)


async def _cohort_results_handle(client: Client) -> str:
    data = await _call(client, "fetch_passages", {"record_ids": [f"pmid:{COHORT_PMID}"]})
    passages = data["records"][0]["passages"]
    return next(p["handle"] for p in passages if p["section_label"] == "RESULTS")


async def test_search_returns_hits_with_title_handles(client: Client) -> None:
    data = await _call(client, "search_literature", {"query": DEMO_QUERY, "limit": 5})
    assert data["total"] >= 5 and len(data["hits"]) == 5
    assert CODEC.verify(data["hits"][0]["title_handle"]).path == "title"
    assert data["source_status"] == {"pubmed": "ok", "europepmc": "ok"}
    assert data["next_cursor"]


async def test_search_with_filters(client: Client) -> None:
    data = await _call(client, "search_literature", TEST_SEARCH_ARGS)
    assert data["hits"]
    for hit in data["hits"]:
        assert set(hit["study_designs"]["value"] or []) & {"meta_analysis", "systematic_review"}
        assert hit["retraction"]["value"]["retracted"] is False


async def test_search_paging_with_cursor(client: Client) -> None:
    first = await _call(client, "search_literature", {"query": DEMO_QUERY, "limit": 2})
    second = await _call(
        client,
        "search_literature",
        {"query": DEMO_QUERY, "limit": 2, "cursor": first["next_cursor"]},
    )
    first_ids = {hit["record_id"] for hit in first["hits"]}
    assert second["hits"] and first_ids.isdisjoint(hit["record_id"] for hit in second["hits"])


async def test_cursor_from_another_query_is_rejected(client: Client) -> None:
    first = await _call(client, "search_literature", {"query": DEMO_QUERY, "limit": 2})
    result = await client.call_tool(
        "search_literature", {"query": "something else", "cursor": first["next_cursor"]}
    )
    assert result.is_error and result.structured_content is not None
    assert result.structured_content["code"] == "invalid_input"


async def test_fetch_returns_records_and_abstract_passages(client: Client) -> None:
    data = await _call(
        client,
        "fetch_passages",
        {"record_ids": [f"pmid:{COHORT_PMID}", f"pmid:{RETRACTED_PMID}"], "sections": ["abstract"]},
    )
    records = {item["record"]["record_id"]: item for item in data["records"]}
    assert set(records) == {f"pmid:{COHORT_PMID}", f"pmid:{RETRACTED_PMID}"}
    retraction = records[f"pmid:{RETRACTED_PMID}"]["record"]["quality"]["retraction"]
    assert retraction["value"]["retracted"] is True
    paths = [p["path"] for p in records[f"pmid:{COHORT_PMID}"]["passages"]]
    assert paths[0] == "abstract.1" and "title" not in paths


async def test_fetch_pages_passages(client: Client) -> None:
    args = {"record_ids": [f"pmid:{COHORT_PMID}"], "max_passages": 4}
    first = await _call(client, "fetch_passages", args)
    assert len(first["records"][0]["passages"]) == 4 and first["next_cursor"]
    second = await _call(client, "fetch_passages", {**args, "cursor": first["next_cursor"]})
    assert second["records"][0]["passages"][0]["path"] == "abstract.4"


async def test_not_yet_indexed_record(client: Client, scenario: dict[str, str]) -> None:
    pmid = scenario["in_process_pmid"]
    data = await _call(client, "fetch_passages", {"record_ids": [f"pmid:{pmid}"]})
    quality = data["records"][0]["record"]["quality"]
    assert quality["medline_indexed"]["value"] is False
    assert quality["study_designs"]["state"] == "unknown"


async def test_resolve_handles(client: Client) -> None:
    handle = await _cohort_results_handle(client)
    data = await _call(client, "resolve_handles", {"handles": [handle, forge(handle), "nope"]})
    assert [r["status"] for r in data["results"]] == ["ok", "forged", "malformed"]
    assert data["results"][0]["passage"]["section_label"] == "RESULTS"
    assert data["results"][0]["checked_against"] == "issued"


async def test_audit_structured_claims(client: Client) -> None:
    handle = await _cohort_results_handle(client)
    claims = [
        {
            "sentence": "No increased risk.",
            "citations": [{"handle": handle, "quote": "6517 children"}],
        }
    ]
    report = await _call(client, "audit_answer", {"claims": claims})
    assert report["verdict"] == "pass"
    assert report["claims"][0]["citations"][0]["support"] == "not_checked"


async def test_handle_for_a_passage_that_no_longer_exists_is_withdrawn(client: Client) -> None:
    handle = CODEC.issue(Locator("pmid", COHORT_PMID, None, "abstract.99"))
    report = await _call(client, "audit_answer", {"answer_text": f"Claim [{handle}]."})
    assert report["claims"][0]["citations"][0]["verdict"] == "withdrawn"


async def test_resources(client: Client) -> None:
    handle = await _cohort_results_handle(client)
    assert (await _read_json(client, f"evidence://passage/{handle}"))["handle"] == handle
    record = await _read_json(client, f"evidence://record/pmid:{COHORT_PMID}")
    assert record["record_id"] == f"pmid:{COHORT_PMID}"
    with pytest.raises(MCPError):
        await client.read_resource(f"evidence://passage/{forge(handle)}")


async def test_each_tool_call_logs_one_line_with_its_upstream_calls(
    client: Client, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    for _ in range(2):  # the second call is answered from the cache
        await _call(client, "fetch_passages", {"record_ids": [f"pmid:{COHORT_PMID}"]})
    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    first, second = [line for line in lines if line.get("tool") == "fetch_passages"]
    assert first["event"] == "tool_ok" and isinstance(first["ms"], int)
    assert first["source_status"]["pubmed"] == "ok"
    assert {"host": "ncbi", "status": 200, "cache": False} in first["upstream"]
    # Only these three fields: never a URL, a query or a key.
    assert all(set(call) == {"host", "status", "cache"} for call in first["upstream"])
    assert second["upstream"] and all(call["cache"] for call in second["upstream"])
