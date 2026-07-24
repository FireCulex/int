"""Tests for int.tools — the five MCP tool definitions.

This module tests the tool *shape* and orchestration, not the store/embedder
internals (those are covered by test_store and test_embeddings). Store and
embedder are replaced with deterministic fakes so these tests run fast and
never touch Qdrant or Gemini.

Per spec (docs/spec.md):
- int.add(project, type, content) -> memory_id (UUID)
- int.delete(memory_id) -> deleted: bool
- int.search(project, query, limit=5) -> list[SearchResult]
- int.list(project) -> list[MemoryMetadata] (no content, no embedding call)
- int.recall(project, query, limit=5) -> list[SearchResult] (pass-through to
  search in v1)

Validation: each tool raises int.models.ValidationError for bad input
(empty project, empty content, malformed UUID-string, negative/zero limit).
Auth lives at the server boundary, not in tools.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

# --- Fakes injected into ToolsRegistry ---


class FakeStore:
    def __init__(self) -> None:
        self.memories: dict[uuid.UUID, tuple[str, str, str]] = {}
        self.add_calls: list[tuple[Any, list[float]]] = []
        self.delete_calls: list[uuid.UUID] = []
        self.search_calls: list[tuple[str, list[float], int]] = []

    def add(self, memory: Any, embedding: list[float]) -> uuid.UUID:
        self.add_calls.append((memory, list(embedding)))
        self.memories[memory.id] = (memory.project, memory.type, memory.content)
        return memory.id

    def delete(self, memory_id: uuid.UUID) -> bool:
        self.delete_calls.append(memory_id)
        return self.memories.pop(memory_id, None) is not None

    def search(
        self,
        project: str,
        *,
        query_vector: list[float],
        limit: int = 5,
    ) -> list[Any]:
        self.search_calls.append((project, list(query_vector), limit))
        # Return canned SearchResult-shaped objects for tests asserting shape.
        from int.models import SearchResult

        return [
            SearchResult(
                id=uuid.uuid4(), type="architecture", content="canned", score=0.9
            ),
            SearchResult(
                id=uuid.uuid4(), type="command", content="canned2", score=0.5
            ),
        ][:limit]

    def recall(
        self,
        project: str,
        *,
        query_vector: list[float],
        limit: int = 5,
    ) -> list[Any]:
        return self.search(project, query_vector=query_vector, limit=limit)

    def list(self, project: str) -> list[Any]:  # noqa: A003
        from datetime import UTC, datetime

        from int.models import MemoryMetadata

        return [
            MemoryMetadata(
                id=mid,
                type=t,
                created_at=datetime.now(UTC),
            )
            for mid, (p, t, c) in self.memories.items()  # noqa: B007
            if p == project
        ]


class FakeEmbedder:
    def __init__(self) -> None:
        self.doc_calls: list[str] = []
        self.query_calls: list[str] = []

    async def embed_document(self, content: str) -> list[float]:
        self.doc_calls.append(content)
        return [1.0] + [0.0] * 767  # bogus but unit-length for shape checks

    async def embed_query(self, content: str) -> list[float]:
        self.query_calls.append(content)
        return [1.0] + [0.0] * 767


def _make_tools() -> tuple[Any, FakeStore, FakeEmbedder]:
    from int.tools import ToolsRegistry

    store = FakeStore()
    embedder = FakeEmbedder()
    tools = ToolsRegistry(store=store, embedder=embedder)
    return tools, store, embedder


# --- Tool registry exposes the five names per the spec ---


def test_tools_registry_lists_the_five_tools() -> None:
    tools, _, _ = _make_tools()
    names = {t.name for t in tools.list_tools()}
    assert names == {"int.add", "int.delete", "int.search", "int.list", "int.recall"}


def test_each_tool_has_a_description() -> None:
    tools, _, _ = _make_tools()
    for t in tools.list_tools():
        assert t.description, f"{t.name} has no description"
        assert len(t.description) > 0


def test_tool_input_schemas_match_spec() -> None:
    """Each tool must declare exactly the spec'd input parameters."""
    tools, _, _ = _make_tools()
    schemas = {t.name: set(t.input_schema.get("required", [])) for t in tools.list_tools()}
    assert schemas["int.add"] == {"project", "type", "content"}
    assert schemas["int.delete"] == {"memory_id"}
    assert schemas["int.search"] == {"project", "query"}
    assert schemas["int.list"] == {"project"}
    assert schemas["int.recall"] == {"project", "query"}


# --- int.add ---


@pytest.mark.asyncio
async def test_add_calls_embedder_with_retrieval_document_and_stores_memory() -> None:
    from int.models import Memory

    tools, store, embedder = _make_tools()
    mid = await tools.call("int.add", {
        "project": "pianoweb",
        "type": "architecture",
        "content": "flask backend",
    })
    assert isinstance(mid, str)
    assert uuid.UUID(mid)
    assert embedder.doc_calls == ["flask backend"]
    assert embedder.query_calls == []  # add uses document, never query
    assert len(store.add_calls) == 1
    memory, vec = store.add_calls[0]
    assert isinstance(memory, Memory)
    assert memory.project == "pianoweb"
    assert memory.type == "architecture"
    assert memory.content == "flask backend"
    assert vec == [1.0] + [0.0] * 767


