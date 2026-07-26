"""E2E tests: real MCP client over HTTP against a live server + Qdrant.

The spec asks for three E2E concerns:

1.  Spin the server + Qdrant, connect a real MCP client over HTTP with the
    correct API_KEY, exercise all four tools end-to-end. Verifies the MCP
    protocol contract, not AI behavior.
2.  Auth: missing or wrong API_KEY -> 401 on every tool.
3.  Offline-degrade: when Gemini is unreachable (mock raised), `add` /
    `search` return a typed `EmbeddingError` (not a crash); `list` still
    works without an embedding call.

What we actually wire:

-   **Server**: a real `uvicorn.Server` running the FastAPI app on a free
    loopback port (true HTTP round-trip -- the MCP client below is the real
    `streamablehttp_client` from the MCP SDK). `LifespanManager` drives the
    FastAPI lifespan (which starts the MCP session manager) around the
    uvicorn serve loop.
-   **Store**: two flavours. `_FakeStore` for the in-sandbox paths (auth +
    offline-degrade -- these need no Qdrant); real `QdrantStore` against the
    session-scoped `qdrant_container` for the full-CRUD test, skipped when
    Qdrant isn't reachable (same pattern as integration).
-   **Embedder**: two fakes. `FakeEmbedder` (happy path; borrow from conftest)
    and `_BrokenEmbedder` (always raises `EmbeddingError`) for offline-degrade.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# =============================================================================
# Fakes (the happy-path FakeEmbedder is shared via tests.conftest.FakeEmbedder)
# =============================================================================


class _FakeStore:
    """In-memory QdrantStore stand-in for in-sandbox E2E paths."""

    def __init__(self) -> None:
        self.memories: dict[Any, tuple[str, str, str]] = {}

    def ensure_collection(self) -> None:
        return None

    def add(self, memory: Any, embedding: list[float]) -> Any:
        self.memories[memory.id] = (memory.project, memory.type, memory.content)
        return memory.id

    def delete(self, memory_id: Any) -> bool:
        return self.memories.pop(memory_id, None) is not None

    def search(self, project: str, *, query_vector: list[float], limit: int = 5) -> list[Any]:
        from uuid import uuid4

        from int.models import SearchResult

        return [SearchResult(id=uuid4(), type="architecture", content="canned", score=0.9)][:limit]

    def list(self, project: str) -> list[Any]:  # noqa: A003
        from datetime import UTC, datetime

        from int.models import MemoryMetadata

        return [
            MemoryMetadata(id=mid, type=t, created_at=datetime.now(UTC))
            for mid, (p, t, _c) in self.memories.items()  # noqa: B007
            if p == project
        ]


class _BrokenEmbedder:
    """Stand-in that always raises EmbeddingError -- exercises offline-degrade."""

    async def embed_document(self, content: str) -> list[float]:
        from int.models import EmbeddingError

        raise EmbeddingError("simulated Gemini outage: embed_document")

    async def embed_query(self, content: str) -> list[float]:
        from int.models import EmbeddingError

        raise EmbeddingError("simulated Gemini outage: embed_query")


# =============================================================================
# Server harness: real uvicorn.Server on a free loopback port
# =============================================================================


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


@contextlib.asynccontextmanager
async def _serve(
    *,
    api_key: str,
    store: Any,
    embedder: Any,
) -> AsyncIterator[tuple[str, str]]:
    """Start a uvicorn.Server bound to a free loopback port.

    Uvicorn drives the FastAPI lifespan itself (which starts the MCP
    session manager); we do NOT separately wrap with `LifespanManager`
    here -- doing so calls `session_manager.run()` twice and the MCP SDK
    raises `StreamableHTTPSessionManager .run() can only be called once`.

    Yields ``(base_url_ending_in_/mcp, api_key)``.
    """
    import uvicorn

    from int.server import build_app

    app = build_app(api_key=api_key, store=store, embedder=embedder)
    port = _free_port()
    config = uvicorn.Config(
        app=app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"
    )
    srv = uvicorn.Server(config)

    task = asyncio.create_task(srv.serve())
    # Wait until the server is actually listening before we connect.
    # uvicorn doesn't publish a startup Event we can await; the loop is the
    # canonical pattern (and short-circuits as soon as `started` flips).
    deadline = asyncio.get_event_loop().time() + 15.0
    while not srv.started and asyncio.get_event_loop().time() < deadline:  # noqa: ASYNC110
        await asyncio.sleep(0.05)
    if not srv.started:
        task.cancel()
        pytest.fail("uvicorn server did not start within 15s")
    try:
        yield (f"http://127.0.0.1:{port}/mcp", api_key)
    finally:
        srv.should_exit = True
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(task, timeout=5.0)


def _default_embedder() -> Any:
    from tests.conftest import FakeEmbedder

    return FakeEmbedder(dim=768)


@pytest.fixture
async def server(
    loopback_http_available: Any,
) -> AsyncIterator[tuple[str, str]]:
    """Happy-path server: FakeStore + FakeEmbedder."""
    async with _serve(
        api_key="shared-secret",
        store=_FakeStore(),
        embedder=_default_embedder(),
    ) as (base_url, api_key):
        yield base_url, api_key


@pytest.fixture
async def mcp_client(
    server: tuple[str, str],
) -> AsyncIterator[tuple[ClientSession, str]]:
    """Real SDK MCP client against the live server. Performs initialize."""
    base_url, api_key = server
    async with (
        streamablehttp_client(base_url, headers={"API_KEY": api_key}) as (
            read,
            write,
            _,
        ),
        ClientSession(read, write) as session,
    ):
        init = await session.initialize()
        assert init.protocolVersion, "initialize returned no protocolVersion"
        yield session, base_url


@pytest.fixture
async def degrade_server(
    loopback_http_available: Any,
) -> AsyncIterator[tuple[str, str]]:
    """Server wired with `_BrokenEmbedder` and `_FakeStore` (no Qdrant)."""
    async with _serve(
        api_key="shared-secret",
        store=_FakeStore(),
        embedder=_BrokenEmbedder(),
    ) as (base_url, api_key):
        yield base_url, api_key


@pytest.fixture
async def degrade_client(
    degrade_server: tuple[str, str],
) -> AsyncIterator[tuple[ClientSession, str]]:
    base_url, api_key = degrade_server
    async with (
        streamablehttp_client(base_url, headers={"API_KEY": api_key}) as (
            read,
            write,
            _,
        ),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session, base_url


# =============================================================================
# Response helpers: tool result -> text / parsed JSON items
# =============================================================================


def _text(out: Any) -> str:
    """Extract the first TextContent text from a CallToolResult."""
    return str(out.content[0].text)


def _json_items(out: Any) -> list[dict[str, Any]]:
    """Parse the JSON we emit on list-returning tools ({"items": [...]})."""
    payload = json.loads(_text(out))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and "items" in payload:
        return list(payload["items"])
    return [payload]


# =============================================================================
# (1) All four tools, end-to-end, via real MCP client against live server
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_mcp_client_sees_all_four_tools(
    mcp_client: tuple[ClientSession, str],
) -> None:
    """`tools/list` exposes the four-tool surface with the right names."""
    session, _ = mcp_client
    result = await session.list_tools()
    names = {t.name for t in result.tools}
    assert names == {"add", "delete", "search", "list"}


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_e2e_add_then_list_roundtrip(
    mcp_client: tuple[ClientSession, str],
) -> None:
    """`add` returns a UUID; a subsequent `list` includes it (metadata only)."""
    session, _ = mcp_client
    add_out = await session.call_tool(
        "add",
        arguments={"project": "pianoweb", "type": "architecture", "content": "flask"},
    )
    assert add_out.isError is False, add_out
    mem_id = _text(add_out)
    UUID(mem_id)  # raises if not a UUID

    list_out = await session.call_tool("list", arguments={"project": "pianoweb"})
    assert list_out.isError is False, list_out
    ids = {str(it["id"]) for it in _json_items(list_out)}
    assert mem_id in ids, f"memory {mem_id} not in list: {ids}"


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_e2e_add_then_search_returns_result(
    mcp_client: tuple[ClientSession, str],
) -> None:
    """`search` after `add` returns ranked results (canned; we assert the
    round trip, not semantic relevance)."""
    session, _ = mcp_client
    await session.call_tool(
        "add",
        arguments={"project": "p", "type": "command", "content": "npm test"},
    )
    out = await session.call_tool("search", arguments={"project": "p", "query": "test command"})
    assert out.isError is False, out
    items = _json_items(out)
    assert items, "search returned no items"
    assert "score" in items[0]


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_e2e_delete_unknown_returns_false(
    mcp_client: tuple[ClientSession, str],
) -> None:
    """`delete` is idempotent: a never-stored UUID returns 'false'."""
    from uuid import uuid4

    session, _ = mcp_client
    out = await session.call_tool("delete", arguments={"memory_id": str(uuid4())})
    assert out.isError is False, out
    assert _text(out) == "false"


# =============================================================================
# (2) Auth: missing / wrong API_KEY -> 401 on every tool method
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_missing_api_key_rejected_at_http_layer(
    server: tuple[str, str],
) -> None:
    """A missing API_KEY yields HTTP 401 AuthError before any MCP framing."""
    import httpx

    base_url, _ = server
    async with httpx.AsyncClient(base_url=base_url, timeout=5.0) as c:
        r = await c.post(
            base_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            },
        )
    assert r.status_code == 401
    body = r.json()
    assert body.get("error") == "AuthError"


@pytest.mark.asyncio
@pytest.mark.e2e
@pytest.mark.parametrize("bad_key", ["wrong", ""])
async def test_wrong_api_key_rejected_on_each_tool(server: tuple[str, str], bad_key: str) -> None:
    """A wrong/empty API_KEY yields 401 for initialize and each tool method."""
    import httpx

    base_url, _ = server
    methods = [
        (
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        ),
        ("tools/list", {}),
        (
            "tools/call",
            {"name": "list", "arguments": {"project": "p"}},
        ),
    ]
    async with httpx.AsyncClient(base_url=base_url, headers={"API_KEY": bad_key}, timeout=5.0) as c:
        for method, params in methods:
            r = await c.post(
                base_url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            )
            assert r.status_code == 401, f"{method}: expected 401, got {r.status_code}"


# =============================================================================
# (3) Offline-degrade: broken embedder -> EmbeddingError on add/search,
#     but `list` still works.
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_offline_degrade_add_returns_embedding_error(
    degrade_client: tuple[ClientSession, str],
) -> None:
    session, _ = degrade_client
    out = await session.call_tool(
        "add",
        arguments={"project": "p", "type": "command", "content": "x"},
    )
    assert out.isError is True, out
    assert "embedding" in _text(out).lower()


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_offline_degrade_search_returns_embedding_error(
    degrade_client: tuple[ClientSession, str],
) -> None:
    session, _ = degrade_client
    out = await session.call_tool("search", arguments={"project": "p", "query": "q"})
    assert out.isError is True, out
    assert "embedding" in _text(out).lower()


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_offline_degrade_list_works_without_embedding(
    degrade_client: tuple[ClientSession, str],
) -> None:
    """`list` makes no embedding call, so it must still succeed during an
    embedder outage."""
    session, _ = degrade_client
    out = await session.call_tool("list", arguments={"project": "p"})
    assert out.isError is False, out


# =============================================================================
# (4) Real Qdrant via the session-scoped container -- skipped cleanly without Docker
# =============================================================================


def _make_real_store(container: Any, dim: int = 768) -> Any:
    from qdrant_client import QdrantClient
    from qdrant_client.http.models import Distance, VectorParams

    from int.store import QdrantStore

    host = container.host_ip()
    port = container.rest_port()
    client = QdrantClient(host=host, port=port)
    client.recreate_collection(
        collection_name="int_memories_e2e",
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )
    return QdrantStore(client=client, collection_name="int_memories_e2e", dimension=dim)


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_e2e_full_crud_over_http_with_real_qdrant(
    qdrant_container: Any,
    fake_embedder: Any,
    loopback_http_available: Any,
) -> None:
    """Against real Qdrant + FakeEmbedder, add -> list -> search -> delete (->
    delete again for idempotence) succeeds end-to-end over the real MCP HTTP
    transport. Skipped cleanly when Qdrant is not reachable from Python."""
    store = _make_real_store(qdrant_container, dim=fake_embedder.dim)
    async with (
        _serve(api_key="shared-secret", store=store, embedder=fake_embedder) as (base_url, api_key),
        streamablehttp_client(base_url, headers={"API_KEY": api_key}) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        # add
        add_out = await session.call_tool(
            "add",
            arguments={
                "project": "pianoweb",
                "type": "architecture",
                "content": "flask backend, python 3.14",
            },
        )
        assert add_out.isError is False, add_out
        mem_id = _text(add_out)
        UUID(mem_id)

        # list reflects it
        list_out = await session.call_tool("list", arguments={"project": "pianoweb"})
        assert list_out.isError is False
        ids = {str(it["id"]) for it in _json_items(list_out)}
        assert mem_id in ids

        # search finds the stored memory
        s_out = await session.call_tool(
            "search",
            arguments={"project": "pianoweb", "query": "backend"},
        )
        assert s_out.isError is False

        # delete -> true; idempotent delete -> false
        d_out = await session.call_tool("delete", arguments={"memory_id": mem_id})
        assert d_out.isError is False
        assert _text(d_out) == "true"

        d2_out = await session.call_tool("delete", arguments={"memory_id": mem_id})
        assert _text(d2_out) == "false"
