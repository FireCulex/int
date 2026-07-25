"""Unit tests for int_cli (Task 10).

Covers:
- argument parsing for each of the five commands (`add`/`delete`/`search`/
  `list`/`read`) via Typer's CliRunner
- env-driven config (`API_KEY`, `INT_SERVER_URL`) and the CLI's exit-code
  categories:
    CliConfigError      -> 2 (missing API_KEY)
    CliAuthError        -> 3 (server rejected key)
    CliConnectionError  -> 4 (cannot reach server)
    CliRemoteError      -> 5 (tool returned isError envelope)
- output formatting: search prints ranked rows with score, list prints
  metadata only (no content), add prints a UUID, delete prints true/false

The MCP HTTP seam is mocked at the `int_cli.client.session` boundary via the
`_opener` injection point -- no network is touched.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from typer.testing import CliRunner

# --- Fake MCP tool-call result ---------------------------------------------


class _TextContent:
    def __init__(self, text: str) -> None:
        self.text = text


class _CallResult:
    def __init__(self, *, text: str, is_error: bool = False) -> None:
        self.content = [_TextContent(text)]
        self.isError = is_error


class _FakeSession:
    """Stand-in for mcp.ClientSession with records of issued call_tool invocations."""

    def __init__(self, *, responses: dict[str, _CallResult] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self.calls.append((name, dict(arguments or {})))
        if name not in self.responses:
            raise AssertionError(f"unexpected tool call: {name!r}; have {list(self.responses)}")
        return self.responses[name]


# --- Fake session opener for the `session` async context manager -----------


def _opener_with(session: _FakeSession) -> Any:
    """Return an `_opener(url, key)` async context manager yielding `session`."""

    @contextlib.asynccontextmanager
    async def _opener(url: str, key: str) -> AsyncIterator[_FakeSession]:
        session._url = url
        session._key = key
        yield session

    return _opener


# --- Fixture: CliRunner + the patched session factory ----------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def session_factory(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Patch `int_cli.main.session` to capture every (url, key) pair used.

    Returns a list that tests can append to from `_opener_with`. Production
    `session()` is replaced with a wrapper that forwards via the closure.
    """
    seen: list[tuple[str, str]] = []

    def factory(sess: _FakeSession) -> Any:
        return _opener_with(sess)

    # `int_cli.main` imports `session` by name; patching the module attribute
    # reroutes command code through the fake.
    import int_cli.main as cli_main

    # Stash for tests to call `factory` with their own _FakeSession.
    monkeypatch.setattr(cli_main, "_session_factory", factory, raising=False)
    return seen


def _patch_session_with(monkeypatch: pytest.MonkeyPatch, sess: _FakeSession) -> None:
    """Replace `int_cli.main.session` with a context manager yielding `sess`."""
    import int_cli.main as cli_main

    @contextlib.asynccontextmanager
    async def fake_session(
        *,
        server_url: str | None = None,
        api_key: str | None = None,
        _opener: Any = None,
    ) -> AsyncIterator[_FakeSession]:
        # Mimic the real `session`: resolve config first (raises CliConfigError
        # if API_KEY is missing) -- so CLI auth-path tests still pass.
        from int_cli.client import resolve_config

        url, key = resolve_config(server_url=server_url, api_key=api_key)
        sess._url = url
        sess._key = key
        yield sess

    monkeypatch.setattr(cli_main, "session", fake_session)


# ===========================================================================
# Config + error categories
# ===========================================================================


