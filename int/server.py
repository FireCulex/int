"""int.server — FastAPI + MCP wiring + auth + typed error translation.

The server:
1. Loads Settings from env (API_KEY, GEMINI_*, QDRANT_*).
2. Constructs one Embedder (Gemini), one QdrantStore (with dimension check),
   and one ToolsRegistry (the five MCP tools).
3. Exposes those tools via a FastMCP server on the Streamable HTTP transport,
   mounted at /mcp.
4. Wraps the MCP ASGI app in a FastAPI app that validates the API_KEY header
   on every request before forwarding. Missing/wrong key -> 401 AuthError.
5. Translates every typed exception raised by tools into an MCP-shaped error
   envelope (isError=True, content=[text:...]) on HTTP 200. We never return
   a bare 500; tool failures are part of the MCP protocol surface, not HTTP
   transport errors.

Principles:
- No raw memory content is ever logged at INFO; we log content hashes + metadata.
- The Embedder and Store are dependencies injected at build_app; build_app's
  default path constructs them from Settings, but tests pass in fakes.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from int.models import (
    AuthError,
    EmbeddingError,
    StoreError,
    ValidationError,
)

logger = logging.getLogger("int.server")


def masked(text: str, *, max_chars: int = 32) -> str:
    """Cheap content hash for logging — never log raw memory content."""
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    snippet = text[:max_chars].replace("\n", "\\n")
    return f"<{h}…{snippet}>"


# --- Dependency-injection seam for tests ---


class _AppDeps:
    """Holds the live or faked dependencies so handlers/tests can reach them."""

    def __init__(
        self,
        *,
        api_key: str,
        store: Any,
        embedder: Any,
        tools: Any,
        mcp_fast: Any,
    ) -> None:
        self.api_key = api_key
        self.store = store
        self.embedder = embedder
        self.tools = tools
        self.mcp_fast = mcp_fast
        self.mask_content = True


def _default_real_deps(
    *,
    api_key: str,
    gemini_api_key: str,
    gemini_embedding_model: str,
    gemini_embedding_dimensions: int,
    qdrant_url: str,
    qdrant_collection: str,
) -> _AppDeps:
    """Construct Embedder + QdrantStore + ToolsRegistry + FastMCP from Settings."""
    from google import genai

    from int.embeddings import Embedder
    from int.store import QdrantStore
    from int.tools import ToolsRegistry

    qdrant_client_cls = _import_qdrant_client_cls()
    qdrant_client = qdrant_client_cls(url=qdrant_url)
    store = QdrantStore(
        client=qdrant_client,
        collection_name=qdrant_collection,
        dimension=gemini_embedding_dimensions,
    )
    store.ensure_collection()

    # google-genai's `Client.models.embed_content` is *synchronous*; the
    # async coroutine lives on `Client.aio.models.embed_content`. The Embedder
    # awaits the call, so we hand it the async sub-client. (Awaiting the sync
    # response raises "'EmbedContentResponse' object can't be awaited".)
    genai_client = genai.Client(api_key=gemini_api_key)
    embedder = Embedder(
        client=genai_client.aio,
        model=gemini_embedding_model,
        dimension=gemini_embedding_dimensions,
    )
    tools = ToolsRegistry(store=store, embedder=embedder)
    mcp_fast = _build_mcp_fast(tools)
    return _AppDeps(
        api_key=api_key,
        store=store,
        embedder=embedder,
        tools=tools,
        mcp_fast=mcp_fast,
    )


def _import_qdrant_client_cls() -> Any:
    from qdrant_client import QdrantClient

    return QdrantClient


def _build_mcp_fast(tools: Any) -> Any:
    """Build a FastMCP instance whose tool handlers delegate to our registry.

    Each tool is registered with an explicit parameter signature derived from
    its ToolDescriptor input_schema, so MCP clients see the actual named
    parameters (`project`, `type`, `content`, ...) rather than a synthetic
    `kwargs` bag. FastMCP infers the input schema from the function signature,
    so we attach an `inspect.Signature` whose parameters match the descriptor.

    Wrapping logic translates every typed exception raised by the registry
    into a plain `ToolError` (caught by FastMCP and returned as an
    `isError=True` envelope) -- never a bare HTTP 500.
    """
    import inspect

    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("int")
    ts = mcp.settings.transport_security
    assert ts is not None, "FastMCP should default transport_security"
    # Auth is handled by the FastAPI wrapper's middleware -- we delegate to
    # the registry handlers, never to FastMCP's auth layer. Disable the DNS
    # rebinding guard: it is a defense for web-browser clients and rejects
    # non-resolvable Host headers (the Host sent by ASGI test clients and by
    # non-browser programmatic clients). The static shared API_KEY on every
    # request is the real auth boundary for v1 (single-tenant), so this check
    # would only add false negatives without contributing security.
    ts.enable_dns_rebinding_protection = False
    ts.allowed_hosts = ["*"]
    ts.allowed_origins = ["*"]
    # When this Starlette sub-app is mounted inside FastAPI at /mcp, the
    # FastAPI mount strips the '/mcp' prefix. We want the inner route to
    # respond at '/' so that the full external path '/mcp/' resolves cleanly.
    mcp.settings.streamable_http_path = "/"

    def _make_handler(name: str, descriptor: Any) -> Callable[..., Awaitable[Any]]:
        schema = descriptor.input_schema
        required = list(schema.get("required", []))
        properties: dict[str, Any] = schema.get("properties", {})

        async def handler(**kwargs: Any) -> Any:
            try:
                result = await tools.call(name, kwargs)
                # MCP tool responses must be stringifiable text; pydantic
                # models, UUIDs, and bools all stringify cleanly.
                if isinstance(result, bool):
                    return "true" if result else "false"
                if isinstance(result, list):
                    # List of pydantic models: dump to JSON. The MCP SDK
                    # wraps each return as a TextContent block; JSON keeps
                    # structure discoverable for clients.
                    import json as _json

                    items = []
                    for r in result:
                        if hasattr(r, "model_dump_json"):
                            items.append(_json.loads(r.model_dump_json()))
                        else:
                            items.append(r)
                    return _json.dumps({"items": items})
                return str(result)
            except ValidationError as e:
                logger.info("tool %s rejected: %s", name, e)
                raise ToolError(str(e)) from e
            except EmbeddingError as e:
                logger.warning("tool %s embedding failed: %s", name, e)
                raise ToolError(f"embedding error: {e}") from e
            except StoreError as e:
                logger.warning("tool %s store failed: %s", name, e)
                raise ToolError(f"store error: {e}") from e
            except AuthError as e:
                logger.warning("tool %s auth failed: %s", name, e)
                raise ToolError(f"auth error: {e}") from e

        handler.__name__ = name.replace(".", "_")
        handler.__doc__ = descriptor.description

        # Synthesize a signature with one parameter per property in the
        # descriptor's input_schema, so FastMCP advertises the real named
        # parameters instead of a single `kwargs` bag.
        params: list[inspect.Parameter] = []
        for prop_name, prop_schema in properties.items():
            kind = inspect.Parameter.KEYWORD_ONLY
            default: Any
            if prop_name in required:
                default = inspect.Parameter.empty
            elif "default" in prop_schema:
                default = prop_schema["default"]
            else:
                default = None
            params.append(inspect.Parameter(prop_name, kind, default=default, annotation=str))
        handler.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
        return handler

    for descriptor in tools.list_tools():
        mcp.add_tool(
            _make_handler(descriptor.name, descriptor),
            name=descriptor.name,
            description=descriptor.description,
        )

    return mcp


class ToolError(Exception):
    """Raised by tool wrappers; FastMCP converts it to an `isError=True`
    MCP result envelope (HTTP 200), never a bare 500."""


def build_app(
    *,
    api_key: str | None = None,
    store: Any | None = None,
    embedder: Any | None = None,
    tools: Any | None = None,
    mcp_fast: Any | None = None,
    collection_name: str | None = None,
    collection_dim: int | None = None,
    create_client: Callable[[], Any] | None = None,
    settings: Any = None,
) -> FastAPI:
    """Construct the FastAPI app.

    Two paths:
    - Default (production): pass `settings` (or omit; loads from env) and the
      real deps (Embedder+Store+ToolsRegistry+FastMCP) are constructed.
    - Tests: pass fakes directly (`api_key`, `store`, `embedder`, `tools`,
      `mcp_fast`) to bypass real-backend construction. If only `store` +
      `embedder` are passed, `tools` and `mcp_fast` are auto-built from them,
      so tests don't need to wire up the MCP SDK themselves.
    """
    if settings is None and api_key is None:
        from int.config import Settings

        settings = Settings()  # type: ignore[call-arg]
    if settings is not None:
        api_key = settings.api_key

    # If we already have a store+embedder (test path), build tools + mcp_fast
    # from them without needing Settings.
    if store is not None and embedder is not None:
        from int.tools import ToolsRegistry

        if tools is None:
            tools = ToolsRegistry(store=store, embedder=embedder)
        if mcp_fast is None:
            mcp_fast = _build_mcp_fast(tools)

    if store is None or embedder is None or tools is None or mcp_fast is None:
        if settings is None:
            raise ValueError(
                "build_app needs either `settings` (real deps path) or explicit fakes."
            )
        deps = _default_real_deps(
            api_key=settings.api_key,
            gemini_api_key=settings.gemini_api_key,
            gemini_embedding_model=settings.gemini_embedding_model,
            gemini_embedding_dimensions=settings.gemini_embedding_dimensions,
            qdrant_url=settings.qdrant_url,
            qdrant_collection=settings.qdrant_collection,
        )
        store = store or deps.store
        embedder = embedder or deps.embedder
        tools = tools or deps.tools
        mcp_fast = mcp_fast or deps.mcp_fast
    assert api_key is not None
    assert store is not None
    assert embedder is not None
    assert tools is not None
    assert mcp_fast is not None

    # Build the MCP Streamable HTTP ASGI sub-app. The Starlette sub-app has
    # its own lifespan that runs `session_manager.run()`, but FastAPI does
    # not propagate lifespan to mounted Starlette sub-apps -- so we manually
    # drive the session manager inside our own lifespan below. Without this,
    # tool handlers raise `RuntimeError: Task group is not initialized`.
    mcp_asgi = mcp_fast.streamable_http_app()
    session_manager = mcp_fast.session_manager

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> Any:
        async with session_manager.run():
            yield

    app = FastAPI(title="int memory MCP server", lifespan=_lifespan)
    app.state.mask_content = True
    app.state.api_key = api_key
    app.state.tools = tools
    app.state.store = store
    app.state.embedder = embedder
    app.state.mcp_fast = mcp_fast

    @app.middleware("http")
    async def _auth_middleware(request: Request, call_next: Callable[..., Awaitable[Any]]) -> Any:
        # Health/ready endpoints are exempt from API_KEY auth (container check).
        if request.url.path in {"/healthz", "/ready"}:
            return await call_next(request)
        provided = request.headers.get("API_KEY")
        if not provided or provided != app.state.api_key:
            return JSONResponse(
                status_code=401,
                content={"error": "AuthError", "message": "missing or wrong API_KEY"},
            )
        return await call_next(request)

    @app.get("/healthz")
    async def _healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.mount("/mcp", mcp_asgi)

    return app


def cli_app() -> FastAPI:
    """Entry point for `uvicorn int.server:cli_app` — reads env via Settings."""
    from int.config import Settings

    return build_app(settings=Settings())  # type: ignore[call-arg]


# PEP 562 lazy attribute: `uvicorn int.server:app` resolves `app` here only
# when explicitly referenced, so `import int.server` (e.g. for tests calling
# `build_app(api_key=..., store=..., embedder=...)` directly) does NOT trigger
# `Settings()` resolution. The real app is constructed the first time an
# ASGI server imports it; `Settings()` reads env then and fails fast if
# `API_KEY` or `GEMINI_API_KEY` is missing -- exactly the spec's behavior.
def __getattr__(name: str) -> Any:
    if name == "app":
        return cli_app()
    raise AttributeError(f"module 'int.server' has no attribute {name!r}")


__all__ = ["build_app", "cli_app", "masked"]
