from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
import asyncio
from types import SimpleNamespace
from unittest.mock import create_autospec

import pytest
from mcp.client.session import ClientSession
from mcp.types import (
    CallToolResult,
    ListToolsResult,
    TextContent,
    Tool,
)

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
    monkeypatch.setattr(client_module, "_MCP_USES_CURSOR_PAGINATION", True)
    session = FakeSession()
    client = McpClient.from_url("https://example.com/sse", token="token")
    client._session = session

    tools = await client.list_tools()

    assert [tool.name for tool in tools] == ["tool"]
    assert session.cursors == [None]


async def test_list_tools_supports_mcp_1_0_zero_argument_contract(monkeypatch) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.calls = 0

        async def list_tools(self):
            self.calls += 1
            return SimpleNamespace(
                tools=[SimpleNamespace(name="tool", description=None, inputSchema={})],
                nextCursor=None,
            )

    monkeypatch.setattr(client_module, "_MCP_USES_PARAMS_PAGINATION", False)
    monkeypatch.setattr(client_module, "_MCP_USES_CURSOR_PAGINATION", False)
    session = FakeSession()
    client = McpClient.from_url("https://example.com/sse", token="token")
    client._session = session

    tools = await client.list_tools()

    assert [tool.name for tool in tools] == ["tool"]
    assert session.calls == 1


async def test_open_from_url_closes_task_affine_transport_from_another_task(monkeypatch) -> None:
    entered_in = None
    exited_in = None

    @asynccontextmanager
    async def fake_sse_client(*args, **kwargs):
        nonlocal entered_in, exited_in
        entered_in = asyncio.current_task()
        try:
            yield object(), object()
        finally:
            exited_in = asyncio.current_task()
            if exited_in is not entered_in:
                raise RuntimeError("transport exited from a different task")

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def initialize(self):
            return None

    monkeypatch.setattr(client_module, "sse_client", fake_sse_client)
    monkeypatch.setattr(client_module, "ClientSession", lambda *args, **kwargs: FakeSession())

    client = await McpClient.open_from_url("https://example.com/sse", token="token")
    await asyncio.create_task(client.aclose())

    assert exited_in is entered_in
    assert client._session is None


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


async def test_call_tool_normalizes_embedded_text_and_image_resources() -> None:
    session = create_autospec(ClientSession, instance=True)
    session.call_tool.return_value = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="resource",
                resource=SimpleNamespace(uri="memory://one", text="embedded memory"),
            ),
            SimpleNamespace(
                type="resource",
                resource=SimpleNamespace(uri="memory://image", mimeType="image/png", blob="aW1hZ2U="),
            ),
        ],
        isError=False,
    )
    client = McpClient.from_url("https://example.com/sse", token="token")
    client._session = session

    result = await client.call_tool("read_resource", {})

    assert result.is_error is False
    assert result.content[0].text == "embedded memory"
    assert result.content[1].data == b"image"
    assert result.content[1].mime_type == "image/png"


async def test_call_tool_fails_closed_for_unrepresentable_supported_content() -> None:
    session = create_autospec(ClientSession, instance=True)
    session.call_tool.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="audio", data="YXVkaW8=", mimeType="audio/wav")],
        isError=False,
    )
    client = McpClient.from_url("https://example.com/sse", token="token")
    client._session = session

    result = await client.call_tool("read_audio", {})

    assert result.is_error is True
    assert result.content == [client_module.ContentBlock(type="text", text="Unsupported MCP content type(s): audio")]
