"""Literature behaviour against recorded responses: Europe PMC, preprints, full text,
PubTator entities and Crossref retractions."""

from dataclasses import replace
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator
from mcp import Client

from tekmeria.__main__ import demo_config
from tekmeria.contract.handles import HandleCodec
from tekmeria.demo.scenario import (
    COHORT_PMID,
    DEMO_QUERY,
    FIXTURES_DIR,
    PUBMED_ONLY_SEARCH,
    RETRACTED_PMID,
)
from tekmeria.infra.fixtures import FixtureTransport
from tekmeria.infra.keys import DEMO_KEY
from tekmeria.server.app import create_server
from tests.conftest import structured

pytestmark = pytest.mark.anyio

CODEC = HandleCodec(DEMO_KEY)


async def _call(client: Client, name: str, args: dict[str, Any]) -> dict[str, Any]:
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    data = structured(await client.call_tool(name, args))
    Draft202012Validator(tools[name].output_schema or {}).validate(data)
    return data


class EuropePmcDown(FixtureTransport):
    """Recorded PubMed, PubTator and Crossref; Europe PMC unreachable."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.ebi.ac.uk":
            raise httpx.ConnectError("Europe PMC is down", request=request)
        return await super().handle_async_request(request)


async def test_default_search_merges_both_sources(client: Client) -> None:
    data = await _call(client, "search_literature", {"query": DEMO_QUERY, "limit": 5})
    assert data["source_status"] == {"pubmed": "ok", "europepmc": "ok"}
    assert set(data["totals"]) == {"pubmed", "europepmc"}
    assert data["total"] == max(data["totals"].values())
    ids = [hit["record_id"] for hit in data["hits"]]
    assert len(ids) == len(set(ids)) == 5
    for hit in data["hits"]:
        if hit["title_handle"]:
            assert CODEC.verify(hit["title_handle"]).record.key == hit["record_id"].split("@")[0]


async def test_europepmc_only_search_pages_without_repeats(client: Client) -> None:
    args = {"query": DEMO_QUERY, "limit": 2, "sources": ["europepmc"]}
    first = await _call(client, "search_literature", args)
    assert first["source_status"] == {"europepmc": "ok"} and first["next_cursor"]
    second = await _call(client, "search_literature", {**args, "cursor": first["next_cursor"]})
    first_ids = {hit["record_id"] for hit in first["hits"]}
    assert second["hits"] and first_ids.isdisjoint(hit["record_id"] for hit in second["hits"])


async def test_merged_search_pages_without_repeats(client: Client) -> None:
    args = {"query": DEMO_QUERY, "limit": 2}
    first = await _call(client, "search_literature", args)
    second = await _call(client, "search_literature", {**args, "cursor": first["next_cursor"]})
    first_ids = {hit["record_id"] for hit in first["hits"]}
    assert second["hits"] and first_ids.isdisjoint(hit["record_id"] for hit in second["hits"])


async def test_one_source_down_still_returns_the_other() -> None:
    config = replace(demo_config(), transport=EuropePmcDown(FIXTURES_DIR))
    async with Client(create_server(config)) as client:
        data = await _call(client, "search_literature", {**PUBMED_ONLY_SEARCH, "sources": None})
    assert data["source_status"] == {"pubmed": "ok", "europepmc": "unavailable"}
    assert data["hits"] and all(hit["record_id"].startswith("pmid:") for hit in data["hits"])


class PubMedDown(FixtureTransport):
    """Recorded Europe PMC, PubTator and Crossref; NCBI E-utilities unreachable."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "eutils.ncbi.nlm.nih.gov":
            raise httpx.ConnectError("PubMed is down", request=request)
        return await super().handle_async_request(request)


async def test_pubmed_down_keeps_pmid_hits_but_mints_no_handle_for_them() -> None:
    config = replace(demo_config(), transport=PubMedDown(FIXTURES_DIR))
    async with Client(create_server(config)) as client:
        data = await _call(client, "search_literature", {"query": DEMO_QUERY, "limit": 5})
    assert data["source_status"] == {"pubmed": "unavailable", "europepmc": "ok"}
    assert data["hits"]
    for hit in data["hits"]:
        # pmid: passages are only citable with PubMed's own text
        assert (hit["title_handle"] is None) == hit["record_id"].startswith("pmid:")


