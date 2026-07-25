# Multi-stage build: builder installs deps with uv; runtime copies only the
# venv + app source. Final image is slim and runs as a non-root user.

# --- Builder ---
FROM python:3.14-slim AS builder
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app

# Copy lockfile + manifest first so dep install is cached when only source
# changes. README.md is referenced by hatchling (pyproject `readme=`) and
# must be present; .dockerignore must allow it through.
COPY pyproject.toml uv.lock README.md ./
COPY int/ ./int/
COPY int_cli/ ./int_cli/

# --locked refuses to fall back if the lock is out of sync; --no-dev skips
# the dev/test deps (pytest, ruff, mypy, etc.). --no-install-project then
# `uv sync` again would double-install; we install everything (deps + the
# project itself) in one shot.
RUN uv sync --locked --no-dev

# --- Runtime ---
FROM python:3.14-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# Non-root user; no home dir, no shell login.
RUN groupadd --system int && useradd --system --gid int --no-create-home int

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY int/ ./int/
COPY int_cli/ ./int_cli/

USER int
EXPOSE 8000

# uvicorn entrypoint. cli_app() reads env via Settings at startup, so the
# container needs API_KEY + GEMINI_API_KEY + QDRANT_* from the env / .env file.
CMD ["uvicorn", "int.server:app", "--host", "0.0.0.0", "--port", "8000"]
