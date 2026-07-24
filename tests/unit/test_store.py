"""Tests for int.store — Qdrant-backed project-scoped memory store.

Uses a FakeQdrantClient that records every call and keeps an in-memory collection.
No network. Verifies:
- add(memory, embedding) stores the memory + embedding keyed by memory.id
- delete(uuid) returns True if existed, False if missing (idempotent)
- search(project, query_vec, limit) returns scored hits filtered by project
- list(project) returns MemoryMetadata (no content, no embedding call)
- recall(project, query_vec, limit) mirrors search (thin pass-through in v1)
- project scoping: search A returns nothing from B
- collection auto-creates on first use with the configured dimension
- startup assert: existing collection with wrong dimension -> StoreError fail-fast
- cosine ordering: closer vectors come first; score is cosine similarity
- zero-vector query raises EmbeddingError (cheap guard, caller's bug)
"""

from __future__ import annotations

import uuid
from typing import Any

import numpy as np
import pytest

# --- Fake Qdrant client infrastructure (in-memory) ---


class FakePoint:
    def __init__(
        self,
        *,
        point_id: uuid.UUID,
        vector: list[float],
        payload: dict[str, Any],
    ) -> None:
        self.id = point_id
        self.vector = list(vector)
        self.payload = dict(payload)


class FakeCollection:
    def __init__(self, dim: int) -> None:
        self.dim = dim
        self.points: dict[uuid.UUID, FakePoint] = {}

    def upsert(self, point: FakePoint) -> None:
        if len(point.vector) != self.dim:
            raise ValueError(f"vector dim {len(point.vector)} != collection dim {self.dim}")
        self.points[point.id] = point

    def delete(self, point_id: uuid.UUID) -> bool:
        return self.points.pop(point_id, None) is not None

    def scroll_all(self) -> list[FakePoint]:
        return list(self.points.values())


class FakeQdrantClient:
    """Sufficient slice of qdrant_client.QdrantClient for unit testing int.store.

    Records calls; no real server. Handles collection create/get and point
    upsert/delete/scroll. Does NOT replicate Qdrant's exact error messages.
    """

    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get_collection(self, *, name: str) -> FakeCollection:
        self.calls.append(("get_collection", {"name": name}))
        if name not in self.collections:
            raise _QdrantCollectionMissing(name)
        return self.collections[name]

    def create_collection(
        self,
        *,
        collection_name: str,
        vectors_config: Any,
    ) -> None:
        self.calls.append(("create_collection", {"collection_name": collection_name}))
        dim = vectors_config.params.size
        self.collections[collection_name] = FakeCollection(dim=dim)

    def collection_exists(self, *, name: str) -> bool:
        return name in self.collections

    def upsert(self, *, collection_name: str, points: list[dict[str, Any]]) -> None:
        self.calls.append(("upsert", {"collection_name": collection_name, "n_points": len(points)}))
        coll = self.collections[collection_name]
        for p in points:
            coll.upsert(
                FakePoint(
                    point_id=p["id"],
                    vector=p["vector"],
                    payload=p["payload"],
                )
            )

    def delete(self, *, collection_name: str, points_selector: Any) -> bool:
        self.calls.append(
            (
                "delete",
                {"collection_name": collection_name, "selector": points_selector},
            )
        )
        coll = self.collections[collection_name]
        ids = points_selector
        deleted = False
        for pid in ids:
            if coll.delete(pid):
                deleted = True
        return deleted

    def scroll(
        self,
        *,
        collection_name: str,
        scroll_filter: dict[str, Any] | None = None,
        limit: int = 100,
        with_payload: bool = True,
        with_vectors: bool = False,
    ) -> Any:
        self.calls.append(
            (
                "scroll",
                {
                    "collection_name": collection_name,
                    "filter": scroll_filter,
                    "limit": limit,
                    "with_payload": with_payload,
                    "with_vectors": with_vectors,
                },
            )
        )
        coll = self.collections[collection_name]
        pts = coll.scroll_all()
        project: str | None = None
        if scroll_filter:
            for cond in scroll_filter.get("must", []):
                if cond.get("key") == "project":
                    project = cond.get("match", {}).get("value")
        if project is not None:
            pts = [p for p in pts if p.payload.get("project") == project]
        pts = pts[:limit]

        items = []
        for p in pts:
            attrs: dict[str, Any] = {"id": p.id, "payload": dict(p.payload)}
            if with_vectors:
                attrs["vector"] = list(p.vector)
            _point_cls = type("Point", (), attrs)
            items.append((_point_cls(), None))
        return (items, None)


