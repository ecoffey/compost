from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.table import Table

from compost.cli._helpers import console


@click.group("session")
def session_group() -> None:
    """Inspect Claude Code session transcripts."""


@session_group.command("list")
@click.pass_context
def session_list(ctx: click.Context) -> None:
    """List sessions for the current repo."""
    from compost.repo import find_repo_root
    from compost.session import list_sessions

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    sessions = list_sessions(repo)
    if not sessions:
        console.print("[dim]No sessions found.[/dim]")
        return

    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("ID", style="cyan")
    table.add_column("Timestamp")
    for s in sessions:
        ts = (s["ts"] or "")[:19].replace("T", " ")
        table.add_row(s["id"], ts)
    console.print(table)


@session_group.command("show")
@click.argument("session_id")
@click.option("--tools", "tools_only", is_flag=True, help="Show tool calls and results only.")
@click.option("--mcp", "mcp_only", is_flag=True, help="Show MCP tool calls and results only.")
@click.option("--snippet", default=400, show_default=True,
              help="Max chars shown per tool result.")
def session_show(session_id: str, tools_only: bool, mcp_only: bool, snippet: int) -> None:
    """Show a session transcript by ID (prefix match supported)."""
    from compost.session import find_session, format_session

    path = find_session(session_id)
    if path is None:
        console.print(f"[red]Session not found: {session_id}[/red]")
        sys.exit(1)

    console.print(f"[dim]{path}[/dim]\n")
    output = format_session(path, tools_only=tools_only, mcp_only=mcp_only,
                            snippet_len=snippet)
    console.print(output)
