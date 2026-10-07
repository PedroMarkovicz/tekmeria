"""Registry behaviour against recorded responses: trial and label search, fetch and audit."""

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from mcp import Client
from mcp.types import TextResourceContents

from medcite.contract.handles import HandleCodec
from medcite.demo.scenario import LABEL_SEARCH, NO_LABEL_SEARCH, TRIAL_SEARCH
from medcite.infra.keys import DEMO_KEY
from tests.conftest import structured

pytestmark = pytest.mark.anyio

CODEC = HandleCodec(DEMO_KEY)


async def _call(client: Client, name: str, args: dict[str, Any]) -> dict[str, Any]:
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    result = await client.call_tool(name, args)
    data = structured(result)
    Draft202012Validator(tools[name].output_schema or {}).validate(data)
    assert result.content and result.content[0].type == "text" and result.content[0].text
    return data


async def test_trial_search_pages_without_repeats(client: Client) -> None:
    first = await _call(client, "search_trials", TRIAL_SEARCH)
    assert first["source_status"] == {"clinicaltrials": "ok"}
    assert len(first["hits"]) == TRIAL_SEARCH["limit"] and first["total"] > len(first["hits"])
    assert all(hit["has_results"] for hit in first["hits"])
    assert all(
        hit["record_id"].startswith("nct:NCT") and "@" in hit["record_id"] for hit in first["hits"]
    )
    second = await _call(client, "search_trials", {**TRIAL_SEARCH, "cursor": first["next_cursor"]})
    first_ids = {hit["record_id"] for hit in first["hits"]}
    assert second["hits"] and first_ids.isdisjoint(hit["record_id"] for hit in second["hits"])


async def test_label_search_pages_and_flags_boxed_warnings(client: Client) -> None:
    first = await _call(client, "search_drug_labels", LABEL_SEARCH)
    assert first["source_status"] == {"openfda": "ok"} and first["next_cursor"]
    assert any(hit["has_boxed_warning"] for hit in first["hits"])
    assert all(hit["record_id"].startswith("setid:") for hit in first["hits"])
    second = await _call(
        client, "search_drug_labels", {**LABEL_SEARCH, "cursor": first["next_cursor"]}
    )
    first_ids = {hit["record_id"] for hit in first["hits"]}
    assert second["hits"] and first_ids.isdisjoint(hit["record_id"] for hit in second["hits"])


async def test_label_search_without_matches_is_empty(client: Client) -> None:
    data = await _call(client, "search_drug_labels", NO_LABEL_SEARCH)
    assert (data["hits"], data["total"], data["next_cursor"]) == ([], 0, None)


async def test_trial_passages_cover_eligibility_outcomes_and_results(
    client: Client, scenario: dict[str, str]
) -> None:
    record_id = scenario["trial_record_id"]
    data = await _call(client, "fetch_passages", {"record_ids": [record_id], "max_passages": 200})
    (item,) = data["records"]
    assert item["record"]["record_id"] == record_id
    assert item["record"]["kind"] == "trial_registration"
    assert {link["rel"] for link in item["record"]["links"]} == {"has_results_in"}
    paths = [p["path"] for p in item["passages"]]
    for prefix in ("summary.", "eligibility.", "outcome.primary.", "results.outcome."):
        assert any(path.startswith(prefix) for path in paths), prefix
    for passage in item["passages"]:
        assert CODEC.verify(passage["handle"]).path == passage["path"]
    results = await _call(
        client, "fetch_passages", {"record_ids": [record_id], "sections": ["results"]}
    )
    assert all(p["path"].startswith("results.") for p in results["records"][0]["passages"])


async def test_label_passages_and_record(client: Client, scenario: dict[str, str]) -> None:
    record_id = scenario["label_record_id"]
    data = await _call(
        client, "fetch_passages", {"record_ids": [record_id], "sections": ["boxed_warning"]}
    )
    (item,) = data["records"]
    assert item["record"]["kind"] == "drug_label"
    assert item["record"]["extras"]["has_boxed_warning"] is True
    assert item["passages"][0]["path"] == "boxed_warning.1"
    assert item["passages"][0]["section_label"] == "Boxed warning"


async def test_audit_of_trial_and_label_citations_passes(
    client: Client, scenario: dict[str, str]
) -> None:
    fetched = await _call(
        client,
        "fetch_passages",
        {
            "record_ids": [scenario["trial_record_id"], scenario["label_record_id"]],
            "sections": ["summary", "boxed_warning"],
        },
    )
    trial, label = (item["passages"][0] for item in fetched["records"])
    quote = " ".join(label["text"].split()[:6])
    answer = (
        f"The trial is described [{trial['handle']}]. "
        f'The label warns [{label["handle"]} "{quote}"].'
    )
    report = await _call(client, "audit_answer", {"answer_text": answer})
    assert report["verdict"] == "pass"
    assert [c["verdict"] for claim in report["claims"] for c in claim["citations"]] == ["ok", "ok"]


async def test_record_resource_for_a_trial(client: Client, scenario: dict[str, str]) -> None:
    uri = f"evidence://record/{scenario['trial_record_id']}"
    (content,) = (await client.read_resource(uri)).contents
    assert isinstance(content, TextResourceContents)
    assert json.loads(content.text)["kind"] == "trial_registration"
