"""int.tools — the MCP tools exposed by the server.

Each tool validates its input via Pydantic, then delegates to the store /
embedder. Auth and protocol transport live in int.server, not here.

The ToolsRegistry is independent of any specific MCP SDK so it can be
exposed via the `mcp` Python SDK's FastMCP adapter in int.server or via
a test harness without re-wiring. The registry exposes call-shaped methods so
tests can exercise the orchestration without standing up a server.

Per spec (docs/spec.md):
- add(project, type, content)        -> memory_id (UUID str)
- delete(memory_id)                  -> deleted (bool)
- search(project, query, limit=5)    -> list[SearchResult]
- list(project)                       -> list[MemoryMetadata] (no content,
                                       no embedding call)
- get(memory_id)                     -> Memory | None (no embedding call)

The descriptions below are the primary steering surface for model
tool-choice: each states WHEN to use the tool — `search` carries a strong
call-me-first directive — not just what it does. Keep that when editing.

Tool names are bare (no `int.` prefix). The MCP server is registered under
the server name `int` at the MCP layer, which already namespaces the tools
for clients. OpenCode's Code Mode exposes them ONLY inside its `execute`
sandbox tool as tools.int.add, tools.int.search, ...; there is no top-level
int_* tool. Re-prefixing the server-side tool names with `int.` caused
OpenCode to render `int_int_add`; bare names are the fix.

Validation: empty / malformed / missing-required inputs raise our own
int.models.ValidationError (distinct from pydantic's), so int.server can map
it to a specific 4xx envelope without string-matching.
"""

from __future__ import annotations

import builtins
import difflib
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
        query_text: str | None = None,
    ) -> builtins.list[SearchResult]: ...
    def list(self, project: str) -> builtins.list[MemoryMetadata]: ...  # noqa: A003
    def get(self, memory_id: UUID) -> Memory | None: ...
    def get_all(self, project: str) -> builtins.list[Memory]: ...
    def project_names(self) -> builtins.list[str]: ...


class _EmbedderLike(Protocol):
    async def embed_document(self, content: str) -> builtins.list[float]: ...
    async def embed_query(self, content: str) -> builtins.list[float]: ...
    @property
    def dimension(self) -> int: ...


# --- Tool descriptors ---


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str
    input_schema: dict[str, Any]


