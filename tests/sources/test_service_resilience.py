"""EvidenceService against a controllable fake of all four upstreams: paging, partial
failures, slow sources and non-journal Europe PMC records."""

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import anyio
import httpx
import pytest

from tekmeria.contract.handles import HandleCodec, Locator
from tekmeria.infra.cache import Cache
from tekmeria.infra.http import Upstream, UpstreamUnavailableError
from tekmeria.sources.clinicaltrials import ClinicalTrialsAdapter, clinicaltrials_host
from tekmeria.sources.crossref import CrossrefAdapter, crossref_host
from tekmeria.sources.europepmc import EuropePmcAdapter, europepmc_host
from tekmeria.sources.openfda import OpenFdaAdapter, openfda_host
from tekmeria.sources.pubmed import LiteratureQuery, PubMedAdapter, ncbi_host
from tekmeria.sources.pubtator import PubTatorAdapter, pubtator_host
from tekmeria.sources.service import EvidenceService, SearchState

pytestmark = pytest.mark.anyio

CODEC = HandleCodec(bytes(range(32)))
QUERY = LiteratureQuery(query="vaccines")


def _article(pmid: str) -> str:
    return (
        f'<PubmedArticle><MedlineCitation Status="MEDLINE"><PMID>{pmid}</PMID><Article>'
        f"<Journal><Title>J</Title></Journal><ArticleTitle>Title {pmid}</ArticleTitle>"
        f"<Abstract><AbstractText>Text {pmid}.</AbstractText></Abstract></Article>"
        f'</MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.9/{pmid}'
        f"</ArticleId></ArticleIdList></PubmedData></PubmedArticle>"
    )


@dataclass
class World:
    pmids: list[str] = field(default_factory=lambda: [str(1000 + i) for i in range(300)])
    preprints: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"id": f"PPR{i}", "source": "PPR", "title": f"Preprint {i}", "doi": f"10.1/p{i}"}
            for i in range(300)
        ]
    )
    extra_results: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    page_sizes: list[int] = field(default_factory=list[int])
    hosts_called: list[str] = field(default_factory=list[str])
    efetch_fails: bool = False
    pubmed_hangs: bool = False
    full_text_fails: set[str] = field(default_factory=set[str])
    crossref_hangs: set[str] = field(default_factory=set[str])

    async def handle(self, request: httpx.Request) -> httpx.Response:
        host, path, params = request.url.host, request.url.path, request.url.params
        self.hosts_called.append(host)
        if host == "eutils.ncbi.nlm.nih.gov":
            if self.pubmed_hangs:
                await anyio.sleep(3600)
            if path.endswith("esearch.fcgi"):
                start, size = int(params["retstart"]), int(params["retmax"])
                body = {
                    "esearchresult": {
                        "count": str(len(self.pmids)),
                        "idlist": self.pmids[start : start + size],
                    }
                }
                return httpx.Response(200, text=json.dumps(body))
            if self.efetch_fails:
                return httpx.Response(503)
            articles = "".join(_article(p) for p in params["id"].split(","))
            return httpx.Response(200, text=f"<PubmedArticleSet>{articles}</PubmedArticleSet>")
        if host == "www.ebi.ac.uk":
            if path.endswith("/fullTextXML"):
                pmcid = path.split("/")[-2]
                if pmcid in self.full_text_fails:
                    return httpx.Response(500)
                return httpx.Response(200, text="<article><body><p>Body.</p></body></article>")
            query, size = params["query"], int(params["pageSize"])
            if query.startswith("(PMCID:"):
                ids = [part.split(":")[1].strip("() ") for part in query.split(" OR ")]
                results = [
                    {
                        "id": i,
                        "source": "PMC",
                        "pmcid": i,
                        "title": f"Article {i}",
                        "isOpenAccess": "Y",
                    }
                    for i in ids
                ]
                return httpx.Response(
                    200,
                    text=json.dumps({"hitCount": len(results), "resultList": {"result": results}}),
                )
            self.page_sizes.append(size)
            mark = params["cursorMark"]
            offset = 0 if mark == "*" else int(mark[1:])
            pool = self.extra_results + self.preprints
            page = pool[offset : offset + size]
            nxt = f"o{offset + size}" if offset + size < len(pool) else mark
            body = {"hitCount": len(pool), "nextCursorMark": nxt, "resultList": {"result": page}}
            return httpx.Response(200, text=json.dumps(body))
        if host == "api.crossref.org":
            doi = params["filter"].removeprefix("updates:")
            if doi in self.crossref_hangs:
                await anyio.sleep(3600)
            return httpx.Response(200, text=json.dumps({"message": {"items": []}}))
        return httpx.Response(200, text=json.dumps({"PubTator3": []}))


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
async def world() -> World:
    return World()


