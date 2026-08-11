from __future__ import annotations

import base64
import binascii
from contextlib import AsyncExitStack
from datetime import timedelta
import inspect
from types import TracebackType
from typing import Any, Self

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
try:
    from mcp.types import PaginatedRequestParams
except ImportError:  # MCP 1.x accepts the cursor directly
    PaginatedRequestParams = None  # type: ignore[assignment,misc]

from .types import ContentBlock, ToolResult, ToolSchema


_MCP_USES_PARAMS_PAGINATION = "params" in inspect.signature(ClientSession.list_tools).parameters


class McpClient:
    def __init__(self, url: str, *, token: str, timeout: float = 30) -> None:
        self.url = url
        self.token = token
        self.timeout = timeout
        self._exit_stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    @classmethod
    def from_url(cls, url: str, *, token: str, timeout: float = 30) -> Self:
        return cls(url, token=token, timeout=timeout)

    @classmethod
    async def open_from_url(cls, url: str, *, token: str, timeout: float = 30) -> Self:
        """Construct + open the SSE session in one call. Equivalent to
        ``client = McpClient.from_url(...); await client.__aenter__()``.
        Adapters that don't want to wrap every call site in ``async with``
        should use this and pair it with ``await client.aclose()`` (or
        ``await client.__aexit__(None, None, None)``) on shutdown.
        """
        client = cls.from_url(url, token=token, timeout=timeout)
        await client.__aenter__()
        return client

    async def aclose(self) -> None:
        """Close the SSE session if open. Mirrors __aexit__ but takes no
        exception args. Safe to call multiple times."""
        await self.__aexit__(None, None, None)

    async def __aenter__(self) -> Self:
        self._exit_stack = AsyncExitStack()
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            read_stream, write_stream = await self._exit_stack.enter_async_context(
                sse_client(self.url, headers=headers, timeout=self.timeout)
            )
            session = ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=(
                    self.timeout if _MCP_USES_PARAMS_PAGINATION else timedelta(seconds=self.timeout)
                ),
            )
            self._session = await self._exit_stack.enter_async_context(session)
            await self._session.initialize()
        except BaseException:
            try:
                await self._exit_stack.aclose()
            finally:
                self._session = None
                self._exit_stack = None
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._session = None
        self._exit_stack = None

    async def list_tools(self) -> list[ToolSchema]:
        session = self._require_session()
        tools = []
        cursor = None
        while True:
            if _MCP_USES_PARAMS_PAGINATION:
                assert PaginatedRequestParams is not None
                params = PaginatedRequestParams(cursor=cursor) if cursor is not None else None
                result = await session.list_tools(params=params)
            else:
                result = await session.list_tools(cursor=cursor)
            tools.extend(result.tools)
            cursor = getattr(result, "next_cursor", None)
            if cursor is None:
                cursor = getattr(result, "nextCursor", None)
            if cursor is None:
                break
        return [
            ToolSchema(
                name=tool.name,
                description=tool.description or "",
                input_schema=getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {},
            )
            for tool in tools
        ]

    async def call_tool(self, name: str, args: dict) -> ToolResult:
        session = self._require_session()
        result = await session.call_tool(name, arguments=args)
        return ToolResult(
            content=[block for item in result.content if (block := self._map_content(item)) is not None],
            is_error=bool(getattr(result, "is_error", getattr(result, "isError", False))),
        )

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("McpClient session is not open; use 'async with McpClient.from_url(...) as c'.")
        return self._session

    @staticmethod
    def _map_content(item: Any) -> ContentBlock | None:
        item_type = getattr(item, "type", None)
        if item_type == "text":
            return ContentBlock(type="text", text=getattr(item, "text", ""))
        if item_type == "image":
            return ContentBlock(
                type="image",
                data=_decode_image_data(getattr(item, "data", b"")),
                mime_type=getattr(item, "mimeType", None) or getattr(item, "mime_type", None),
            )
        if item_type == "resource_link":
            uri = getattr(item, "uri", None)
            return ContentBlock(type="resource_link", uri=str(uri) if uri is not None else None)
        return None


def _decode_image_data(data: Any) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        try:
            return base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            return data.encode("utf-8")
    return bytes(data)
