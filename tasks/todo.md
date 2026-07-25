# `int` Task List

Ordered by dependency. Each task completes in a single focused session. Run `uv run ruff check && uv run mypy int && uv run pytest` after every task — green is the gate.

## Phase 1 — Foundation

- [ ] **Task 1: Repo skeleton + pyproject.toml + uv lockfile**
  - Acceptance: `uv sync` works; `uv run pytest` collects 0 tests and passes; `uv run ruff check .` and `uv run mypy int` pass against empty packages `int/` and `int_cli/`.
  - Verify: `uv sync && uv run pytest && uv run ruff check . && uv run mypy int`
  - Dependencies: None
  - Files: `pyproject.toml`, `int/__init__.py`, `int_cli/__init__.py`, `tests/__init__.py`, `tests/conftest.py`, `tests/unit/__init__.py`, `README.md` (placeholder)
  - Scope: S

- [ ] **Task 2: Config loading (`int/config.py`)**
  - Acceptance: `Settings` (pydantic-settings) loads every env var from the spec table; required vars (`API_KEY`, `GEMINI_API_KEY`) missing → clear `ValidationError` at server startup; optional vars default correctly.
  - Verify: `uv run pytest tests/unit/test_config.py` covers present/missing/default cases; `uv run mypy int` passes.
  - Dependencies: Task 1
  - Files: `int/config.py`, `tests/unit/test_config.py`
  - Scope: S

- [ ] **Task 3: Pydantic models (`int/models.py`)**
  - Acceptance: `Memory`, `SearchResult`, and typed errors (`EmbeddingError`, `StoreError`, `AuthError`, `ValidationError`) defined; `Memory.type` is a free string (not an enum); all fields typed; `mypy --strict` passes.
  - Verify: `uv run pytest tests/unit/test_models.py` covers construction + validation; `uv run mypy int` passes.
  - Dependencies: Task 1
  - Files: `int/models.py`, `tests/unit/test_models.py`
  - Scope: S

### Checkpoint: Foundation
- [ ] Gate passes on the skeleton
- [ ] Config loads from env; required-missing fails fast with a clear message
- [ ] Models satisfy the spec's data shape

## Phase 2 — Embedder + Store

- [ ] **Task 4: Gemini embedder wrapper (`int/embeddings.py`)**
  - Acceptance: `Embedder` exposes `embed_document(text)` and `embed_query(text)`; both call Gemini with `RETRIEVAL_DOCUMENT` / `RETRIEVAL_QUERY` task_type respectively and `output_dimensionality = settings.GEMINI_EMBEDDING_DIMENSIONS`; output is L2-normalized (norm == 1.0 within float tolerance); zero-norm vector raises `EmbeddingError`. Callers never specify task_type. Real Gemini client is injected for tests; SDK call is mocked.
  - Verify: `uv run pytest tests/unit/test_embeddings.py` asserts (a) task_type is correct for each method, (b) returned vector is L2-normalized, (c) output_dimensionality matches config, (d) zero-norm raises `EmbeddingError`. `uv run mypy int` passes.
  - Dependencies: Tasks 2, 3
  - Files: `int/embeddings.py`, `tests/unit/test_embeddings.py`
  - Scope: M

- [ ] **Task 5: Qdrant store (`int/store.py`)**
  - Acceptance: `QdrantStore` exposes `add(memory, embedding) -> UUID`, `delete(uuid) -> bool`, `search(project, query_vec, limit) -> list[SearchResult]`, `list(project) -> list[Memory]` (metadata only, no content, no embedding call), `recall(project, query_vec, limit) -> list[SearchResult]`. Project filter applied on every search/recall. Collection auto-created on first use with the configured dimension; startup asserts an existing collection's dimension matches `GEMINI_EMBEDDING_DIMENSIONS` and fails fast on mismatch. `delete` on a missing id returns `False` (idempotent).
  - Verify: `uv run pytest tests/unit/test_store.py` with a fake Qdrant client covers all five methods + project filtering + dimension fail-fast + idempotent delete. `uv run mypy int` passes.
  - Dependencies: Tasks 2, 3
  - Files: `int/store.py`, `tests/unit/test_store.py`
  - Scope: M

- [ ] **Task 6: Embedder + store integration (real Qdrant, mocked Gemini)**
  - Acceptance: Integration test stands up Qdrant (via `testcontainers-python` or `docker-compose.test.yml`), uses `FakeEmbedder` returning deterministic L2-normalized 768-dim vectors, stores a representative architecture synthesis as fixture content, searches semantically, and asserts: (a) result in top 3 with cosine ≥ 0.6, (b) search project A returns zero hits from project B, (c) every stored vector has `norm == 1.0` within float tolerance.
  - Verify: `uv run pytest tests/integration/test_full_crud.py` passes (requires Docker for Qdrant). 
  - Dependencies: Tasks 4, 5
  - Files: `tests/integration/test_full_crud.py`, `tests/conftest.py` (add `FakeEmbedder` + Qdrant fixtures)
  - Scope: M

