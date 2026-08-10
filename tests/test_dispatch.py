from __future__ import annotations

import importlib

import httpx
import pytest
from mcp.shared.exceptions import MCPError
from mcp.types import CONNECTION_CLOSED, ErrorData

from mnemos_bridge_core import ToolResult
from mnemos_bridge_core.dispatch import dispatch


dispatch_module = importlib.import_module("mnemos_bridge_core.dispatch")


@pytest.mark.parametrize("error_code", [httpx.codes.REQUEST_TIMEOUT, CONNECTION_CLOSED])
async def test_dispatch_retries_retryable_mcp_errors(monkeypatch, error_code) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        async def call_tool(self, name, args):
            self.calls += 1
            if self.calls == 1:
                raise MCPError.from_error_data(ErrorData(code=error_code, message="transient MCP error"))
            return ToolResult(content=[])

    async def no_sleep(delay):
        return None

    monkeypatch.setattr(dispatch_module.anyio, "sleep", no_sleep)
    client = FakeClient()

    result = await dispatch(client, "search_memories", {}, retries=1)

    assert result == ToolResult(content=[])
    assert client.calls == 2
