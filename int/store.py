"""int.store — Qdrant-backed project-scoped memory store.

Public surface mirrors the spec's four tools (minus embedding generation,
which lives in int.embeddings). The store is project-scoped: every search and
list passes a Qdrant payload filter on the `project` field, so cross-project
leakage is impossible by construction.

Collection lifecycle (see `ensure_collection`):
- If the collection is missing, create it with the configured dimension.
- If it exists at the wrong dimension, raise `StoreError` fail-fast. We never
  silently accept a mismatch — changing GEMINI_EMBEDDING_DIMENSIONS after
  memories exist invalidates stored vectors, and the safe action is to halt.
"""

from __future__ import annotations

import builtins
from collections.abc import Sequence
from typing import Any, Protocol
from uuid import UUID

import numpy as np

from int.models import EmbeddingError, Memory, MemoryMetadata, SearchResult, StoreError

# Inside QdrantStore's class body, the method name `list` shadows the builtin.
# Use `builtins.list` for return-type annotations to avoid the mypy collision.
list_alias = builtins.list


class _UpsertLike(Protocol):
    def __call__(self) -> None: ...


class _QdrantClientLike(Protocol):
    def get_collection(self, *, collection_name: str) -> Any: ...
    def collection_exists(self, *, collection_name: str) -> bool: ...
    def create_collection(self, *, collection_name: str, vectors_config: Any) -> None: ...
    def upsert(self, *, collection_name: str, points: Sequence[dict[str, Any]]) -> None: ...
    def delete(self, *, collection_name: str, points_selector: Any) -> bool: ...
    def retrieve(
        self,
        *,
        collection_name: str,
        ids: Sequence[Any],
        with_payload: bool,
        with_vectors: bool,
    ) -> Any: ...
    def scroll(
        self,
        *,
        collection_name: str,
        scroll_filter: dict[str, Any] | None,
        limit: int,
        offset: int | None = None,
        with_payload: bool,
        with_vectors: bool,
    ) -> Any: ...


def _project_filter(project: str) -> dict[str, Any]:
    return {
        "must": [
            {"key": "project", "match": {"value": project}},
        ],
    }