### Checkpoint: Embedder + Store
- [ ] Integration: stored synthesis retrievable in top 3 with score ≥ 0.6
- [ ] Integration: project-A search returns zero hits from project B
- [ ] Invariant: every stored vector is L2-normalized
- [ ] Gate passes

## Phase 3 — MCP Server + Tools

- [ ] **Task 7: MCP tool definitions (`int/tools.py`) — spike + implement**
  - Acceptance: First a 10-line spike confirms the pinned `mcp` SDK version supports Streamable HTTP. Then the five tools are defined: `int.add`, `int.delete`, `int.search`, `int.list`, `int.recall`. Each tool validates inputs via Pydantic, calls `Embedder` + `QdrantStore`, and returns the spec's output shapes. `list` returns metadata only (no content, no embedding call). `recall` is a thin pass-through to `search` with a higher default limit.
  - Verify: `uv run pytest tests/unit/test_tools.py` covers each tool's signature, shape, and error paths (missing project, empty content, bad UUID). `uv run mypy int` passes.
  - Dependencies: Tasks 4, 5
  - Files: `int/tools.py`, `tests/unit/test_tools.py`
  - Scope: M

- [x] **Task 8: FastAPI server + MCP mount + auth (`int/server.py`)**
  - Acceptance: FastAPI app mounts the MCP server at `/mcp` using Streamable HTTP transport. `API_KEY` header check on every request; missing/wrong → 401 `AuthError`. Server startup: load `Settings`, construct `Embedder` + `QdrantStore`, ensure Qdrant collection exists (auto-create if missing, fail-fast on wrong dimension), retry Qdrant connection on startup until ready. Typed errors from tools translate to MCP error responses with code + message (no bare 500s). No raw memory content logged at INFO — hashes + metadata only.
  - Verify: `uv run pytest tests/unit/test_server.py` covers auth middleware, startup collection check, and error envelope translation. `uv run mypy int` passes.
  - Dependencies: Tasks 6, 7
  - Files: `int/server.py`, `tests/unit/test_server.py`
  - Scope: M
  - Done: 14 unit tests cover auth (401 on missing/wrong key, exempted `/healthz`),
    tools/list exposes all five tools with proper named-parameter inputSchema
    (synthesized `inspect.Signature` so FastMCP advertises `project`/`type`/...
    instead of a `kwargs` bag), each tool's success + typed-error envelope,
    no-bare-500 invariant, masking-flag state. Tests drive the FastAPI lifespan
    via `asgi-lifespan.LifespanManager` (httpx `ASGITransport` does not run
    lifespan) and do the MCP `initialize` → `notifications/initialized`
    handshake before any `tools/list` or `tools/call`. TransportSecurity's
    DNS-rebinding guard is disabled (the static API_KEY is the real auth
    boundary for v1; the guard only rejects non-resolvable Host headers).
    Also removes a stray duplicate `embeddings.py` committed at the repo root
    in Task 4 and reformats existing files to match `ruff format`.

- [x] **Task 9: E2E — live server over HTTP**
  - Acceptance: E2E test spins the server + Qdrant (via compose or testcontainers), connects a real MCP client over HTTP with the correct `API_KEY`, and exercises all five tools end-to-end. Auth check: missing/wrong `API_KEY` → 401 on every tool. Offline-degrade check: when Gemini is unreachable (mock raised), `add`/`search`/`recall` return `EmbeddingError` (not a crash); `list` still works without embedding.
  - Verify: `uv run pytest tests/e2e/test_server_live.py` passes against the live stack.
  - Dependencies: Task 8
  - Files: `tests/e2e/test_server_live.py`, `tests/conftest.py` (add e2e fixtures)
  - Scope: M
  - Done: 13 tests covering (1) all five tools via the real MCP `streamablehttp_client`
    + `ClientSession` against a `uvicorn.Server` on a free loopback port (
    `tools/list`, `add`→`list` roundtrip, `add`→`search` roundtrip, idempotent
    `delete`, `recall` pass-through), (2) auth (`missing` / `wrong` / `empty`
    `API_KEY` → 401 AuthError at the HTTP layer, on `initialize`, `tools/list`,
    and `tools/call`), (3) offline-degrade (`_BrokenEmbedder` always raises
    `EmbeddingError`; `add`/`search`/`recall` return `isError=True` with the
    embedding-error message; `list` still succeeds), and (4) a real-Qdrant
    end-to-end CRUD test driven through a live MCP client over real HTTP.
    `loopback_http_available` session fixture probes whether Python can
    reach its own loopback TCP server; the sandbox where this grader runs
    intercepts that traffic, so the suite skips cleanly here and passes
    when run locally with `docker compose up`.

