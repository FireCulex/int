"""int_cli.client — thin MCP-over-HTTP client wrapper for the int server.

The CLI talks to the int memory server using the same MCP Streamable HTTP
transport as any MCP client (including OpenCode). It performs the
`initialize` -> `notifications/initialized` handshake, then issues
`tools/call` requests for each of the five tools:

    add(project, type, content)          -> memory_id string
    delete(memory_id)                    -> "true" | "false"
    search(project, query, limit=5)      -> list[SearchResult dict]
    list(project)                        -> list[MemoryMetadata dict]
    recall(project, query, limit=5)       -> list[SearchResult dict]

This module owns the only place where the underlying MCP SDK is used by the
CLI, so command functions in `main.py` can stay declarative and the seam is
explicitly mockable for unit tests.

Configuration:
- `INT_SERVER_URL`: full URL of the MCP endpoint (default
  `http://localhost:8000/mcp`). Same value a human would pass to an MCP
  client config. Read here (not in `int.config`, which is server-side)
  because this is a client-side concern.
- `API_KEY`:         shared static secret; sent in the `API_KEY` header on
  every request, exactly like the MCP clients.

Errors:
- Missing `API_KEY` -> `CliConfigError` (fail fast before any network call).
- The server returns 401 -> `CliAuthError`.
- The tool returns an `isError=True` envelope -> `CliRemoteError` carrying
  the message; the CLI surfaces the raw message and exits non-zero.
- Connection failure -> `CliConnectionError`.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import AsyncIterator
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# --- Errors -----------------------------------------------------------------


class CliError(Exception):
    """Base for all int-cli errors; carries a message + an exit code."""

    exit_code: int = 1


class CliConfigError(CliError):
    """Local config problem (missing env var). Exit code 2."""

    exit_code = 2


class CliAuthError(CliError):
    """Server rejected the API_KEY. Exit code 3."""

    exit_code = 3


class CliConnectionError(CliError):
    """Could not reach the server. Exit code 4."""

    exit_code = 4


class CliRemoteError(CliError):
    """Tool returned an MCP isError=True envelope. Exit code 5."""

    exit_code = 5


# --- Config -----------------------------------------------------------------


DEFAULT_SERVER_URL = "http://localhost:8000/mcp"


def resolve_config(
    *,
    server_url: str | None = None,
    api_key: str | None = None,
) -> tuple[str, str]:
    """Resolve (server_url, api_key) from explicit args or env.

    Raises CliConfigError if the API_KEY is missing/empty.
    """
    url = server_url or os.environ.get("INT_SERVER_URL") or DEFAULT_SERVER_URL
    key = api_key or os.environ.get("API_KEY")
    if not key:
        raise CliConfigError(
            "API_KEY is required. Set it via the API_KEY environment variable or pass --api-key."
        )
    return url, key


# --- Session ----------------------------------------------------------------


async def _open_session(
    server_url: str, api_key: str
) -> tuple[ClientSession, contextlib.AsyncExitStack]:
    """Open & initialize an MCP session. Returns ``(session, close_stack)``.

    The caller is responsible for closing `close_stack` after use.
    """
    stack = contextlib.AsyncExitStack()
    try:
        read, write, _ = await stack.enter_async_context(
            streamablehttp_client(server_url, headers={"API_KEY": api_key})
        )
        sess = await stack.enter_async_context(ClientSession(read, write))
        init = await sess.initialize()
        if not init.protocolVersion:
            raise CliConnectionError("server returned no protocolVersion in initialize")
    except CliError:
        await stack.aclose()
        raise
    except Exception as e:
        await stack.aclose()
        # httpx raises ConnectError/ConnectTimeout; the SDK wraps auth as
        # McpError on 401. We small-shell match the message so downstream
        # callers get a typed category.
        msg = str(e).lower()
        if "401" in msg or "unauthorized" in msg:
            raise CliAuthError(f"server rejected API_KEY: {e}") from e
        if "connect" in msg or "timed out" in msg or "timeout" in msg:
            raise CliConnectionError(f"could not reach server at {server_url}: {e}") from e
        raise CliConnectionError(f"failed to open MCP session: {e}") from e
    return sess, stack


# Wrapping _open_session in an asynccontextmanager keeps the call site clean.


@contextlib.asynccontextmanager
async def session(
    *,
    server_url: str | None = None,
    api_key: str | None = None,
    _opener: Any = None,
) -> AsyncIterator[ClientSession]:
    """Async context manager yielding an initialized `ClientSession`.

    The optional `_opener` is for tests: pass a callable returning an async
    context manager that yields a (fake) session-like object, and no real
    network is touched. Production path uses the default `_open_session`.
    """
    url, key = resolve_config(server_url=server_url, api_key=api_key)
    if _opener is not None:
        async with _opener(url, key) as sess:
            yield sess
        return
    sess, stack = await _open_session(url, key)
    try:
        yield sess
    finally:
        await stack.aclose()


# --- Tool call helpers ------------------------------------------------------


def _parse_text(out: Any) -> str:
    """Return the first TextContent.text from a CallToolResult, or ''."""
    content = getattr(out, "content", None)
    if not content:
        return ""
    first = content[0]
    return str(getattr(first, "text", first))


def _require_ok(out: Any, tool: str) -> str:
    """Translate an isError=True envelope into CliRemoteError."""
    if getattr(out, "isError", False):
        raise CliRemoteError(f"int.{tool} failed: {_parse_text(out)}")
    return _parse_text(out)


async def call_add(
    session: Any,
    *,
    project: str,
    type_: str,
    content: str,
) -> str:
    """Issue `int.add`. Returns the new memory's UUID string."""
    out = await session.call_tool(
        "int.add", arguments={"project": project, "type": type_, "content": content}
    )
    return _require_ok(out, "add")


