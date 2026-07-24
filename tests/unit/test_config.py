"""Tests for int.config.Settings.

Validates:
- required vars (`API_KEY`, `GEMINI_API_KEY`) missing -> clear error at startup
- optional vars default correctly per the spec table
- types are honored (int for dimensions/port, str elsewhere)
- env loading works via pydantic-settings
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def _settings_from_env() -> object:
    """Construct Settings using only env vars set on the current process."""
    from int.config import Settings

    return Settings()


def test_required_api_key_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.delenv("API_KEY", raising=False)
    with pytest.raises(ValidationError) as exc:
        _settings_from_env()
    assert "api_key" in str(exc.value)


def test_required_gemini_api_key_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "fake-shared-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValidationError) as exc:
        _settings_from_env()
    assert "gemini_api_key" in str(exc.value)


def test_both_required_present_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "shared-secret-123")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret-456")
    s = _settings_from_env()
    assert s.api_key == "shared-secret-123"
    assert s.gemini_api_key == "gemini-secret-456"


def test_optional_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    for var in [
        "GEMINI_EMBEDDING_MODEL",
        "GEMINI_EMBEDDING_DIMENSIONS",
        "QDRANT_URL",
        "QDRANT_COLLECTION",
        "SERVER_HOST",
        "SERVER_PORT",
        "LOG_LEVEL",
    ]:
        monkeypatch.delenv(var, raising=False)
    s = _settings_from_env()
    assert s.gemini_embedding_model == "gemini-embedding-001"
    assert s.gemini_embedding_dimensions == 768
    assert s.qdrant_url == "http://qdrant:6333"
    assert s.qdrant_collection == "int_memories"
    assert s.server_host == "0.0.0.0"
    assert s.server_port == 8000
    assert s.log_level == "INFO"


def test_dimensions_parsed_as_int(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("GEMINI_EMBEDDING_DIMENSIONS", "1536")
    s = _settings_from_env()
    assert s.gemini_embedding_dimensions == 1536
    assert isinstance(s.gemini_embedding_dimensions, int)


def test_dimensions_invalid_string_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("GEMINI_EMBEDDING_DIMENSIONS", "not-an-int")
    with pytest.raises(ValidationError):
        _settings_from_env()


def test_port_parsed_as_int(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("SERVER_PORT", "9000")
    s = _settings_from_env()
    assert s.server_port == 9000
    assert isinstance(s.server_port, int)


def test_model_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("GEMINI_EMBEDDING_MODEL", "experimental-thing")
    s = _settings_from_env()
    assert s.gemini_embedding_model == "experimental-thing"


def test_qdrant_url_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6334")
    s = _settings_from_env()
    assert s.qdrant_url == "http://localhost:6334"


def test_collection_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("QDRANT_COLLECTION", "custom_memories")
    s = _settings_from_env()
    assert s.qdrant_collection == "custom_memories"
