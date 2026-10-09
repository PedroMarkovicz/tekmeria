"""Recorded upstream responses for the offline demo and the tests.

One JSON file per request, named by a hash of the request with credentials
removed. `FixtureTransport` replays them; `RecordingTransport` writes them.

Recorded responses are redistributed with the repository, so the recorder keeps only text
that may be redistributed (see NOTICE): a search result's abstract is dropped unless its
licence is CC BY or CC0. Lookups of one record by ID keep theirs, since the demo and the
evaluation fetch those records' text.
"""

import hashlib
import json
from pathlib import Path

import httpx

VOLATILE_PARAMS = frozenset({"api_key", "tool", "email", "mailto"})
OPEN_LICENCES = frozenset({"cc by", "cc0"})
_EUROPEPMC_SEARCH = "/europepmc/webservices/rest/search"
_LOOKUPS = ("EXT_ID:", "PMCID:")


def request_key(method: str, url: httpx.URL) -> str:
    params = sorted((k, v) for k, v in url.params.multi_items() if k not in VOLATILE_PARAMS)
    canonical = json.dumps([method.upper(), url.host, url.path, params])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _display_url(url: httpx.URL) -> str:
    params = [(k, v) for k, v in url.params.multi_items() if k not in VOLATILE_PARAMS]
    return str(url.copy_with(params=params))


def scrub(url: httpx.URL, body: str) -> str:
    """`body` without the abstracts a search result may not redistribute."""
    if url.path != _EUROPEPMC_SEARCH or any(x in url.params.get("query", "") for x in _LOOKUPS):
        return body
    data = json.loads(body)
    results = data.get("resultList", {}).get("result", [])
    restricted = [
        r for r in results if "abstractText" in r and r.get("license") not in OPEN_LICENCES
    ]
    if not restricted:
        return body
    for result in restricted:
        del result["abstractText"]
    return json.dumps(data, ensure_ascii=False)


class FixtureTransport(httpx.AsyncBaseTransport):
    """Serves recorded responses; a missing fixture behaves like a network failure."""

    def __init__(self, root: Path) -> None:
        self.root = root

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = self.root / f"{request_key(request.method, request.url)}.json"
        if not path.exists():
            raise httpx.ConnectError(f"no fixture for {_display_url(request.url)}", request=request)
        data = json.loads(path.read_text(encoding="utf-8"))
        return httpx.Response(
            status_code=data["status"],
            headers={"content-type": data["content_type"]},
            text=data["body"],
            request=request,
        )


class RecordingTransport(httpx.AsyncBaseTransport):
    """Forwards to the real network and writes every response as a fixture."""

    def __init__(self, root: Path, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self.root = root
        self.inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        body = scrub(request.url, (await response.aread()).decode("utf-8"))
        self.root.mkdir(parents=True, exist_ok=True)
        record = {
            "url": _display_url(request.url),
            "status": response.status_code,
            "content_type": response.headers.get("content-type", "text/plain"),
            "body": body,
        }
        path = self.root / f"{request_key(request.method, request.url)}.json"
        path.write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
        return httpx.Response(
            status_code=response.status_code,
            headers={"content-type": record["content_type"]},
            text=body,
            request=request,
        )

    async def aclose(self) -> None:
        await self.inner.aclose()