@pytest.mark.asyncio
async def test_add_empty_project_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("int.add", {"project": "", "type": "t", "content": "x"})


@pytest.mark.asyncio
async def test_add_empty_content_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("int.add", {"project": "p", "type": "t", "content": ""})


@pytest.mark.asyncio
async def test_add_empty_type_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("int.add", {"project": "p", "type": "", "content": "x"})


@pytest.mark.asyncio
async def test_add_missing_required_field_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("int.add", {"project": "p", "type": "t"})  # no content


# --- int.delete ---


@pytest.mark.asyncio
async def test_delete_existing_returns_true() -> None:
    from int.models import Memory

    tools, store, _ = _make_tools()
    m = Memory(project="p", type="t", content="x")
    store.memories[m.id] = (m.project, m.type, m.content)
    ok = await tools.call("int.delete", {"memory_id": str(m.id)})
    assert ok is True
    assert store.delete_calls == [m.id]
    assert m.id not in store.memories


@pytest.mark.asyncio
async def test_delete_missing_returns_false_idempotent() -> None:
    tools, _, _ = _make_tools()
    missing = str(uuid.uuid4())
    ok = await tools.call("int.delete", {"memory_id": missing})
    assert ok is False
    ok2 = await tools.call("int.delete", {"memory_id": missing})
    assert ok2 is False


@pytest.mark.asyncio
async def test_delete_malformed_uuid_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("int.delete", {"memory_id": "not-a-uuid"})


@pytest.mark.asyncio
async def test_delete_missing_memory_id_field_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("int.delete", {})  # type: ignore[call-arg]


# --- int.search ---


@pytest.mark.asyncio
async def test_search_returns_ranked_search_results() -> None:
    from int.models import SearchResult

    tools, _, embedder = _make_tools()
    results = await tools.call("int.search", {"project": "p", "query": "stack"})
    assert isinstance(results, list)
    assert len(results) >= 1
    assert all(isinstance(r, SearchResult) for r in results)
    assert embedder.query_calls == ["stack"]


@pytest.mark.asyncio
async def test_search_respects_limit_argument() -> None:
    tools, store, _ = _make_tools()
    await tools.call("int.search", {"project": "p", "query": "q", "limit": 1})
    assert store.search_calls[0][2] == 1
    await tools.call("int.search", {"project": "p", "query": "q", "limit": 3})
    assert store.search_calls[1][2] == 3


@pytest.mark.asyncio
async def test_search_default_limit_is_five() -> None:
    tools, store, _ = _make_tools()
    await tools.call("int.search", {"project": "p", "query": "q"})
    assert store.search_calls[0][2] == 5


@pytest.mark.asyncio
async def test_search_negative_limit_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("int.search", {"project": "p", "query": "q", "limit": -1})


@pytest.mark.asyncio
async def test_search_zero_limit_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("int.search", {"project": "p", "query": "q", "limit": 0})


@pytest.mark.asyncio
async def test_search_empty_project_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("int.search", {"project": "", "query": "q"})


@pytest.mark.asyncio
async def test_search_empty_query_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("int.search", {"project": "p", "query": ""})


@pytest.mark.asyncio
async def test_search_missing_query_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("int.search", {"project": "p"})  # type: ignore[call-arg]


# --- int.list ---


@pytest.mark.asyncio
async def test_list_returns_metadata_only_no_embedding_call() -> None:
    from int.models import MemoryMetadata

    tools, store, embedder = _make_tools()
    from int.models import Memory

    m = Memory(project="pianoweb", type="t", content="x")
    store.memories[m.id] = (m.project, m.type, m.content)
    metas = await tools.call("int.list", {"project": "pianoweb"})
    assert isinstance(metas, list)
    assert all(isinstance(m, MemoryMetadata) for m in metas)
    # CRITICAL: list must NOT call the embedder -- no embedding work happens.
    assert embedder.doc_calls == []
    assert embedder.query_calls == []


@pytest.mark.asyncio
async def test_list_empty_project_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("int.list", {"project": ""})


@pytest.mark.asyncio
async def test_list_missing_project_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("int.list", {})  # type: ignore[call-arg]


# --- int.recall ---


@pytest.mark.asyncio
async def test_recall_passes_through_to_search_in_v1() -> None:
    from int.models import SearchResult

    tools, _, _ = _make_tools()
    results = await tools.call("int.recall", {"project": "p", "query": "stack"})
    assert all(isinstance(r, SearchResult) for r in results)


@pytest.mark.asyncio
async def test_recall_default_limit_higher_than_search_if_spec_calls_for_it() -> None:
    """In v1 recall is a thin pass-through. Default limit is 5 (same as
    search); a higher default is reserved for a future summary+recall behavior.
    Tests the actual v1 contract."""
    tools, store, _ = _make_tools()
    await tools.call("int.recall", {"project": "p", "query": "q"})
    assert store.search_calls[0][2] == 5


@pytest.mark.asyncio
async def test_recall_empty_query_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("int.recall", {"project": "p", "query": ""})


# --- Unknown tool routing ---


@pytest.mark.asyncio
async def test_unknown_tool_raises_value_error() -> None:
    tools, _, _ = _make_tools()
    with pytest.raises(KeyError):
        await tools.call("int.does-not-exist", {})
