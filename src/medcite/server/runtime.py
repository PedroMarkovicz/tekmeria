"""Builds the per-process dependencies the tools use (opened in the server lifespan)."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path

import anyio
import httpx

from medcite.contract.handles import HandleCodec
from medcite.infra.cache import Cache
from medcite.infra.http import Upstream
from medcite.sources.pubmed import PubMedAdapter, ncbi_host
from medcite.sources.service import EvidenceService

# Handle resolution gets most of the tool deadline; the rest is left for judging and output.
RESOLVE_SHARE_OF_DEADLINE = 0.8


@dataclass(frozen=True)
class RuntimeConfig:
    handle_key: bytes
    cache_path: Path | str
    transport: httpx.AsyncBaseTransport | None = None
    sleep: Callable[[float], Awaitable[None]] = anyio.sleep
    ncbi_api_key: str | None = None
    ncbi_email: str | None = None
    rate_override: float | None = None  # requests/second; fixtures need no spacing
    deadline_s: float = 45.0


@dataclass(frozen=True)
class AppContext:
    service: EvidenceService
    codec: HandleCodec
    deadline_s: float


@asynccontextmanager
async def open_runtime(config: RuntimeConfig) -> AsyncIterator[AppContext]:
    codec = HandleCodec(config.handle_key)
    async with Cache(config.cache_path) as cache:
        host = ncbi_host(api_key=config.ncbi_api_key, email=config.ncbi_email)
        if config.rate_override is not None:
            host = replace(host, rate=config.rate_override)
        ncbi = Upstream(host, cache=cache, transport=config.transport, sleep=config.sleep)
        try:
            service = EvidenceService(
                codec=codec,
                cache=cache,
                pubmed=PubMedAdapter(ncbi),
                resolve_budget_s=config.deadline_s * RESOLVE_SHARE_OF_DEADLINE,
            )
            yield AppContext(service=service, codec=codec, deadline_s=config.deadline_s)
        finally:
            await ncbi.aclose()
