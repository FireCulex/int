"""int.config — environment-driven configuration.

All secrets and tunables load from env via pydantic-settings. No hardcoded keys,
model names, or endpoints. Missing required vars raise a clear ValidationError
at server startup.

See `.env.example` for the documentation; this file is the consumer.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Server configuration loaded from env (or `.env`).

    Required:
        api_key:           shared static secret for client->server auth.
        gemini_api_key:    Google Gemini API key for embeddings.

    Optional (defaults below match the spec and `.env.example`).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Auth ---
    api_key: str = Field(..., description="Shared static secret for client->server auth.")

    # --- Embeddings ---
    gemini_api_key: str = Field(..., description="Google Gemini API key.")
    gemini_embedding_model: str = Field(
        default="gemini-embedding-001",
        description="Gemini embedding model name. Switching invalidates stored vectors.",
    )
    gemini_embedding_dimensions: int = Field(
        default=768,
        description="Output embedding dimension. Changing invalidates stored vectors.",
    )
    embedding_base_delay: float = Field(
        default=2.0,
        description=(
            "Base delay (seconds) for exponential backoff on rate limits. "
            "No max retries, no max delay."
        ),
    )

    # --- Qdrant ---
    qdrant_url: str = Field(
        default="http://qdrant:6333",
        description="Qdrant endpoint (inside compose network).",
    )
    qdrant_collection: str = Field(
        default="int_memories",
        description="Qdrant collection name.",
    )

    # --- Server ---
    server_host: str = Field(default="0.0.0.0", description="FastAPI bind host.")
    server_port: int = Field(default=8000, description="FastAPI bind port.")

    # --- Logging ---
    log_level: str = Field(default="INFO", description="Logging level.")


class CliEnv(BaseSettings):
    """Minimal env view for `int_cli` (client-side settings).

    Deliberately separate from :class:`Settings`: the CLI must resolve
    `API_KEY` without also requiring the server-only `GEMINI_API_KEY`, so
    reusing `Settings` here would make `uv run int-cli ...` fail for anyone
    who has no Gemini key. Every field is optional; `int_cli.client` decides
    what is actually required.

    Reads `os.environ` first and falls back to `.env`, matching how the
    direct-Qdrant subcommands (`create`/`copy`/`purge`/`export`/`import`)
    already load `Settings` -- the CLI should not be half-dotenv, half-env.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: str | None = Field(
        default=None, description="Shared static secret for the int server."
    )
    int_server_url: str | None = Field(
        default=None, description="MCP endpoint URL used by int_cli."
    )
