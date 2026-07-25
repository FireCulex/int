# Deployment

How to run `int` locally and point OpenCode (or any MCP client) at it.

## Prerequisites

- Docker with Compose v2 (`docker compose version` ≥ 2.20).
- A Gemini API key. Get one at <https://aistudio.google.com/apikey>.

That's it. No Python install required for the normal path — everything runs
inside Docker. The Python toolchain (`uv`) is only needed if you want to run
the dev server or the tests directly on your host (see
[Local dev without Docker](#local-dev-without-docker)).

## Quick start

```bash
git clone https://github.com/<you>/int && cd int
cp .env.example .env
# Edit .env and fill in:
#   API_KEY=<any random shared secret — both server and clients must send this>
#   GEMINI_API_KEY=<your Gemini key>
docker compose up -d --build
```

Both services (`qdrant` and `int`) should reach `healthy` within ~20s on a
warm cache. Verify:

```bash
docker compose ps                # both services should show "(healthy)"
curl http://localhost:8000/healthz   # -> {"status":"ok"}
```

`/healthz` is the only endpoint exempt from auth — use it for liveness
checks and container health.

## Point OpenCode at `int`

Add a remote MCP entry to your `opencode.json` (project-level or `~/.config/opencode/opencode.json`):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "int": {
      "type": "remote",
      "url": "http://localhost:8000/mcp",
      "headers": {
        "API_KEY": "{env:API_KEY}"
      }
    }
  }
}
```

`{env:API_KEY}` tells OpenCode to substitute the host's `API_KEY` environment
variable at request time, so the secret never has to be written into the
config file. Make sure the same `API_KEY` you put in `.env` is exported in
the shell that launches OpenCode:

```bash
export API_KEY="<the same value you set in .env>"
```

Restart OpenCode (or run `opencode mcp list`) and you should see all five
`int.*` tools (`int.add`, `int.delete`, `int.search`, `int.list`,
`int.read`) available to the agent. The repo's `AGENTS.md` already carries
the "search memory first" policy, so the agent will call `int.search` before
re-running expensive discovery work in this project.

### Pointing other MCP clients

Any MCP-compatible client works. Two things are required:

1. The MCP endpoint URL: `http://<host>:8000/mcp`.
2. An `API_KEY` HTTP header on every request, matching the server's `API_KEY`.

For clients that don't speak the Streamable HTTP transport natively, run the
server without Docker and front it with whatever transport the client expects
— v1 ships Streamable HTTP only.

## Using `int-cli` for inspection

