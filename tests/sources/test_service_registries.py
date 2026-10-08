"""EvidenceService with ClinicalTrials.gov and openFDA: paging across registry pages,
partial failure, ID case, and version changes seen by the audit."""

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pydantic import JsonValue

from medcite.contract.audit import check_handles
from medcite.contract.handles import HandleCodec, Locator
from medcite.infra.cache import Cache
from medcite.infra.http import Upstream
from medcite.sources.clinicaltrials import (
    PAGE_SIZE,
    ClinicalTrialsAdapter,
    TrialQuery,
    clinicaltrials_host,
)
from medcite.sources.crossref import CrossrefAdapter, crossref_host
from medcite.sources.errors import InvalidInputError
from medcite.sources.europepmc import EuropePmcAdapter, europepmc_host
from medcite.sources.openfda import OpenFdaAdapter, openfda_host
from medcite.sources.pubmed import PubMedAdapter, ncbi_host
from medcite.sources.pubtator import PubTatorAdapter, pubtator_host
from medcite.sources.service import EvidenceService, TrialSearchState

pytestmark = pytest.mark.anyio

CODEC = HandleCodec(bytes(range(32)))
QUERY = TrialQuery(condition="asthma")
TRIAL_VERSION = "2025-01-02"


def _set_id(i: int) -> str:
    return f"00000000-0000-0000-0000-{i:012d}"


