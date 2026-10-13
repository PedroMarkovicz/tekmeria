import json
from typing import Any

import httpx
import pytest

from tekmeria.infra.cache import Cache
from tekmeria.infra.http import Upstream, UpstreamResponseError
from tekmeria.sources.clinicaltrials import (
    MAX_IDS_PER_CALL,
    PAGE_SIZE,
    ClinicalTrialsAdapter,
    TrialQuery,
    build_params,
    clinicaltrials_host,
)
from tekmeria.sources.errors import InvalidInputError

pytestmark = pytest.mark.anyio


def _study(nct: str) -> dict[str, Any]:
    return {
        "protocolSection": {
            "identificationModule": {"nctId": nct, "briefTitle": f"Trial {nct}"},
            "statusModule": {"lastUpdatePostDateStruct": {"date": "2025-01-02"}},
        }
    }


def test_params_push_filters_upstream() -> None:
    params = build_params(
        TrialQuery(
            condition="heart failure",
            intervention="dapagliflozin",
            terms="AstraZeneca",
            statuses=("COMPLETED", "TERMINATED"),
            phases=("PHASE3", "PHASE4"),
            has_results=True,
        )
    )
    assert params == {
        "query.cond": "heart failure",
        "query.intr": "dapagliflozin",
        "query.term": "AstraZeneca",
        "filter.overallStatus": "COMPLETED,TERMINATED",
        "filter.advanced": "AREA[Phase](PHASE3 OR PHASE4) AND AREA[HasResults]true",
    }
    assert build_params(TrialQuery(terms="x", has_results=False)) == {
        "query.term": "x",
        "filter.advanced": "AREA[HasResults]false",
    }


def test_a_query_needs_a_condition_intervention_or_terms() -> None:
    with pytest.raises(InvalidInputError):
        build_params(TrialQuery(statuses=("COMPLETED",)))


async def test_search_uses_fixed_pages_and_page_tokens() -> None:
    seen: list[httpx.QueryParams] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params)
        body = {"totalCount": 128, "studies": [_study("NCT00000001")], "nextPageToken": "abc"}
        return httpx.Response(200, text=json.dumps(body))

    async with Cache(":memory:") as cache:
        upstream = Upstream(
            clinicaltrials_host(), cache=cache, transport=httpx.MockTransport(handler)
        )
        adapter = ClinicalTrialsAdapter(upstream)
        first = await adapter.search(TrialQuery(condition="asthma"), token=None)
        await adapter.search(TrialQuery(condition="asthma"), token="abc")
        await upstream.aclose()
    assert (first.total, first.next_token, len(first.studies)) == (128, "abc", 1)
    assert seen[0]["pageSize"] == str(PAGE_SIZE) and "pageToken" not in seen[0]
    assert seen[1]["pageToken"] == "abc"
    assert "NCTId" in seen[0]["fields"] and seen[0]["countTotal"] == "true"


async def test_load_batches_ids_and_reports_missing() -> None:
    batches: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        ids = request.url.params["filter.ids"].split(",")
        batches.append(ids)
        studies = [_study(i) for i in ids if i != "NCT00000003"]
        return httpx.Response(200, text=json.dumps({"studies": studies}))

    wanted = [f"NCT{i:08d}" for i in range(1, MAX_IDS_PER_CALL + 3)]
    async with Cache(":memory:") as cache:
        upstream = Upstream(
            clinicaltrials_host(), cache=cache, transport=httpx.MockTransport(handler)
        )
        outcome = await ClinicalTrialsAdapter(upstream).load(wanted)
        await upstream.aclose()
    assert [len(b) for b in batches] == [MAX_IDS_PER_CALL, 2]
    assert outcome.missing == ["NCT00000003"]
    assert outcome.bundles["NCT00000001"].record.record_id == "nct:NCT00000001@2025-01-02"


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        '{"studies": {}}',
        '{"studies": [{}]}',
        '{"studies": ["NCT00000001"]}',
        '{"studies": [{"protocolSection": []}]}',
    ],
)
async def test_unexpected_bodies_are_upstream_errors(body: str) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body))
    async with Cache(":memory:") as cache:
        upstream = Upstream(clinicaltrials_host(), cache=cache, transport=transport)
        with pytest.raises(UpstreamResponseError):
            await ClinicalTrialsAdapter(upstream).load(["NCT00000001"])
        await upstream.aclose()


async def test_an_obsolete_alias_finds_the_merged_study() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        study = _study("NCT05024188")
        study["protocolSection"]["identificationModule"]["nctIdAliases"] = ["NCT04707222"]
        return httpx.Response(200, text=json.dumps({"studies": [study]}))

    async with Cache(":memory:") as cache:
        upstream = Upstream(
            clinicaltrials_host(), cache=cache, transport=httpx.MockTransport(handler)
        )
        outcome = await ClinicalTrialsAdapter(upstream).load(["NCT04707222"])
        await upstream.aclose()
    assert outcome.missing == []
    assert outcome.bundles["NCT04707222"].record.record_id == "nct:NCT05024188@2025-01-02"


async def test_a_query_the_registry_rejects_is_invalid_input() -> None:
    message = "Error parsing query in Other terms: Unknown area name: `Foo`"
    transport = httpx.MockTransport(lambda request: httpx.Response(400, text=message))
    async with Cache(":memory:") as cache:
        upstream = Upstream(clinicaltrials_host(), cache=cache, transport=transport)
        with pytest.raises(InvalidInputError, match="Unknown area name") as caught:
            await ClinicalTrialsAdapter(upstream).search(TrialQuery(terms="AREA[Foo]("), token=None)
        await upstream.aclose()
    assert caught.value.hint
