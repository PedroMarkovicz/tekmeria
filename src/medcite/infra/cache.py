"""SQLite cache: upstream responses with TTLs, and passages as issued (spec section 9.2)."""

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS responses (
    key TEXT PRIMARY KEY,
    status INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    body TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS issued_passages (
    locator TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    record_version TEXT,
    issued_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class CachedResponse:
    status: int
    content_type: str
    body: str


@dataclass(frozen=True)
class IssuedPassage:
    locator: str
    text: str
    sha256: str
    record_version: str | None
    issued_at: str


class Cache:
    """Use as `async with Cache(path) as cache:`. Pass ":memory:" for tests."""

    def __init__(self, path: Path | str, *, clock: Callable[[], float] = time.time) -> None:
        self._path = str(path)
        self._clock = clock
        self._db: aiosqlite.Connection | None = None

    async def __aenter__(self) -> Self:
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("cache is not open")
        return self._db

    async def get_response(self, key: str) -> CachedResponse | None:
        async with self.db.execute(
            "SELECT status, content_type, body, expires_at FROM responses WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None or row[3] <= self._clock():
            return None
        return CachedResponse(status=row[0], content_type=row[1], body=row[2])

    async def put_response(self, key: str, response: CachedResponse, ttl_s: float) -> None:
        await self.db.execute(
            "INSERT OR REPLACE INTO responses VALUES (?, ?, ?, ?, ?)",
            (key, response.status, response.content_type, response.body, self._clock() + ttl_s),
        )
        await self.db.commit()

    async def record_issued(self, rows: list[tuple[str, str, str | None]]) -> None:
        """Store (locator, text, record_version) rows.

        The first text ever issued for a locator is kept; later issues never overwrite it.
        """
        if not rows:
            return
        issued_at = datetime.now(UTC).isoformat()
        await self.db.executemany(
            "INSERT OR IGNORE INTO issued_passages VALUES (?, ?, ?, ?, ?)",
            [
                (
                    locator,
                    text,
                    hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    version,
                    issued_at,
                )
                for locator, text, version in rows
            ],
        )
        await self.db.commit()

    async def get_issued(self, locators: list[str]) -> dict[str, IssuedPassage]:
        if not locators:
            return {}
        marks = ",".join("?" * len(locators))
        async with self.db.execute(
            f"SELECT locator, text, sha256, record_version, issued_at "
            f"FROM issued_passages WHERE locator IN ({marks})",
            locators,
        ) as cursor:
            rows = await cursor.fetchall()
        return {row[0]: IssuedPassage(*row) for row in rows}
