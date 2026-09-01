# Intent: `int`

Source of truth for the `int` project. Downstream skills (`spec-driven-development`, `planning-and-task-breakdown`, `incremental-implementation`) consume this. Update this file before code when schema or scope breaks.

## Outcome

A self-hosted, Dockerized, open-source AI memory system. RAG-backed. Exposed to AI coding assistants via the Model Context Protocol (MCP), so an assistant can recall prior-session learnings without re-running expensive discovery (e.g. a 20-tool-call tech-stack synthesis stored once, retrieved thereafter).

## User

- **Primary:** the maintainer, running it locally inside OpenCode.
- **Secondary:** GitHub contributors who self-host their own instance.

## Why now

`int` exists to give the same capability without depending on someone else's servers, and to keep the design easy to understand and contribute to.

## Success

An assistant, working in a project, prompted to re-derive something a prior session already discovered, calls `search` first and answers from stored memory instead of re-running the discovery. Concretely: given a stored architecture synthesis for a project, a semantic query returns it in the top 3 results.

## Constraints

- Self-hosted, Dockerized, local.
- Client/server split: memory server runs in a Docker container; OpenCode (the client) connects over HTTP. v1 is single-tenant.
- Env-configured: `SERVER_IP`, `API_KEY` on the client; server-side `GEMINI_API_KEY`, `GEMINI_EMBEDDING_MODEL`, `GEMINI_EMBEDDING_DIMENSIONS`, etc.
- No transport encryption in v1. Static shared-key auth only. TLS and richer auth are v2.
- OpenCode tools are the integration point — the server ships as an MCP server, not a standalone web app.

## Stack decisions (v1)

| Layer | Choice | Rationale |
|---|---|---|
| Server language | Python (3.14+) | Familiarity; first-class Qdrant + Gemini SDK support |
| Web framework | FastAPI + Uvicorn | Async, typed, OpenAPI-friendly |
| MCP transport | Streamable HTTP | Container exposes a port OpenCode connects to (not stdio) |
| Vector store | Qdrant (separate container) | Author has been burned by Chroma; Qdrant's compose story is simpler |
| Embeddings | `gemini-embedding-001` via Gemini API | No local model in v1, smaller image; local fallback (`bge-m3`) deferred to v2 |
| Default dimension | 768 | MRL truncation; minimal MTEB loss vs 3072; configurable via env |
| Embedding tasks | `RETRIEVAL_DOCUMENT` for `add`, `RETRIEVAL_QUERY` for `search` | Set by `Embedder` wrapper, never by callers |
| Normalization | L2-norm every vector before Qdrant | `gemini-embedding-001` does not auto-normalize non-3072 dims |
| Auth | Single shared static key (`API_KEY`), header-based | v2 adds real auth |
| Packaging | Docker, multi-stage; docker-compose for server + Qdrant | |
| Dep mgmt | `uv` (lockfile); `pip` compatible read-only | |

### Tension flagged honestly

`int` depends on Google's Gemini API for embedding generation. The *memory store* (Qdrant) is fully self-hosted; only the *embedder* is external. This tensions the "no reliance on someone else's servers" stance — accepted for v1 in exchange for a small image and simple code. v2 should support a local embedder (`bge-m3`) as an alternative, with a migration path.

## Memory model

- **Schema:** freeform + a `type` tag with a *recommended* enum, not an enforced one. Optional `summary` field (one-line, written by the assistant) for quick scanning in list views. Promote to semi-structured in v2 once recurring patterns ossify.
- **Recommended `type` values:** `architecture`, `preference`, `command`, `learned-pattern`, `conversation`, `error-solution`, `project-config`.
- **Scoping:** per-project. One Qdrant collection, project carried as a payload field with an indexed filter. Per-project collections are a v2 option.
- **Lifecycle:** immutable-append. "Edit" = `delete` + `add`. Revisions get a new UUID.
- **Project creation is CLI-only.** A project is materialized by `int-cli create --project <name>`, which writes a single seed memory (`type="project-config"`, content = creation timestamp). The assistant-facing MCP surface can never invent a project name: `add`, `search`, and `list` reject any project string not already present with `Project 'X' does not exist.` (plus a `Did you mean 'Y'?` hint when a close name exists). This is deliberate — a typo'd project name must fail loudly rather than silently auto-create a polluted project.
- **Tool surface (MCP, project-scoped):** `add`, `delete`, `search`, `list`, `get`. Project-scoped tools (`add`/`search`/`list`) require the project to already exist (created via CLI); `delete`/`get` are keyed by `memory_id` and need no project string.
- **Resource surface (MCP, read-only):** `int://projects` — the sorted project names that have memories (metadata only, no content). Exists so clients can enumerate projects via `list_mcp_resources`/`read_mcp_resource` instead of guessing project names.

## Capture model

Autonomous but rule-governed, not trigger-detected. `AGENTS.md` (this repo's) encodes the salience policy:

1. **Search memory first** before redundant tool calls, glob/grep sweeps, or multi-step discovery.
2. **Save when the cost to re-discover exceeds the cost to save.** A 20-tool-call synthesis is worth saving; a single file path lookup is not.

No forced `[MEMORY TRIGGER DETECTED]` pattern. The assistant decides; the AGENTS.md policy shapes the decision.

## Out of scope (v1)

- Forced memory-trigger pattern — not copied.
- Multi-user SaaS surface (hosted dashboard, billing, accounts).
- Transport encryption (TLS).
- Per-user accounts or multi-tenancy.
- Local embeddings (`bge-m3`) — v2 with a migration path.
- `gemini-embedding-2` migration — tracked as a v2 doc, not auto. Embedding spaces are incompatible; switching requires re-embedding everything.
- Fully-structured memory schema (knowledge graph) — v2.
- Web UI / dashboard — v2.
