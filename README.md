# int

Self-hosted, Dockerized, open-source AI memory system — a local-first alternative to Supermemory. Exposes a project-scoped memory store to AI coding assistants via the Model Context Protocol (MCP), so assistants can recall prior-session learnings without re-running expensive discovery work.

## Status

v1 in progress. Spec at `docs/spec.md`; intent at `docs/intent.md`; task breakdown at `tasks/todo.md`.

## Quick start

```bash
cp .env.example .env          # fill in API_KEY and GEMINI_API_KEY
docker compose up -d          # server + Qdrant
docker compose logs -f int    # tail server
```

Point OpenCode at the server by adding an MCP server entry to your `opencode.json`:

```json
{
  "mcp": {
    "int": {
      "url": "http://localhost:8000/mcp",
      "env": { "API_KEY": "<your API_KEY>" }
    }
  }
}
```

See `docs/deployment.md` (once written) for full setup and pitfalls.

## Tools

| Tool | Inputs | Output |
|---|---|---|
| `int.add` | `project`, `type`, `content` | `memory_id` |
| `int.delete` | `memory_id` | `deleted: bool` |
| `int.search` | `project`, `query`, `limit=5` | ranked `SearchResult[]` |
| `int.list` | `project` | metadata-only `Memory[]` |
| `int.recall` | `project`, `query`, `limit=5` | ranked `SearchResult[]` |

## Stack

Python 3.12, FastAPI + MCP (`mcp` Python SDK, Streamable HTTP), Qdrant (separate container), `gemini-embedding-001` (L2-normalized to 768 dims via MRL), Docker Compose, `uv` for dep management.

## License

TBD (intended: MIT or Apache-2.0).
