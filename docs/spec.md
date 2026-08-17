# Spec: `int` — Self-hosted AI Memory

Companion to `docs/intent.md` (the *why*) and `tasks/plan.md` (the *how*). This file is the technical contract: what's built, the API surface, the verification gates.

## Objective

A self-hosted, Dockerized, open-source AI memory system. The system exposes a project-scoped memory store to AI coding assistants via the Model Context Protocol (MCP), allowing assistants to recall prior-session learnings without re-running expensive discovery work.

**Primary user:** the maintainer, running it locally inside OpenCode.
**Secondary users:** GitHub contributors who self-host their own instance.
**Success:** an assistant working in a project, prompted with "what's the tech stack here?", calls `search` first, hits a stored synthesis from a prior session, and answers without reading a single file.

## Tech Stack

| Layer | Choice |
|---|---|
| Server language | Python (3.14+) |
| Web framework | FastAPI + Uvicorn |
| MCP transport | Streamable HTTP (container exposes a port OpenCode connects to) |
| MCP SDK | `mcp` Python SDK |
| Vector store | Qdrant (separate container in compose) |
| Embeddings | `gemini-embedding-001` via Gemini API (`google-genai` SDK) |
| Default dimension | 768 (MRL truncation of 3072; configurable via `GEMINI_EMBEDDING_DIMENSIONS`) |
| Embedding tasks | `RETRIEVAL_DOCUMENT` for `add`; `RETRIEVAL_QUERY` for `search` (`list` needs no embedding) |
| Normalization | L2-normalize every vector in `int/embeddings.py` before Qdrant (`gemini-embedding-001` does not auto-normalize non-3072 dims) |
| Auth | Static shared key (`API_KEY`), header-based. No encryption in v1. |
| Packaging | Docker, multi-stage build; `docker-compose` for server + Qdrant |
| Dep mgmt | `uv` (lockfile); `pip` compatible read-only fallback |

### Tension flagged honestly

`int` depends on Google's Gemini API for embedding generation. The *memory store* (Qdrant) is fully self-hosted; only the *embedder* is external. Accepted for v1; v2 should support a local embedder (`bge-m3`) with a migration path.

## Commands

```bash
# Dev — Docker path (preferred)
docker compose up -d                                   # start server + Qdrant
docker compose logs -f int                             # tail server
docker compose down                                    # stop everything

# Dev — local Python path (without Docker)
uv sync                                                # install deps
uv run uvicorn int.server:app --reload --port 8000     # run MCP server
uv run pytest                                          # test suite
uv run ruff check .                                    # lint
uv run ruff format .                                   # format
uv run mypy int                                        # typecheck

# CLI inspection (talks to the server over HTTP, same API_KEY as MCP clients)
uv run int-cli add    --project <p> --type architecture --content "..."
uv run int-cli search --project <p> --query "tech stack"
uv run int-cli list   --project <p>
uv run int-cli delete --memory-id <uuid>
```

Gate (must pass before any commit): `uv run ruff check && uv run mypy int && uv run pytest`

## Project Structure

```
int/                                # repo root
├── AGENTS.md                       # search-memory-first rule + salience policy
├── pyproject.toml                  # uv/pip project config; deps; ruff/mypy/pytest config
├── Dockerfile                       # multi-stage Python build (server only; Qdrant is its own image)
├── docker-compose.yml               # int server + Qdrant
├── .env.example                    # all env vars documented; no values
├── docs/
│   ├── intent.md                   # confirmed intent — source of truth for downstream skills
│   ├── spec.md                      # this file
│   └── deployment.md                # how to run it, configure OpenCode, common pitfalls
├── int/                             # the MCP server package
│   ├── __init__.py
│   ├── server.py                    # FastAPI app + MCP server (Streamable HTTP transport)
│   ├── config.py                    # env var loading (pydantic-settings)
│   ├── embeddings.py                # Gemini embedding client; bakes in task_type + L2 norm
│   ├── store.py                     # Qdrant client wrapper: add/delete/search/list
│   ├── models.py                    # Pydantic types: Memory, SearchResult, etc.
│   └── tools.py                     # MCP tool definitions (the 4 tool surface)
├── int_cli/                         # dev/ops CLI for manual inspection
│   ├── __init__.py
│   └── main.py                      # Typer/Click CLI; calls server over HTTP
└── tests/
    ├── conftest.py
    ├── unit/                        # fast, no network — models, store (Qdrant stubbed), CLI args
    ├── integration/                 # real Qdrant container, mocked Gemini — full CRUD paths
    └── e2e/                         # spin server + Qdrant, call MCP tools over HTTP
```

