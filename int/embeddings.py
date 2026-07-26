"""int.embeddings — Gemini embedding wrapper.

The single place where:
- `task_type` is set (`RETRIEVAL_DOCUMENT` for add, `RETRIEVAL_QUERY` for search)
- L2 normalization happens (gemini-embedding-001 does not auto-normalize
  non-3072 dimensions; cosine similarity requires unit vectors)
- Gemini API failures translate to our typed `EmbeddingError`

Callers (tools, store, CLI) never specify task_type or normalization — both are
baked into this wrapper. Swap to a local embedder in v2 by replacing this
module's internals; the public surface stays the same.

The `client` passed to `Embedder` must expose an async `models.embed_content`
coroutine. For google-genai 2.x that means `Client(api_key=...).aio` (the
async sub-client), *not* the top-level `Client` — `Client.models.embed_content`
is synchronous and awaiting its return value raises
`'EmbedContentResponse' object can't be awaited`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

import numpy as np

from int.models import EmbeddingError


class _EmbedContentLike(Protocol):
    def __call__(
        self,
        *,
        model: str,
        contents: list[str],
        config: Any = None,
    ) -> Any: ...


class _ModelsLike(Protocol):
    embed_content: _EmbedContentLike


class _ClientLike(Protocol):
    @property
    def models(self) -> Any: ...


class Embedder:
    """Wraps `google-genai` embed_content.

    Constructed once at server startup with the resolved `Settings` values.
    Async methods are safe to call concurrently — no shared mutable state.
    """

    def __init__(
        self,
        *,
        client: _ClientLike,
        model: str,
        dimension: int,
    ) -> None:
        if dimension <= 0:
            raise ValueError(f"dimension must be positive, got {dimension}")
        self._client = client
        self._model = model
        self._dim = dimension

    async def embed_document(self, content: str) -> list[float]:
        """For `add`. task_type=RETRIEVAL_DOCUMENT."""
        return await self._embed(content, task_type="RETRIEVAL_DOCUMENT")

    async def embed_query(self, content: str) -> list[float]:
        """For `search`. task_type=RETRIEVAL_QUERY."""
        return await self._embed(content, task_type="RETRIEVAL_QUERY")

    async def _embed(self, content: str, *, task_type: str) -> list[float]:
        raw = await self._call_gemini(content, task_type=task_type)
        return self._normalize(raw, content=content)

    async def _call_gemini(self, content: str, *, task_type: str) -> Iterable[float]:
        try:
            from google.genai import types
        except ImportError as e:
            raise EmbeddingError("google-genai SDK not installed") from e

        try:
            config = types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=self._dim,
            )
            result = await self._client.models.embed_content(
                model=self._model,
                contents=[content],
                config=config,
            )
        except Exception as e:
            raise EmbeddingError(f"Gemini embed_content call failed: {e}") from e

        embeddings = getattr(result, "embeddings", None)
        if not embeddings:
            raise EmbeddingError("Gemini returned no embeddings")
        values = getattr(embeddings[0], "values", None)
        if values is None:
            raise EmbeddingError("Gemini returned an embedding without values")
        return list(values)

    @staticmethod
    def _normalize(values: Iterable[float], *, content: str) -> list[float]:
        v = np.asarray(list(values), dtype=np.float32)
        if v.size == 0:
            raise EmbeddingError("empty embedding vector")
        n = float(np.linalg.norm(v))
        if n == 0.0:
            raise EmbeddingError(f"zero-norm embedding returned for content (len={len(content)})")
        normalized: list[float] = (v / n).tolist()
        return normalized


__all__ = ["Embedder"]
