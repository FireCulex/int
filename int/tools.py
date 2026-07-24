"""int.tools — the five MCP tools exposed by the server.

Each tool validates its input via Pydantic, then delegates to the store /
embedder. Auth and protocol transport live in int.server, not here.

The ToolsRegistry is independent of any specific MCP SDK so it can be
exposed via the `mcp` Python SDK's FastMCP adapter in int.server or via
a test harness without re-wiring. The registry exposes call-shaped methods so
tests can exercise the orchestration without standing up a server.

Per spec (docs/spec.md):
- int.add(project, type, content)        -> memory_id (UUID str)
- int.delete(memory_id)                    -> deleted (bool)
- int.search(project, query, limit=5)     -> list[SearchResult]
- int.list(project)                       -> list[MemoryMetadata] (no content,
                                             no embedding call)
- int.recall(project, query, limit=5)    -> list[SearchResult] (pass-through
                                             to search in v1)

Validation: empty / malformed / missing-required inputs raise our own
int.models.ValidationError (distinct from pydantic's), so int.server can map
it to a specific 4xx envelope without string-matching.
"""

from __future__ import annotations

import builtins
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID, uuid4

from int.models import (
    EmbeddingError,
    Memory,
    MemoryMetadata,
    SearchResult,
    ValidationError,
)

# --- Protocols so we can depend on minimal shapes, not concrete classes ---


class _StoreLike(Protocol):
    def add(self, memory: Memory, embedding: Sequence[float]) -> UUID: ...
    def delete(self, memory_id: UUID) -> bool: ...
    def search(
        self,
        project: str,
        *,
        query_vector: Sequence[float],
        limit: int = 5,
    ) -> builtins.list[SearchResult]: ...
    def recall(
        self,
        project: str,
        *,
        query_vector: Sequence[float],
        limit: int = 5,
    ) -> builtins.list[SearchResult]: ...
    def list(self, project: str) -> builtins.list[MemoryMetadata]: ...  # noqa: A003


class _EmbedderLike(Protocol):
    async def embed_document(self, content: str) -> builtins.list[float]: ...
    async def embed_query(self, content: str) -> builtins.list[float]: ...


# --- Tool descriptors ---


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str
    input_schema: dict[str, Any]


