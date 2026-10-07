from typing import Any

import pytest
from mcp.types import CallToolResult


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def structured(result: CallToolResult) -> dict[str, Any]:
    """The structured content of a successful tool result."""
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content
