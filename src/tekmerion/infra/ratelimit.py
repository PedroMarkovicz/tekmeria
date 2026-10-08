"""Per-host request spacing (spec section 9.1)."""

import time
from collections.abc import Awaitable, Callable

import anyio


class RateLimiter:
    """Allows at most `rate` requests per `per_seconds`, evenly spaced, no bursts."""

    def __init__(
        self,
        rate: float,
        per_seconds: float = 1.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        if rate <= 0 or per_seconds <= 0:
            raise ValueError("rate and per_seconds must be positive")
        self.interval = per_seconds / rate
        self._clock = clock
        self._sleep = sleep
        self._next_free = 0.0
        self._lock = anyio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = self._clock()
            wait = self._next_free - now
            if wait > 0:
                await self._sleep(wait)
                now = self._next_free
            self._next_free = now + self.interval
