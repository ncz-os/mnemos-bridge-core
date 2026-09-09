from __future__ import annotations

import asyncio
import base64
import binascii
import inspect
import json
import os
import warnings
from contextlib import AsyncExitStack
from datetime import timedelta
from importlib.metadata import version
from types import TracebackType
from typing import Any, Self
from urllib.parse import SplitResult, urlsplit

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client

try:
    from mcp.types import PaginatedRequestParams
except ImportError:  # MCP 1.x accepts the cursor directly
    PaginatedRequestParams = None  # type: ignore[assignment,misc]

from .types import ContentBlock, ToolResult, ToolSchema


def _mcp_uses_float_timeout(mcp_version: str) -> bool:
    return int(mcp_version.split(".", 1)[0]) >= 2


_MCP_USES_PARAMS_PAGINATION = "params" in inspect.signature(ClientSession.list_tools).parameters
_MCP_USES_CURSOR_PAGINATION = "cursor" in inspect.signature(ClientSession.list_tools).parameters
_MCP_USES_FLOAT_TIMEOUT = _mcp_uses_float_timeout(version("mcp"))
_SUPPORTED_URL_SCHEMES = frozenset({"http", "https"})
_INSECURE_TRANSPORT_ENV = "MNEMOS_ALLOW_INSECURE_TRANSPORT"
_TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


def _insecure_transport_enabled(allow_insecure_transport: bool | None) -> bool:
    if allow_insecure_transport is not None:
        return allow_insecure_transport
    return os.environ.get(_INSECURE_TRANSPORT_ENV, "").strip().lower() in _TRUE_ENV_VALUES


def _safe_endpoint(parts: SplitResult) -> str:
    """Render an endpoint without URL credentials, paths, queries, or fragments."""
    host = parts.hostname or "<missing-host>"
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    return f"{parts.scheme.lower()}://{host}{f':{port}' if port is not None else ''}"


def _validate_url(url: str, *, allow_insecure_transport: bool) -> str:
    """Validate an SSE URL before the bearer token can reach the transport."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise ValueError("McpClient URL is invalid") from None

    scheme = parts.scheme.lower()
    if scheme not in _SUPPORTED_URL_SCHEMES:
        raise ValueError("McpClient URL must use https://, or http:// with the LAN-only insecure opt-in")
    if not parts.hostname:
        raise ValueError("McpClient URL must include a host")
    if port is not None and not 0 < port <= 65535:
        raise ValueError("McpClient URL port is out of range")
    if scheme == "http":
        if not allow_insecure_transport:
            raise ValueError(
                "Refusing to send the MNEMOS bearer token over plaintext http://. "
                "Use https://, or explicitly enable the LAN-only insecure transport opt-in."
            )
        warnings.warn(
            f"Sending the MNEMOS bearer token over plaintext HTTP to {_safe_endpoint(parts)}; "
            "allow_insecure_transport is for trusted LAN deployments only.",
            UserWarning,
            stacklevel=3,
        )
    return url


class McpClient:
    def __init__(
        self,
        url: str,
        *,
        token: str,
        timeout: float = 30,
        allow_insecure_transport: bool | None = None,
    ) -> None:
        insecure_transport_enabled = _insecure_transport_enabled(allow_insecure_transport)
        self.url = _validate_url(url, allow_insecure_transport=insecure_transport_enabled)
        self.token = token
        self.timeout = timeout
        self.allow_insecure_transport = insecure_transport_enabled
        self._exit_stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._owner_task: asyncio.Task[None] | None = None
        self._owner_close: asyncio.Event | None = None

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        token: str,
        timeout: float = 30,
        allow_insecure_transport: bool | None = None,
    ) -> Self:
        if allow_insecure_transport is None:
            return cls(url, token=token, timeout=timeout)
        return cls(url, token=token, timeout=timeout, allow_insecure_transport=allow_insecure_transport)

    @classmethod
    async def open_from_url(
        cls,
        url: str,
        *,
        token: str,
        timeout: float = 30,
        allow_insecure_transport: bool | None = None,
    ) -> Self:
        """Construct + open the SSE session in one call. Equivalent to
        ``client = McpClient.from_url(...); await client.__aenter__()``.
        Adapters that don't want to wrap every call site in ``async with``
        should use this and pair it with ``await client.aclose()`` (or
        ``await client.__aexit__(None, None, None)``) on shutdown.
        """
        if allow_insecure_transport is None:
            client = cls.from_url(url, token=token, timeout=timeout)
        else:
            client = cls.from_url(
                url,
                token=token,
                timeout=timeout,
                allow_insecure_transport=allow_insecure_transport,
            )
        started = asyncio.get_running_loop().create_future()
        client._owner_close = asyncio.Event()
        client._owner_task = asyncio.create_task(client._own_lifecycle(started))
        try:
            await started
        except BaseException:
            owner_task = client._owner_task
            if owner_task is not None and not owner_task.done():
                owner_task.cancel()
                try:
                    await owner_task
                except asyncio.CancelledError:
                    pass
            client._owner_task = None
            client._owner_close = None
            raise
        return client

    async def _own_lifecycle(self, started: asyncio.Future[None]) -> None:
        """Enter and exit task-affine MCP contexts from one owner task."""
        try:
            await self.__aenter__()
        except BaseException as error:
            if not started.done():
                started.set_exception(error)
            return
        if started.cancelled():
            await self._close_stack()
            return
        started.set_result(None)
        assert self._owner_close is not None
        await self._owner_close.wait()
        await self._close_stack()

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
                read_timeout_seconds=(self.timeout if _MCP_USES_FLOAT_TIMEOUT else timedelta(seconds=self.timeout)),
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
        owner_task = self._owner_task
        if owner_task is not None and asyncio.current_task() is not owner_task:
            assert self._owner_close is not None
            self._owner_close.set()
            await owner_task
            self._owner_task = None
            self._owner_close = None
            return
        await self._close_stack()

    async def _close_stack(self) -> None:
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
            elif _MCP_USES_CURSOR_PAGINATION:
                result = await session.list_tools(cursor=cursor)
            else:
                result = await session.list_tools()
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
        content = []
        unsupported = []
        for item in result.content:
            block = self._map_content(item)
            if block is None:
                unsupported.append(str(getattr(item, "type", type(item).__name__)))
            else:
                content.append(block)
        if unsupported:
            content.append(ContentBlock(type="text", text=f"Unsupported MCP content type(s): {', '.join(unsupported)}"))
        return ToolResult(
            content=content,
            is_error=(
                bool(getattr(result, "is_error", getattr(result, "isError", False)))
                or bool(unsupported)
                or _content_reports_error(content)
            ),
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
        if item_type == "resource":
            resource = getattr(item, "resource", None)
            text = getattr(resource, "text", None)
            if text is not None:
                return ContentBlock(type="text", text=text)
            blob = getattr(resource, "blob", None)
            mime_type = getattr(resource, "mimeType", None) or getattr(resource, "mime_type", None)
            if blob is not None and mime_type and mime_type.startswith("image/"):
                return ContentBlock(type="image", data=_decode_image_data(blob), mime_type=mime_type)
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


def _content_reports_error(content: list[ContentBlock]) -> bool:
    """Recognize the JSON failure envelopes returned by MNEMOS MCP servers."""
    for block in content:
        if block.type != "text" or block.text is None:
            continue
        try:
            payload = json.loads(block.text)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict) and (payload.get("success") is False or "error" in payload):
            return True
    return False
