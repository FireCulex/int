"""Tests for int.tools — the four MCP tool definitions.

This module tests the tool *shape* and orchestration, not the store/embedder
internals (those are covered by test_store and test_embeddings). Store and
embedder are replaced with deterministic fakes so these tests run fast and
never touch Qdrant or Gemini.

Per spec (docs/spec.md):
- add(project, type, content) -> memory_id (UUID)
- delete(memory_id) -> deleted: bool
- search(project, query, limit=5) -> list[SearchResult]
- list(project) -> list[MemoryMetadata] (no content, no embedding call)

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
    def __init__(self, *, known_projects: list[str] | None = None) -> None:
        self.memories: dict[uuid.UUID, tuple[str, str, str, str | None]] = {}
        self.add_calls: list[tuple[Any, list[float]]] = []
        self.delete_calls: list[uuid.UUID] = []
        self.search_calls: list[tuple[str, list[float], int, str | None]] = []
        self.created: dict[uuid.UUID, Any] = {}
        # Projects that already exist (have at least one memory). The
        # existence guard derives from project_names(); pre-seed the names
        # the tests reference so the guard passes for them.
        self._known_projects: set[str] = set(known_projects or [])

    def add(self, memory: Any, embedding: list[float]) -> uuid.UUID:
        self.add_calls.append((memory, list(embedding)))
        self.memories[memory.id] = (memory.project, memory.type, memory.content, memory.summary)
        self.created[memory.id] = memory.created_at
        self._known_projects.add(memory.project)
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
        query_text: str | None = None,
    ) -> list[Any]:
        self.search_calls.append((project, list(query_vector), limit, query_text))
        # Return canned SearchResult-shaped objects for tests asserting shape.
        from datetime import UTC, datetime

        from int.models import SearchResult

        return [
            SearchResult(
                id=uuid.uuid4(),
                type="architecture",
                content="canned",
                score=0.9,
                created_at=datetime.now(UTC),
            ),
            SearchResult(
                id=uuid.uuid4(),
                type="command",
                content="canned2",
                score=0.5,
                created_at=datetime.now(UTC),
            ),
        ][:limit]

    def list(self, project: str) -> list[Any]:  # noqa: A003
        from datetime import UTC, datetime

        from int.models import MemoryMetadata

        return [
            MemoryMetadata(
                id=mid,
                type=t,
                summary=s,
                created_at=self.created.get(mid, datetime.now(UTC)),
            )
            for mid, (p, t, c, s) in self.memories.items()  # noqa: B007
            if p == project
        ]

    def get_all(self, project: str) -> list[Any]:
        from int.models import Memory

        return [
            Memory(
                id=mid,
                project=p,
                type=t,
                content=c,
                summary=s,
                created_at=self.created[mid],
            )
            for mid, (p, t, c, s) in self.memories.items()  # noqa: B007
            if p == project and mid in self.created
        ]

    def get(self, memory_id: uuid.UUID) -> Any | None:
        if memory_id not in self.memories:
            return None
        p, t, c, s = self.memories[memory_id]
        from datetime import UTC, datetime

        from int.models import Memory

        return Memory(
            id=memory_id, project=p, type=t, content=c, summary=s, created_at=datetime.now(UTC)
        )

    def project_names(self) -> list[str]:
        names = self._known_projects | {p for (p, _, _, _) in self.memories.values()}
        return sorted(names)


class FakeEmbedder:
    def __init__(self) -> None:
        self.doc_calls: list[str] = []
        self.query_calls: list[str] = []

    @property
    def dimension(self) -> int:
        return 768

    async def embed_document(self, content: str) -> list[float]:
        self.doc_calls.append(content)
        return [1.0] + [0.0] * 767  # bogus but unit-length for shape checks

    async def embed_query(self, content: str) -> list[float]:
        self.query_calls.append(content)
        return [1.0] + [0.0] * 767


def _make_tools(*, known_projects: list[str] | None = None) -> tuple[Any, FakeStore, FakeEmbedder]:
    from int.tools import ToolsRegistry

    if known_projects is None:
        known_projects = ["p", "pianoweb"]
    store = FakeStore(known_projects=known_projects)
    embedder = FakeEmbedder()
    tools = ToolsRegistry(store=store, embedder=embedder)
    return tools, store, embedder


# --- Tool registry exposes the four names per the spec ---


def test_tools_registry_lists_the_four_tools() -> None:
    tools, _, _ = _make_tools()
    names = {t.name for t in tools.list_tools()}
    assert names == {"add", "delete", "search", "list", "get"}


def test_each_tool_has_a_description() -> None:
    tools, _, _ = _make_tools()
    for t in tools.list_tools():
        assert t.description, f"{t.name} has no description"
        assert len(t.description) > 0


def test_tool_input_schemas_match_spec() -> None:
    """Each tool must declare exactly the spec'd input parameters."""
    tools, _, _ = _make_tools()
    schemas = {t.name: set(t.input_schema.get("required", [])) for t in tools.list_tools()}
    assert schemas["add"] == {"project", "type", "content"}
    assert schemas["delete"] == {"memory_id"}
    assert schemas["search"] == {"project", "query"}
    assert schemas["list"] == {"project"}


