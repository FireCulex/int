"""int_cli.main — Typer CLI for inspecting/manipulating the int memory server.

Four commands, matching the MCP tool surface 1:1:

    int-cli add        --project <p> --type <t>          --content <str>
    int-cli delete     --memory-id <uuid>
    int-cli search     --project <p> --query <q>         [--limit <n>]
    int-cli list       --project <p>

Each command talks to the server over the MCP Streamable HTTP transport and
sends the same `API_KEY` header as any MCP client. Auth failures exit 3;
missing local config exit 2; a tool-level error envelope exits 5; a
connection problem exits 4. Tool success exits 0.

Env vars (overridable via flags on commands that accept them):

    INT_SERVER_URL  -- full URL of the MCP endpoint. Default http://localhost:8000/mcp
    API_KEY         -- shared static secret. Required (no default).

Output is human-readable, not JSON, on purpose: this is the dev/ops
inspection tool. `search` prints ranked rows with score; `list` prints
(id, type, created_at) -- never content (matches the tool's metadata-only
contract).
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

import typer

from int_cli.client import (
    CliError,
    call_add,
    call_delete,
    call_list,
    call_search,
    session,
)

app = typer.Typer(
    name="int-cli",
    help=(
        "Inspect and manipulate the int memory server. Talks to the server "
        "over the MCP Streamable HTTP transport using the same API_KEY header "
        "as any MCP client. Configure via INT_SERVER_URL and API_KEY env."
    ),
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)


# --- Flags shared across commands ------------------------------------------


def _common_options() -> dict[str, Any]:
    """Options that every command accepts: --server-url, --api-key (override env)."""
    return {
        "server_url": typer.Option(
            None,
            "--server-url",
            help="Overrides INT_SERVER_URL. Default: http://localhost:8000/mcp",
            show_default=False,
        ),
        "api_key": typer.Option(
            None,
            "--api-key",
            help="Overrides API_KEY (required if API_KEY env is unset).",
            show_default=False,
        ),
    }


# --- Async lift + error translation ----------------------------------------


def _run(coro: Any) -> Any:
    """Run a coroutine synchronously and translate CliError into a Typer Exit."""
    try:
        return asyncio.run(coro)
    except CliError as e:
        typer.secho(f"error: {e}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=e.exit_code) from e


# --- Commands --------------------------------------------------------------


@app.command(help="Store a memory in a project. Returns the new memory's UUID.")
def add(
    project: Annotated[str, typer.Option("--project", help="Project scoping the memory.")],
    type: Annotated[str, typer.Option("--type", help="Free-text type tag (e.g. architecture).")],
    content: Annotated[str, typer.Option("--content", help="Memory content text.")],
    server_url: Annotated[
        str | None, typer.Option("--server-url", help="Overrides INT_SERVER_URL.")
    ] = None,
    api_key: Annotated[str | None, typer.Option("--api-key", help="Overrides API_KEY.")] = None,
) -> None:
    """Store a memory."""

    async def _go() -> None:
        async with session(server_url=server_url, api_key=api_key) as s:
            mid = await call_add(s, project=project, type_=type, content=content)
        typer.echo(mid)

    _run(_go())


@app.command(help="Delete a memory by UUID. Idempotent: prints 'false' if absent.")
def delete(
    memory_id: Annotated[str, typer.Option("--memory-id", help="Memory UUID to delete.")],
    server_url: Annotated[
        str | None, typer.Option("--server-url", help="Overrides INT_SERVER_URL.")
    ] = None,
    api_key: Annotated[str | None, typer.Option("--api-key", help="Overrides API_KEY.")] = None,
) -> None:
    """Delete a memory."""

    async def _go() -> None:
        async with session(server_url=server_url, api_key=api_key) as s:
            ok = await call_delete(s, memory_id=memory_id)
        typer.echo("true" if ok else "false")

    _run(_go())


@app.command(help="Search a project's memories by semantic query. Prints ranked rows.")
def search(
    project: Annotated[str, typer.Option("--project", help="Project to search.")],
    query: Annotated[str, typer.Option("--query", help="Semantic query.")],
    limit: Annotated[int, typer.Option("--limit", help="Max results (default 5).")] = 5,
    server_url: Annotated[
        str | None, typer.Option("--server-url", help="Overrides INT_SERVER_URL.")
    ] = None,
    api_key: Annotated[str | None, typer.Option("--api-key", help="Overrides API_KEY.")] = None,
) -> None:
    """Search memories."""

    async def _go() -> None:
        async with session(server_url=server_url, api_key=api_key) as s:
            results = await call_search(s, project=project, query=query, limit=limit)
        _print_results(results)

    _run(_go())


@app.command(
    "list",
    help="List all memories in a project. Metadata only -- no content.",
)
def list_cmd(
    project: Annotated[str, typer.Option("--project", help="Project to list.")],
    server_url: Annotated[
        str | None, typer.Option("--server-url", help="Overrides INT_SERVER_URL.")
    ] = None,
    api_key: Annotated[str | None, typer.Option("--api-key", help="Overrides API_KEY.")] = None,
) -> None:
    """List memories (metadata only)."""

    async def _go() -> None:
        async with session(server_url=server_url, api_key=api_key) as s:
            items = await call_list(s, project=project)
        _print_metadata(items)

    _run(_go())


@app.command(help="Export all memories in a project to a Markdown file (direct store access).")
def export(
    project: Annotated[str, typer.Option("--project", help="Project to export.")],
    output: Annotated[str, typer.Option("--output", "-o", help="Output .md file path.")],
    limit: Annotated[
        int, typer.Option("--limit", help="Max memories to export (default 1000).")
    ] = 1000,
    qdrant_url: Annotated[
        str | None, typer.Option("--qdrant-url", help="Qdrant URL (overrides env).")
    ] = None,
) -> None:
    """Export memories to Markdown via direct Qdrant access (bypasses MCP)."""

    async def _go() -> None:
        items = await _export_via_direct_store(project, limit, qdrant_url)
        _write_markdown(output, project, items)

    _run(_go())


# --- Output formatters -----------------------------------------------------


def _print_results(results: list[dict[str, Any]]) -> None:
    """Human-readable ranked rows: rank, score, type, id, content snippet."""
    if not results:
        typer.echo("(no results)")
        return
    for i, r in enumerate(results, start=1):
        score = r.get("score", 0.0)
        type_ = r.get("type", "")
        mid = r.get("id", "")
        content = r.get("content", "")
        snippet = (content[:60] + "…") if len(content) > 60 else content
        snippet = snippet.replace("\n", " ")
        typer.echo(f"{i:>2}.  score={score:.4f}  type={type_}  id={mid}")
        typer.echo(f"     {snippet}")


def _print_metadata(items: list[dict[str, Any]]) -> None:
    """Human-readable metadata rows: created_at, type, id (no content)."""
    if not items:
        typer.echo("(no memories)")
        return
    for it in items:
        created = it.get("created_at", "")
        # Drop sub-seconds for table readability -- keeps ISO 8601 date.
        if isinstance(created, str) and "T" in created:
            created = created.split(".")[0]
        type_ = it.get("type", "")
        mid = it.get("id", "")
        typer.echo(f"  {created}  {type_:<18}  {mid}")


def _write_markdown(output_path: str, project: str, items: list[dict[str, Any]]) -> None:
    """Write memories to a Markdown file."""
    from datetime import datetime
    
    with open(output_path, "w") as f:
        f.write(f"# Memories Export: {project}\n\n")
        f.write(f"Exported: {datetime.now().isoformat()}\n")
        f.write(f"Total memories: {len(items)}\n\n")
        f.write("---\n\n")
        
        if not items:
            f.write("(no memories)\n")
            return
        
        # Sort by created_at descending (newest first)
        sorted_items = sorted(
            items, 
            key=lambda x: x.get("created_at", ""), 
            reverse=True
        )
        
        for item in sorted_items:
            created = item.get("created_at", "")
            if isinstance(created, str) and "T" in created:
                created = created.split(".")[0]
            type_ = item.get("type", "")
            mid = item.get("id", "")
            content = item.get("content", "")
            score = item.get("score")
            
            f.write(f"## {created} — {type_}\n\n")
            f.write(f"**ID:** {mid}  \n")
            if score is not None:
                f.write(f"**Score:** {score:.4f}  \n")
            f.write("\n")
            f.write(f"{content}\n\n")
            f.write("---\n\n")
    
    typer.echo(f"Exported {len(items)} memories to {output_path}")


async def _export_via_direct_store(
    project: str,
    limit: int,
    qdrant_url: str | None,
) -> list[dict[str, Any]]:
    """Export memories by directly accessing Qdrant store (bypasses MCP)."""
    from qdrant_client import QdrantClient

    from int.config import Settings
    from int.store import QdrantStore
    
    # Load settings (uses same env as server)
    settings = Settings()
    
    # Create Qdrant client and store (same as server)
    # Use provided qdrant_url or fall back to settings (which may be docker-internal)
    url = qdrant_url or settings.qdrant_url
    qdrant_client = QdrantClient(url=url)
    store = QdrantStore(
        client=qdrant_client,
        collection_name=settings.qdrant_collection,
        dimension=settings.gemini_embedding_dimensions,
    )
    
    # Get all memories directly from store
    memories = store.get_all(project)
    
    # Sort by created_at descending and limit
    memories.sort(key=lambda m: m.created_at, reverse=True)
    memories = memories[:limit]
    
    # Convert to dict format matching MCP output
    return [
        {
            "id": str(m.id),
            "project": m.project,
            "type": m.type,
            "content": m.content,
            "created_at": m.created_at.isoformat(),
        }
        for m in memories
    ]


def main() -> None:
    """Entry point."""
    app()


if __name__ == "__main__":
    main()
