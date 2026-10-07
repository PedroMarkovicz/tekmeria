import json

import httpx
import pytest

from medcite.infra.cache import Cache
from medcite.infra.http import Upstream, UpstreamResponseError
from medcite.sources.errors import InvalidInputError
from medcite.sources.europepmc import EuropePmcAdapter, build_query, europepmc_host
from medcite.sources.pubmed import LiteratureQuery

pytestmark = pytest.mark.anyio


def test_query_with_all_filters() -> None:
    query = build_query(
        LiteratureQuery(
            query="mmr autism",
            study_designs=("meta_analysis", "guideline"),
            date_from="2020",
            date_to="2024-06-30",
            open_access_only=True,
            peer_reviewed_only=True,
            exclude_retracted=True,
        )
    )
    assert query == (
        "(mmr autism) AND (SRC:MED OR SRC:PMC OR SRC:PPR) AND "
        '(PUB_TYPE:"Meta-Analysis" OR PUB_TYPE:"Guideline" OR '
        'PUB_TYPE:"Practice Guideline") AND FIRST_PDATE:[2020-01-01 TO 2024-06-30] AND '
        'OPEN_ACCESS:y NOT SRC:PPR NOT PUB_TYPE:"Retracted Publication"'
    )


def test_open_ended_date_range() -> None:
    assert "FIRST_PDATE:[1800-01-01 TO 2019-12-31]" in build_query(
        LiteratureQuery(query="x", date_to="2019")
    )


def test_other_design_cannot_be_filtered() -> None:
    with pytest.raises(InvalidInputError):
        build_query(LiteratureQuery(query="x", study_designs=("other",)))


def _search_body(results: list[dict[str, object]], mark: str = "next") -> str:
    return json.dumps(
        {"hitCount": len(results), "nextCursorMark": mark, "resultList": {"result": results}}
    )


async def test_load_matches_ids_and_fetches_full_text_only_when_open() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/search"):
            return httpx.Response(
                200,
                text=_search_body(
                    [
                        {
                            "id": "PMC1",
                            "source": "PMC",
                            "pmcid": "PMC1",
                            "title": "Open",
                            "isOpenAccess": "Y",
                        },
                        {
                            "id": "PMC2",
                            "source": "PMC",
                            "pmcid": "PMC2",
                            "title": "Closed",
                            "isOpenAccess": "N",
                        },
                    ]
                ),
            )
        return httpx.Response(200, text="<article><body><p>Full.</p></body></article>")

    async with Cache(":memory:") as cache:
        upstream = Upstream(europepmc_host(), cache=cache, transport=httpx.MockTransport(handler))
        outcome = await EuropePmcAdapter(upstream).load(["PMC1", "PMC2", "PMC3"], full_text=True)
        await upstream.aclose()
    assert outcome.missing == ["PMC3"]
    assert [p.path for p in outcome.bundles["PMC1"].passages] == ["title", "body.p1"]
    assert [p.path for p in outcome.bundles["PMC2"].passages] == ["title"]
    assert outcome.bundles["PMC1"].record.record_id == "pmcid:PMC1"
    assert sum(path.endswith("/fullTextXML") for path in seen) == 1


async def test_full_text_that_is_not_a_jats_article_keeps_the_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            body = _search_body(
                [{"id": "PMC1", "source": "PMC", "pmcid": "PMC1", "isOpenAccess": "Y"}]
            )
            return httpx.Response(200, text=body)
        return httpx.Response(200, text="<html><body>error</body></html>")

    async with Cache(":memory:") as cache:
        upstream = Upstream(europepmc_host(), cache=cache, transport=httpx.MockTransport(handler))
        outcome = await EuropePmcAdapter(upstream).load(["PMC1"], full_text=True)
        await upstream.aclose()
    assert outcome.full_text_failed == ["PMC1"]
    assert outcome.bundles["PMC1"].record.record_id == "pmcid:PMC1"
    assert outcome.bundles["PMC1"].passages == []  # the search result has no title


async def test_search_without_result_list_is_an_upstream_error() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text='{"error": "busy"}'))
    async with Cache(":memory:") as cache:
        upstream = Upstream(europepmc_host(), cache=cache, transport=transport)
        with pytest.raises(UpstreamResponseError):
            await EuropePmcAdapter(upstream).search(LiteratureQuery(query="x"), mark="*", size=5)
        await upstream.aclose()


async def test_last_page_has_no_next_mark() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=_search_body([], mark="*"))
    )
    async with Cache(":memory:") as cache:
        upstream = Upstream(europepmc_host(), cache=cache, transport=transport)
        page = await EuropePmcAdapter(upstream).search(LiteratureQuery(query="x"), mark="*", size=5)
        await upstream.aclose()
    assert page.next_mark is None and page.results == []