## Code Style

Python, typed, one style across the project. Representative snippet (the `Embedder` wrapper — the one place task_type and normalization live):

```python
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4
from datetime import datetime

import numpy as np
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from int.embeddings import Embedder
from int.store import QdrantStore


class Memory(BaseModel):
    """A single project-scoped memory record. Immutable-append; revise via delete+add."""

    id: UUID = Field(default_factory=uuid4)
    project: str
    type: str  # "architecture" | "preference" | "command" | "learned-pattern" | ...
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Embedder:
    """Wraps Gemini embed_content. Hides task_type and L2 normalization from callers."""

    def __init__(self, client: genai.Client, model: str, dimension: int) -> None:
        self._client = client
        self._model = model
        self._dim = dimension

    async def embed_document(self, content: str) -> list[float]:
        # Used by `add`. task_type=RETRIEVAL_DOCUMENT.
        return await self._embed(content, "RETRIEVAL_DOCUMENT")

    async def embed_query(self, content: str) -> list[float]:
        # Used by `search`. task_type=RETRIEVAL_QUERY.
        return await self._embed(content, "RETRIEVAL_QUERY")

    async def _embed(self, content: str, task_type: str) -> list[float]:
        result = await self._client.models.embed_content(
            model=self._model,
            contents=[content],
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=self._dim,
            ),
        )
        return self._normalize(result.embeddings[0].values)

    @staticmethod
    def _normalize(vec: list[float]) -> list[float]:
        v = np.array(vec, dtype=np.float32)
        n = np.linalg.norm(v)
        if n == 0:
            raise ValueError("zero-norm embedding")
        return (v / n).tolist()
```

Conventions:

- **Typing:** full type hints everywhere. `mypy --strict` passes.
- **Async-first:** server is async; all I/O (`embeddings`, `store`, `tools`) is async end-to-end.
- **Pydantic** for all data crossing boundaries (MCP, HTTP, CLI). No bare dicts.
- **Naming:** snake_case for code; kebab-case for CLI flags; `PascalCase` for models. Memory `type` is a free string with a *recommended* enum, not enforced.
- **Error handling:** raise typed exceptions (`EmbeddingError`, `StoreError`); translate to HTTP status at the server boundary; never swallow.
- **Deps:** `uv` for dev + lockfile. `pip` remains compatible (read-only fallback).
- **Comments:** only when something is genuinely non-obvious; code is the default documentation.

## API Surface — MCP Tools (v1)

All tools are project-scoped. All inputs validated at the MCP boundary; raise typed exceptions → translated to MCP error responses. Tool names are bare (`add`, `search`, ...) — the MCP server's name (`int`) already namespaces them, so MCP clients expose them as `int_add`, `int_search`, etc.

| Tool | Inputs | Output | Description |
|---|---|---|---|
| `add` | `project: str`, `type: str`, `content: str` | `memory_id: str` (UUID) | Embed content as `RETRIEVAL_DOCUMENT`, store in Qdrant, return new ID. |
| `delete` | `memory_id: str` | `deleted: bool` | Remove a memory by ID. Idempotent on missing (returns `false`). |
| `search` | `project: str`, `query: str`, `limit: int = 5` | `list[SearchResult]` (content, score, id, type) | Embed query as `RETRIEVAL_QUERY`, cosine search Qdrant filtered by `project`. |
| `list` | `project: str` | `list[Memory]` (id, type, created_at; **no content**) | List memories in a project — metadata only, no embedding call, no content returned. |