# --- add ---


@pytest.mark.asyncio
async def test_add_calls_embedder_with_retrieval_document_and_stores_memory() -> None:
    from int.models import Memory

    tools, store, embedder = _make_tools()
    mid = await tools.call(
        "add",
        {
            "project": "pianoweb",
            "type": "architecture",
            "content": "flask backend",
        },
    )
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
    assert memory.summary is None
    assert vec == [1.0] + [0.0] * 767


@pytest.mark.asyncio
async def test_add_with_summary_stores_summary() -> None:
    tools, store, embedder = _make_tools()
    mid = await tools.call(
        "add",
        {
            "project": "pianoweb",
            "type": "architecture",
            "content": "flask backend with SQLAlchemy",
            "summary": "Flask backend using SQLAlchemy ORM",
        },
    )
    assert isinstance(mid, str)
    assert uuid.UUID(mid)
    assert len(store.add_calls) == 1
    memory, _ = store.add_calls[0]
    assert memory.summary == "Flask backend using SQLAlchemy ORM"


@pytest.mark.asyncio
async def test_add_empty_project_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("add", {"project": "", "type": "t", "content": "x"})


@pytest.mark.asyncio
async def test_add_empty_content_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("add", {"project": "p", "type": "t", "content": ""})


@pytest.mark.asyncio
async def test_add_empty_type_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("add", {"project": "p", "type": "", "content": "x"})


@pytest.mark.asyncio
async def test_add_missing_required_field_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("add", {"project": "p", "type": "t"})  # no content


# --- delete ---


@pytest.mark.asyncio
async def test_delete_existing_returns_true() -> None:
    from int.models import Memory

    tools, store, _ = _make_tools()
    m = Memory(project="p", type="t", content="x")
    store.memories[m.id] = (m.project, m.type, m.content, m.summary)
    ok = await tools.call("delete", {"memory_id": str(m.id)})
    assert ok is True
    assert store.delete_calls == [m.id]
    assert m.id not in store.memories


@pytest.mark.asyncio
async def test_delete_missing_returns_false_idempotent() -> None:
    tools, _, _ = _make_tools()
    missing = str(uuid.uuid4())
    ok = await tools.call("delete", {"memory_id": missing})
    assert ok is False
    ok2 = await tools.call("delete", {"memory_id": missing})
    assert ok2 is False


@pytest.mark.asyncio
async def test_delete_malformed_uuid_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("delete", {"memory_id": "not-a-uuid"})


@pytest.mark.asyncio
async def test_delete_missing_memory_id_field_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("delete", {})  # type: ignore[call-arg]


# --- search ---


@pytest.mark.asyncio
async def test_search_returns_ranked_search_results() -> None:
    from int.models import SearchResult

    tools, _, embedder = _make_tools()
    results = await tools.call("search", {"project": "p", "query": "stack"})
    assert isinstance(results, list)
    assert len(results) >= 1
    assert all(isinstance(r, SearchResult) for r in results)
    assert embedder.query_calls == ["stack"]


