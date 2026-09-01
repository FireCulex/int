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
    summary: Annotated[
        str | None, typer.Option("--summary", help="Optional one-line summary for list views.")
    ] = None,
    server_url: Annotated[
        str | None, typer.Option("--server-url", help="Overrides INT_SERVER_URL.")
    ] = None,
    api_key: Annotated[str | None, typer.Option("--api-key", help="Overrides API_KEY.")] = None,
) -> None:
    """Store a memory."""

    async def _go() -> None:
        async with session(server_url=server_url, api_key=api_key) as s:
            mid = await call_add(s, project=project, type_=type, content=content, summary=summary)
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


@app.command("import", help="Import memories from a Markdown or JSON export file into a project.")
def import_cmd(
    project: Annotated[str, typer.Option("--project", help="Target project to import into.")],
    input: Annotated[str, typer.Option("--input", "-i", help="Input file path (.md or .json).")],
    server_url: Annotated[
        str | None, typer.Option("--server-url", help="Overrides INT_SERVER_URL.")
    ] = None,
    api_key: Annotated[str | None, typer.Option("--api-key", help="Overrides API_KEY.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Parse only, don't import.")] = False,
) -> None:
    """Import memories from an export file using the MCP add tool."""

    async def _go() -> None:
        items = _parse_import_file(input)
        if not items:
            typer.echo("No memories found in import file.")
            return

        typer.echo(f"Found {len(items)} memories to import.")
        if dry_run:
            typer.echo("Dry run: skipping import.")
            for i, item in enumerate(items, 1):
                type_ = item.get("type", "")
                content_preview = item.get("content", "")[:60].replace("\n", " ")
                typer.echo(f"  {i}. type={type_} content={content_preview}...")
            return

        async with session(server_url=server_url, api_key=api_key) as s:
            imported = 0
            failed = 0
            for i, item in enumerate(items, 1):
                type_ = item.get("type", "imported")
                content = item.get("content", "")
                if not content:
                    typer.secho(f"  {i}/{len(items)} SKIP (empty content)", fg=typer.colors.YELLOW)
                    continue
                try:
                    mid = await call_add(s, project=project, type_=type_, content=content)
                    typer.echo(f"  {i}/{len(items)} OK id={mid} type={type_}")
                    imported += 1
                except Exception as e:
                    typer.secho(f"  {i}/{len(items)} FAILED: {e}", fg=typer.colors.RED)
                    failed += 1
            typer.echo(f"\nImport complete: {imported} imported, {failed} failed.")

    _run(_go())


@app.command(
    "copy",
    help=(
        "Copy all memories from one project to another "
        "(direct store, preserves vectors, no embedding)."
    ),
)
def copy_cmd(
    source_project: Annotated[
        str, typer.Option("--source", "-s", help="Source project to copy from.")
    ],
    target_project: Annotated[
        str, typer.Option("--target", "-t", help="Target project to copy into.")
    ],
    qdrant_url: Annotated[
        str | None, typer.Option("--qdrant-url", help="Qdrant URL (overrides env).")
    ] = None,
    preserve_timestamps: Annotated[
        bool,
        typer.Option(
            "--preserve-timestamps/--new-timestamps", help="Keep original created_at."
        ),
    ] = True,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Count only, don't copy.")] = False,
) -> None:
    """Copy memories directly in Qdrant, preserving vectors (bypasses MCP and embedding)."""

    async def _go() -> None:
        from qdrant_client import QdrantClient

        from int.config import Settings
        from int.store import QdrantStore

        settings = Settings()
        url = qdrant_url or settings.qdrant_url
        qdrant_client = QdrantClient(url=url)
        store = QdrantStore(
            client=qdrant_client,
            collection_name=settings.qdrant_collection,
            dimension=settings.gemini_embedding_dimensions,
        )

        # Count first
        items = store.get_all_with_vectors(source_project)
        count = len(items)
        
        if count == 0:
            typer.echo(f"No memories found in project '{source_project}'.")
            return

        typer.echo(f"Found {count} memories in '{source_project}'.")
        
        if dry_run:
            typer.echo("Dry run: skipping copy.")
            return

        # Do the copy
        copied = store.copy_project(
            source_project,
            target_project,
            preserve_timestamps=preserve_timestamps,
        )
        typer.echo(f"Copied {copied} memories to '{target_project}'.")

    _run(_go())


@app.command(
    "create",
    help=(
        "Create a project by writing a seed memory (direct store access). "
        "Projects must exist before the MCP add/search/list tools accept them."
    ),
)
def create_cmd(
    project: Annotated[str, typer.Option("--project", help="Project to create.")],
    qdrant_url: Annotated[
        str | None, typer.Option("--qdrant-url", help="Qdrant URL (overrides env).")
    ] = None,
) -> None:
    """Create a project via direct Qdrant access (bypasses MCP)."""

    async def _go() -> None:
        from datetime import UTC, datetime

        from qdrant_client import QdrantClient

        from int.config import Settings
        from int.models import Memory
        from int.store import QdrantStore

        settings = Settings()
        url = qdrant_url or settings.qdrant_url
        qdrant_client = QdrantClient(url=url)
        store = QdrantStore(
            client=qdrant_client,
            collection_name=settings.qdrant_collection,
            dimension=settings.gemini_embedding_dimensions,
        )

        if project in store.project_names():
            typer.echo(f"Project '{project}' already exists.")
            return

        seed = Memory(
            project=project,
            type="project-config",
            content=f"Project created {datetime.now(UTC).isoformat()}",
        )
        store.add(seed, [0.0] * settings.gemini_embedding_dimensions)
        typer.echo(f"Created project '{project}'.")

    _run(_go())