**Auth:** every tool call requires `API_KEY` header matching server's `API_KEY`. Missing/mismatched → 401.

**Error envelope:** typed errors (`EmbeddingError`, `StoreError`, `AuthError`, `ValidationError`) → MCP/HTTP error with code + message. Never bare 500s.

## API Surface — MCP Resources (v1)

The server registers exactly one read-only resource. It exists so MCP clients can enumerate which projects have memories without guessing project names (the store has no list-projects tool). It never exposes memory content.

| URI | MimeType | Read returns |
|---|---|---|
| `int://projects` | `application/json` | `{"projects": ["<sorted unique project names>"]}` |

Implemented in `int/server.py::_register_projects_resource`, backed by `QdrantStore.project_names()` (scroll-all over the collection, payloads only, no vectors). Auth: same `API_KEY` header as tools. Note: FastMCP auto-registers the `resources/list` + `resources/read` protocol handlers unconditionally, so the server *advertises* the resources capability even with zero resources registered; `int://projects` is the one actual entry.

## Env Vars

Loaded via `int/config.py` (pydantic-settings). Fail-fast on missing required values at server startup.

| Var | Required | Default | Used by | Purpose |
|---|---|---|---|---|
| `API_KEY` | yes | — | server, client (CLI) | Shared static secret for client→server auth |
| `GEMINI_API_KEY` | yes | — | server (embedder) | Google Gemini API key |
| `GEMINI_EMBEDDING_MODEL` | no | `gemini-embedding-001` | server (embedder) | Pin model; switching invalidates stored vectors |
| `GEMINI_EMBEDDING_DIMENSIONS` | no | `768` | server (embedder) + Qdrant collection | Output dim; changing invalidates stored vectors |
| `QDRANT_URL` | no | `http://qdrant:6333` | server (store) | Qdrant endpoint (inside compose network) |
| `QDRANT_COLLECTION` | no | `int_memories` | server (store) | Qdrant collection name |
| `SERVER_HOST` | no | `0.0.0.0` | server | FastAPI bind host |
| `SERVER_PORT` | no | `8000` | server | FastAPI bind port |
| `LOG_LEVEL` | no | `INFO` | server | Logging level |

`.env` is gitignored. `.env.example` is the documentation.

## Testing Strategy

**Framework:** `pytest`, `pytest-asyncio`, `httpx` (server tests), `pytest-mock` (stubs). `pytest-cov` for coverage ratchet.

**Levels:**
- **Unit (~70%):** pure logic — model validation, store wrappers with a fake Qdrant client, CLI argument parsing, config loading, embedding wrapper normalization. Fast, no network.
- **Integration (~20%):** real Qdrant (via `testcontainers-python` or pinned `docker-compose.test.yml`), mocked Gemini (`FakeEmbedder` returning deterministic 768-dim vectors). Covers the five CRUD paths end-to-end at the store layer.
- **E2E (~10%):** spin the server + Qdrant, call the MCP tools over HTTP with a real MCP client. Verifies the MCP protocol contract, not AI behavior.

**Coverage:** target 80% on `int/`; no hard gate on `int_cli/`.

**Gemini in tests:** always mocked. Never hit the real API in CI. `FakeEmbedder` fixture returns deterministic L2-normalized 768-dim vectors.

**Qdrant in tests:** fresh collection per test. Either `testcontainers-python` (preferred) or `docker-compose.test.yml`.

**Hard test invariants:**
- Every vector written to Qdrant has `norm == 1.0` within float tolerance. Assertion on every `add`.
- Every `search` result respects the `project` filter — zero cross-project leaks. Integration test stores in A, queries B, asserts empty.