class QdrantStore:
    """Project-scoped memory store backed by Qdrant.

    Constructed once at server startup with the resolved client, collection
    name, and dimension. All methods are synchronous: Qdrant's sync client is
    used. (Async wrapping is a thin future layer if/when we want full async
    end-to-end; the MCP server is async via ASGI but the store call is a
    sub-millisecond local-network hop that doesn't benefit from awaiting.)
    """

    def __init__(
        self,
        *,
        client: _QdrantClientLike,
        collection_name: str,
        dimension: int,
    ) -> None:
        if dimension <= 0:
            raise ValueError(f"dimension must be positive, got {dimension}")
        self._client = client
        self._collection = collection_name
        self._dim = dimension
        self._ensured = False

    def ensure_collection(self) -> None:
        """Create the collection if missing; fail-fast on dimension mismatch.

        Idempotent: safe to call multiple times. The first successful call
        flips `_ensured` so subsequent calls are a cheap no-op.
        """
        if self._ensured:
            return

        if self._client.collection_exists(collection_name=self._collection):
            existing = self._client.get_collection(collection_name=self._collection)
            # Qdrant exposes config.params.vectors.size for the dim, but our
            # tests use a fake; both expose `.dim` via the FakeCollection's
            # own attribute. Read the dimension through a small dispatch so
            # we support the real client and our fake uniformly.
            existing_dim = _read_collection_dim(existing)
            if existing_dim is not None and existing_dim != self._dim:
                raise StoreError(
                    f"Collection {self._collection!r} exists at dim {existing_dim} "
                    f"but Settings.GEMINI_EMBEDDING_DIMENSIONS={self._dim}. "
                    f"Changing the dimension invalidates stored vectors; "
                    f"either reset the Qdrant collection or revert the env."
                )
        else:
            try:
                # Import the real Qdrant models lazily so the unit tests (which
                # use a FakeClient that doesn't actually call create_collection)
                # don't need qdrant_client installed. The integration / E2E tests
                # do import qdrant_client for real, so this is available there.
                from qdrant_client.http.models import Distance, VectorParams

                vectors_config = VectorParams(size=self._dim, distance=Distance.COSINE)
                self._client.create_collection(
                    collection_name=self._collection,
                    vectors_config=vectors_config,
                )
            except Exception as e:
                raise StoreError(f"Failed to create Qdrant collection: {e}") from e

        self._ensured = True

    def add(self, memory: Memory, embedding: Sequence[float]) -> UUID:
        """Store a memory with its (already L2-normalized) embedding.

        Doesn't re-normalize — embeddings must arrive unit-length from the
        Embedder. Qdrant's Cosine distance also normalizes, but storing
        pre-normalized vectors keeps the cosine math cheap and the
        invariant checkable in tests.
        """
        self.ensure_collection()
        vec = list(embedding)
        if len(vec) != self._dim:
            raise StoreError(f"embedding dim {len(vec)} != collection dim {self._dim}")
        try:
            self._client.upsert(
                collection_name=self._collection,
                points=[
                    {
                        "id": memory.id,
                        "vector": vec,
                        "payload": {
                            "project": memory.project,
                            "type": memory.type,
                            "content": memory.content,
                            "created_at": memory.created_at.isoformat(),
                        },
                    }
                ],
            )
        except StoreError:
            raise
        except Exception as e:
            raise StoreError(f"Qdrant upsert failed: {e}") from e
        return memory.id

    def delete(self, memory_id: UUID) -> bool:
        """Idempotent delete. Returns True if something was deleted, else False.

        Qdrant's `delete` returns an ack (`status: "completed"`) without
        indicating whether any point actually matched the selector, so we
        probe with `retrieve` first. An empty result means nothing to delete
        and we return False without issuing a no-op delete -- so callers see
        idempotent semantics (test_store.py::test_delete_missing_returns_false)
        and the CLI prints 'false' for unknown ids.
        """
        self.ensure_collection()
        try:
            existing = self._client.retrieve(
                collection_name=self._collection,
                ids=[memory_id],
                with_payload=False,
                with_vectors=False,
            )
        except Exception as e:
            raise StoreError(f"Qdrant retrieve (for delete) failed: {e}") from e
        if not existing:
            return False
        try:
            self._client.delete(
                collection_name=self._collection,
                points_selector=[memory_id],
            )
        except Exception as e:
            raise StoreError(f"Qdrant delete failed: {e}") from e
        return True

    def search(
        self,
        project: str,
        *,
        query_vector: Sequence[float],
        limit: int = 5,
    ) -> list_alias[SearchResult]:
        """Cosine search filtered to one project, ranked by descending score."""
        return self._do_search(project, query_vector=query_vector, limit=limit)

    def list(self, project: str) -> list_alias[MemoryMetadata]:
        """All memory metadata in a project. No content, no embedding call."""
        self.ensure_collection()
        from datetime import datetime

        metas: list[MemoryMetadata] = []
        offset: int | None = None
        try:
            while True:
                items, _next = self._client.scroll(
                    collection_name=self._collection,
                    scroll_filter=_project_filter(project),
                    limit=1_000,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                for point in items:
                    p = point.payload
                    metas.append(
                        MemoryMetadata(
                            id=_to_uuid(point.id),
                            type=p["type"],
                            created_at=datetime.fromisoformat(p["created_at"]),
                        )
                    )
                if _next is None:
                    break
                offset = _next
        except Exception as e:
            raise StoreError(f"Qdrant scroll failed: {e}") from e
        return metas

    def project_names(self) -> list_alias[str]:
        """Sorted, de-duplicated project names that have at least one memory.

        Enumerates the whole collection (no project filter) via scroll
        pagination, reading payloads only (never vectors). Powers the read-only
        `int://projects` MCP resource. Returns [] when the collection is empty.
        """
        self.ensure_collection()
        names: builtins.set[str] = set()
        offset: int | None = None
        try:
            while True:
                items, _next = self._client.scroll(
                    collection_name=self._collection,
                    scroll_filter=None,
                    limit=1_000,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                for point in items:
                    project = point.payload.get("project")
                    if project:
                        names.add(project)
                if _next is None:
                    break
                offset = _next
        except Exception as e:
            raise StoreError(f"Qdrant project_names scroll failed: {e}") from e
        return builtins.sorted(names)

    def _do_search(
        self,
        project: str,
        *,
        query_vector: Sequence[float],
        limit: int,
    ) -> list_alias[SearchResult]:

        self.ensure_collection()
        vec = list(query_vector)
        if len(vec) != self._dim:
            raise StoreError(f"query_vector dim {len(vec)} != collection dim {self._dim}")
        if all(v == 0.0 for v in vec):
            raise EmbeddingError("zero-vector query yields no signal")

        # Fetch all points for the project and compute cosine similarity in
        # Python. This is a cheap local-network path; switching to Qdrant's
        # native search() is a future optimization (same public method, so
        # no callers break).
        try:
            items, _next = self._client.scroll(
                collection_name=self._collection,
                scroll_filter=_project_filter(project),
                limit=10_000,
                with_payload=True,
                with_vectors=True,
            )
        except Exception as e:
            raise StoreError(f"Qdrant search scroll failed: {e}") from e

        q = np.asarray(vec, dtype=np.float32)
        qn = np.linalg.norm(q)
        if qn == 0.0:
            raise EmbeddingError("zero-norm query vector")
        q = q / qn

        scored: list[SearchResult] = []
        for point in items:
            v = np.asarray(point.vector, dtype=np.float32)
            vn = np.linalg.norm(v)
            if vn == 0.0:
                continue
            v_norm = v / vn
            score = float(np.dot(q, v_norm))
            payload = point.payload
            scored.append(
                SearchResult(
                    id=_to_uuid(point.id),
                    type=payload["type"],
                    content=payload["content"],
                    score=score,
                )
            )
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:limit]


def _read_collection_dim(existing: Any) -> int | None:
    """Best-effort dim read across the real QdrantClient and our FakeCollection.

    Real client: existing.config.params.vectors.size
    FakeCollection: existing.dim
    """
    # Fake
    if hasattr(existing, "dim"):
        return int(existing.dim)
    # Real
    try:
        return int(existing.config.params.vectors.size)
    except Exception:
        return None


def _to_uuid(point_id: Any) -> UUID:
    if isinstance(point_id, UUID):
        return point_id
    return UUID(str(point_id))


__all__ = ["QdrantStore"]
