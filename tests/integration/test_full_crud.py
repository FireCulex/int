"""Integration test: real Qdrant + mocked Gemini.

Stands up Qdrant via testcontainers, uses FakeEmbedder from conftest.py to
avoid the live Gemini API contact, and exercises the full CRUD path against
the real store implementation. Verifies:

- (a) Result in top 3 with cosine >= 0.6 for a representative stored synthesis.
  This is the spec's headline success criterion (recall without re-discovery).
- (b) Project-A search returns zero hits from project B's storage.
- (c) Every stored vector has norm == 1.0 within float tolerance.

Requires Docker (for testcontainers / Qdrant). The session-scoped
qdrant_container fixture in conftest.py skips the suite cleanly when Docker
isn't available so the gate stays green on machines without Docker.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import numpy as np

# Real pianoweb synthesis from the user's earlier example. Used as the canonical
# fixture to match the spec's first success criterion verbatim.
PIANOWEB_SYNTHESIS = """\
PianoTrainer Tech Stack Summary
- Python 3.14 (venv, python3.14)
- Vanilla JavaScript (ES module) — no TypeScript, no framework
- Flask backend (server.py, ~76 lines)
- No backend framework beyond Flask; no Django/FastAPI
- Browser APIs: Canvas 2D, Web MIDI API, Web Audio API
- Build/bundler: none — static files served by Flask
- No CDN links in index.html; app.js dynamically imports smplr from jsdelivr
- No tests, no linting, no typing, no CI
- Key files: server.py, static/app.js (~1440 lines), static/index.html"""


def _make_real_store(container: Any, dim: int = 768) -> Any:
    from qdrant_client import QdrantClient
    from qdrant_client.http.models import Distance, VectorParams

    host = container.host_ip()
    port = container.rest_port()
    client = QdrantClient(host=host, port=port)
    client.recreate_collection(
        collection_name="int_memories",
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )
    from int.store import QdrantStore

    return QdrantStore(client=client, collection_name="int_memories", dimension=dim)


def test_stored_synthesis_retrievable_in_top_3_with_score(
    qdrant_container: Any,
    fake_embedder: Any,
) -> None:
    """Spec success criterion #3: recall without re-discovery."""
    from int.models import Memory

    store = _make_real_store(qdrant_container)
    m = Memory(
        id=uuid.uuid4(),
        project="pianoweb",
        type="architecture",
        content=PIANOWEB_SYNTHESIS,
        created_at=datetime.now(UTC),
    )
    doc_vec = fake_embedder.embed_document_sync(m.content)
    store.add(m, doc_vec)

    q_vec = fake_embedder.embed_query_sync("what is the backend framework?")
    results = store.search("pianoweb", query_vector=q_vec, limit=5)
    assert len(results) >= 1
    top3 = results[:3]
    matches = [r for r in top3 if r.id == m.id]
    assert matches, f"synthesis {m.id} not in top 3; got {[r.id for r in top3]}"
    assert matches[0].score >= 0.6, f"score {matches[0].score} below 0.6"


def test_search_project_a_returns_zero_from_project_b(
    qdrant_container: Any,
    fake_embedder: Any,
) -> None:
    """Spec success criterion #2: project scoping enforced."""
    from int.models import Memory

    store = _make_real_store(qdrant_container)
    a_vec = fake_embedder.embed_document_sync("flask backend, python 3.14")
    b_vec = fake_embedder.embed_document_sync("rust, actix, sqlx")

    ma = Memory(
        id=uuid.uuid4(),
        project="pianoweb",
        type="architecture",
        content="flask backend, python 3.14",
        created_at=datetime.now(UTC),
    )
    mb = Memory(
        id=uuid.uuid4(),
        project="rustsvc",
        type="architecture",
        content="rust, actix, sqlx",
        created_at=datetime.now(UTC),
    )
    store.add(ma, a_vec)
    store.add(mb, b_vec)

    q_vec = fake_embedder.embed_query_sync(
        "what framework does the python service use?"
    )
    results = store.search("pianoweb", query_vector=q_vec, limit=10)
    assert all(r.id != mb.id for r in results), "cross-project leak detected"


def test_every_stored_vector_is_l2_normalized(
    qdrant_container: Any,
    fake_embedder: Any,
) -> None:
    """Spec success criterion #10: every vector in Qdrant has norm == 1.0."""
    from int.models import Memory

    store = _make_real_store(qdrant_container)
    for i in range(5):
        m = Memory(
            id=uuid.uuid4(),
            project="normtest",
            type="command",
            content=f"npm test {i}",
            created_at=datetime.now(UTC),
        )
        v = fake_embedder.embed_document_sync(m.content)
        store.add(m, v)

    container = qdrant_container
    from qdrant_client import QdrantClient

    host = container.host_ip()
    port = container.rest_port()
    client = QdrantClient(host=host, port=port)
    pts, _ = client.scroll(
        collection_name="int_memories",
        scroll_filter={
            "must": [{"key": "project", "match": {"value": "normtest"}}]
        },
        limit=100,
        with_payload=False,
        with_vectors=True,
    )
    for point in pts:
        v = np.asarray(point.vector, dtype=np.float32)
        norm = float(np.linalg.norm(v))
        assert abs(norm - 1.0) < 1e-5, f"vector {point.id} has norm {norm}"