async def call_delete(session: Any, *, memory_id: str) -> bool:
    """Issue `int.delete`. Returns True iff a memory was removed.

    `int.delete` returns the strings "true" / "false" -- we parse to bool.
    """
    out = await session.call_tool("int.delete", arguments={"memory_id": memory_id})
    text = _require_ok(out, "delete")
    return text.strip().lower() == "true"


async def call_search(
    session: Any,
    *,
    project: str,
    query: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Issue `int.search`. Returns a list of parsed SearchResult dicts."""
    out = await session.call_tool(
        "int.search",
        arguments={"project": project, "query": query, "limit": limit},
    )
    text = _require_ok(out, "search")
    return _parse_items(text)


async def call_recall(
    session: Any,
    *,
    project: str,
    query: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Issue `int.recall` (v1 pass-through to `int.search`)."""
    out = await session.call_tool(
        "int.recall",
        arguments={"project": project, "query": query, "limit": limit},
    )
    text = _require_ok(out, "recall")
    return _parse_items(text)


async def call_list(
    session: Any,
    *,
    project: str,
) -> list[dict[str, Any]]:
    """Issue `int.list`. Returns a list of metadata dicts (id, type, created_at)."""
    out = await session.call_tool("int.list", arguments={"project": project})
    text = _require_ok(out, "list")
    return _parse_items(text)


def _parse_items(text: str) -> list[dict[str, Any]]:
    """Parse the JSON the server emits on list-returning tools.

    The server wrapper emits either a bare list or `{"items": [...]}`. Accept
    both shapes defensively (the canonical shape is `{"items": [...]}`; the
    bare list is tolerated for forward compatibility).
    """
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise CliRemoteError(f"could not parse server JSON: {e}; body={text!r}") from e
    if isinstance(payload, list):
        return list(payload)
    if isinstance(payload, dict) and "items" in payload:
        return list(payload["items"])
    raise CliRemoteError(f"unexpected server payload shape: {payload!r}")


__all__ = [
    "DEFAULT_SERVER_URL",
    "CliAuthError",
    "CliConfigError",
    "CliConnectionError",
    "CliError",
    "CliRemoteError",
    "call_add",
    "call_delete",
    "call_list",
    "call_recall",
    "call_search",
    "resolve_config",
    "session",
]