@dataclass
class Registries:
    trials: list[str] = field(default_factory=lambda: [f"NCT{i:08d}" for i in range(1, 121)])
    labels: list[str] = field(default_factory=lambda: [_set_id(i) for i in range(1, 76)])
    trial_pages: list[tuple[str | None, int]] = field(default_factory=list[tuple[str | None, int]])
    down: set[str] = field(default_factory=set[str])

    def _study(self, nct: str) -> dict[str, Any]:
        return {
            "protocolSection": {
                "identificationModule": {"nctId": nct, "briefTitle": f"Trial {nct}"},
                "statusModule": {
                    "overallStatus": "COMPLETED",
                    "lastUpdatePostDateStruct": {"date": TRIAL_VERSION},
                },
                "descriptionModule": {"briefSummary": f"Summary of {nct}."},
            },
            "hasResults": False,
        }

    def _label(self, set_id: str) -> dict[str, Any]:
        return {
            "set_id": set_id,
            "id": "doc",
            "version": "3",
            "effective_time": "20250101",
            "openfda": {"brand_name": ["Brand"], "generic_name": ["generic"]},
            "boxed_warning": ["WARNING: serious."],
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        host, params = request.url.host, request.url.params
        if host in self.down:
            raise httpx.ConnectError(f"{host} is down", request=request)
        if host == "clinicaltrials.gov":
            if "filter.ids" in params:
                ids = params["filter.ids"].split(",")
                studies = [self._study(i) for i in ids if i in self.trials]
                return httpx.Response(200, text=json.dumps({"studies": studies}))
            token, size = params.get("pageToken"), int(params["pageSize"])
            self.trial_pages.append((token, size))
            start = int(token[1:]) if token else 0
            body: dict[str, Any] = {
                "studies": [self._study(i) for i in self.trials[start : start + size]],
            }
            if token is None:  # like the real registry: the count comes with page 1 only
                body["totalCount"] = len(self.trials)
            if start + size < len(self.trials):
                body["nextPageToken"] = f"t{start + size}"
            return httpx.Response(200, text=json.dumps(body))
        if host == "api.fda.gov":
            search = params["search"]
            if search.startswith("set_id:"):
                wanted = re.findall(r'set_id:"([^"]+)"', search)
                results = [self._label(i) for i in wanted if i in self.labels]
            elif "nomatch" in search:
                results = []
            else:
                skip, limit = int(params["skip"]), int(params["limit"])
                results = [self._label(i) for i in self.labels[skip : skip + limit]]
            if not results:
                return httpx.Response(404, text='{"error": {"code": "NOT_FOUND"}}')
            meta = {"results": {"total": len(self.labels)}}
            return httpx.Response(200, text=json.dumps({"meta": meta, "results": results}))
        raise AssertionError(f"unexpected request to {host}")


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
async def cache() -> AsyncIterator[Cache]:
    async with Cache(":memory:") as opened:
        yield opened


def _service(world: Registries, cache: Cache) -> EvidenceService:
    transport = httpx.MockTransport(world.handle)

    def up(host: Any) -> Upstream:
        object.__setattr__(host, "rate", 1000.0)
        object.__setattr__(host, "per_seconds", 1.0)
        return Upstream(host, cache=cache, transport=transport, sleep=_no_sleep)

    return EvidenceService(
        codec=CODEC,
        cache=cache,
        pubmed=PubMedAdapter(up(ncbi_host(api_key=None, email=None))),
        europepmc=EuropePmcAdapter(up(europepmc_host())),
        pubtator=PubTatorAdapter(up(pubtator_host())),
        crossref=CrossrefAdapter(up(crossref_host(mailto=None))),
        trials=ClinicalTrialsAdapter(up(clinicaltrials_host())),
        labels=OpenFdaAdapter(up(openfda_host(api_key=None)), sleep=_no_sleep),
    )


async def test_trial_pages_span_registry_pages_without_gaps(cache: Cache) -> None:
    world = Registries()
    service = _service(world, cache)
    state: TrialSearchState | None = TrialSearchState()
    seen: list[str] = []
    while state is not None:
        page = await service.search_trials(QUERY, state=state, limit=30)
        assert page.total == 120 and len(page.hits) <= 30
        seen.extend(hit.record_id for hit in page.hits)
        state = page.next_state
    assert seen == [f"nct:NCT{i:08d}@{TRIAL_VERSION}" for i in range(1, 121)]
    assert {size for _, size in world.trial_pages} == {PAGE_SIZE}


async def test_trial_search_state_round_trips_through_json() -> None:
    state = TrialSearchState(token="t50", skip=10, total=120)
    assert TrialSearchState.from_json(state.to_json()) == state
    bad_states: list[JsonValue] = [{"t": None, "s": -1}, {"t": None, "s": 0, "n": "9"}, {"t": 5}]
    for bad in bad_states:
        with pytest.raises(InvalidInputError):
            TrialSearchState.from_json(bad)
    assert TrialSearchState.from_json(TrialSearchState().to_json()) == TrialSearchState()


async def test_label_paging_and_no_match(cache: Cache) -> None:
    service = _service(Registries(), cache)
    first = await service.search_labels("warfarin", offset=0, limit=50)
    assert (len(first.hits), first.total, first.next_offset) == (50, 75, 50)
    assert first.hits[0].record_id == f"setid:{_set_id(1)}@3"
    last = await service.search_labels("warfarin", offset=50, limit=50)
    assert (len(last.hits), last.next_offset) == (25, None)
    empty = await service.search_labels("nomatch", offset=0, limit=10)
    assert (empty.hits, empty.total, empty.next_offset) == ([], 0, None)


async def test_fetch_accepts_any_case_and_keeps_the_other_source_when_one_is_down(
    cache: Cache,
) -> None:
    world = Registries(down={"api.fda.gov"})
    service = _service(world, cache)
    page = await service.fetch_passages(
        ["nct:nct00000001", f"setid:{_set_id(1).upper()}", "nct:NCT99999999"],
        sections=None,
        max_passages=60,
        offset=0,
    )
    assert [r.record.record_id for r in page.records] == [f"nct:NCT00000001@{TRIAL_VERSION}"]
    assert [p.path for p in page.records[0].passages] == ["summary.1"]
    assert page.not_found == ["nct:NCT99999999"]
    assert page.unavailable == [f"setid:{_set_id(1)}"]
    assert page.source_status == {"clinicaltrials": "ok", "openfda": "unavailable"}


async def test_a_handle_to_an_older_trial_version_is_flagged(cache: Cache) -> None:
    service = _service(Registries(), cache)
    old = CODEC.issue(Locator("nct", "NCT00000001", "2020-01-01", "summary.1"))
    label = CODEC.issue(Locator("setid", _set_id(2), "3", "boxed_warning.1"))
    checks = await check_handles([old, label], codec=CODEC, resolver=service)
    assert [c.status for c in checks] == ["ok", "ok"]
    assert [f.name for f in checks[0].flags] == ["version_changed"]
    assert checks[0].flags[0].detail == TRIAL_VERSION
    assert checks[1].flags == [] and checks[1].passage is not None
    assert checks[1].passage.text == "WARNING: serious."


async def test_a_record_asked_for_twice_comes_back_once(cache: Cache) -> None:
    service = _service(Registries(), cache)
    page = await service.fetch_passages(
        ["nct:NCT00000001", f"nct:NCT00000001@{TRIAL_VERSION}", "nct:nct00000001"],
        sections=None,
        max_passages=60,
        offset=0,
    )
    assert [r.record.record_id for r in page.records] == [f"nct:NCT00000001@{TRIAL_VERSION}"]
