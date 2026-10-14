import json
from pathlib import Path

import httpx
import pytest

from tekmeria.infra.cache import Cache
from tekmeria.infra.fixtures import (
    FixtureTransport,
    RecordingTransport,
    is_pubmed_fetch,
    keep_abstracts,
    open_licence_pmids,
    request_key,
    scrub,
    strip_abstracts,
)
from tekmeria.infra.http import (
    HostConfig,
    Upstream,
    UpstreamNotFoundError,
    UpstreamResponseError,
    UpstreamUnavailableError,
)

pytestmark = pytest.mark.anyio

HOST = HostConfig(name="test", base_url="https://example.org/api/", rate=1000.0)


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def scripted(
    *responses: httpx.Response | Exception,
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    queue = list(responses)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.MockTransport(handler), seen


async def test_success_is_cached() -> None:
    transport, seen = scripted(httpx.Response(200, text="hello"))
    async with Cache(":memory:") as cache:
        upstream = Upstream(HOST, cache=cache, transport=transport, sleep=Sleeps())
        assert await upstream.get_text("x", {"a": "1"}, ttl_s=60) == "hello"
        assert await upstream.get_text("x", {"a": "1"}, ttl_s=60) == "hello"
        await upstream.aclose()
    assert len(seen) == 1


async def test_retries_then_succeeds_honouring_retry_after() -> None:
    transport, seen = scripted(
        httpx.Response(429, headers={"retry-after": "2"}),
        httpx.ConnectError("boom"),
        httpx.Response(200, text="ok"),
    )
    sleeps = Sleeps()
    async with Cache(":memory:") as cache:
        upstream = Upstream(HOST, cache=cache, transport=transport, sleep=sleeps)
        assert await upstream.get_text("x", {}, ttl_s=60) == "ok"
    assert len(seen) == 3
    assert sleeps.calls[0] == 2.0


async def test_gives_up_after_three_retries() -> None:
    transport, seen = scripted(*[httpx.Response(503) for _ in range(4)])
    async with Cache(":memory:") as cache:
        upstream = Upstream(HOST, cache=cache, transport=transport, sleep=Sleeps())
        with pytest.raises(UpstreamUnavailableError):
            await upstream.get_text("x", {}, ttl_s=60)
    assert len(seen) == 4


async def test_404_and_400_are_not_retried() -> None:
    transport, seen = scripted(httpx.Response(404), httpx.Response(400, text="bad"))
    async with Cache(":memory:") as cache:
        upstream = Upstream(HOST, cache=cache, transport=transport, sleep=Sleeps())
        with pytest.raises(UpstreamNotFoundError):
            await upstream.get_text("x", {}, ttl_s=60)
        with pytest.raises(UpstreamResponseError):
            await upstream.get_text("y", {}, ttl_s=60)
    assert len(seen) == 2


def test_request_key_ignores_credentials() -> None:
    a = httpx.URL("https://e.org/p?db=pubmed&api_key=SECRET&tool=x&email=me%40x.org")
    b = httpx.URL("https://e.org/p?email=other&db=pubmed")
    assert request_key("GET", a) == request_key("GET", b)
    assert request_key("GET", a) != request_key("GET", httpx.URL("https://e.org/p?db=pmc"))


async def test_record_then_replay(tmp_path: Path) -> None:
    live, _ = scripted(httpx.Response(200, text="<xml/>", headers={"content-type": "text/xml"}))
    async with httpx.AsyncClient(transport=RecordingTransport(tmp_path, inner=live)) as client:
        await client.get("https://e.org/p", params={"id": "1", "api_key": "SECRET"})
    (written,) = list(tmp_path.glob("*.json"))
    assert "SECRET" not in written.read_text(encoding="utf-8")
    assert json.loads(written.read_text(encoding="utf-8"))["body"] == "<xml/>"
    async with httpx.AsyncClient(transport=FixtureTransport(tmp_path)) as client:
        response = await client.get("https://e.org/p", params={"id": "1"})
        assert response.text == "<xml/>"
        with pytest.raises(httpx.ConnectError):
            await client.get("https://e.org/p", params={"id": "2"})


async def test_body_rejected_by_the_validator_is_not_cached() -> None:
    transport, seen = scripted(
        httpx.Response(200, text="<html>busy</html>"), httpx.Response(200, text="good")
    )

    def must_be_good(body: str) -> None:
        if body != "good":
            raise UpstreamResponseError(200, "unexpected body")

    async with Cache(":memory:") as cache:
        upstream = Upstream(HOST, cache=cache, transport=transport, sleep=Sleeps())
        with pytest.raises(UpstreamResponseError):
            await upstream.get_text("x", {}, ttl_s=60, validate=must_be_good)
        assert await upstream.get_text("x", {}, ttl_s=60, validate=must_be_good) == "good"
    assert len(seen) == 2


SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def _results(*licences: str | None) -> str:
    return json.dumps(
        {"resultList": {"result": [{"license": x, "abstractText": "text"} for x in licences]}}
    )


def test_scrub_drops_the_abstracts_a_search_may_not_redistribute() -> None:
    url = httpx.URL(SEARCH, params={"query": "measles"})
    kept = json.loads(scrub(url, _results("cc by", "cc0", "cc by-nc-nd", None)))
    abstracts = ["abstractText" in r for r in kept["resultList"]["result"]]
    assert abstracts == [True, True, False, False]
    open_only = _results("cc by")
    assert scrub(url, open_only) is open_only  # nothing to drop: the body is kept as recorded


def test_scrub_keeps_lookups_by_id_and_other_hosts() -> None:
    body = _results("cc by-nc-nd")
    for query in ("(EXT_ID:PPR1297067) AND SRC:PPR", "(PMCID:PMC12585062)"):
        assert scrub(httpx.URL(SEARCH, params={"query": query}), body) is body
    assert scrub(httpx.URL("https://e.org/search", params={"query": "x"}), body) is body


async def test_the_recorder_writes_scrubbed_bodies(tmp_path: Path) -> None:
    live, _ = scripted(httpx.Response(200, text=_results("cc by-nc-nd")))
    async with httpx.AsyncClient(transport=RecordingTransport(tmp_path, inner=live)) as client:
        response = await client.get(SEARCH, params={"query": "measles"})
    assert "abstractText" not in response.text
    (written,) = list(tmp_path.glob("*.json"))
    assert "abstractText" not in json.loads(written.read_text(encoding="utf-8"))["body"]


async def test_the_recorder_writes_an_error_page_as_it_came(tmp_path: Path) -> None:
    # A search can fail with an HTML page; the recorder must pass it on for the upstream's
    # retries and the degraded-result check, not fail on it.
    page = "<html>Service Unavailable</html>"
    live, _ = scripted(httpx.Response(503, text=page, headers={"content-type": "text/html"}))
    async with httpx.AsyncClient(transport=RecordingTransport(tmp_path, inner=live)) as client:
        response = await client.get(SEARCH, params={"query": "measles"})
    assert (response.status_code, response.text) == (503, page)


def _articles(*pmids: str) -> str:
    return (
        "<PubmedArticleSet>"
        + "".join(
            f'<PubmedArticle><MedlineCitation><PMID Version="1">{pmid}</PMID><Article>'
            f"<ArticleTitle>Title {pmid}</ArticleTitle>"
            f"<Abstract><AbstractText>Abstract of {pmid}.</AbstractText></Abstract>"
            "</Article></MedlineCitation></PubmedArticle>"
            for pmid in pmids
        )
        + "</PubmedArticleSet>"
    )


def test_keep_abstracts_removes_only_the_abstracts_of_other_records() -> None:
    body = _articles("1", "2")
    kept = keep_abstracts(body, {"1"})
    assert "Abstract of 1." in kept and "Abstract of 2." not in kept
    assert "Title 2" in kept  # the rest of the record stays
    assert keep_abstracts(body, {"1", "2"}) == body


def test_strip_abstracts_rewrites_only_pubmed_fetches(tmp_path: Path) -> None:
    fetch = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id=1%2C2"
    other = "https://example.org/other?db=pubmed"
    for name, url in (("fetch", fetch), ("other", other)):
        record = {
            "url": url,
            "status": 200,
            "content_type": "text/xml",
            "body": _articles("1", "2"),
        }
        (tmp_path / f"{name}.json").write_text(json.dumps(record), encoding="utf-8")
    (tmp_path / "scenario.json").write_text(json.dumps({"open_access_pmid": "2"}), encoding="utf-8")
    assert strip_abstracts(tmp_path, {"1"}) == 1
    fetched = json.loads((tmp_path / "fetch.json").read_text(encoding="utf-8"))["body"]
    assert "Abstract of 2." not in fetched and "Abstract of 1." in fetched
    assert (
        "Abstract of 2."
        in json.loads((tmp_path / "other.json").read_text(encoding="utf-8"))["body"]
    )
    assert strip_abstracts(tmp_path, {"1"}) == 0  # nothing left to remove


EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"{EFETCH}?db=pubmed&id=1%2C2&retmode=xml", True),
        (f"{EFETCH}?id=1&retmode=xml&db=pubmed", True),
        (f"{EFETCH}?db=pmc&id=1", False),
        (f"{EFETCH}?db=pubmedcentral&id=1", False),  # contains "db=pubmed", names another db
        ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=x", False),
        ("https://example.org/entrez/eutils/efetch.fcgi?db=pubmed&id=1", False),
        ("", False),
    ],
)
def test_is_pubmed_fetch_reads_the_host_path_and_database(url: str, expected: bool) -> None:
    assert is_pubmed_fetch(url) is expected


