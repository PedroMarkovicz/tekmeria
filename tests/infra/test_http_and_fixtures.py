import json
from pathlib import Path

import httpx
import pytest

from medcite.infra.cache import Cache
from medcite.infra.fixtures import FixtureTransport, RecordingTransport, request_key
from medcite.infra.http import (
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
