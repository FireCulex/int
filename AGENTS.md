# AGENTS.md

This file configures agents working in the `int` repository.

## What this project is

`int` is a self-hosted, Dockerized, open-source AI memory system — a local-first alternative to Supermemory. It exposes a project-scoped memory store to AI coding assistants via the Model Context Protocol (MCP), so assistants can recall prior-session learnings without re-running expensive discovery work (e.g. reconstructing a project's tech stack from 20 tool calls).

The server is Python/FastAPI (3.14+, uv-managed). Vector store is Qdrant (separate container). Embeddings are `gemini-embedding-001` via the Gemini API. v1 is single-tenant, no transport encryption.

## Repo layout

- `opencode.json` — root OpenCode config. Delegates runtime behavior to `~/github/agent-skills/AGENTS.md` (read it first).
- `int/` — the MCP server package (FastAPI + MCP tools + Qdrant client + Gemini embedder).
- `int_cli/` — dev/ops CLI for manual inspection (`int-cli add|search|list|delete|read`). Talks to the server over HTTP, same `API_KEY` as any MCP client.
- `tests/` — `unit/` (fast, no network), `integration/` (real Qdrant, mocked Gemini), `e2e/` (live server over HTTP).
- `docs/` — `intent.md` (project intent, source of truth for downstream skills), `spec.md` (technical spec), `deployment.md`.
- `tasks/` — `plan.md` and `todo.md` produced by `planning-and-task-breakdown`.
- `Dockerfile`, `docker-compose.yml`, `.env.example`, `pyproject.toml` — packaging and config.
- `.opencode/` — OpenCode runtime config (plugin dep, project-specific skills go in `.opencode/skills/`).

## Commands

```bash
docker compose up -d                                # server + Qdrant
docker compose logs -f int                          # tail server
docker compose down                                 # stop

uv sync                                             # install deps
uv run uvicorn int.server:app --reload --port 8000   # dev server (without Docker)
uv run pytest                                       # tests
uv run ruff check .                                 # lint
uv run ruff format .                                # format
uv run mypy int                                     # typecheck
uv run int-cli <command> --project <p> ...          # CLI inspection
```

Gate: `uv run ruff check && uv run mypy int && uv run pytest` must all pass before any commit.

## Memory system: two rules for agents working in this repo

1. **Search memory first.** Before running redundant file reads, glob/grep sweeps, or multi-step discovery, call `int.search` for the project. If a prior session already synthesized what you're about to discover, recall it instead of re-discovering.
2. **Save when the cost to re-discover exceeds the cost to save.** A 20-tool-call tech-stack synthesis is worth saving. A single file path lookup is not. Store memory via `int.add` with a `type` tag (`architecture` / `preference` / `command` / `learned-pattern` / `conversation` / `error-solution` / `project-config`). Memories are immutable-append; "edit" = `delete` + `add`.

These are policies for the assistant using `int`, not for the server's code.

## Conventions

- Runtime behavior (skill invocation, lifecycle, persona orchestration) is governed by `~/github/agent-skills/AGENTS.md`. Do not redefine it here.
- Embeddings are L2-normalized in `int/embeddings.py` before storage. `task_type` is set by the `Embedder` wrapper (`embed_document` vs `embed_query`), never by callers.
- Memory `type` is a free string with a *recommended* enum, not a strict enum. Don't enforce it in code.
- All secrets and tunables load from env via `int/config.py`. No hardcoded keys, model names, or endpoints. `.env` is gitignored; `.env.example` is the documentation.
- Changing `GEMINI_EMBEDDING_MODEL` or `GEMINI_EMBEDDING_DIMENSIONS` after memories exist silently invalidates stored vectors. Treat as a fail-fast, not silent corruption.
- Project-specific workflows belong in `.opencode/skills/<name>/SKILL.md`. Add a skill here only when this repo has a workflow agent-skills does not cover. Follow the skill format from `~/github/agent-skills/docs/skill-anatomy.md`.

## Boundaries

- **Always:** run the gate before commit; validate inputs at MCP/HTTP boundaries; TDD for non-trivial store/embedding logic; update `docs/intent.md` and this file before code when schema breaks.
- **Ask first:** swapping the embedding model or changing the collection dimension; adding new MCP tools beyond v1's five (`add`/`delete`/`search`/`list`/`read`); adding any dependency; changing the compose service topology; introducing auth beyond the static shared key.
- **Never:** commit `.env` or real API keys; log raw memory content at INFO (log hashes + metadata only); add per-user accounts or multi-tenancy in v1; add TLS in v1; edit vendor directories (`node_modules`, `.venv`, Qdrant data volume); delete failing tests without replacing them.
