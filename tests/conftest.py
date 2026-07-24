"""Shared pytest fixtures.

- `FakeEmbedder` returns deterministic L2-normalized 768-dim vectors for tests
  that need an embedder without hitting the Gemini API.
- Live-API tests are opt-in via the `live` marker; skipped by default
  (`pytest --run-live` to enable). Run those only after providing a real
  GEMINI_API_KEY in your .env.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="run opt-in live-API tests (requires GEMINI_API_KEY in env)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(reason="needs --run-live and a real GEMINI_API_KEY")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


class FakeEmbedder:
    """Deterministic stand-in for int.embeddings.Embedder.

    Returns an L2-normalized `dim`-length vector derived from the content + a
    task-type salt, so query and document embeddings of identical text differ
    (mirroring RETRIEVAL_QUERY vs RETRIEVAL_DOCUMENT).
    """

    def __init__(self, dim: int = 768) -> None:
        self.dim = dim
        self.calls: list[tuple[str, str]] = []

    async def embed_document(self, content: str) -> list[float]:
        self.calls.append(("document", content))
        return self._vec(content, salt="doc")

    async def embed_query(self, content: str) -> list[float]:
        self.calls.append(("query", content))
        return self._vec(content, salt="qry")

    def _vec(self, content: str, salt: str) -> list[float]:
        seed = hashlib.sha256((salt + "|" + content).encode()).digest()
        bytes_needed = self.dim * 4
        buf = b""
        counter = 0
        while len(buf) < bytes_needed:
            buf += hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
            counter += 1
        arr = np.frombuffer(buf[:bytes_needed], dtype=np.uint32).astype(np.float32)
        arr = arr - arr.mean()
        n = np.linalg.norm(arr)
        if n == 0:
            arr[0] = 1.0
            n = 1.0
        return (arr / n).tolist()


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder(dim=768)