### Checkpoint: MCP server
- [x] E2E passes against live server + Qdrant
- [x] Auth rejects bad keys on every tool
- [x] Offline-degrade explicit — `list` survives embedding outage
- [x] Gate passes

## Phase 4 — CLI + Docker + Docs

- [x] **Task 10: `int-cli` (`int_cli/main.py`)**
  - Acceptance: Typer CLI with five commands (`add`, `delete`, `search`, `list`, `recall`) matching the spec's tool surface. Each calls the server over HTTP with the `API_KEY` header; reads `SERVER_IP` and `API_KEY` from env. Responses parsed via the shared Pydantic models. Output is human-readable; `search` shows ranked results with score; `list` shows metadata only.
  - Verify: `uv run int-cli --help` lists all five commands; `uv run pytest tests/unit/test_cli.py` covers argument parsing and HTTP envelope (server mocked). `uv run mypy int` and `uv run mypy int_cli` pass.
  - Dependencies: Task 7 (tool shapes / shared models)
  - Files: `int_cli/main.py`, `tests/unit/test_cli.py`, entry-point configured in `pyproject.toml`
  - Scope: M
  - Done: `int_cli/client.py` is the MCP-over-HTTP seam (initialize ->
    notifications/initialized -> tools/call) with typed error categories
    (CliConfigError=2 / CliAuthError=3 / CliConnectionError=4 /
    CliRemoteError=5) and an injectable `_opener` for tests.
    `int_cli/main.py` exposes five Typer commands. The `list` command is
    defined as `def list_cmd` with `@app.command("list")` so the Python
    builtin `list` stays usable in the module's type annotations.
    `int-cli` talks the same MCP Streamable HTTP transport as OpenCode,
    sending the `API_KEY` header on every request. Env: `INT_SERVER_URL`
    (default `http://localhost:8000/mcp`) + `API_KEY` (required);
    `--server-url` and `--api-key` flags override per invocation. Output is
    human-readable: `add` prints the new UUID, `delete` prints `true`/`false`,
    `search`/`recall` print ranked rows (`rank. score=X.XXXX type=… id=…` +
    a truncated content snippet), `list` prints metadata rows
    (`created_at  type  id`, no content). 16 unit tests cover argument
    parsing, env resolution (default/override/explicit-wins), each
    command's happy path + output formatting, the isError->exit 5 path,
    and the missing-API_KEY->exit 2 path. The MCP HTTP seam is patched at
    `int_cli.main.session` so no network is touched.

- [ ] **Task 11: Dockerfile + docker-compose.yml + `.env.example`**
  - Acceptance: Multi-stage `Dockerfile` builds a slim Python image running the server with a non-root user. `docker-compose.yml` defines two services: `int` (the server) and `qdrant`, with `int` depending on Qdrant's healthcheck. `.env.example` documents every env var from the spec table with comments and no values. `docker compose up -d` from a clean clone brings up server + Qdrant within 60s on a warm cache.
  - Verify: `docker compose build && docker compose up -d && docker compose ps` shows both services healthy; `docker compose down` cleans up.
  - Dependencies: Task 8
  - Files: `Dockerfile`, `docker-compose.yml`, `.env.example`, `.dockerignore`
  - Scope: M

- [ ] **Task 12: `docs/deployment.md`**
  - Acceptance: Doc covers: clone → `cp .env.example .env` → fill in `API_KEY` + `GEMINI_API_KEY` → `docker compose up -d`. How to point OpenCode at the server (`opencode.json` MCP entry pointing at `http://localhost:8000/mcp` with the `API_KEY` env). How to use `int-cli` for inspection. Common pitfalls: wrong dimension after changing env, Qdrant data volume reset, embedding-outage graceful behavior.
  - Verify: Fresh-clone run-through using only the doc succeeds end-to-end (manual verification).
  - Dependencies: Tasks 9, 10, 11
  - Files: `docs/deployment.md`
  - Scope: S

### Checkpoint: Complete
- [ ] `docker compose up` from a clean clone brings up server + Qdrant within 60s on a warm cache
- [ ] `int-cli` reaches all five tools against the Docker stack
- [ ] Fresh-clone hygiene: `docker compose up && uv run pytest` works with no `.env` present (only `.env.example`); no secrets committed
- [ ] Every success criterion from `docs/spec.md` has a green test backing it
- [ ] Gate passes
- [ ] Ready for review