class _QdrantCollectionMissing(Exception):  # noqa: N818 - mirrors an SDK signal
    """Mirrors qdrant_client's collection-missing signal so store.py can branch."""


def _cos(a: list[float], b: list[float]) -> float:
    av = np.array(a, dtype=np.float32)
    bv = np.array(b, dtype=np.float32)
    na = np.linalg.norm(av)
    nb = np.linalg.norm(bv)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(av, bv) / (na * nb))


def _make_store(dim: int = 768, collection: str = "int_memories") -> Any:
    from int.store import QdrantStore

    client = FakeQdrantClient()
    store = QdrantStore(client=client, collection_name=collection, dimension=dim)
    return store, client


# --- Tests ---


def test_add_stores_vector_and_memory() -> None:
    from int.models import Memory

    store, client = _make_store(dim=8)
    m = Memory(project="p", type="command", content="npm test")
    vec = [1.0] + [0.0] * 7
    mid = store.add(m, vec)
    assert mid == m.id
    coll = client.collections["int_memories"]
    stored = coll.points[m.id]
    assert stored.payload["project"] == "p"
    assert stored.payload["type"] == "command"
    assert stored.payload["content"] == "npm test"
    assert "created_at" in stored.payload
    assert stored.vector == vec


def test_add_auto_creates_collection_with_configured_dim() -> None:
    store, client = _make_store(dim=512, collection="custom")
    from int.models import Memory

    store.add(Memory(project="p", type="command", content="x"), [1.0] * 512)
    assert "custom" in client.collections
    assert client.collections["custom"].dim == 512


def test_add_rejects_wrong_dimension_vector() -> None:
    from int.models import Memory, StoreError

    store, _ = _make_store(dim=768)
    with pytest.raises(StoreError):
        store.add(Memory(project="p", type="command", content="x"), [1.0] * 5)


def test_search_returns_results_filtered_by_project() -> None:
    from int.models import Memory

    store, _ = _make_store(dim=4)
    # project A: 3 memories
    store.add(
        Memory(project="A", type="architecture", content="flask backend"),
        [1.0, 0.0, 0.0, 0.0],
    )
    store.add(
        Memory(project="A", type="command", content="npm test"),
        [0.0, 1.0, 0.0, 0.0],
    )
    store.add(
        Memory(project="A", type="preference", content="prefer tabs"),
        [0.0, 0.0, 1.0, 0.0],
    )
    # project B: 1 memory
    store.add(
        Memory(project="B", type="architecture", content="django backend"),
        [1.0, 0.9, 0.0, 0.0],  # very similar to A's flask vector in dim 0
    )
    results = store.search("A", query_vector=[1.0, 0.0, 0.0, 0.0], limit=5)
    assert len(results) == 3
    assert all(r.content != "django backend" for r in results)
    types = {r.type for r in results}
    assert types == {"architecture", "command", "preference"}


def test_search_orders_by_descending_cosine_similarity() -> None:
    from int.models import Memory

    store, _ = _make_store(dim=4)
    store.add(
        Memory(project="p", type="a", content="near"),
        [1.0, 0.0, 0.0, 0.0],
    )
    store.add(
        Memory(project="p", type="b", content="far"),
        [0.0, 0.0, 0.0, 1.0],
    )
    store.add(
        Memory(project="p", type="c", content="mid"),
        [0.7, 0.7, 0.0, 0.0],
    )
    results = store.search("p", query_vector=[1.0, 0.0, 0.0, 0.0], limit=5)
    assert [r.content for r in results] == ["near", "mid", "far"]
    assert results[0].score > results[1].score > results[2].score
    assert results[0].score == pytest.approx(1.0, abs=1e-5)


def test_search_respects_limit() -> None:
    from int.models import Memory

    store, _ = _make_store(dim=2)
    for i in range(10):
        store.add(
            Memory(project="p", type="t", content=f"c{i}"),
            [float(i), 0.0],
        )
    results = store.search("p", query_vector=[1.0, 0.0], limit=3)
    assert len(results) == 3


def test_search_empty_project_returns_empty() -> None:
    store, _ = _make_store(dim=4)
    results = store.search("nope", query_vector=[1.0, 0.0, 0.0, 0.0], limit=5)
    assert results == []


