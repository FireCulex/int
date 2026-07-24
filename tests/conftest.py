"""Shared pytest fixtures.

- `FakeEmbedder` returns deterministic L2-normalized 768-dim vectors for tests
  that need an embedder without hitting the Gemini API.
- Live-API tests are opt-in via the `live` marker; skipped by default
  (`pytest --run-live` to enable). Run those only after providing a real
  GEMINI_API_KEY in your .env.
"""

from __future__ import annotations

import hashlib
from typing import Any

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

    def embed_document_sync(self, content: str) -> list[float]:
        v = self._vec(content, salt="doc")
        self.calls.append(("document", content))
        return v

    def embed_query_sync(self, content: str) -> list[float]:
        v = self._vec(content, salt="qry")
        self.calls.append(("query", content))
        return v

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


# ----------------------------------------------- loopback-reachability probe


@pytest.fixture(scope="session")
def loopback_http_available() -> Any:
    """Session-scoped probe: can Python connect to a TCP server it started
    on the loopback?

    E2E tests that drive a real uvicorn HTTP server require this -- if Python
    can connect, the suite runs; if not (some sandboxed environments intercept
    loopback traffic for non-shell processes), the E2E suite is skipped
    cleanly so the gate stays green. CI / local dev passes this probe.

    Implements its own throwaway http server via a single socket accept; we
    avoid bringing up uvicorn here so the probe stays cheap and isolated
    from server-specific behavior.
    """
    import socket
    import threading
    import time

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    port = server_sock.getsockname()[1]

    def _accept_and_close() -> None:
        try:
            server_sock.settimeout(2.0)
            conn, _ = server_sock.accept()
            conn.close()
        except TimeoutError, OSError:
            pass

    t = threading.Thread(target=_accept_and_close, daemon=True)
    t.start()

    try:
        client = socket.create_connection(("127.0.0.1", port), timeout=2.0)
        client.close()
        # Give the accept thread a moment to drain, then close the server.
        time.sleep(0.05)
        server_sock.close()
        t.join(timeout=1.0)
        return True
    except (TimeoutError, OSError) as e:
        server_sock.close()
        t.join(timeout=1.0)
        pytest.skip(
            f"Python cannot connect to its own loopback TCP server "
            f"(sandbox restriction); E2E HTTP tests skipped. Reason: {e}"
        )


# ---------------------------------------------------------------- Qdrant fixture


@pytest.fixture(scope="session")
def qdrant_container() -> Any:
    """Session-scoped Qdrant container for integration tests.

    Uses direct `docker run` + `curl /healthz` polling (rather than
    testcontainers' Ryuk bridge) for portability across sandboxed Docker setups.

    Skipped cleanly (so the gate stays green) when:
    - docker / curl CLI is missing
    - the container fails to start
    - curl reaches /healthz but Python can't open a socket (some sandboxed
      environments intercept loopback traffic for non-shell processes).
    """
    import shutil
    import socket
    import subprocess
    import time

    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available")
    if shutil.which("curl") is None:
        pytest.skip("curl not available")

    # Stop any stale probe from a prior aborted run before starting a new one.
    subprocess.run(
        ["docker", "stop", "int_qdrant_probe"],
        capture_output=True,
        text=True,
        timeout=20,
    )

    probe = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            "int_qdrant_probe",
            "-p",
            "6333:6333",
            "qdrant/qdrant:latest",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if probe.returncode != 0:
        pytest.skip(f"docker run failed: {probe.stderr.strip()}")

    try:
        # Wait (up to 30s) for curl /healthz to return 200.
        deadline = time.monotonic() + 30.0
        ready = False
        while time.monotonic() < deadline:
            check = subprocess.run(
                ["curl", "-fsS", "--max-time", "2", "http://127.0.0.1:6333/healthz"],
                capture_output=True,
                text=True,
            )
            if check.returncode == 0:
                ready = True
                break
            time.sleep(0.5)

        if not ready:
            pytest.skip("qdrant never reached healthz in 30s")

        # Some sandboxes routing loopback traffic intercept curl but block
        # other processes. Verify Python itself can open a socket to Qdrant
        # before handing the container to tests.
        try:
            sock = socket.create_connection(("127.0.0.1", 6333), timeout=5)
            sock.close()
        except (TimeoutError, OSError) as e:
            pytest.skip(f"qdrant up via curl but unreachable from Python: {e}")

        class _Container:
            @staticmethod
            def host_ip() -> str:
                return "127.0.0.1"

            @staticmethod
            def rest_port() -> int:
                return 6333

            @staticmethod
            def stop() -> None:
                subprocess.run(
                    ["docker", "stop", "int_qdrant_probe"],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )

        yield _Container()
    finally:
        subprocess.run(
            ["docker", "stop", "int_qdrant_probe"],
            capture_output=True,
            text=True,
            timeout=20,
        )