## Boundaries

**Always do:**
- Run `uv run ruff check && uv run mypy int && uv run pytest` before any commit. Green is the gate.
- Load all secrets and tunables from env via `int/config.py`. No hardcoded keys, model names, or endpoints.
- Validate inputs at MCP/HTTP boundaries; raise typed exceptions.
- Write the test *before* the implementation for any non-trivial store/embedding logic (TDD).
- L2-normalize every embedding before Qdrant.
- On schema-breaking changes (collection dimension, memory field names, `type` enum): update `docs/intent.md` + `AGENTS.md` first, then implement.

**Ask first:**
- Swapping the embedding model or changing the collection dimension (silently invalidates stored vectors).
- Adding a new MCP tool outside v1's four (`add`/`delete`/`search`/`list`), or any new MCP resource beyond the single read-only `int://projects` (resources and tools are both MCP surface).
- Adding any dependency to `pyproject.toml`.
- Changing the docker-compose service topology (e.g. embedding Qdrant in the server container).
- Introducing auth beyond the static shared key.

**Never do:**
- Commit `.env` or any real API key. `.env.example` only.
- Log raw memory content at INFO. Log content hashes + metadata only.
- Add per-user accounts or multi-tenancy in v1.
- Add transport encryption (TLS) in v1. Plain HTTP inside the local Docker network is v1 scope.
- Edit vendor directories (`node_modules`, `.venv`, Qdrant data volume).
- Delete failing tests without replacing them.

## Success Criteria

Specific, testable — every line maps to a verification:

1. **Four-tool MCP surface works.** `add`, `delete`, `search`, `list` are all callable from an MCP client against the running server. `search` returns ranked results; `list` returns metadata without content.
2. **Project scoping is enforced.** `search` on project A returns zero hits from project B's stored memories. Stored as a Qdrant payload field with an indexed filter.
3. **Recall without re-discovery.** Given a stored architecture synthesis for a project, a semantic query returns it in the top 3 results with cosine similarity ≥ 0.6. Verified by an integration test using a representative synthesis as fixture content.
4. **Single container dependency.** `docker compose up` from a clean clone brings up a working server + Qdrant within 60s on a warm cache. First-run (incl. Docker image pull) ≤ 5 min.
5. **Env-driven config verified.** Changing `GEMINI_EMBEDDING_DIMENSIONS` creates a new Qdrant collection with that size; changing `API_KEY` rejects clients using the old key. Both covered by integration tests.
6. **Offline-degrade is explicit.** If `GEMINI_API_KEY` is missing or the API is unreachable, `add`/`search` return a typed `EmbeddingError` (not a crash). `list` (no embedding needed) still works.
7. **AGENTS.md policy is in place.** The repo's `AGENTS.md` contains the search-first rule and a salience policy ("save when the cost to re-discover > cost to save"). Read-only check.
8. **CLI inspection works.** All four operations reachable from `int-cli` for debugging/operational access without an MCP client.
9. **Public repo hygiene.** `docker compose up && uv run pytest` works from a fresh clone. No `.env` present in git; `.env.example` documented.
10. **Embeddings are L2-normalized.** Every vector in Qdrant has `norm == 1.0` within float tolerance. Asserted in every integration test that calls `add` + `search`.

## Open Questions (resolved before/during plan)

1. **MCP SDK supports Streamable HTTP?** Spike during plan: 10-line server to verify the pinned `mcp` SDK version exposes Streamable HTTP transport. Fallback: SSE transport (still HTTP, supported).
2. ~~**Gemini embedding dimension**~~ — resolved: default 768, configurable via env, L2-normalized in code.
3. **Qdrant collection lifecycle.** Auto-create collection on server startup if missing, fail-fast with a clear message if it exists at a wrong dimension.
4. **CLI placement.** Same package; separate entry-point via `uv` script (`int-cli`).
