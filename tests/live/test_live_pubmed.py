"""Live smoke tests: catch upstream API drift. Run with `uv run pytest -m live`."""

import pytest
from mcp import Client

from tekmerion.__main__ import live_config
from tekmerion.infra.settings import Settings
from tekmerion.server.app import create_server
from tests.conftest import structured

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_live_search_fetch_and_audit() -> None:
    settings = Settings.model_validate({"TEKMERION_CACHE_PATH": ":memory:"})
    config = live_config(settings)
    async with Client(create_server(config)) as client:
        search = structured(
            await client.call_tool(
                "search_literature", {"query": "MMR vaccine autism cohort", "limit": 3}
            )
        )
        assert search["hits"], "PubMed returned no hits"
        fetched = structured(
            await client.call_tool("fetch_passages", {"record_ids": ["pmid:30831578"]})
        )
        passages = fetched["records"][0]["passages"]
        results = next(p for p in passages if p["section_label"] == "RESULTS")
        report = structured(
            await client.call_tool(
                "audit_answer", {"answer_text": f"Autism was diagnosed [{results['handle']}]."}
            )
        )
        assert report["verdict"] == "pass"
