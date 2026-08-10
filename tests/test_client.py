from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import mnemos_bridge_core.client as client_module
from mnemos_bridge_core import McpClient


async def test_failed_initialization_closes_entered_contexts(monkeypatch) -> None:
    closed = []

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
    monkeypatch.setattr(client_module, "ClientSession", lambda *args, **kwargs: FakeSession())
    client = McpClient.from_url("https://example.com/sse", token="token")

    with pytest.raises(RuntimeError, match="initialization failed"):
        await client.__aenter__()

    assert closed == ["session", "transport"]
    assert client._session is None
    assert client._exit_stack is None


async def test_list_tools_accumulates_all_pages() -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.cursors = []

        async def list_tools(self, cursor=None):
            self.cursors.append(cursor)
            if cursor is None:
                return SimpleNamespace(
                    tools=[SimpleNamespace(name="first", description=None, inputSchema={})],
                    nextCursor="page-2",
                )
            return SimpleNamespace(
                tools=[SimpleNamespace(name="second", description="Second", inputSchema={"type": "object"})],
                nextCursor=None,
            )

    session = FakeSession()
    client = McpClient.from_url("https://example.com/sse", token="token")
    client._session = session

    tools = await client.list_tools()

    assert [tool.name for tool in tools] == ["first", "second"]
    assert session.cursors == [None, "page-2"]
