# Multi-stage build: keep the final image small.
# Builder installs deps with uv; runtime copies only what's needed.

# --- Builder ---
FROM python:3.12-slim AS builder
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml ./
COPY int/ ./int/
COPY int_cli/ ./int_cli/
RUN uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev

# --- Runtime ---
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"
RUN groupadd --system int && useradd --system --gid int --no-create-home int
WORKDIR /app
COPY --from=builder /app /app
COPY --from=builder /app/.venv /app/.venv
USER int
EXPOSE 8000
CMD ["uvicorn", "int.server:app", "--host", "0.0.0.0", "--port", "8000"]
