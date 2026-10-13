import json
import re
from typing import Any

import httpx
import pytest

from tekmeria.infra.cache import Cache
from tekmeria.infra.http import Upstream, UpstreamResponseError
from tekmeria.sources.errors import InvalidInputError
from tekmeria.sources.openfda import (
    NOT_FOUND_ATTEMPTS,
    NOT_FOUND_RETRY_DELAY_S,
    OpenFdaAdapter,
    label_query,
    openfda_host,
)

pytestmark = pytest.mark.anyio

NOT_FOUND = '{"error": {"code": "NOT_FOUND", "message": "No matches found!"}}'


async def _no_sleep(_: float) -> None:
    return None


def _label(set_id: str) -> dict[str, Any]:
    return {"set_id": set_id, "id": "doc", "version": "3", "boxed_warning": ["WARNING: x."]}


def _page(results: list[dict[str, Any]], total: int) -> str:
    return json.dumps({"meta": {"results": {"total": total}}, "results": results})


def test_label_query_matches_any_name_field() -> None:
    assert label_query("  Warfarin  sodium ") == (
        'openfda.brand_name:"Warfarin sodium" OR openfda.generic_name:"Warfarin sodium" '
        'OR openfda.substance_name:"Warfarin sodium" '
        'OR (_missing_:openfda AND spl_product_data_elements:"Warfarin sodium")'
    )


@pytest.mark.parametrize("drug", ['warfarin" OR set_id:"x', "back\\slash", "   "])
def test_label_query_rejects_names_that_break_the_phrase(drug: str) -> None:
    with pytest.raises(InvalidInputError):
        label_query(drug)


async def test_search_sends_paging_sort_and_key() -> None:
    seen: list[httpx.QueryParams] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params)
        return httpx.Response(200, text=_page([_label("a")], total=75))

    async with Cache(":memory:") as cache:
        upstream = Upstream(
            openfda_host(api_key="secret"), cache=cache, transport=httpx.MockTransport(handler)
        )
        page = await OpenFdaAdapter(upstream).search("warfarin", skip=10, limit=5)
        await upstream.aclose()
    assert (page.total, len(page.results)) == (75, 1)
    params = seen[0]
    assert (params["skip"], params["limit"], params["sort"]) == ("10", "5", "effective_time:desc")
    assert params["api_key"] == "secret"


async def test_no_match_404_is_an_empty_page_once_confirmed() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["search"])
        return httpx.Response(404, text=NOT_FOUND)

    transport = httpx.MockTransport(handler)
    async with Cache(":memory:") as cache:
        upstream = Upstream(openfda_host(api_key=None), cache=cache, transport=transport)
        adapter = OpenFdaAdapter(upstream, sleep=_no_sleep)
        page = await adapter.search("zzzqqq", skip=0, limit=5)
        outcome = await adapter.load(["00000000-0000-0000-0000-000000000000"])
        await upstream.aclose()
    assert (page.results, page.total) == ([], 0)
    assert outcome.missing == ["00000000-0000-0000-0000-000000000000"]
    assert len(calls) == 2 * NOT_FOUND_ATTEMPTS


async def test_a_spurious_404_is_retried() -> None:
    answers = [
        httpx.Response(404, text=NOT_FOUND),
        httpx.Response(200, text=_page([_label("a")], 1)),
    ]
    transport = httpx.MockTransport(lambda request: answers.pop(0))
    async with Cache(":memory:") as cache:
        upstream = Upstream(openfda_host(api_key=None), cache=cache, transport=transport)
        page = await OpenFdaAdapter(upstream, sleep=_no_sleep).search("warfarin", skip=0, limit=5)
        await upstream.aclose()
    assert [r["set_id"] for r in page.results] == ["a"]


async def test_load_finds_labels_by_set_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        ids = re.findall(r'set_id:"([^"]+)"', request.url.params["search"])
        return httpx.Response(200, text=_page([_label(i.upper()) for i in ids[:1]], total=1))

    async with Cache(":memory:") as cache:
        upstream = Upstream(
            openfda_host(api_key=None), cache=cache, transport=httpx.MockTransport(handler)
        )
        outcome = await OpenFdaAdapter(upstream).load(["aa-1", "bb-2"])
        await upstream.aclose()
    assert list(outcome.bundles) == ["aa-1"] and outcome.missing == ["bb-2"]


async def test_label_without_version_is_an_upstream_error() -> None:
    body = _page([{"set_id": "a"}], total=1)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body))
    async with Cache(":memory:") as cache:
        upstream = Upstream(openfda_host(api_key=None), cache=cache, transport=transport)
        with pytest.raises(UpstreamResponseError):
            await OpenFdaAdapter(upstream).search("warfarin", skip=0, limit=5)
        await upstream.aclose()


async def test_the_confirming_attempt_waits_first() -> None:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    answers = [httpx.Response(404, text=NOT_FOUND), httpx.Response(404, text=NOT_FOUND)]
    transport = httpx.MockTransport(lambda request: answers.pop(0))
    async with Cache(":memory:") as cache:
        upstream = Upstream(openfda_host(api_key=None), cache=cache, transport=transport)
        page = await OpenFdaAdapter(upstream, sleep=sleep).search("zzzqqq", skip=0, limit=5)
        await upstream.aclose()
    assert page.results == []
    assert slept == [NOT_FOUND_RETRY_DELAY_S] and NOT_FOUND_RETRY_DELAY_S >= 0.5
