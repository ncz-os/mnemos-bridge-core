from __future__ import annotations

import anyio
import httpx
try:
    from mcp.shared.exceptions import MCPError
except ImportError:  # mcp releases before the exception was capitalized
    from mcp.shared.exceptions import McpError as MCPError
from mcp.types import CONNECTION_CLOSED

from .client import McpClient
from .types import ToolResult


_TIMEOUT_ERRORS = tuple(error for error in (TimeoutError, getattr(anyio, "TimeoutError", None)) if error is not None)
_TRANSIENT_ERRORS = (httpx.ConnectError, httpx.ReadError)
_RETRYABLE_ERRORS = _TIMEOUT_ERRORS + _TRANSIENT_ERRORS
_DISPATCH_ERRORS = _RETRYABLE_ERRORS + (MCPError,)


def _is_retryable(error: BaseException) -> bool:
    if isinstance(error, _RETRYABLE_ERRORS):
        return True
    return isinstance(error, MCPError) and error.error.code in (httpx.codes.REQUEST_TIMEOUT, CONNECTION_CLOSED)


async def dispatch(
    client: McpClient,
    name: str,
    args: dict,
    *,
    retries: int = 2,
    timeout: float = 30,
    idempotent: bool = False,
) -> ToolResult:
    """Call a tool, retrying transient failures only when explicitly safe.

    MCP request IDs do not make tool mutations idempotent. Callers must opt in
    only for tools whose contract is read-only or otherwise idempotent.
    """
    max_retries = max(0, retries) if idempotent else 0
    for attempt in range(max_retries + 1):
        try:
            with anyio.fail_after(timeout):
                return await client.call_tool(name, args)
        except _DISPATCH_ERRORS as error:
            if not _is_retryable(error):
                raise
            if attempt >= max_retries:
                raise
            await anyio.sleep(1)

    raise RuntimeError("dispatch exhausted retry loop unexpectedly")


class Dispatcher:
    """Class-shaped wrapper around ``dispatch`` for adapters that prefer
    instance-method ergonomics. ``Dispatcher().dispatch(client, ...)``
    is identical to calling the module-level ``dispatch`` directly.

    Adapters can hold a ``Dispatcher`` instance with custom retry/timeout
    defaults and reuse it across many tool calls without re-passing those
    knobs every time.
    """

    def __init__(self, *, retries: int = 2, timeout: float = 30, idempotent: bool = False) -> None:
        self._retries = retries
        self._timeout = timeout
        self._idempotent = idempotent

    async def dispatch(
        self,
        client: McpClient,
        name: str,
        args: dict,
        *,
        retries: int | None = None,
        timeout: float | None = None,
        idempotent: bool | None = None,
    ) -> ToolResult:
        return await dispatch(
            client,
            name,
            args,
            retries=self._retries if retries is None else retries,
            timeout=self._timeout if timeout is None else timeout,
            idempotent=self._idempotent if idempotent is None else idempotent,
        )
