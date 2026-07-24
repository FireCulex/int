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
