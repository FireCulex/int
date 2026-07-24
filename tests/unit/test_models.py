"""Tests for int.models — Pydantic types and typed errors.

Validates:
- Memory: required fields, UUID auto-generated, type is a free string (not enum),
  created_at defaults to now (UTC, tz-aware).
- SearchResult: carries content, score, id, type; score is float.
- Typed errors: EmbeddingError, StoreError, AuthError, ValidationError (our own
  -- distinct from pydantic.ValidationError so handlers can disambiguate).
- Errors carry a useful message and are subclasses of Exception.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pydantic
import pytest


def test_memory_constructs_with_required_fields() -> None:
    from int.models import Memory

    m = Memory(project="pianoweb", type="architecture", content="Flask on Python 3.12")
    assert m.project == "pianoweb"
    assert m.type == "architecture"
    assert m.content == "Flask on Python 3.12"


def test_memory_id_auto_generated_uuid() -> None:
    from int.models import Memory

    m1 = Memory(project="p", type="command", content="a")
    m2 = Memory(project="p", type="command", content="b")
    assert isinstance(m1.id, uuid.UUID)
    assert m1.id != m2.id


def test_memory_accepts_explicit_id() -> None:
    from int.models import Memory

    fixed = uuid.uuid4()
    m = Memory(id=fixed, project="p", type="command", content="x")
    assert m.id == fixed


def test_memory_type_is_free_string_not_enum() -> None:
    from int.models import Memory

    # Any string is accepted; the recommended enum is convention, not enforcement.
    m = Memory(project="p", type="custom-type-no-one-has-seen", content="x")
    assert m.type == "custom-type-no-one-has-seen"


def test_memory_created_at_defaults_to_utc_now() -> None:
    from int.models import Memory

    before = datetime.now(UTC)
    m = Memory(project="p", type="command", content="x")
    after = datetime.now(UTC)
    assert before <= m.created_at <= after
    assert m.created_at.tzinfo is not None  # tz-aware, not naive


def test_memory_missing_required_field_raises() -> None:
    from int.models import Memory

    with pytest.raises(pydantic.ValidationError):
        Memory(project="p", type="command")  # type: ignore[call-arg]
    with pytest.raises(pydantic.ValidationError):
        Memory(project="p", content="x")  # type: ignore[call-arg]
    with pytest.raises(pydantic.ValidationError):
        Memory(type="command", content="x")  # type: ignore[call-arg]


def test_memory_empty_project_rejected() -> None:
    from int.models import Memory

    with pytest.raises(pydantic.ValidationError):
        Memory(project="", type="command", content="x")


def test_memory_empty_content_rejected() -> None:
    from int.models import Memory

    with pytest.raises(pydantic.ValidationError):
        Memory(project="p", type="command", content="")


def test_search_result_constructs() -> None:
    from int.models import SearchResult

    r = SearchResult(
        id=uuid.uuid4(),
        type="architecture",
        content="Flask backend",
        score=0.874,
    )
    assert r.score == 0.874
    assert r.type == "architecture"


def test_search_result_score_is_float() -> None:
    from int.models import SearchResult

    r = SearchResult(id=uuid.uuid4(), type="architecture", content="x", score=1)
    assert isinstance(r.score, float)
    assert r.score == 1.0


def test_typed_errors_exist_and_carry_message() -> None:
    from int.models import AuthError, EmbeddingError, StoreError, ValidationError

    for exc_cls in (EmbeddingError, StoreError, AuthError, ValidationError):
        e = exc_cls("boom")
        assert isinstance(e, Exception)
        assert str(e) == "boom"


def test_typed_errors_are_distinct_classes() -> None:
    from int.models import AuthError, EmbeddingError, StoreError, ValidationError

    classes = {EmbeddingError, StoreError, AuthError, ValidationError}
    assert len(classes) == 4
    # None is a subclass of another in the set
    for cls in classes:
        for other in classes:
            if cls is other:
                continue
            assert not issubclass(cls, other)


def test_validation_error_is_not_pydantic_validation_error() -> None:
    """Our own ValidationError must be distinguisable from pydantic's
    so the server boundary can route errors to the right envelope."""
    import pydantic

    from int.models import ValidationError as IntValidationError

    assert IntValidationError is not pydantic.ValidationError
    assert not issubclass(IntValidationError, pydantic.ValidationError)
    assert not issubclass(pydantic.ValidationError, IntValidationError)
