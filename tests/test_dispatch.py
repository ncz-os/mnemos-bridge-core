from __future__ import annotations

import importlib

import httpx
import pytest

try:
    from mcp.shared.exceptions import MCPError
except ImportError:  # MCP 1.x spelling
    from mcp.shared.exceptions import McpError as MCPError
from mcp.types import ErrorData

try:
    from mcp.types import CONNECTION_CLOSED, REQUEST_TIMEOUT
except ImportError:  # MCP 1.0 does not export these constants
    CONNECTION_CLOSED = -32000
    REQUEST_TIMEOUT = -32001

from mnemos_bridge_core import ToolResult
from mnemos_bridge_core.dispatch import dispatch

dispatch_module = importlib.import_module("mnemos_bridge_core.dispatch")


def _make_mcp_error(error_code: int) -> MCPError:
    error_data = ErrorData(code=error_code, message="transient MCP error")
    factory = getattr(MCPError, "from_error_data", None)
    return factory(error_data) if factory is not None else MCPError(error_data)


async def test_dispatch_default_configuration_calls_once() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        async def call_tool(self, name, args):
            self.calls += 1
            return ToolResult(content=[])

    client = FakeClient()

    result = await dispatch(client, "search_memories", {})

    assert result == ToolResult(content=[])
    assert client.calls == 1


@pytest.mark.parametrize("error_code", [REQUEST_TIMEOUT, CONNECTION_CLOSED])
async def test_dispatch_retries_retryable_mcp_errors(monkeypatch, error_code) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        async def call_tool(self, name, args):
            self.calls += 1
            if self.calls == 1:
                raise _make_mcp_error(error_code)
            return ToolResult(content=[])

    async def no_sleep(delay):
        return None

    monkeypatch.setattr(dispatch_module.anyio, "sleep", no_sleep)
    client = FakeClient()

    result = await dispatch(client, "search_memories", {}, retries=1, idempotent=True)

    assert result == ToolResult(content=[])
    assert client.calls == 2


async def test_dispatch_does_not_confuse_http_408_with_mcp_timeout(monkeypatch) -> None:
    class FakeClient:
        async def call_tool(self, name, args):
            raise _make_mcp_error(httpx.codes.REQUEST_TIMEOUT)

    async def no_sleep(delay):
        pytest.fail("HTTP status 408 is not an MCP transport error code")

    monkeypatch.setattr(dispatch_module.anyio, "sleep", no_sleep)

    with pytest.raises(MCPError):
        await dispatch(FakeClient(), "search_memories", {}, retries=1, idempotent=True)


async def test_dispatch_does_not_retry_non_idempotent_tool_after_partial_mutation(monkeypatch) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0
            self.committed_side_effects = 0

        async def call_tool(self, name, args):
            self.calls += 1
            self.committed_side_effects += 1
            raise httpx.ReadError("response connection closed")

    async def no_sleep(delay):
        pytest.fail("non-idempotent dispatch must not sleep for a retry")

    monkeypatch.setattr(dispatch_module.anyio, "sleep", no_sleep)
    client = FakeClient()

    with pytest.raises(httpx.ReadError):
        await dispatch(client, "create_memory", {}, retries=2)

    assert client.calls == 1
    assert client.committed_side_effects == 1