def _require_str(value: Any, *, field: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    if not value and not allow_empty:
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
    """Owns and dispatches the MCP tools.

    Constructed once at server startup with the store + embedder. The MCP
    layer (int.server) reflects on `list_tools()` to register them with the
    SDK; it calls `call(name, args)` in response to incoming tool invocations.
    """

    def __init__(self, *, store: _StoreLike, embedder: _EmbedderLike) -> None:
        self._store = store
        self._embedder = embedder
        self._dim = embedder.dimension

    def list_tools(self) -> builtins.list[ToolDescriptor]:
        return [
            ToolDescriptor(
                name="add",
                description=(
                    "Persist a learning for future sessions. Call when the "
                    "cost to re-discover exceeds the cost to save: multi-step "
                    "findings, architecture facts, config decisions, bug "
                    "diagnoses, error-solution pairs (include exact file:line "
                    "locators), and negative facts (something conspicuously "
                    "absent). Content is embedded (RETRIEVAL_DOCUMENT task_type) "
                    "and persisted to the project's memory store. Returns the "
                    "new memory's UUID. Memories are immutable-append; to "
                    "correct one, delete then re-add with the fixed content. "
                    "In OpenCode Code Mode, callable only inside the execute "
                    "tool as tools.int.add — there is no top-level int.add."
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
                        "summary": {
                            "type": "string",
                            "description": (
                                "Optional one-line summary, 120 chars max, "
                                "written by the assistant for quick scanning "
                                "in list views."
                            ),
                        },
                    },
                },
            ),
            ToolDescriptor(
                name="delete",
                description=(
                    "Delete a memory by ID. Idempotent: returns False if no "
                    "memory existed at that ID. Memories are immutable-append: "
                    "to correct or update a memory, delete it and add the fixed "
                    "content back. In OpenCode Code Mode, callable only inside "
                    "the execute tool as tools.int.delete — there is no "
                    "top-level int.delete."
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
                name="search",
                description=(
                    "Recall what prior sessions already learned. Call this "
                    "FIRST — before grep, glob, read, bash, web search, or any "
                    "exploration tool — for any 'how does X work', 'where is Y', "
                    "'why does Z behave', 'does it support W' question about "
                    "this project: a previous session may already have "
                    "synthesized the answer (architecture, config, bugs, past "
                    "fixes, negative facts). Cheap: one round-trip. Returns "
                    "ranked memories with content + score (hybrid: semantic "
                    "similarity blended with keyword coverage, descending — "
                    "rephrase with the memory's likely content vocabulary if "
                    "the first query misses). Empty results mean fall through "
                    "to file tools. In OpenCode Code Mode, callable only inside "
                    "the execute tool as tools.int.search — there is no "
                    "top-level int.search."
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
                name="list",
                description=(
                    "Orientation and recency scan of a project's memory. "
                    "Returns all metadata (id, type, summary, created_at), "
                    "newest first — no content, no embedding call, cheap. Use "
                    "before deeper work, AND as the fallback when a question "
                    "references dates or when semantic search may have missed "
                    "('what did we do last week', 'on the 16th'): scan "
                    "created_at to find memories by when they were written, "
                    "then fetch full content with get by id. In OpenCode "
                    "Code Mode, callable only inside the execute tool as "
                    "tools.int.list — there is no top-level int.list."
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
                name="get",
                description=(
                    "Get a single memory by ID with full content. No embedding "
                    "call. Use after search or list when metadata alone is not "
                    "enough. In OpenCode Code Mode, callable only inside the "
                    "execute tool as tools.int.get — there is no top-level "
                    "int.get."
                ),
                input_schema={
                    "type": "object",
                    "required": ["memory_id"],
                    "properties": {
                        "memory_id": {"type": "string", "format": "uuid"},
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

    def _require_project_exists(self, project: str) -> None:
        """Reject a project string that is not yet a materialized project.

        Projects are created via the CLI (`int-cli create`), never by the
        assistant-facing MCP surface. A typo'd project name must fail loudly
        here rather than silently auto-creating a polluted project. Unknown
        names raise ValidationError with an optional near-match hint so the
        client sees "did you mean 'X'?" instead of a bare failure.
        """
        known = self._store.project_names()
        if project in known:
            return
        hint = difflib.get_close_matches(project, known, n=1, cutoff=0.6)
        if hint:
            raise ValidationError(f"Project {project!r} does not exist. Did you mean {hint[0]!r}?")
        raise ValidationError(f"Project {project!r} does not exist.")

    async def _add(self, args: dict[str, Any]) -> str:
        project = _require_str(args.get("project"), field="project")
        type_ = _require_str(args.get("type"), field="type")
        content = _require_str(args.get("content"), field="content")
        summary = args.get("summary")
        if summary is not None:
            summary = _require_str(summary, field="summary", allow_empty=True)
        self._require_project_exists(project)

        vec = await self._embedder.embed_document(content)
        memory = Memory(
            id=uuid4(),
            project=project,
            type=type_,
            content=content,
            summary=summary,
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
            allow_empty_query=True,
        )

    async def _list(self, args: dict[str, Any]) -> builtins.list[MemoryMetadata]:
        project = _require_str(args.get("project"), field="project")
        self._require_project_exists(project)
        metas = builtins.list(self._store.list(project))
        # Newest first: recency scanning is list's second use case (see the
        # description) and raw scroll order is id-based, not time-based.
        metas.sort(key=lambda m: m.created_at, reverse=True)
        return metas

    async def _get(self, args: dict[str, Any]) -> Memory | None:
        memory_id = _require_uuid_str(args.get("memory_id"), field="memory_id")
        return self._store.get(memory_id)

    async def _do_search(
        self,
        args: dict[str, Any],
        *,
        query_method: Callable[[str], Awaitable[builtins.list[float]]],
        store_search_method: Callable[..., builtins.list[SearchResult]],
        allow_empty_query: bool = False,
    ) -> builtins.list[SearchResult]:
        project = _require_str(args.get("project"), field="project")
        query = _require_str(args.get("query"), field="query", allow_empty=allow_empty_query)
        limit = _require_int_in_range(args.get("limit", 5), field="limit", minimum=1, default=5)
        self._require_project_exists(project)

        if not query:
            q_vec = [0.0] * self._dim
        else:
            q_vec = await query_method(query)
            if not q_vec:
                raise EmbeddingError("empty embedding for query")
        return builtins.list(
            store_search_method(project, query_vector=q_vec, limit=limit, query_text=query)
        )

    # Dispatch table built lazily after methods are defined.
    _dispatch: dict[str, Callable[[ToolsRegistry, dict[str, Any]], Awaitable[Any]]] = {}


ToolsRegistry._dispatch = {
    "add": ToolsRegistry._add,
    "delete": ToolsRegistry._delete,
    "search": ToolsRegistry._search,
    "list": ToolsRegistry._list,
    "get": ToolsRegistry._get,
}


__all__ = ["ToolsRegistry", "ToolDescriptor"]