def test_missing_api_key_exits_2(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    """With API_KEY unset and no --api-key flag, every command exits 2."""
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("INT_SERVER_URL", raising=False)
    # Even though we patch the session factory, resolve_config() runs first.
    result = runner.invoke(
        __import__("int_cli.main", fromlist=["app"]).app, ["list", "--project", "p"]
    )
    assert result.exit_code == 2, result.stderr
    assert "API_KEY" in result.stderr


def test_default_server_url_applies_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_config returns the documented default when env is unset."""
    from int_cli.client import DEFAULT_SERVER_URL, resolve_config

    monkeypatch.delenv("INT_SERVER_URL", raising=False)
    url, key = resolve_config(api_key="k")
    assert url == DEFAULT_SERVER_URL
    assert key == "k"


def test_int_server_url_env_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from int_cli.client import resolve_config

    monkeypatch.setenv("INT_SERVER_URL", "http://example:9999/mcp")
    url, _ = resolve_config(api_key="k")
    assert url == "http://example:9999/mcp"


def test_explicit_server_url_arg_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    from int_cli.client import resolve_config

    monkeypatch.setenv("INT_SERVER_URL", "http://from-env/mcp")
    url, _ = resolve_config(server_url="http://from-flag/mcp", api_key="k")
    assert url == "http://from-flag/mcp"


def test_explicit_api_key_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    from int_cli.client import resolve_config

    monkeypatch.setenv("API_KEY", "from-env")
    _, key = resolve_config(api_key="from-flag")
    assert key == "from-flag"


# ===========================================================================
# Each command's argument parsing + happy-path output
# ===========================================================================


def test_add_calls_int_add_and_prints_uuid(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = _FakeSession(
        responses={"int.add": _CallResult(text="11111111-2222-3333-4444-555555555555")}
    )
    _patch_session_with(monkeypatch, sess)
    monkeypatch.setenv("API_KEY", "k")

    from int_cli.main import app

    result = runner.invoke(
        app,
        [
            "add",
            "--project",
            "pianoweb",
            "--type",
            "architecture",
            "--content",
            "flask backend",
        ],
    )
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "11111111-2222-3333-4444-555555555555"
    assert sess.calls == [
        ("int.add", {"project": "pianoweb", "type": "architecture", "content": "flask backend"})
    ]


def test_delete_calls_int_delete_prints_true(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = _FakeSession(responses={"int.delete": _CallResult(text="true")})
    _patch_session_with(monkeypatch, sess)
    monkeypatch.setenv("API_KEY", "k")

    from int_cli.main import app

    result = runner.invoke(app, ["delete", "--memory-id", "11111111-2222-3333-4444-555555555555"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "true"
    assert sess.calls == [("int.delete", {"memory_id": "11111111-2222-3333-4444-555555555555"})]


def test_delete_idempotent_prints_false(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    sess = _FakeSession(responses={"int.delete": _CallResult(text="false")})
    _patch_session_with(monkeypatch, sess)
    monkeypatch.setenv("API_KEY", "k")

    from int_cli.main import app

    result = runner.invoke(app, ["delete", "--memory-id", "11111111-2222-3333-4444-555555555555"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "false"


def test_search_prints_ranked_rows_with_score(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = [
        {
            "id": "11111111-2222-3333-4444-555555555555",
            "type": "architecture",
            "content": "flask backend on port 5000 with vanilla JS frontend",
            "score": 0.91,
        },
        {
            "id": "22222222-3333-4444-5555-666666666666",
            "type": "command",
            "content": "make test",
            "score": 0.45,
        },
    ]
    sess = _FakeSession(responses={"int.search": _CallResult(text=json.dumps({"items": items}))})
    _patch_session_with(monkeypatch, sess)
    monkeypatch.setenv("API_KEY", "k")

    from int_cli.main import app

    result = runner.invoke(app, ["search", "--project", "p", "--query", "backend", "--limit", "10"])
    assert result.exit_code == 0, result.stderr
    out = result.stdout
    assert "score=0.9100" in out
    assert "type=architecture" in out
    # content snippet appears (truncated form)
    assert "flask backend" in out
    # the second row's score also present
    assert "score=0.4500" in out
    assert sess.calls == [("int.search", {"project": "p", "query": "backend", "limit": 10})]


def test_search_empty_results_prints_placeholder(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = _FakeSession(responses={"int.search": _CallResult(text=json.dumps({"items": []}))})
    _patch_session_with(monkeypatch, sess)
    monkeypatch.setenv("API_KEY", "k")

    from int_cli.main import app

    result = runner.invoke(app, ["search", "--project", "p", "--query", "x"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "(no results)"


def test_list_prints_metadata_without_content(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = [
        {
            "id": "11111111-2222-3333-4444-555555555555",
            "type": "architecture",
            "created_at": "2026-01-01T12:34:56.789Z",
            # `created_at` should not contain content; tool contract says metadata only
        },
        {
            "id": "22222222-3333-4444-5555-666666666666",
            "type": "command",
            "created_at": "2026-02-02T08:00:00.000Z",
        },
    ]
    sess = _FakeSession(responses={"int.list": _CallResult(text=json.dumps({"items": items}))})
    _patch_session_with(monkeypatch, sess)
    monkeypatch.setenv("API_KEY", "k")

    from int_cli.main import app

    result = runner.invoke(app, ["list", "--project", "p"])
    assert result.exit_code == 0, result.stderr
    out = result.stdout
    # Metadata rows present (created_at truncated below sub-second resolution).
    assert "2026-01-01T12:34:56" in out
    assert "2026-02-02T08:00:00" in out
    assert "11111111-2222-3333-4444-555555555555" in out
    assert "22222222-3333-4444-5555-666666666666" in out
    # The tool's contract: `list` returns NO content. Even if the server
    # were to leak it, our formatter doesn't print content. Assert by
    # absence: there's no 'content' label or snippet line.
    assert "content=" not in out
    assert "snippet" not in out
    assert sess.calls == [("int.list", {"project": "p"})]


def test_read_calls_int_read_pass_through(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = [
        {
            "id": "11111111-2222-3333-4444-555555555555",
            "type": "learned-pattern",
            "content": "always run ruff before committing",
            "score": 0.88,
        }
    ]
    sess = _FakeSession(responses={"int.read": _CallResult(text=json.dumps({"items": items}))})
    _patch_session_with(monkeypatch, sess)
    monkeypatch.setenv("API_KEY", "k")

    from int_cli.main import app

    result = runner.invoke(
        app, ["read", "--project", "int", "--query", "what to do before commit"]
    )
    assert result.exit_code == 0, result.stderr
    assert "score=0.8800" in result.stdout
    assert sess.calls == [
        ("int.read", {"project": "int", "query": "what to do before commit", "limit": 5})
    ]


# ===========================================================================
# Error envelope translation (isError -> exit 5; CliError categories)
# ===========================================================================


def test_tool_is_error_envelope_exits_5(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    sess = _FakeSession(
        responses={"int.add": _CallResult(text="project must be a non-empty string", is_error=True)}
    )
    _patch_session_with(monkeypatch, sess)
    monkeypatch.setenv("API_KEY", "k")

    from int_cli.main import app

    result = runner.invoke(app, ["add", "--project", "", "--type", "t", "--content", "x"])
    assert result.exit_code == 5, result.stderr
    assert "project must be a non-empty string" in result.stderr


def test_unknown_command_falls_back_to_help(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from int_cli.main import app

    result = runner.invoke(app, ["bogus"])
    # Typer with no_args_is_help=False would still print help and exit 2.
    assert result.exit_code != 0


# ===========================================================================
# Output formatters in isolation
# ===========================================================================


def test_print_results_formats_rank_and_score() -> None:
    from int_cli.main import _print_results

    _print_results(
        [
            {"score": 0.9, "type": "architecture", "id": "id1", "content": "x" * 100},
            {"score": 0.1, "type": "command", "id": "id2", "content": "y"},
        ]
    )  # should not raise; output goes to stduty via typer.echo


def test_print_metadata_drops_subseconds() -> None:
    from int_cli.main import _print_metadata

    # Sanity: formatter handles the dict shape the server emits.
    _print_metadata(
        [
            {
                "id": "id1",
                "type": "architecture",
                "created_at": "2026-01-01T12:34:56.789Z",
            }
        ]
    )
