# Contributing to int

Thank you for your interest in contributing! This document outlines the process and standards for contributing to this project.

## Code of Conduct

By participating, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## Development Setup

```bash
# Clone and enter
git clone https://github.com/your-org/int.git
cd int

# Install dependencies
uv sync

# Run quality gate
uv run ruff check && uv run mypy int && uv run pytest

# Start dev server (requires .env with API_KEY and GEMINI_API_KEY)
uv run uvicorn int.server:app --reload --port 8000

# Or use Docker
cp .env.example .env  # fill in required values
docker compose up -d
```

## Pull Request Process

1. **Fork** the repository
2. **Create a feature branch** from `main`: `git checkout -b feature/your-change`
3. **Make changes** following the guidelines below
4. **Run the quality gate**: `uv run ruff check && uv run mypy int && uv run pytest`
5. **Commit with conventional messages** (see below)
6. **Push and open a PR** against `main`

## Commit Convention

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>: <short description>

[optional body explaining why]

[optional footer]
```

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`

Examples:
```
feat: add project-scoped memory listing via int://projects resource
fix: handle zero-norm embeddings in embedder wrapper
docs: update deployment guide with troubleshooting matrix
```

## Code Standards

- **Python 3.14+** with type hints (mypy strict mode)
- **Ruff** for linting/formatting (`uv run ruff check && uv run ruff format .`)
- **Pytest** for tests (coverage target: 80%)
- **Pydantic** for all data validation
- **No secrets** in code or commits (use `.env`)

## Architecture Constraints (v1)

Before proposing changes, note these deliberate boundaries:

- **No new MCP tools** beyond `add`/`delete`/`search`/`list`
- **No new MCP resources** beyond `int://projects`
- **No per-user accounts or multi-tenancy**
- **No TLS** (single-tenant, local-first)
- **No embedding model changes** without dimension fail-fast migration

See `AGENTS.md` > "Boundaries" for the full list.

## Testing

```bash
# Unit tests (fast, no network)
uv run pytest tests/unit

# Integration tests (real Qdrant, mocked Gemini)
uv run pytest tests/integration

# E2E tests (live server over HTTP)
uv run pytest tests/e2e

# All tests
uv run pytest
```

## Reporting Issues

- Search existing issues first
- Use the issue template
- Include: steps to reproduce, expected vs actual behavior, logs

## Security

Report security vulnerabilities privately via [GitHub Security Advisories](https://github.com/your-org/int/security/advisories) — not public issues.