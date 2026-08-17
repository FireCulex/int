# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2025-08-17

### Added
- Initial release of `int` — self-hosted, Dockerized AI memory system
- MCP server exposing four project-scoped tools: `add`, `delete`, `search`, `list`
- FastAPI server with Streamable HTTP transport at `/mcp`
- Static API key authentication on all endpoints
- Qdrant vector store with single collection, project-filtered search
- Gemini embeddings (`gemini-embedding-001`) with L2 normalization to 768 dims via MRL
- `int-cli` for manual inspection (`add`, `delete`, `search`, `list` commands)
- Multi-stage Dockerfile + docker-compose.yml (server + Qdrant)
- Comprehensive test suite (unit, integration, e2e)
- Full documentation: spec, intent, deployment guide, memory policy

### Security
- No TLS in v1 (single-tenant, local-first design)
- Static shared API key authentication
- No raw memory content logged at INFO level

### License
- Apache-2.0 (explicit patent grant for ML/embeddings use cases)

### Infrastructure
- Python 3.14, uv for dependency management
- GitHub Actions ready (CI workflow to be added)
- Health check endpoint at `/healthz`