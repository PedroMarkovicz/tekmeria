import httpx
import pytest

from tekmeria.infra.cache import Cache
from tekmeria.infra.http import Upstream, UpstreamResponseError
from tekmeria.sources.errors import InvalidInputError
from tekmeria.sources.pubmed import (
    LiteratureQuery,
    PubMedAdapter,
    _date_params,
    build_term,
    ncbi_host,
)


def test_plain_query_is_parenthesized() -> None:
    assert build_term(LiteratureQuery(query="mmr autism")) == "(mmr autism)"


def test_all_filters() -> None:
    term = build_term(
        LiteratureQuery(
            query="autism",
            study_designs=("meta_analysis", "guideline"),
            open_access_only=True,
            peer_reviewed_only=True,
            exclude_retracted=True,
        )
    )
    assert term == (
        "(autism) AND (meta-analysis[pt] OR guideline[pt] OR practice guideline[pt])"
        ' AND "pubmed pmc open access"[filter] NOT preprint[pt] NOT retracted publication[pt]'
    )


def test_other_design_cannot_be_filtered() -> None:
    with pytest.raises(InvalidInputError):
        build_term(LiteratureQuery(query="x", study_designs=("other",)))


def test_date_bounds_are_always_paired() -> None:
    assert _date_params(LiteratureQuery(query="x")) == {}
    assert _date_params(LiteratureQuery(query="x", date_from="2020")) == {
        "datetype": "pdat",
        "mindate": "2020",
        "maxdate": "3000",
    }
    assert _date_params(LiteratureQuery(query="x", date_to="2024-12-31"))["maxdate"] == "2024/12/31"


def test_api_key_raises_rate() -> None:
    assert ncbi_host(api_key=None, email=None).rate == 3.0
    keyed = ncbi_host(api_key="k", email="a@b.org")
    assert keyed.rate == 10.0
    assert keyed.default_params == {"tool": "tekmeria", "email": "a@b.org", "api_key": "k"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "body"),
    [("esearch.fcgi", "<html>busy</html>"), ("efetch.fcgi", "<html><body>busy")],
)
async def test_unparseable_success_body_is_an_upstream_error(path: str, body: str) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body))
    async with Cache(":memory:") as cache:
        upstream = Upstream(ncbi_host(api_key=None, email=None), cache=cache, transport=transport)
        adapter = PubMedAdapter(upstream)
        with pytest.raises(UpstreamResponseError):
            if path == "esearch.fcgi":
                await adapter.search(LiteratureQuery(query="x"), offset=0, limit=1)
            else:
                await adapter.fetch(["1"])
        await upstream.aclose()


@pytest.mark.anyio
async def test_well_formed_error_xml_is_an_upstream_error_not_a_missing_record() -> None:
    body = "<eFetchResult><ERROR>Empty id list - nothing todo</ERROR></eFetchResult>"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body))
    async with Cache(":memory:") as cache:
        upstream = Upstream(ncbi_host(api_key=None, email=None), cache=cache, transport=transport)
        with pytest.raises(UpstreamResponseError):
            await PubMedAdapter(upstream).fetch(["1"])
        await upstream.aclose()


@pytest.mark.anyio
async def test_bad_search_body_is_retried_on_the_next_call() -> None:
    bodies = ["<html>busy</html>", '{"esearchresult": {"count": "1", "idlist": ["7"]}}']
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=bodies.pop(0)))
    async with Cache(":memory:") as cache:
        upstream = Upstream(ncbi_host(api_key=None, email=None), cache=cache, transport=transport)
        adapter = PubMedAdapter(upstream)
        with pytest.raises(UpstreamResponseError):
            await adapter.search(LiteratureQuery(query="x"), offset=0, limit=1)
        assert await adapter.search(LiteratureQuery(query="x"), offset=0, limit=1) == (1, ["7"])
        await upstream.aclose()
