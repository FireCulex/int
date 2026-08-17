# int

Self-hosted, Dockerized, open-source AI memory system. Exposes a project-scoped memory store to AI coding assistants via the Model Context Protocol (MCP), so assistants can recall prior-session learnings without re-running expensive discovery work.

## Status

v1 in progress. Spec at `docs/spec.md`; intent at `docs/intent.md`; task breakdown at `tasks/todo.md`.

## Quick start

```bash
cp .env.example .env          # fill in API_KEY and GEMINI_API_KEY
docker compose up -d          # server + Qdrant
docker compose logs -f int    # tail server
```

Point OpenCode at the server by adding a remote MCP entry to your `opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "int": {
      "type": "remote",
      "url": "http://localhost:8000/mcp",
      "headers": { "API_KEY": "{env:API_KEY}" }
    }
  }
}
```

See `docs/deployment.md` for full setup, CLI usage, and common pitfalls.

## Tools

Tool names are bare (`add`, `search`, ...). The MCP server is registered as
`int`, so MCP clients expose them as `int_add`, `int_search`, `int_list`,
`int_delete`.

| Tool | Inputs | Output |
|---|---|---|
| `add` | `project`, `type`, `content` | `memory_id` |
| `delete` | `memory_id` | `deleted: bool` |
| `search` | `project`, `query`, `limit=5` | ranked `SearchResult[]` |
| `list` | `project` | metadata-only `Memory[]` |

## Stack

Python 3.14, FastAPI + MCP (`mcp` Python SDK, Streamable HTTP), Qdrant (separate container), `gemini-embedding-001` (L2-normalized to 768 dims via MRL), Docker Compose, `uv` for dep management.

## License

Apache-2.0 — see [LICENSE](LICENSE) for details.