def _require_str(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a non-empty string")
    if not value:
        raise ValidationError(f"{field} must be a non-empty string")
    return value


def _require_int_in_range(value: Any, *, field: str, minimum: int, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field} must be an integer")
    if value < minimum:
        raise ValidationError(f"{field} must be >= {minimum}, got {value}")
    return value


def _require_uuid_str(value: Any, *, field: str) -> UUID:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a UUID string")
    try:
        return UUID(value)
    except (ValueError, AttributeError) as e:
        raise ValidationError(f"{field} is not a valid UUID: {value!r}") from e


class ToolsRegistry:
    """Owns and dispatches the five MCP tools.

    Constructed once at server startup with the store + embedder. The MCP
    layer (int.server) reflects on `list_tools()` to register them with the
    SDK; it calls `call(name, args)` in response to incoming tool invocations.
    """

    def __init__(self, *, store: _StoreLike, embedder: _EmbedderLike) -> None:
        self._store = store
        self._embedder = embedder

    def list_tools(self) -> builtins.list[ToolDescriptor]:
        return [
            ToolDescriptor(
                name="int.add",
                description=(
                    "Store a memory in a project. The content is embedded "
                    "(RETRIEVAL_DOCUMENT task_type) and persisted to the project's "
                    "memory store. Returns the new memory's UUID."
                ),
                input_schema={
                    "type": "object",
                    "required": ["project", "type", "content"],
                    "properties": {
                        "project": {"type": "string", "minLength": 1},
                        "type": {
                            "type": "string",
                            "minLength": 1,
                            "description": (
                                "Free-text type tag. Recommended: architecture / "
                                "preference / command / learned-pattern / conversation "
                                "/ error-solution / project-config. Not enforced."
                            ),
                        },
                        "content": {"type": "string", "minLength": 1},
                    },
                },
            ),
            ToolDescriptor(
                name="int.delete",
                description=(
                    "Delete a memory by ID. Idempotent: returns False if no memory "
                    "existed at that ID."
                ),
                input_schema={
                    "type": "object",
                    "required": ["memory_id"],
                    "properties": {
                        "memory_id": {"type": "string", "format": "uuid"},
                    },
                },
            ),
            ToolDescriptor(
                name="int.search",
                description=(
                    "Search a project's memories by semantic query. Returns ranked "
                    "results (cosine similarity, descending) with content + score."
                ),
                input_schema={
                    "type": "object",
                    "required": ["project", "query"],
                    "properties": {
                        "project": {"type": "string", "minLength": 1},
                        "query": {"type": "string", "minLength": 1},
                        "limit": {"type": "integer", "minimum": 1, "default": 5},
                    },
                },
            ),
            ToolDescriptor(
                name="int.list",
                description=(
                    "List all memories in a project. Metadata only (id, type, "
                    "created_at); no content, no embedding call."
                ),
                input_schema={
                    "type": "object",
                    "required": ["project"],
                    "properties": {
                        "project": {"type": "string", "minLength": 1},
                    },
                },
            ),
            ToolDescriptor(
                name="int.recall",
                description=(
                    "Recall memories from a project by semantic query. v1 is a "
                    "pass-through to search; reserved for future summary+recall."
                ),
                input_schema={
                    "type": "object",
                    "required": ["project", "query"],
                    "properties": {
                        "project": {"type": "string", "minLength": 1},
                        "query": {"type": "string", "minLength": 1},
                        "limit": {"type": "integer", "minimum": 1, "default": 5},
                    },
                },
            ),
        ]

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        """Dispatch a tool invocation by name.

        Raises:
            KeyError: unknown tool name.
            int.models.ValidationError: input shape/value invalid.
            int.models.EmbeddingError: embedding backend failed.
            int.models.StoreError: store backend failed.
        """
        if name not in self._dispatch:
            raise KeyError(f"unknown tool: {name!r}")
        return await self._dispatch[name](self, args)

    # --- Tool implementations ---

    async def _add(self, args: dict[str, Any]) -> str:
        project = _require_str(args.get("project"), field="project")
        type_ = _require_str(args.get("type"), field="type")
        content = _require_str(args.get("content"), field="content")

        vec = await self._embedder.embed_document(content)
        memory = Memory(
            id=uuid4(),
            project=project,
            type=type_,
            content=content,
        )
        mid = self._store.add(memory, vec)
        return str(mid)

    async def _delete(self, args: dict[str, Any]) -> bool:
        memory_id = _require_uuid_str(args.get("memory_id"), field="memory_id")
        return bool(self._store.delete(memory_id))

    async def _search(self, args: dict[str, Any]) -> builtins.list[SearchResult]:
        return await self._do_search(
            args,
            query_method=self._embedder.embed_query,
            store_search_method=self._store.search,
        )

    async def _recall(self, args: dict[str, Any]) -> builtins.list[SearchResult]:
        return await self._do_search(
            args,
            query_method=self._embedder.embed_query,
            store_search_method=self._store.recall,
        )

    async def _list(self, args: dict[str, Any]) -> builtins.list[MemoryMetadata]:
        project = _require_str(args.get("project"), field="project")
        return builtins.list(self._store.list(project))

    async def _do_search(
        self,
        args: dict[str, Any],
        *,
        query_method: Callable[[str], Awaitable[builtins.list[float]]],
        store_search_method: Callable[..., builtins.list[SearchResult]],
    ) -> builtins.list[SearchResult]:
        project = _require_str(args.get("project"), field="project")
        query = _require_str(args.get("query"), field="query")
        limit = _require_int_in_range(args.get("limit", 5), field="limit", minimum=1, default=5)

        q_vec = await query_method(query)
        if not q_vec:
            raise EmbeddingError("empty embedding for query")
        return builtins.list(store_search_method(project, query_vector=q_vec, limit=limit))

    # Dispatch table built lazily after methods are defined.
    _dispatch: dict[str, Callable[[ToolsRegistry, dict[str, Any]], Awaitable[Any]]] = {}


ToolsRegistry._dispatch = {
    "int.add": ToolsRegistry._add,
    "int.delete": ToolsRegistry._delete,
    "int.search": ToolsRegistry._search,
    "int.list": ToolsRegistry._list,
    "int.recall": ToolsRegistry._recall,
}


__all__ = ["ToolsRegistry", "ToolDescriptor"]