@app.command("purge", help="Delete ALL memories in a project (direct store access).")
def purge_cmd(
    project: Annotated[str, typer.Option("--project", help="Project to purge.")],
    qdrant_url: Annotated[
        str | None, typer.Option("--qdrant-url", help="Qdrant URL (overrides env).")
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation.")] = False,
) -> None:
    """Purge all memories in a project via direct Qdrant access."""

    if not yes:
        confirm = typer.confirm(f"Delete ALL memories in '{project}'? This cannot be undone.")
        if not confirm:
            typer.echo("Aborted.")
            return

    async def _go() -> None:
        from qdrant_client import QdrantClient

        from int.config import Settings
        from int.store import QdrantStore

        settings = Settings()
        url = qdrant_url or settings.qdrant_url
        qdrant_client = QdrantClient(url=url)
        store = QdrantStore(
            client=qdrant_client,
            collection_name=settings.qdrant_collection,
            dimension=settings.gemini_embedding_dimensions,
        )

        items = store.get_all(project)
        count = len(items)
        if count == 0:
            typer.echo(f"No memories found in project '{project}'.")
            return

        # Delete all by UUID
        deleted = 0
        for mem in items:
            if store.delete(mem.id):
                deleted += 1

        typer.echo(f"Deleted {deleted}/{count} memories from '{project}'.")

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
    """Human-readable metadata rows: created_at, type, id, summary (no content)."""
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
        summary = it.get("summary", "")
        if summary:
            # Truncate summary for table display
            summary_display = (summary[:120] + "…") if len(summary) > 120 else summary
            summary_display = summary_display.replace("\n", " ")
            typer.echo(f"  {created}  {type_:<18}  {mid}  {summary_display}")
        else:
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


def _parse_import_file(input_path: str) -> list[dict[str, Any]]:
    """Parse an import file (Markdown or JSON) and return list of memory dicts."""
    import json
    import re
    from pathlib import Path
    
    path = Path(input_path)
    if not path.exists():
        raise typer.BadParameter(f"File not found: {input_path}")
    
    content = path.read_text(encoding="utf-8")
    
    # Try JSON first
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(content)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "memories" in data:
                return data["memories"]
            if isinstance(data, dict) and "items" in data:
                return data["items"]
            # Single memory object
            return [data]
        except json.JSONDecodeError as e:
            raise typer.BadParameter(f"Invalid JSON: {e}") from e
    
    # Parse Markdown format (as produced by export command)
    # Format:
    # ## {created} — {type}
    # **ID:** {mid}
    # **Score:** {score}  (optional)
    # 
    # {content}
    # ---
    items = []
    
    # Split by --- delimiter
    sections = content.split("\n---\n")
    for section in sections:
        section = section.strip()
        if not section or section.startswith("# Memories Export"):
            continue
        
        lines = section.split("\n")
        if not lines:
            continue
        
        # First non-empty line should be: ## {created} — {type}
        header_idx = 0
        while header_idx < len(lines) and lines[header_idx] == "":
            header_idx += 1
        if header_idx >= len(lines):
            continue
        
        first_line = lines[header_idx]
        match = re.match(r"^##\s+(.+?)\s+—\s+(.+)$", first_line)
        if not match:
            continue
        
        created_str = match.group(1).strip()
        type_ = match.group(2).strip()
        
        # Parse metadata lines (ID, Score) - skip them when finding content
        mid = ""
        content_start_idx = header_idx + 1
        seen_metadata = False
        for i in range(header_idx + 1, len(lines)):
            line = lines[i]
            if line.startswith("**ID:**"):
                mid = line.replace("**ID:**", "").strip()
                seen_metadata = True
            elif line.startswith("**Score:**"):
                seen_metadata = True
            elif line == "" and seen_metadata:
                # Found blank line after metadata - content starts after this
                content_start_idx = i + 1
                break
            elif line != "" and not seen_metadata:
                # Content line before any metadata - treat as content start
                content_start_idx = i
                break
        
        # Content starts after the blank line following metadata
        content_lines = lines[content_start_idx:]
        content = "\n".join(content_lines).strip()
        if content.endswith("---"):
            content = content[:-3].strip()
        
        if content:
            items.append({
                "id": mid,
                "type": type_,
                "content": content,
                "created_at": created_str,
            })
    
    return items


def main() -> None:
    """Entry point."""
    app()


if __name__ == "__main__":
    main()