def test_keep_abstracts_covers_book_records_too() -> None:
    # A PubMed search can return a Bookshelf record (StatPearls, GeneReviews), whose abstract
    # sits in a PubmedBookArticle.
    book = (
        '<PubmedArticleSet><PubmedBookArticle><BookDocument><PMID Version="1">7</PMID>'
        "<Abstract><AbstractText>Abstract of 7.</AbstractText></Abstract>"
        "</BookDocument></PubmedBookArticle></PubmedArticleSet>"
    )
    assert "Abstract of 7." not in keep_abstracts(book, {"1"})
    assert keep_abstracts(book, {"7"}) == book


def test_open_licence_pmids_reads_the_licences_of_recorded_searches(tmp_path: Path) -> None:
    results = [
        {"pmid": "1", "license": "cc by"},
        {"pmid": "2", "license": "cc0"},
        {"pmid": "3", "license": "cc by-nc-nd"},
        {"pmid": "4"},
        {"license": "cc by"},  # a preprint without a PMID
    ]
    search = {
        "url": SEARCH + "?query=measles",
        "body": json.dumps({"resultList": {"result": results}}),
    }
    other = {
        "url": "https://example.org/rest/search",
        "body": json.dumps({"resultList": {"result": results}}),
    }
    for name, record in (("search", search), ("other", other)):
        (tmp_path / f"{name}.json").write_text(json.dumps(record), encoding="utf-8")
    (tmp_path / "scenario.json").write_text("{}", encoding="utf-8")
    (tmp_path / "busy.json").write_text(
        json.dumps({"url": SEARCH, "body": "<html>busy</html>"}), encoding="utf-8"
    )
    assert open_licence_pmids(tmp_path) == {"1", "2"}