@pytest.mark.asyncio
async def test_search_respects_limit_argument() -> None:
    tools, store, _ = _make_tools()
    await tools.call("search", {"project": "p", "query": "q", "limit": 1})
    assert store.search_calls[0][2] == 1
    await tools.call("search", {"project": "p", "query": "q", "limit": 3})
    assert store.search_calls[1][2] == 3


@pytest.mark.asyncio
async def test_search_default_limit_is_five() -> None:
    tools, store, _ = _make_tools()
    await tools.call("search", {"project": "p", "query": "q"})
    assert store.search_calls[0][2] == 5


@pytest.mark.asyncio
async def test_search_negative_limit_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("search", {"project": "p", "query": "q", "limit": -1})


@pytest.mark.asyncio
async def test_search_zero_limit_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("search", {"project": "p", "query": "q", "limit": 0})


@pytest.mark.asyncio
async def test_search_empty_project_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("search", {"project": "", "query": "q"})


@pytest.mark.asyncio
async def test_search_empty_query_returns_all_memories() -> None:
    """Empty query should return all memories (export use case)."""
    from uuid import uuid4

    from int.models import Memory, SearchResult

    tools, store, _ = _make_tools()

    # Add some memories first
    store.add(Memory(project="p", type="type1", content="content 1", id=uuid4()), [1.0] * 768)
    store.add(Memory(project="p", type="type2", content="content 2", id=uuid4()), [1.0] * 768)

    results = await tools.call("search", {"project": "p", "query": "", "limit": 10})

    assert len(results) == 2
    assert all(isinstance(r, SearchResult) for r in results)
    assert all(r.content for r in results)


@pytest.mark.asyncio
async def test_search_missing_query_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("search", {"project": "p"})  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_search_passes_query_text_to_store() -> None:
    """The raw query reaches the store so hybrid lexical scoring can use it."""
    tools, store, _ = _make_tools()
    await tools.call("search", {"project": "p", "query": "anchor scorer"})
    assert store.search_calls[0][3] == "anchor scorer"


# --- list ---


@pytest.mark.asyncio
async def test_list_returns_metadata_only_no_embedding_call() -> None:
    from int.models import MemoryMetadata

    tools, store, embedder = _make_tools()
    from int.models import Memory

    m = Memory(project="pianoweb", type="t", content="x")
    store.memories[m.id] = (m.project, m.type, m.content, m.summary)
    metas = await tools.call("list", {"project": "pianoweb"})
    assert isinstance(metas, list)
    assert all(isinstance(m, MemoryMetadata) for m in metas)
    # CRITICAL: list must NOT call the embedder -- no embedding work happens.
    assert embedder.doc_calls == []
    assert embedder.query_calls == []


@pytest.mark.asyncio
async def test_list_returns_summary_in_metadata() -> None:
    from int.models import Memory, MemoryMetadata

    tools, store, _ = _make_tools()

    m1 = Memory(project="pianoweb", type="t", content="x", summary="first summary")
    m2 = Memory(project="pianoweb", type="t", content="y")  # no summary
    store.memories[m1.id] = (m1.project, m1.type, m1.content, m1.summary)
    store.memories[m2.id] = (m2.project, m2.type, m2.content, m2.summary)

    metas = await tools.call("list", {"project": "pianoweb"})

    assert len(metas) == 2
    for meta in metas:
        assert isinstance(meta, MemoryMetadata)
        if meta.id == m1.id:
            assert meta.summary == "first summary"
        else:
            assert meta.summary is None


@pytest.mark.asyncio
async def test_list_empty_project_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("list", {"project": ""})


