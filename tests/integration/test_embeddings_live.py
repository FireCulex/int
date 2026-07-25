"""Live-API tests for int.embeddings against the real Gemini endpoint.

These are SKIPPED by default to avoid network contact in CI and routine tests.
Run them only after you've put a real GEMINI_API_KEY in your .env:

    uv run pytest tests/integration/test_embeddings_live.py --run-live

If the live test passes, you have empirical proof that:
- the SDK call shape (model, contents, EmbedContentConfig) matches what the
  Gemini API actually expects for gemini-embedding-001
- output_dimensionality=768 returns 768-dim vectors
- task_type is accepted (not rejected as unsupported)
- our normalization produces a unit vector against real output
"""

from __future__ import annotations

import os

import numpy as np
import pytest

pytestmark = pytest.mark.live


def _live_client() -> object:
    from google import genai

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        pytest.skip("GEMINI_API_KEY not set in env; cannot run live test")
    # The Embedder awaits the call, so it needs the async sub-client
    # (`Client.aio`, whose `models.embed_content` is a real coroutine).
    # The sync `Client.models.embed_content` returns the response directly
    # and awaiting it raises "'EmbedContentResponse' object can't be awaited".
    return genai.Client(api_key=key).aio


def _live_settings() -> tuple[object, str, int]:
    model = os.environ.get("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
    dim = int(os.environ.get("GEMINI_EMBEDDING_DIMENSIONS", "768"))
    return _live_client(), model, dim


@pytest.mark.asyncio
async def test_live_embed_document_returns_normalized_unit_vector() -> None:
    from int.embeddings import Embedder

    client, model, dim = _live_settings()
    e = Embedder(client=client, model=model, dimension=dim)
    out = await e.embed_document("Flask backend on Python 3.12.")

    assert len(out) == dim
    got = np.array(out, dtype=np.float32)
    assert abs(float(np.linalg.norm(got)) - 1.0) < 1e-5


@pytest.mark.asyncio
async def test_live_embed_query_returns_normalized_unit_vector() -> None:
    from int.embeddings import Embedder

    client, model, dim = _live_settings()
    e = Embedder(client=client, model=model, dimension=dim)
    out = await e.embed_query("what is the backend framework?")

    assert len(out) == dim
    got = np.array(out, dtype=np.float32)
    assert abs(float(np.linalg.norm(got)) - 1.0) < 1e-5


@pytest.mark.asyncio
async def test_live_doc_and_query_of_same_text_differ() -> None:
    """task_type must actually affect the embedding — same text, different
    task_type, should produce different vectors."""
    from int.embeddings import Embedder

    client, model, dim = _live_settings()
    e = Embedder(client=client, model=model, dimension=dim)
    text = "the tech stack is Flask and Python 3.12"
    doc = await e.embed_document(text)
    qry = await e.embed_query(text)

    assert len(doc) == dim
    assert len(qry) == dim
    diff = np.array(doc, dtype=np.float32) - np.array(qry, dtype=np.float32)
    assert float(np.linalg.norm(diff)) > 1e-3
