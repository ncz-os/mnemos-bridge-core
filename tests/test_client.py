from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import create_autospec

import pytest
from mcp.client.session import ClientSession
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

import mnemos_bridge_core.client as client_module
from mnemos_bridge_core import McpClient


@pytest.mark.parametrize(
    ("mcp_version", "expected"),
    [("1.0.0", False), ("1.26.0", False), ("2.0.0", True)],
)
def test_timeout_type_follows_mcp_major_version(mcp_version, expected) -> None:
    assert client_module._mcp_uses_float_timeout(mcp_version) is expected


@pytest.mark.parametrize(
    ("uses_params_pagination", "uses_float_timeout", "expected_timeout"),
    [
        (True, False, timedelta(seconds=30)),  # MCP 1.26
        (False, False, timedelta(seconds=30)),  # earlier MCP 1.x
        (True, True, 30),  # MCP 2.x
    ],
)
async def test_failed_initialization_closes_entered_contexts(
    monkeypatch, uses_params_pagination, uses_float_timeout, expected_timeout
) -> None:
    closed = []
    session_kwargs = {}

    @asynccontextmanager
    async def fake_sse_client(*args, **kwargs):
        try:
            yield object(), object()
        finally:
            closed.append("transport")

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            closed.append("session")

        async def initialize(self):
            raise RuntimeError("initialization failed")

    monkeypatch.setattr(client_module, "sse_client", fake_sse_client)
    monkeypatch.setattr(client_module, "_MCP_USES_PARAMS_PAGINATION", uses_params_pagination)
    monkeypatch.setattr(client_module, "_MCP_USES_FLOAT_TIMEOUT", uses_float_timeout)

    def fake_session(*args, **kwargs):
        session_kwargs.update(kwargs)
        return FakeSession()

    monkeypatch.setattr(client_module, "ClientSession", fake_session)
    client = McpClient.from_url("https://example.com/sse", token="token")

    with pytest.raises(RuntimeError, match="initialization failed"):
        await client.__aenter__()

    assert closed == ["session", "transport"]
    assert client._session is None
    assert client._exit_stack is None
    assert session_kwargs["read_timeout_seconds"] == expected_timeout


async def test_list_tools_accumulates_all_pages() -> None:
    session = create_autospec(ClientSession, instance=True)
    session.list_tools.side_effect = [
        ListToolsResult(
            tools=[Tool(name="first", description=None, inputSchema={})],
            nextCursor="page-2",
        ),
        ListToolsResult(
            tools=[Tool(name="second", description="Second", inputSchema={"type": "object"})],
            nextCursor=None,
        ),
    ]
    client = McpClient.from_url("https://example.com/sse", token="token")
    client._session = session

    tools = await client.list_tools()

    assert [tool.name for tool in tools] == ["first", "second"]
    first_params = session.list_tools.await_args_list[0].kwargs["params"]
    second_params = session.list_tools.await_args_list[1].kwargs["params"]
    assert first_params is None
    assert second_params.cursor == "page-2"


async def test_list_tools_supports_mcp_1_cursor_contract(monkeypatch) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.cursors = []

        async def list_tools(self, cursor=None):
            self.cursors.append(cursor)
            return SimpleNamespace(
                tools=[SimpleNamespace(name="tool", description=None, inputSchema={})],
                nextCursor=None,
            )

    monkeypatch.setattr(client_module, "_MCP_USES_PARAMS_PAGINATION", False)
    session = FakeSession()
    client = McpClient.from_url("https://example.com/sse", token="token")
    client._session = session

    tools = await client.list_tools()

    assert [tool.name for tool in tools] == ["tool"]
    assert session.cursors == [None]


async def test_call_tool_preserves_sdk_error_flag() -> None:
    session = create_autospec(ClientSession, instance=True)
    session.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text="failed")],
        isError=True,
    )
    client = McpClient.from_url("https://example.com/sse", token="token")
    client._session = session

    result = await client.call_tool("create_memory", {})

    assert result.is_error is True


@pytest.mark.parametrize(
    "payload",
    [
        '{"success": false, "error": "backend unavailable"}',
        '{"error": "503 Service Unavailable", "detail": {"retry": true}}',
    ],
)
async def test_call_tool_recognizes_mnemos_json_failure_envelopes(payload) -> None:
    session = create_autospec(ClientSession, instance=True)
    session.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text=payload)],
        isError=False,
    )
    client = McpClient.from_url("https://example.com/sse", token="token")
    client._session = session

    result = await client.call_tool("create_memory", {})

    assert result.is_error is True


@pytest.mark.parametrize(
    "payload",
    [
        '{"success": true, "result": {"error": "historical value"}}',
        "ordinary non-JSON tool output",
    ],
)
async def test_call_tool_does_not_misclassify_successful_content(payload) -> None:
    session = create_autospec(ClientSession, instance=True)
    session.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text=payload)],
        isError=False,
    )
    client = McpClient.from_url("https://example.com/sse", token="token")
    client._session = session

    result = await client.call_tool("search_memories", {})

    assert result.is_error is False
