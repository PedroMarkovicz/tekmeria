from pathlib import Path

import pytest

from tekmerion.infra.cache import Cache, CachedResponse
from tekmerion.infra.ratelimit import RateLimiter

pytestmark = pytest.mark.anyio


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 6))
        self.now += seconds


async def test_rate_limiter_spaces_requests_evenly() -> None:
    clock = FakeClock()
    limiter = RateLimiter(4, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        await limiter.acquire()
    assert clock.slept == [0.25, 0.25]


async def test_rate_limiter_does_not_sleep_after_idle_time() -> None:
    clock = FakeClock()
    limiter = RateLimiter(1, clock=clock, sleep=clock.sleep)
    await limiter.acquire()
    clock.now += 5
    await limiter.acquire()
    assert clock.slept == []


def test_rate_limiter_rejects_non_positive_rate() -> None:
    with pytest.raises(ValueError):
        RateLimiter(0)


async def test_cache_respects_ttl() -> None:
    clock = FakeClock()
    async with Cache(":memory:", clock=clock) as cache:
        await cache.put_response("k", CachedResponse(200, "application/json", "{}"), ttl_s=10)
        assert await cache.get_response("k") == CachedResponse(200, "application/json", "{}")
        clock.now += 11
        assert await cache.get_response("k") is None


async def test_issued_text_is_never_overwritten() -> None:
    async with Cache(":memory:") as cache:
        await cache.record_issued([("pmid:1/title", "first", None)])
        await cache.record_issued([("pmid:1/title", "second", None)])
        issued = await cache.get_issued(["pmid:1/title", "pmid:2/title"])
    assert list(issued) == ["pmid:1/title"]
    assert issued["pmid:1/title"].text == "first"


async def test_cache_file_is_created(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "cache.sqlite3"
    async with Cache(path) as cache:
        await cache.record_issued([("pmid:1/title", "t", None)])
    assert path.exists()


async def test_a_cache_file_uses_wal_and_waits_for_other_writers(tmp_path: Path) -> None:
    async with Cache(tmp_path / "cache.sqlite3") as cache:
        async with cache.db.execute("PRAGMA journal_mode") as cursor:
            assert await cursor.fetchone() == ("wal",)
        async with cache.db.execute("PRAGMA busy_timeout") as cursor:
            assert await cursor.fetchone() == (5000,)


async def test_expired_responses_are_purged_when_the_cache_opens(tmp_path: Path) -> None:
    clock, path = FakeClock(), tmp_path / "cache.sqlite3"
    async with Cache(path, clock=clock) as cache:
        await cache.put_response("old", CachedResponse(200, "text/plain", "x"), ttl_s=10)
        await cache.put_response("new", CachedResponse(200, "text/plain", "y"), ttl_s=1000)
        await cache.record_issued([("pmid:1/title", "t", None)])
    clock.now += 100
    async with Cache(path, clock=clock) as cache:
        async with cache.db.execute("SELECT key FROM responses") as cursor:
            assert await cursor.fetchall() == [("new",)]
        assert list(await cache.get_issued(["pmid:1/title"])) == ["pmid:1/title"]