async def _service(world: World, cache: Cache, *, deadline_s: float = 30.0) -> EvidenceService:
    transport = httpx.MockTransport(world.handle)

    def up(host: Any) -> Upstream:
        return Upstream(host, cache=cache, transport=transport, sleep=_no_sleep, limiter=None)

    hosts = [
        ncbi_host(api_key="k", email=None),
        europepmc_host(),
        pubtator_host(),
        crossref_host(mailto=None),
        clinicaltrials_host(),
        openfda_host(api_key=None),
    ]
    for host in hosts:
        object.__setattr__(host, "rate", 1000.0)
    return EvidenceService(
        codec=CODEC,
        cache=cache,
        pubmed=PubMedAdapter(up(hosts[0])),
        europepmc=EuropePmcAdapter(up(hosts[1])),
        pubtator=PubTatorAdapter(up(hosts[2])),
        crossref=CrossrefAdapter(up(hosts[3])),
        trials=ClinicalTrialsAdapter(up(hosts[4])),
        labels=OpenFdaAdapter(up(hosts[5])),
        deadline_s=deadline_s,
    )


@pytest.fixture
async def cache() -> AsyncIterator[Cache]:
    async with Cache(":memory:") as opened:
        yield opened


async def test_non_journal_europepmc_records_never_become_hits(world: World, cache: Cache) -> None:
    world.extra_results = [{"id": "IE20110217", "source": "PAT", "title": "A patent"}]
    service = await _service(world, cache)
    page = await service.search_literature(
        QUERY, sources=["europepmc"], state=SearchState(), limit=5
    )
    assert [hit.record_id for hit in page.hits] == [f"ppr:PPR{i}" for i in range(5)]


async def test_deep_merged_paging_keeps_a_fixed_page_size_and_skips_nothing(
    world: World, cache: Cache
) -> None:
    service = await _service(world, cache)
    state: SearchState | None = SearchState()
    seen: list[str] = []
    for _ in range(12):
        assert state is not None
        page = await service.search_literature(
            QUERY, sources=["pubmed", "europepmc"], state=state, limit=10
        )
        assert page.source_status == {"pubmed": "ok", "europepmc": "ok"}
        seen.extend(hit.record_id for hit in page.hits)
        state = page.next_state
    assert max(world.page_sizes) <= 50
    preprints = [rid for rid in seen if rid.startswith("ppr:")]
    assert preprints == [f"ppr:PPR{i}" for i in range(len(preprints))]
    pubmed = [rid for rid in seen if rid.startswith("pmid:")]
    assert pubmed == [f"pmid:{1000 + i}" for i in range(len(pubmed))]
    assert len(seen) == len(set(seen)) == 120


async def test_pubmed_fetch_failure_after_search_is_not_a_silent_empty_success(
    world: World, cache: Cache
) -> None:
    world.efetch_fails = True
    service = await _service(world, cache)
    with pytest.raises(UpstreamUnavailableError):
        await service.search_literature(QUERY, sources=["pubmed"], state=SearchState(), limit=10)
    page = await service.search_literature(
        QUERY, sources=["pubmed", "europepmc"], state=SearchState(), limit=10
    )
    assert page.source_status["pubmed"] == "unavailable"
    assert page.next_state is not None and page.next_state.pubmed == 0  # nothing skipped


async def test_one_full_text_failure_keeps_the_other_records(world: World, cache: Cache) -> None:
    world.full_text_fails = {"PMC2"}
    service = await _service(world, cache)
    page = await service.fetch_passages(
        ["pmcid:PMC1", "pmcid:PMC2", "ppr:PPR3"],
        sections=None,
        max_passages=60,
        offset=0,
        full_text=True,
    )
    paths = {item.record.record_id: [p.path for p in item.passages] for item in page.records}
    assert paths["pmcid:PMC1"] == ["title", "body.p1"]
    assert paths["pmcid:PMC2"] == ["title"]
    assert page.source_status["europepmc"] == "degraded"
    assert page.unavailable == []


async def test_a_hanging_source_does_not_lose_the_other_sources_results(
    world: World, cache: Cache
) -> None:
    world.pubmed_hangs = True
    service = await _service(world, cache, deadline_s=2.0)
    started = time.perf_counter()
    page = await service.search_literature(
        QUERY, sources=["pubmed", "europepmc"], state=SearchState(), limit=5
    )
    assert time.perf_counter() - started < 2.0
    assert page.source_status == {"pubmed": "unavailable", "europepmc": "ok"}
    assert len(page.hits) == 5


async def test_slow_crossref_keeps_the_retractions_it_did_get(world: World, cache: Cache) -> None:
    world.crossref_hangs = {"10.9/1001"}
    service = await _service(world, cache, deadline_s=2.0)
    locators = [Locator("pmid", "1000", None, "title"), Locator("pmid", "1001", None, "title")]
    resolved = await service.resolve(locators)
    first, second = (resolved[loc] for loc in locators)
    assert type(first).__name__ == type(second).__name__ == "Resolved"
    assert first.record.quality.retraction.sources == ["pubmed", "crossref"]  # type: ignore[union-attr]
    assert "crossref" not in second.record.quality.retraction.sources  # type: ignore[union-attr]


async def test_pubmed_field_tags_are_not_sent_to_europepmc(world: World, cache: Cache) -> None:
    service = await _service(world, cache)
    query = LiteratureQuery(query='"heart failure"[MeSH] AND therapy[tiab]')
    page = await service.search_literature(
        query, sources=["pubmed", "europepmc"], state=SearchState(), limit=5
    )
    assert "www.ebi.ac.uk" not in world.hosts_called
    assert page.source_status == {"pubmed": "ok", "europepmc": "degraded"}
    assert page.hits