def test_list_returns_metadata_only_no_content_no_vector() -> None:
    from int.models import Memory, MemoryMetadata

    store, _ = _make_store(dim=4)
    store.add(
        Memory(project="p", type="command", content="npm test"),
        [1.0, 0.0, 0.0, 0.0],
    )
    store.add(
        Memory(project="p", type="architecture", content="flask"),
        [0.0, 1.0, 0.0, 0.0],
    )
    metas = store.list("p")
    assert len(metas) == 2
    assert all(isinstance(m, MemoryMetadata) for m in metas)
    # MemoryMetadata has no content field; ensure attribute access raises
    for m in metas:
        with pytest.raises(AttributeError):
            _ = m.content
    assert {m.type for m in metas} == {"command", "architecture"}


def test_list_filters_by_project() -> None:
    from int.models import Memory

    store, _ = _make_store(dim=4)
    store.add(Memory(project="A", type="t", content="a1"), [1.0, 0.0, 0.0, 0.0])
    store.add(Memory(project="A", type="t", content="a2"), [1.0, 0.0, 0.0, 0.0])
    store.add(Memory(project="B", type="t", content="b1"), [1.0, 0.0, 0.0, 0.0])
    metas_a = store.list("A")
    metas_b = store.list("B")
    assert len(metas_a) == 2
    assert len(metas_b) == 1


def test_delete_existing_returns_true() -> None:
    from int.models import Memory

    store, _ = _make_store(dim=4)
    m = Memory(project="p", type="t", content="x")
    store.add(m, [1.0, 0.0, 0.0, 0.0])
    assert store.delete(m.id) is True


def test_delete_missing_returns_false_idempotent() -> None:
    store, _ = _make_store(dim=4)
    missing = uuid.uuid4()
    assert store.delete(missing) is False
    # Calling again on the same missing id should still be False, not raise.
    assert store.delete(missing) is False


def test_recall_mirrors_search_in_v1() -> None:
    from int.models import Memory

    store, _ = _make_store(dim=4)
    store.add(
        Memory(project="p", type="t", content="near"),
        [1.0, 0.0, 0.0, 0.0],
    )
    store.add(
        Memory(project="p", type="t", content="far"),
        [0.0, 0.0, 0.0, 1.0],
    )
    search_results = store.search("p", query_vector=[1.0, 0.0, 0.0, 0.0], limit=5)
    recall_results = store.recall("p", query_vector=[1.0, 0.0, 0.0, 0.0], limit=5)
    assert [r.id for r in search_results] == [r.id for r in recall_results]
    assert [r.score for r in search_results] == [r.score for r in recall_results]


def test_dimension_mismatch_on_existing_collection_raises_store_error() -> None:
    """If the collection already exists at the wrong dimension, the store must
    fail-fast at startup (or first use), not silently corrupt stored vectors."""
    from int.models import StoreError

    client = FakeQdrantClient()
    # Pre-create the collection at 3072 (mismatch with our 768 setting)
    client.collections["int_memories"] = FakeCollection(dim=3072)
    from int.store import QdrantStore

    store = QdrantStore(client=client, collection_name="int_memories", dimension=768)
    with pytest.raises(StoreError):
        store.ensure_collection()


def test_ensure_collection_creates_if_missing() -> None:
    store, client = _make_store(dim=768)
    # Store construction doesn't create the collection by itself; ensure does.
    assert "int_memories" not in client.collections
    store.ensure_collection()
    assert "int_memories" in client.collections
    coll = client.collections["int_memories"]
    assert coll.dim == 768
    # Calling ensure again on an existing correct-dim collection is a no-op (no raise).
    store.ensure_collection()


def test_search_zero_vector_raises_embedding_error() -> None:
    """Cheap guard against caller bugs: a zero-vector query produces no signal."""
    from int.models import EmbeddingError

    store, _ = _make_store(dim=4)
    with pytest.raises(EmbeddingError):
        store.search("p", query_vector=[0.0, 0.0, 0.0, 0.0], limit=5)


def test_score_reported_is_cosine_similarity() -> None:
    from int.models import Memory

    store, _ = _make_store(dim=4)
    stored_vec = [1.0, 1.0, 0.0, 0.0]
    store.add(Memory(project="p", type="t", content="x"), stored_vec)
    query = [1.0, 0.0, 0.0, 0.0]
    expected = _cos(stored_vec, query) / 1  # stored vec isn't normalized in fake; cosine is fine
    results = store.search("p", query_vector=query, limit=5)
    assert results[0].score == pytest.approx(expected, abs=1e-5)
