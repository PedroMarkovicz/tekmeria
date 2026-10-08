"""One upstream host: pooled client, rate limit, retries, cache (spec section 9.1)."""

import random
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

import anyio
import httpx

from tekmerion.infra.cache import Cache, CachedResponse
from tekmerion.infra.fixtures import request_key
from tekmerion.infra.ratelimit import RateLimiter

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_RETRIES = 3


class UpstreamUnavailableError(Exception):
    """Network failure, timeout, 429 or 5xx after all retries. Retryable."""


class UpstreamNotFoundError(Exception):
    """The upstream answered 404."""


class UpstreamResponseError(Exception):
    """Any other non-success status. Not retryable."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


@dataclass(frozen=True)
class HostConfig:
    name: str
    base_url: str
    rate: float
    per_seconds: float = 1.0
    default_params: Mapping[str, str] | None = None


# The upstream calls of the current tool call, for its log line (spec 9.3).
_calls: ContextVar[list[dict[str, object]] | None] = ContextVar("upstream_calls", default=None)


@contextmanager
def record_upstream_calls() -> Iterator[list[dict[str, object]]]:
    """Collect {host, status, cache} for every upstream call made inside the block,
    including calls made by tasks started inside it."""
    calls: list[dict[str, object]] = []
    token = _calls.set(calls)
    try:
        yield calls
    finally:
        _calls.reset(token)


def _note(host: str, status: int | str, *, cache: bool) -> None:
    calls = _calls.get()
    if calls is not None:
        calls.append({"host": host, "status": status, "cache": cache})


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


class Upstream:
    def __init__(
        self,
        config: HostConfig,
        *,
        cache: Cache,
        transport: httpx.AsyncBaseTransport | None = None,
        limiter: RateLimiter | None = None,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        user_agent: str = "tekmerion",
    ) -> None:
        self.config = config
        self.cache = cache
        self.limiter = limiter or RateLimiter(config.rate, config.per_seconds)
        self._sleep = sleep
        self.client = httpx.AsyncClient(
            base_url=config.base_url,
            transport=transport,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"user-agent": user_agent},
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def get_text(
        self,
        path: str,
        params: Mapping[str, str],
        *,
        ttl_s: float,
        validate: Callable[[str], None] | None = None,
    ) -> str:
        """GET with cache, rate limit and retries. Returns the body of a 2xx response.

        `validate` runs before caching and raises UpstreamResponseError for a body the
        caller cannot use (e.g. an HTML error page sent with status 200), so a bad body
        is never cached and the next call asks upstream again.
        """
        merged = {**(self.config.default_params or {}), **params}
        request = self.client.build_request("GET", path, params=merged)
        key = f"{self.config.name}:{request_key('GET', request.url)}"
        cached = await self.cache.get_response(key)
        if cached is not None:
            _note(self.config.name, cached.status, cache=True)
            return cached.body
        try:
            response = await self._send_with_retries(request)
        except UpstreamUnavailableError:
            _note(self.config.name, "unavailable", cache=False)
            raise
        _note(self.config.name, response.status_code, cache=False)
        if response.status_code == 404:
            raise UpstreamNotFoundError(str(request.url.copy_with(params=None)))
        if response.status_code >= 400:
            raise UpstreamResponseError(response.status_code, response.text[:300])
        body = response.text
        if validate is not None:
            validate(body)
        await self.cache.put_response(
            key,
            CachedResponse(
                status=response.status_code,
                content_type=response.headers.get("content-type", ""),
                body=body,
            ),
            ttl_s,
        )
        return body

    async def _send_with_retries(self, request: httpx.Request) -> httpx.Response:
        last_error = "no attempt made"
        for attempt in range(MAX_RETRIES + 1):
            await self.limiter.acquire()
            try:
                response = await self.client.send(request)
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                wait = None
            else:
                if response.status_code not in RETRYABLE_STATUS:
                    return response
                last_error = f"HTTP {response.status_code}"
                wait = _retry_after_seconds(response)
            if attempt == MAX_RETRIES:
                break
            backoff = random.uniform(0, min(8.0, 0.5 * 2**attempt))  # full jitter
            await self._sleep(wait if wait is not None else backoff)
        raise UpstreamUnavailableError(f"{self.config.name}: {last_error}")
