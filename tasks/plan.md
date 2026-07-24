# Implementation Plan: `int`

## Overview

Build a self-hosted, Dockerized, open-source AI memory system. A FastAPI MCP server backed by Qdrant, embedding via the Gemini API (`gemini-embedding-001`), exposed as five project-scoped tools (`add`/`delete`/`search`/`list`/`recall`) callable from OpenCode. v1 is single-tenant, no TLS, static-key auth.

## Architecture Decisions

- **Client/server split.** Server runs in Docker; OpenCode connects over HTTP. Reason: env vars (`SERVER_IP`/`API_KEY`) only make sense with a network hop.
- **Streamable HTTP MCP transport** (not stdio). Reason: Docker container exposes a port rather than being spawned as a subprocess.
- **Qdrant in a separate container** (not embedded). Reason: matches Qdrant's deployment model; easier to swap/reset.
- **Embeddings via Gemini API** (`gemini-embedding-001`), not local. Reason: smaller image, simpler code. Trade-off: external embedding dependency; v2 has a local fallback path.
- **One Qdrant collection, `project` as payload field with an indexed filter.** Reason: simpler than per-project collections; v2 can promote to per-project if isolation needs grow.
- **Freeform + `type` tag schema.** Reason: avoid ossifying structure too early. `type` is a free string with a recommended enum, not enforced in code.
- **Immutable-append memory lifecycle.** "Edit" = `delete` + `add`. Reason: simpler conflict story.
- **L2-normalize every embedding before Qdrant.** Reason: `gemini-embedding-001` does not auto-normalize non-3072 dims; cosine similarity requires unit vectors.

## Task List

### Phase 1: Foundation (config, models, skeleton)

- [ ] Task 1: Repo skeleton + pyproject.toml + uv lockfile
- [ ] Task 2: Config loading (`int/config.py`) with env validation
- [ ] Task 3: Pydantic models (`int/models.py`)

### Checkpoint: Foundation
- [ ] `uv run ruff check && uv run mypy int && uv run pytest` passes (skeleton)
- [ ] Config loads from env; required vars missing → clear startup error

### Phase 2: Embedder + Store (the vertical slice: store a memory, search it back)

- [ ] Task 4: Gemini embedder wrapper (`int/embeddings.py`) — task_type baked in, L2-normalize
- [ ] Task 5: Qdrant store (`int/store.py`) — `add`, `delete`, `search`, `list`, `recall`
- [ ] Task 6: Embedder + store integration test against real Qdrant (mocked Gemini)

### Checkpoint: Embedder + Store
- [ ] Integration test: add a memory to a project, search, get it back in top 3 with score ≥ 0.6
- [ ] Integration test: search project A returns zero hits from project B
- [ ] Every stored vector has `norm == 1.0` within float tolerance

### Phase 3: MCP server + tools (the vertical slice: callable from an MCP client)

- [ ] Task 7: MCP tool definitions (`int/tools.py`) — the five tools, project-scoped
- [ ] Task 8: FastAPI server + MCP mount (`int/server.py`) with `API_KEY` auth + typed errors
- [ ] Task 9: E2E test — spin server, call all five tools over HTTP with a real MCP client

### Checkpoint: MCP server
- [ ] E2E test passes end-to-end against live server + Qdrant
- [ ] Auth: missing/wrong `API_KEY` → 401 on every tool
- [ ] Offline-degrade: no `GEMINI_API_KEY` → `EmbeddingError` (not crash); `list` still works

### Phase 4: CLI + Docker + docs (operational layer)

- [ ] Task 10: `int-cli` (`int_cli/main.py`) — five commands over HTTP
- [ ] Task 11: Dockerfile (multi-stage) + docker-compose.yml (int server + Qdrant) + `.env.example`
- [ ] Task 12: `docs/deployment.md` (run, configure OpenCode, common pitfalls)

### Checkpoint: Complete
- [ ] `docker compose up` from a clean clone brings up server + Qdrant within 60s on warm cache
- [ ] `int-cli` reaches all five tools against the Docker stack
- [ ] Fresh-clone hygiene: `docker compose up && uv run pytest` works with no `.env` present
- [ ] Ready for review

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| The pinned `mcp` Python SDK doesn't expose Streamable HTTP transport | High — blocks Task 7-8 | 10-line spike at the start of Task 7; fallback is SSE transport (also HTTP). |
| Gemini embedding dimension is set wrong vs the Qdrant collection | High — silent corruption | Fail-fast in `int/store.py` on collection creation; assertion that collection dim == `GEMINI_EMBEDDING_DIMENSIONS` at startup. |
| `gemini-embedding-001` returns non-normalized vectors at 768 dims | High — cosine similarity breaks | Normalize in `int/embeddings.py` before Qdrant; invariant asserted in every `add` integration test. |
| Qdrant container not ready before server boots | Med — startup flake | Healthcheck + retry loop in `int/server.py` startup; or `depends_on: condition: service_healthy` in compose. |
| Tests need Qdrant but CI doesn't run Docker | Med — integration tests can't run | `testcontainers-python` brings up Qdrant on demand; fallback: skip integration tests if Docker not present, run them in CI separately. |
| CLI drifts from server API | Low — surface area is small | CLI calls server via the same HTTP client as MCP; typed responses enforced via shared Pydantic models. |

## Open Questions

- **MCP SDK version + transport confirmation.** Resolved as part of Task 7 spike.
- **Qdrant auto-create collection?** Decision: yes, on server startup, fail-fast if it exists at wrong dimension.
- **CLI placement.** Same package, separate `uv` script entry-point.