The CLI is for humans poking at memories — debugging, ops, one-off imports.
It is not the path the assistant uses (that's MCP). It talks the same MCP
Streamable HTTP transport as OpenCode, with the same `API_KEY` header, so
anything you can do via MCP you can do from the shell.

```bash
# Resolve the running server's API_KEY from .env and reuse it.
export API_KEY="$(grep ^API_KEY= .env | cut -d= -f2-)"
export INT_SERVER_URL="http://localhost:8000/mcp"

# Store something
uv run int-cli add --project myproj --type architecture --content \
  "The backend is FastAPI; the store is Qdrant; embeddings are gemini-embedding-001."

# Semantic search
uv run int-cli search --project myproj --query "what's the backend?"

# Metadata-only listing (no content, no embedding call)
uv run int-cli list --project myproj

# Delete by UUID (idempotent — prints 'false' if the id no longer exists)
uv run int-cli delete --memory-id 550e8400-e29b-41d4-a716-446655440000

# read is a thin pass-through to search in v1 (reserved for future
# summary+read behavior)
uv run int-cli read --project myproj --query "tech stack"
```

CLI flags override env if you need to talk to a different server or use a
different key: `--server-url`, `--api-key`. Run `uv run int-cli --help` for
the full surface.

### Exit codes

The CLI deliberately uses non-zero exit codes that distinguish failure
categories, so it composes well in shell scripts and CI:

| Code | Meaning |
|---|---|
| 0 | Success. |
| 2 | `CliConfigError` — typically `API_KEY` is missing. |
| 3 | `CliAuthError` — server returned 401. |
| 4 | `CliConnectionError` — server unreachable. |
| 5 | `CliRemoteError` — tool ran but returned `isError=True` (e.g. the embedder is offline). |

## Configuration

All configuration is via environment variables, loaded by
`int/config.py` (pydantic-settings). `.env.example` is the canonical
documentation; copy it to `.env` and edit.

| Var | Required | Default | Purpose |
|---|---|---|---|
| `API_KEY` | yes | — | Shared static secret for client→server auth. |
| `GEMINI_API_KEY` | yes | — | Google Gemini API key for `gemini-embedding-001`. |
| `GEMINI_EMBEDDING_MODEL` | no | `gemini-embedding-001` | Embedding model. Switching invalidates stored vectors. |
| `GEMINI_EMBEDDING_DIMENSIONS` | no | `768` | Output dim (MRL truncation of 3072). Changing invalidates stored vectors. |
| `QDRANT_URL` | no | `http://qdrant:6333` | Qdrant endpoint. Hardcoded in compose to the service name. |
| `QDRANT_COLLECTION` | no | `int_memories` | Qdrant collection name. |
| `SERVER_HOST` | no | `0.0.0.0` | FastAPI bind host. |
| `SERVER_PORT` | no | `8000` | FastAPI bind port. |
| `LOG_LEVEL` | no | `INFO` | Logging level. The server logs metadata + content hashes at INFO; never raw memory content. |

### CLI-only env

`INT_SERVER_URL` (default `http://localhost:8000/mcp`) is read by `int_cli`
only — the server never reads it. Same `API_KEY` as the MCP clients.

## Auth model

Static shared-key only, for v1. No TLS, no per-user accounts, no transport
encryption. The server sends plaintext over the local Docker bridge network;
the assumption is that you run `int` on a single machine and only expose port
8000 to localhost.

- Every request to `/mcp/*` requires the `API_KEY` header to match the
  server's `API_KEY`; otherwise the server returns 401.
- `/healthz` is exempt (used by `docker compose` healthcheck and liveness
  probes).
- The CLI and any MCP client (OpenCode, etc.) must send the same `API_KEY`.

This is intentionally simple — v2 should add TLS + richer auth. Do **not**
expose port 8000 to the public internet in v1.

## Common pitfalls

### Changing `GEMINI_EMBEDDING_MODEL` or `GEMINI_EMBEDDING_DIMENSIONS` after memories exist

The vectors already stored in Qdrant were produced by a specific model at
a specific dimension. Switching model or dimension silently invalidates
them — Qdrant will happily return zero-match or garbage results because
the new query vectors are in a different embedding space.

The server fails fast at startup if the existing Qdrant collection's
dimension doesn't match `GEMINI_EMBEDDING_DIMENSIONS` — it raises a
`StoreError` and refuses to start. The safe recovery is:

1. Stop the stack: `docker compose down`.
2. Delete the Qdrant volume to discard the old collection:
   ```bash
   docker compose down -v
   # or, more surgically:
   docker volume rm int_qdrant-data
   ```
3. Update `.env`.
4. `docker compose up -d --build`. The server will auto-create the collection
   at the new dimension.

Note: `docker compose down` (without `-v`) preserves the volume across
restarts — that's what you want for normal restarts, but it means a stale
collection will keep the server from starting after a model swap.

### Qdrant data volume resets

The Qdrant data lives in the named Docker volume `qdrant-data`. It survives
`docker compose down` and `docker compose up -d`. The only operations that
wipe it are:

- `docker compose down -v` (deletes all named volumes)
- `docker volume rm int_qdrant-data` (surgical)
- `docker volume prune` (aggressive — prunes unnamed volumes too)

If you accidentally prune the volume, all memories are gone and there is no
recovery — the server will just auto-create a fresh, empty collection on next
start.

### Embedding outage — graceful behavior

If `GEMINI_API_KEY` is missing/wrong, the Gemini API is rate-limiting, or
the network can't reach `generativelanguage.googleapis.com`, the three
operations that need embeddings (`add`, `search`, `read`) all return a
typed `EmbeddingError` envelope. They do not crash the server.

Crucially, `list` doesn't need an embedding call — it just lists Qdrant
payloads filtered by project — so it keeps working during an embedding
outage. The same is true for `delete` (id lookup only, no embedding).

From the CLI, an `EmbeddingError` surfaces as exit code 5
(`CliRemoteError`). From an MCP client (OpenCode), it surfaces as an
`isError=True` response that the model can read and relay to the user.

### The `int` container exits with a `Settings` validation error on first start

If you `docker compose up -d` before filling in `.env`, or you filled in one
of `API_KEY` / `GEMINI_API_KEY` but left the other blank, the `int`
container will exit immediately with a pydantic `ValidationError` in its
logs:

```
int-1  | pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
int-1  |   api_key
int-1  |     Field required [type=missing, ...]
```

`docker compose ps` will show the service restarting. Fix by completing
`.env` and rerunning `docker compose up -d`.

### Python unit tests fail with `ValidationError` for `API_KEY` missing

If you're running `uv run pytest` locally with a `.env` file present, the
unit-test config tests will *still* pass — the test helper constructs
`Settings(_env_file=None)` so `.env` doesn't leak into the test process. If
you see config tests failing for missing-required, make sure you're not
loading via a different code path (e.g. `Settings()` directly).

### Port conflicts

`docker compose up -d` needs ports `6333` (Qdrant) and `8000` (the server)
free on the host. If those are already bound (often by another Qdrant or by
the dev uvicorn server), `docker compose ps` will show the container as
unhealthy and the host port mapping will fail. Either stop the conflicting
process, or remap the host port in `docker-compose.yml` (e.g.
`"8001:8000"` — and update `INT_SERVER_URL` in your MCP config accordingly).

## Local dev without Docker

For working on the server itself:

```bash
uv sync                                         # install dev+test deps
docker compose up -d qdrant                     # just Qdrant, in Docker
export QDRANT_URL=http://localhost:6333
export API_KEY=<dev-key>
export GEMINI_API_KEY=<your-key>
uv run uvicorn int.server:app --reload --port 8000
```

The gate (must pass before any commit):
```bash
uv run ruff check .
uv run ruff format .
uv run mypy                                      # both int and int_cli
uv run pytest
```

Tests use a `FakeEmbedder` for unit + integration work, and the live Gemini
tests are opt-in via `--run-live` (and require a real `GEMINI_API_KEY`).

## Troubleshooting

| Symptom | First thing to check |
|---|---|
| `docker compose ps` shows `int` restarting | `docker compose logs int` — most likely `API_KEY` or `GEMINI_API_KEY` is missing from `.env`. |
| `curl :8000/healthz` → connection refused | The `int` container isn't up, or port 8000 is bound by something else on the host. |
| OpenCode `int.search` returns `AuthError` | The `API_KEY` env var exported in the shell launching OpenCode doesn't match the server's `API_KEY` in `.env`. |
| `int` logs `Failed to create Qdrant collection` | A prior collection exists at a different dimension. Wipe the volume (`docker compose down -v`) and restart. |
| All embeddings tools return `EmbeddingError` | Likely a Gemini API issue — check `GEMINI_API_KEY`, quota, network reachability to `generativelanguage.googleapis.com`. |
| `int-cli` exits 4 (`CliConnectionError`) | `INT_SERVER_URL` is wrong, the server isn't up, or the host can't reach itself on loopback (firewall / WSL2 quirks). |

## v1 boundaries (what's intentionally not in scope)

These are deliberate non-features for v1; raise an issue before adding any of them:
- TLS / transport encryption.
- Per-user accounts or multi-tenancy.
- Auth schemes beyond the static shared `API_KEY`.
- Local embedding alternatives to Gemini (v2 plan: `bge-m3` with a migration path).
- A second Qdrant collection per project (v1 uses one collection with a `project` payload filter).
