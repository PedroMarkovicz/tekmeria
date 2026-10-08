import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from mcp import Client
from mcp.types import CallToolResult

from tekmerion.__main__ import demo_config
from tekmerion.demo.scenario import SCENARIO_FILE
from tekmerion.server.app import create_server


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client() -> AsyncIterator[Client]:
    """An MCP client talking to the server in-process, replaying recorded fixtures."""
    async with Client(create_server(demo_config())) as connected:
        yield connected


@pytest.fixture
def scenario() -> dict[str, str]:
    return json.loads(SCENARIO_FILE.read_text(encoding="utf-8"))


def structured(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content