async def test_preprint_record(client: Client, scenario: dict[str, str]) -> None:
    data = await _call(client, "fetch_passages", {"record_ids": [f"ppr:{scenario['preprint_id']}"]})
    record = data["records"][0]["record"]
    assert record["kind"] == "preprint"
    assert record["quality"]["peer_reviewed"]["value"] is False
    assert any(link["rel"] == "published_as" for link in record["links"])
    assert data["source_status"]["europepmc"] == "ok"


async def test_full_text_arrives_as_a_pmcid_companion(
    client: Client, scenario: dict[str, str]
) -> None:
    pmid, pmcid = scenario["open_access_pmid"], scenario["open_access_pmcid"]
    data = await _call(
        client, "fetch_passages", {"record_ids": [f"pmid:{pmid}"], "full_text": True}
    )
    assert [item["record"]["record_id"] for item in data["records"]] == [
        f"pmid:{pmid}",
        f"pmcid:{pmcid}",
    ]
    oa = data["records"][0]["record"]["quality"]["open_access_full_text"]
    assert oa["state"] == "known" and oa["value"]["available"] is True
    companion = data["records"][1]["passages"]
    # Title and abstract come once, from PubMed; the companion adds only the body.
    assert companion and all(p["path"].startswith("body.") for p in companion)
    assert all(CODEC.verify(p["handle"]).scheme == "pmcid" for p in companion)


async def test_full_text_by_pmcid(client: Client, scenario: dict[str, str]) -> None:
    pmcid = scenario["open_access_pmcid"]
    data = await _call(
        client, "fetch_passages", {"record_ids": [f"pmcid:{pmcid}"], "full_text": True}
    )
    paths = [p["path"] for p in data["records"][0]["passages"]]
    assert paths[0] == "title" and any(path.startswith("body.") for path in paths)


async def test_entities_on_pubmed_passages(client: Client) -> None:
    data = await _call(client, "fetch_passages", {"record_ids": [f"pmid:{COHORT_PMID}"]})
    passages = data["records"][0]["passages"]
    assert data["source_status"]["pubtator"] == "ok"
    assert all(p["entities"]["state"] in ("known", "unknown") for p in passages)
    found = [e for p in passages for e in (p["entities"]["value"] or [])]
    assert any(e["type"] == "disease" and e["id"].startswith("MESH:") for e in found)
    for passage in passages:
        for entity in passage["entities"]["value"] or []:
            start, end = entity["span"]
            assert passage["text"][start:end] == entity["text"]


async def test_entities_can_be_skipped(client: Client) -> None:
    data = await _call(
        client, "fetch_passages", {"record_ids": [f"pmid:{COHORT_PMID}"], "include_entities": False}
    )
    assert "pubtator" not in data["source_status"]
    assert {p["entities"]["state"] for p in data["records"][0]["passages"]} == {"not_checked"}


async def test_crossref_confirms_the_retraction(client: Client) -> None:
    args = {
        "record_ids": [f"pmid:{COHORT_PMID}", f"pmid:{RETRACTED_PMID}"],
        "sections": ["abstract"],
    }
    data = await _call(client, "fetch_passages", args)  # the demo's recorded request
    retraction = data["records"][1]["record"]["quality"]["retraction"]
    assert retraction["value"]["retracted"] is True
    assert retraction["sources"] == ["pubmed", "crossref"]


async def test_audit_cites_full_text_and_preprint_passages(
    client: Client, scenario: dict[str, str]
) -> None:
    full = await _call(
        client,
        "fetch_passages",
        {"record_ids": [f"pmcid:{scenario['open_access_pmcid']}"], "full_text": True},
    )
    body = next(p for p in full["records"][0]["passages"] if p["path"].startswith("body."))
    preprint = await _call(
        client, "fetch_passages", {"record_ids": [f"ppr:{scenario['preprint_id']}"]}
    )
    title = preprint["records"][0]["passages"][0]
    quote = body["text"][:40].rsplit(" ", 1)[0]
    answer = f'One [{body["handle"]} "{quote}"]. Two [{title["handle"]}].'
    report = await _call(client, "audit_answer", {"answer_text": answer})
    first, second = (claim["citations"][0] for claim in report["claims"])
    assert first["verdict"] == "ok"
    assert second["verdict"] == "ok" and [f["name"] for f in second["flags"]] == ["preprint"]
