"""Tests for int.embeddings — the Embedder wrapper.

Validates the contract from the spec:
- `embed_document(content)` -> calls Gemini with task_type=RETRIEVAL_DOCUMENT
- `embed_query(content)`   -> calls Gemini with task_type=RETRIEVAL_QUERY
- output_dimensionality matches Settings.gemini_embedding_dimensions
- output is L2-normalized (norm == 1.0 within float tolerance)
- zero-norm output -> raises EmbeddingError
- Gemini model name comes from Settings.gemini_embedding_model
- callers never specify task_type (it's baked into the wrapper)
- Gemini API call failures -> raise EmbeddingError (not raw SDK error)

Tests use a FakeGenaiClient that records calls and returns canned vectors,
so no real API contact happens in the unit suite. Live API tests are
opt-in under tests/integration/test_embeddings_live.py (skipped by default
via the --run-live marker).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest


class FakeEmbeddings:
    def __init__(self, values: list[float]) -> None:
        self._values = values
        self.last_config: Any = None

    async def embed_content(
        self,
        *,
        model: str,
        contents: list[str],
        config: Any = None,
    ) -> Any:
        self.last_config = config
        cls = type("Result", (), {"embeddings": [type("E", (), {"values": list(self._values)})()]})
        return cls()


class FakeModels:
    def __init__(self, values: list[float]) -> None:
        self.embed_content_fn = FakeEmbeddings(values)

    @property
    def embed_content(self) -> Any:
        return self.embed_content_fn.embed_content


class FakeGenaiClient:
    def __init__(self, values: list[float]) -> None:
        self.models = FakeModels(values)
        # Mirror google-genai's Client.aio: production passes `genai_client.aio`
        # to the Embedder, so the fake must also resolve via `.aio.models`.
        self.aio = self


def _config_task_type(fake_embedder: Any) -> str | None:
    """Resolve the task_type actually passed by the wrapper."""
    cfg = fake_embedder.last_config
    if cfg is None:
        return None
    # the live EmbedContentConfig exposes task_type; FakeEmbeddings stores the
    # exact config object, so just read its attribute (works for the real type
    # if it's used, and for our dataclass-style stand-ins).
    return getattr(cfg, "task_type", None)


def _config_dim(fake_embedder: Any) -> int | None:
    cfg = fake_embedder.last_config
    return getattr(cfg, "output_dimensionality", None) if cfg is not None else None


def _make_embedder(client: Any, dim: int = 768, model: str = "gemini-embedding-001") -> Any:
    from int.embeddings import Embedder

    return Embedder(client=client, model=model, dimension=dim)


def test_embed_document_uses_retrieval_document_task_type() -> None:
    fake = FakeGenaiClient(values=[1.0, 0.0, 0.0])
    e = _make_embedder(fake, dim=3)
    import asyncio

    out = asyncio.run(e.embed_document("hello"))
    assert _config_task_type(fake.models.embed_content_fn) == "RETRIEVAL_DOCUMENT"
    assert len(out) == 3


def test_embed_query_uses_retrieval_query_task_type() -> None:
    fake = FakeGenaiClient(values=[1.0, 0.0, 0.0])
    e = _make_embedder(fake, dim=3)
    import asyncio

    asyncio.run(e.embed_query("hello"))
    assert _config_task_type(fake.models.embed_content_fn) == "RETRIEVAL_QUERY"


def test_callers_never_pass_task_type() -> None:
    """Embedder.embed_document / embed_query accept only `content`."""
    import inspect

    from int.embeddings import Embedder

    doc_sig = inspect.signature(Embedder.embed_document)
    qry_sig = inspect.signature(Embedder.embed_query)
    assert set(doc_sig.parameters) == {"self", "content"}
    assert set(qry_sig.parameters) == {"self", "content"}


def test_output_is_l2_normalized_unit_vector() -> None:
    # 768-length vector of varying magnitudes; wrapper must normalize to unit.
    rng = np.random.default_rng(seed=1)
    raw = rng.standard_normal(768).astype(np.float32)
    norm = np.linalg.norm(raw)
    assert norm > 1.0  # sanity
    fake = FakeGenaiClient(values=raw.tolist())
    e = _make_embedder(fake)
    import asyncio

    out = asyncio.run(e.embed_document("anything"))
    assert len(out) == 768
    got = np.array(out, dtype=np.float32)
    assert abs(np.linalg.norm(got) - 1.0) < 1e-5


def test_output_dimensionality_configured() -> None:
    fake = FakeGenaiClient(values=[1.0] * 1536)
    e = _make_embedder(fake, dim=1536)
    import asyncio

    out = asyncio.run(e.embed_query("q"))
    assert _config_dim(fake.models.embed_content_fn) == 1536
    assert len(out) == 1536


def test_model_name_passed_through() -> None:
    fake = FakeGenaiClient(values=[1.0] * 8)
    e = _make_embedder(fake, dim=8, model="custom-embedding-99")
    import asyncio

    asyncio.run(e.embed_document("x"))
    # we can't read the model kwarg from FakeEmbeddings directly without more
    # plumbing, but we can ensure embed_content was called at all; the next test
    # pins the model=value transmission precisely.
    assert fake.models.embed_content_fn.last_config is not None


def test_model_name_actually_sent() -> None:
    # Stronger than above: assert the kwarg landed.
    captured: dict[str, object] = {}

    async def capture(**kwargs: object) -> Any:
        captured.update(kwargs)
        return type(
            "R",
            (),
            {"embeddings": [type("E", (), {"values": [1.0] * 8})()]},
        )()

    class Client:
        def __init__(self) -> None:
            self.models = type("M", (), {})()

    client = Client()
    client.models.embed_content = capture  # plain async callable, not a bound method

    e = _make_embedder(client, dim=8, model="gemini-embedding-001")
    import asyncio

    asyncio.run(e.embed_document("x"))
    assert captured.get("model") == "gemini-embedding-001"


def test_zero_norm_embedding_raises_embedding_error() -> None:
    fake = FakeGenaiClient(values=[0.0] * 8)
    e = _make_embedder(fake, dim=8)
    import asyncio

    with pytest.raises(Exception) as exc:
        asyncio.run(e.embed_document("x"))
    # Must be our typed error, not a generic one (Numpy raises on /0).
    from int.models import EmbeddingError

    assert isinstance(exc.value, EmbeddingError)


def test_gemini_api_failure_raises_embedding_error() -> None:
    # The wrapper must convert any SDK-side failure into EmbeddingError.
    async def failing(**kwargs: object) -> Any:
        raise RuntimeError("network down")

    class Client:
        def __init__(self) -> None:
            self.models = type("M", (), {})()

    client = Client()
    client.models.embed_content = failing

    e = _make_embedder_with_client(client, dim=8)
    import asyncio

    from int.models import EmbeddingError

    with pytest.raises(EmbeddingError):
        asyncio.run(e.embed_document("x"))


def _make_embedder_with_client(
    client: Any, dim: int = 8, model: str = "gemini-embedding-001"
) -> Any:
    from int.embeddings import Embedder

    return Embedder(client=client, model=model, dimension=dim)


def test_constructor_rejects_non_positive_dimension() -> None:
    from int.embeddings import Embedder

    with pytest.raises(ValueError):
        Embedder(client=FakeGenaiClient(values=[1.0]), model="m", dimension=0)
    with pytest.raises(ValueError):
        Embedder(client=FakeGenaiClient(values=[1.0]), model="m", dimension=-3)


def test_length_of_returned_vector_matches_dimension() -> None:
    fake = FakeGenaiClient(values=[2.0] * 512)
    e = _make_embedder(fake, dim=512)
    import asyncio

    out = asyncio.run(e.embed_query("q"))
    assert len(out) == 512