@pytest.mark.asyncio
async def test_list_missing_project_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()
    with pytest.raises(ValidationError):
        await tools.call("list", {})  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_list_orders_newest_first() -> None:
    """list is the recency-fallback tool: results must be newest-first, not
    raw store order. Raw Qdrant scroll order is id-based, so the sort happens
    at the tools layer."""
    from datetime import UTC, datetime

    from int.models import Memory

    tools, store, _ = _make_tools()
    older = Memory(
        project="p", type="t", content="old", created_at=datetime(2026, 9, 1, tzinfo=UTC)
    )
    newer = Memory(
        project="p", type="t", content="new", created_at=datetime(2026, 9, 22, tzinfo=UTC)
    )
    store.add(older, [1.0] * 768)
    store.add(newer, [1.0] * 768)

    metas = await tools.call("list", {"project": "p"})

    assert [m.id for m in metas] == [newer.id, older.id]


# --- get ---


@pytest.mark.asyncio
async def test_get_returns_memory_with_content() -> None:
    from int.models import Memory

    tools, store, _ = _make_tools()

    m = Memory(project="pianoweb", type="t", content="x")
    store.memories[m.id] = (m.project, m.type, m.content, m.summary)

    result = await tools.call("get", {"memory_id": str(m.id)})

    assert isinstance(result, Memory)
    assert result.id == m.id
    assert result.content == "x"
    assert result.type == "t"
    assert result.summary is None


@pytest.mark.asyncio
async def test_get_returns_summary_when_stored() -> None:
    from int.models import Memory

    tools, store, _ = _make_tools()

    m = Memory(project="pianoweb", type="t", content="x", summary="test summary")
    store.memories[m.id] = (m.project, m.type, m.content, m.summary)

    result = await tools.call("get", {"memory_id": str(m.id)})

    assert isinstance(result, Memory)
    assert result.summary == "test summary"


@pytest.mark.asyncio
async def test_get_missing_returns_none() -> None:
    import uuid

    tools, _, _ = _make_tools()

    result = await tools.call("get", {"memory_id": str(uuid.uuid4())})

    assert result is None


@pytest.mark.asyncio
async def test_get_invalid_uuid_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()

    with pytest.raises(ValidationError):
        await tools.call("get", {"memory_id": "not-a-uuid"})


@pytest.mark.asyncio
async def test_get_missing_memory_id_raises_validation_error() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools()

    with pytest.raises(ValidationError):
        await tools.call("get", {})  # type: ignore[call-arg]


# --- project-existence guard ---


@pytest.mark.asyncio
async def test_add_unknown_project_raises_with_did_you_mean() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools(known_projects=["avermedia-c985-linux"])
    with pytest.raises(ValidationError) as exc:
        await tools.call(
            "add",
            {"project": "avermedia-c985", "type": "t", "content": "x"},
        )
    assert "does not exist" in str(exc.value)
    assert "Did you mean 'avermedia-c985-linux'" in str(exc.value)


@pytest.mark.asyncio
async def test_add_unknown_project_without_near_match() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools(known_projects=["int", "pianoweb"])
    with pytest.raises(ValidationError) as exc:
        await tools.call(
            "add",
            {"project": "zzz-unrelated", "type": "t", "content": "x"},
        )
    assert "zzz-unrelated" in str(exc.value)
    assert "Did you mean" not in str(exc.value)


@pytest.mark.asyncio
async def test_search_unknown_project_raises() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools(known_projects=["pianoweb"])
    with pytest.raises(ValidationError):
        await tools.call("search", {"project": "missing", "query": "q"})


@pytest.mark.asyncio
async def test_list_unknown_project_raises() -> None:
    from int.models import ValidationError

    tools, _, _ = _make_tools(known_projects=["pianoweb"])
    with pytest.raises(ValidationError):
        await tools.call("list", {"project": "missing"})


@pytest.mark.asyncio
async def test_list_known_project_succeeds() -> None:
    tools, _, _ = _make_tools(known_projects=["pianoweb"])
    result = await tools.call("list", {"project": "pianoweb"})
    assert isinstance(result, list)


# --- Unknown tool routing ---


@pytest.mark.asyncio
async def test_unknown_tool_raises_value_error() -> None:
    tools, _, _ = _make_tools()
    with pytest.raises(KeyError):
        await tools.call("does-not-exist", {})
