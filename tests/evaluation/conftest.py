from collections.abc import AsyncIterator

import pytest

from tekmerion.__main__ import demo_config
from tekmerion.evaluation.snapshot import Snapshot, load_snapshot
from tekmerion.server.runtime import open_runtime


@pytest.fixture
async def snapshot() -> AsyncIterator[Snapshot]:
    """The evaluation's records, fetched from the recorded fixtures."""
    async with open_runtime(demo_config()) as app:
        yield await load_snapshot(app.service)
