"""Tests for int.server -- FastAPI + MCP wiring + auth + error envelope.

Confirms:
- the app exposes the MCP endpoint at /mcp
- each of the four tools is registered with the SDK (list_tools response)
- the API_KEY header is required; missing or wrong -> 401 (AuthError envelope)
- typed errors from tools (ValidationError / EmbeddingError / StoreError /
  AuthError) translate to MCP-shaped error responses with code + message,
  never bare 500s

Auth + transport specifics are exercised here; the actual Gemini/Qdrant
backends are faked so the test runs without network. E2E tests (Task 9) wire
the real SDK MCP client to a live-started server.

The MCP Streamable HTTP transport requires an initialize ->
notifications/initialized handshake before any tool call, and a
Mcp-Session-Id header on subsequent requests. We drive the FastAPI lifespan
(via asgi-lifespan.LifespanManager) so the MCP session manager's task group
is actually initialized -- httpx ASGITransport does not run lifespan.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

# --- Fakes injected into the server for unit-level coverage ---


class FakeStore:
    def __init__(self) -> None:
        self.memories: dict[Any, tuple[str, str, str]] = {}
        self.search_calls: list[tuple[str, list[float], int]] = []

    def ensure_collection(self) -> None:
        return None

    def add(self, memory: Any, embedding: list[float]) -> Any:
        self.memories[memory.id] = (memory.project, memory.type, memory.content)
        return memory.id

    def delete(self, memory_id: Any) -> bool:
        return self.memories.pop(memory_id, None) is not None

    def search(self, project: str, *, query_vector: list[float], limit: int = 5) -> list[Any]:
        from int.models import SearchResult

        self.search_calls.append((project, list(query_vector), limit))
        return [
            SearchResult(id=uuid4(), type="architecture", content="canned", score=0.9),
        ][:limit]

    def list(self, project: str) -> list[Any]:  # noqa: A003
        from datetime import UTC, datetime

        from int.models import MemoryMetadata

        return [
            MemoryMetadata(id=mid, type=t, created_at=datetime.now(UTC))
            for mid, (p, t, c) in self.memories.items()  # noqa: B007
            if p == project
        ]

    def project_names(self) -> list[str]:
        return sorted({p for p, _t, _c in self.memories.values()})


class FakeEmbedder:
    async def embed_document(self, content: str) -> list[float]:
        return [1.0] + [0.0] * 767

    async def embed_query(self, content: str) -> list[float]:
        return [1.0] + [0.0] * 767


def _build_app(
    *,
    api_key: str = "shared-secret",
    store: Any = None,
    embedder: Any = None,
) -> Any:
    """Construct the int.server app with injected fakes; bypass Settings env."""
    from int.server import build_app

    store = store or FakeStore()
    embedder = embedder or FakeEmbedder()
    return build_app(
        api_key=api_key,
        store=store,
        embedder=embedder,
        collection_name="int_memories",
        collection_dim=768,
        create_client=lambda: None,  # unused when store is pre-built
    )


# --- Fixtures ---


@pytest.fixture
def app() -> Any:
    return _build_app()


@pytest.fixture
async def lifespan_app(app: Any) -> AsyncIterator[Any]:
    """Drive the FastAPI lifespan so the MCP session manager starts."""
    async with LifespanManager(app):
        yield app


@pytest.fixture
async def client(lifespan_app: Any) -> AsyncIterator[AsyncClient]:
    """Async HTTP client wired to the app, with the API key set."""
    transport = ASGITransport(app=lifespan_app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={
            "API_KEY": "shared-secret",
            "Accept": "application/json, text/event-stream",
        },
    ) as c:
        yield c


# --- MCP helpers ---


def _parse_mcp_response(text: str) -> dict[str, Any]:
    """Parse an MCP Streamable HTTP response.

    The server may answer with either `application/json` (plain JSON object)
    or `text/event-stream` (Server-Sent Events with a single `data:` line
    containing the JSON-RPC envelope). We extract the JSON envelope in either
    case.
    """
    import json as _json

    text = text.strip()
    if not text:
        return {"_http_status": 202}
    # SSE format: lines starting with `event:` and `data:`
    if text.startswith("event:") or "data:" in text:
        data_lines = [
            ln[len("data:") :].strip() for ln in text.splitlines() if ln.startswith("data:")
        ]
        if data_lines:
            return _json.loads("".join(data_lines))
    return _json.loads(text)


async def _init_session(c: AsyncClient) -> str | None:
    """Do the MCP initialize/initialized handshake, return session id."""
    r = await c.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "0.1.0"},
            },
        },
    )
    assert r.status_code == 200, f"init failed: {r.status_code} {r.text}"
    sid = r.headers.get("Mcp-Session-Id")
    if sid:
        c.headers["Mcp-Session-Id"] = sid
    # notifications/initialized has no response body
    await c.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    return sid


async def _call(
    c: AsyncClient, *, id_: int, method: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        payload["params"] = params
    r = await c.post("/mcp/", json=payload)
    if r.status_code not in (200, 202):
        return {"_http_status": r.status_code, "_body": r.text}
    if not r.text:
        return {"_http_status": 202}
    return _parse_mcp_response(r.text)


# --- Auth ---


@pytest.mark.asyncio
async def test_missing_api_key_returns_401(app: Any) -> None:
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c,
    ):
        r = await c.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_wrong_api_key_returns_401(app: Any) -> None:
    async with (
        LifespanManager(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"API_KEY": "wrong"},
        ) as c,
    ):
        r = await c.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401


# --- Tools are registered with the MCP layer ---


@pytest.mark.asyncio
async def test_tools_exposed_at_mcp_endpoint_with_correct_names(
    client: AsyncClient,
) -> None:
    await _init_session(client)
    body = await _call(client, id_=1, method="tools/list")
    assert "result" in body, body
    names = {t["name"] for t in body["result"].get("tools", [])}
    assert names == {
        "add",
        "delete",
        "search",
        "list",
    }


@pytest.mark.asyncio
async def test_projects_resource_listed_at_mcp_endpoint(client: AsyncClient) -> None:
    """The read-only `int://projects` resource is advertised via resources/list.

    This is what makes int show up meaningfully in opencode's
    `list_mcp_resources` (previously int advertised the resources *capability*
    via FastMCP's auto-handlers but registered zero resources).
    """
    await _init_session(client)
    body = await _call(client, id_=1, method="resources/list")
    assert "result" in body, body
    resources = body["result"].get("resources", [])
    uris = {r.get("uri") for r in resources}
    assert "int://projects" in uris, resources
    projects = next(r for r in resources if r["uri"] == "int://projects")
    assert projects.get("mimeType") == "application/json"


@pytest.mark.asyncio
async def test_projects_resource_read_returns_project_names(client: AsyncClient) -> None:
    """resources/read on int://projects returns sorted project names as JSON.

    Uses a store pre-seeded with memories across two projects to confirm the
    resource handler reads live store state (metadata only, no content).
    """
    store = FakeStore()
    from int.models import Memory

    store.add(Memory(project="beta", type="t", content="b1"), [1.0])
    store.add(Memory(project="alpha", type="t", content="a1"), [1.0])
    store.add(Memory(project="beta", type="t", content="b2"), [1.0])
    app = _build_app(store=store)
    async with (
        LifespanManager(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={
                "API_KEY": "shared-secret",
                "Accept": "application/json, text/event-stream",
            },
        ) as c,
    ):
        await _init_session(c)
        body = await _call(
            c,
            id_=1,
            method="resources/read",
            params={"uri": "int://projects"},
        )
    assert "result" in body, body
    contents = body["result"].get("contents", [])
    assert len(contents) == 1
    assert contents[0]["mimeType"] == "application/json"
    text = contents[0]["text"]
    import json as _json

    assert _json.loads(text) == {"projects": ["alpha", "beta"]}


@pytest.mark.asyncio
async def test_search_tool_advertises_integer_limit_with_default_five(
    client: AsyncClient,
) -> None:
    """Regression for the `annotation=str` bug in _build_mcp_fast.

    FastMCP derives its input schema from the synthesized function's parameter
    annotations. When every parameter was force-annotated `str`, FastMCP
    advertised `limit` as `{"type": "string"}` and rejected integer input
    with a Pydantic `string_type` error before our handler ran. The
    descriptor already declares `{"type": "integer", "default": 5}`; the MCP
    schema must reflect that so integer-typed clients (and OpenCode) can
    pass `limit: 3` without a transport-level validation failure.
    """
    await _init_session(client)
    body = await _call(client, id_=1, method="tools/list")
    assert "result" in body, body
    tools = body["result"].get("tools", [])
    search = next((t for t in tools if t["name"] == "search"), None)
    assert search is not None, "search tool not registered"
    props = search["inputSchema"].get("properties", {})
    limit_schema = props.get("limit", {})
    assert limit_schema.get("type") == "integer", limit_schema
    assert limit_schema.get("default") == 5, limit_schema


@pytest.mark.asyncio
async def test_search_tool_integer_limit_reaches_store(
    client: AsyncClient,
    app: Any,
) -> None:
    """End-to-MCP-store check: an integer `limit` argument flows through
    FastMCP's Pydantic layer and our registry, reaching the store at the
    original integer value. Before the fix this raised a Pydantic
    `string_type` validation error before the registry saw anything.
    """
    await _init_session(client)
    body = await _call(
        client,
        id_=1,
        method="tools/call",
        params={
            "name": "search",
            "arguments": {"project": "p", "query": "q", "limit": 3},
        },
    )
    # No transport-level validation error -> we got an MCP result envelope.
    assert "error" not in body, body
    assert "result" in body, body
    assert body["result"].get("isError") is False, body
    store = app.state.store
    assert store.search_calls, "search did not reach the store"
    _, _, limit_seen = store.search_calls[-1]
    assert limit_seen == 3
    assert isinstance(limit_seen, int)


# --- add via HTTP/MCP ---


@pytest.mark.asyncio
async def test_add_tool_invocation_returns_memory_id(
    client: AsyncClient,
) -> None:
    await _init_session(client)
    body = await _call(
        client,
        id_=7,
        method="tools/call",
        params={
            "name": "add",
            "arguments": {
                "project": "pianoweb",
                "type": "architecture",
                "content": "flask backend",
            },
        },
    )
    assert "result" in body, body
    result = body["result"]
    assert result.get("isError") is False
    text = result["content"][0]["text"]
    UUID(text)  # raises if not a uuid


@pytest.mark.asyncio
async def test_add_tool_with_empty_project_returns_typed_error(
    client: AsyncClient,
) -> None:
    await _init_session(client)
    body = await _call(
        client,
        id_=1,
        method="tools/call",
        params={
            "name": "add",
            "arguments": {"project": "", "type": "t", "content": "x"},
        },
    )
    assert "_http_status" not in body, body
    result = body.get("result", {})
    assert result.get("isError") is True, body
    msg = result["content"][0]["text"]
    assert "project" in msg.lower()


@pytest.mark.asyncio
async def test_add_tool_with_missing_content_returns_typed_error(
    client: AsyncClient,
) -> None:
    await _init_session(client)
    body = await _call(
        client,
        id_=2,
        method="tools/call",
        params={
            "name": "add",
            "arguments": {"project": "p", "type": "t"},  # missing content
        },
    )
    result = body["result"]
    assert result["isError"] is True, body
    assert "content" in result["content"][0]["text"].lower()


# --- delete via HTTP/MCP ---


@pytest.mark.asyncio
async def test_delete_missing_memory_returns_false(
    client: AsyncClient,
) -> None:
    await _init_session(client)
    body = await _call(
        client,
        id_=1,
        method="tools/call",
        params={
            "name": "delete",
            "arguments": {"memory_id": str(uuid4())},
        },
    )
    assert body["result"]["content"][0]["text"] == "false"


@pytest.mark.asyncio
async def test_delete_malformed_uuid_returns_typed_error(
    client: AsyncClient,
) -> None:
    await _init_session(client)
    body = await _call(
        client,
        id_=1,
        method="tools/call",
        params={
            "name": "delete",
            "arguments": {"memory_id": "not-a-uuid"},
        },
    )
    assert body["result"]["isError"] is True, body
    assert "uuid" in body["result"]["content"][0]["text"].lower()


# --- list ---


@pytest.mark.asyncio
async def test_list_tool_returns_metadata(client: AsyncClient) -> None:
    await _init_session(client)
    body = await _call(
        client,
        id_=1,
        method="tools/call",
        params={"name": "list", "arguments": {"project": "p"}},
    )
    assert body["result"]["isError"] is False, body


# --- Typed error envelope: never a bare 500 ---


@pytest.mark.asyncio
async def test_unknown_tool_returns_mcp_error_not_500(
    client: AsyncClient,
) -> None:
    await _init_session(client)
    body = await _call(
        client,
        id_=1,
        method="tools/call",
        params={"name": "does-not-exist", "arguments": {}},
    )
    # Either an error envelope (error:{}) or an isError result -- never 500.
    assert body.get("_http_status", 200) != 500, body
    assert "error" in body or body.get("result", {}).get("isError") is True, body


@pytest.mark.asyncio
async def test_unknown_method_returns_mcp_error_not_500(
    client: AsyncClient,
) -> None:
    await _init_session(client)
    body = await _call(client, id_=1, method="nonexistent/method")
    assert body.get("_http_status", 200) != 500, body


# --- No raw memory content is logged at INFO ---


def test_app_state_mask_content_defaults_true(app: Any) -> None:
    """The app must never log raw memory content at INFO. We confirm the
    app-level state sets the masking flag so handlers can later gate on it."""
    assert hasattr(app, "state")
    assert app.state.mask_content is True
