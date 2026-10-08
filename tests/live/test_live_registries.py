"""Live smoke tests for ClinicalTrials.gov and openFDA. Run with `uv run pytest -m live`."""

import pytest
from mcp import Client

from tekmerion.__main__ import live_config
from tekmerion.infra.settings import Settings
from tekmerion.server.app import create_server
from tests.conftest import structured

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_live_trials_and_labels() -> None:
    settings = Settings.model_validate({"TEKMERION_CACHE_PATH": ":memory:"})
    async with Client(create_server(live_config(settings))) as client:
        trials = structured(
            await client.call_tool(
                "search_trials",
                {"condition": "heart failure", "intervention": "dapagliflozin", "limit": 3},
            )
        )
        assert trials["hits"], "ClinicalTrials.gov returned no trials"
        labels = structured(
            await client.call_tool("search_drug_labels", {"drug": "warfarin", "limit": 3})
        )
        assert labels["hits"], "openFDA returned no labels"
        record_ids = [trials["hits"][0]["record_id"], labels["hits"][0]["record_id"]]
        fetched = structured(await client.call_tool("fetch_passages", {"record_ids": record_ids}))
        handles = [item["passages"][0]["handle"] for item in fetched["records"]]
        assert len(handles) == 2
        report = structured(
            await client.call_tool(
                "audit_answer", {"answer_text": f"A trial [{handles[0]}]. A label [{handles[1]}]."}
            )
        )
        assert report["verdict"] == "pass"
