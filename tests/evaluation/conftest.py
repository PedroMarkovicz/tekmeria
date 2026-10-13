from collections.abc import AsyncIterator

import pytest

from tekmeria.__main__ import demo_config
from tekmeria.evaluation.snapshot import Snapshot, load_snapshot
from tekmeria.server.runtime import open_runtime


@pytest.fixture
async def snapshot() -> AsyncIterator[Snapshot]:
    """The evaluation's records, fetched from the recorded fixtures."""
    async with open_runtime(demo_config()) as app:
        yield await load_snapshot(app.service)
