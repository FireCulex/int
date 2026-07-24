"""int.models — Pydantic types and typed errors.

Data shapes crossing every boundary (MCP / HTTP / CLI) live here. No bare dicts
leave this module. Memory `type` is a free string with a *recommended* enum
(`architecture` / `preference` / `command` / `learned-pattern` /
`conversation` / `error-solution` / `project-config`), never enforced in code.

Typed errors are distinct from pydantic.ValidationError so the server boundary
can route them to the right envelope (4xx for auth/validation, 5xx for store /
embedding) without introspecting messages.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

# Recommended `type` values. Convention only -- never enforced.
RECOMMENDED_TYPES: frozenset[str] = frozenset(
    {
        "architecture",
        "preference",
        "command",
        "learned-pattern",
        "conversation",
        "error-solution",
        "project-config",
    }
)


class Memory(BaseModel):
    """A single project-scoped memory record.

    Immutable-append: revision is `delete` + `add` (new UUID), never an in-place
    update. Search/ranking lives in the store, not here.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    project: str = Field(..., min_length=1, description="Project scope (never empty).")
    type: str = Field(
        ...,
        min_length=1,
        description=(
            "Free-text type tag. Recommended values: "
            "architecture / preference / command / learned-pattern / "
            "conversation / error-solution / project-config. Not enforced."
        ),
    )
    content: str = Field(..., min_length=1, description="The memory body.")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC creation timestamp (tz-aware).",
    )


class SearchResult(BaseModel):
    """One hit from a search or recall query."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    type: str
    content: str
    score: float = Field(..., ge=0.0, description="Cosine similarity in [0, 1].")


class MemoryMetadata(BaseModel):
    """Metadata-only view of a Memory (used by `int.list` -- no content)."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    type: str
    created_at: datetime


# --- Typed errors ---

# Each error class is intentionally a leaf class -- not a subclass of any other
# custom error -- so `isinstance` at the server boundary is unambiguous.


class EmbeddingError(Exception):
    """The embedding backend (Gemini) failed or returned unusable output."""


class StoreError(Exception):
    """The vector store (Qdrant) failed."""


class AuthError(Exception):
    """Client failed to authenticate (missing or wrong API_KEY)."""


class ValidationError(Exception):
    """Input failed domain validation (distinct from pydantic.ValidationError).

    Use this for semantic checks pydantic can't express (e.g. project name
    characters). Use pydantic for shape/type checks -- it raises its own
    ValidationError automatically.
    """


__all__ = [
    "RECOMMENDED_TYPES",
    "Memory",
    "SearchResult",
    "MemoryMetadata",
    "EmbeddingError",
    "StoreError",
    "AuthError",
    "ValidationError",
]
