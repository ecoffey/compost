from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.table import Table

from compost.cli._helpers import console


@click.group("shims")
def shims_group() -> None:
    """Manage local ingestion shims (Slack, Entire, GitHub webhook)."""


@shims_group.command("up")
@click.pass_context
def shims_up(ctx: click.Context) -> None:
    """Start all configured shims (blocking; Ctrl-C to stop)."""
    from compost.shims.supervisor import load_shims_config, start_shims
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    config = load_shims_config(repo)
    console.print(
        f"[dim]shims up — slack :{config.slack_port}, "
        f"gh_webhook :{config.gh_webhook_port}[/dim]"
    )
    start_shims(repo, config)


@shims_group.command("down")
@click.pass_context
def shims_down(ctx: click.Context) -> None:
    """Send SIGTERM to running shims (reads PID files)."""
    from compost.shims.supervisor import stop_shims
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    stop_shims(repo)
    console.print("[green]✓[/green] shims stopped")


@shims_group.command("status")
@click.pass_context
def shims_status(ctx: click.Context) -> None:
    """Print per-shim PID and status."""
    import os as _os
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    pid_dir = repo / ".compost" / "shims"
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Shim")
    table.add_column("PID")
    table.add_column("Status")
    for name in ("slack_shim", "gh_webhook"):
        pid_file = pid_dir / f"{name}.pid"
        if pid_file.exists():
            pid = pid_file.read_text().strip()
            try:
                _os.kill(int(pid), 0)
                status = "[green]running[/green]"
            except ProcessLookupError:
                status = "[red]dead (stale PID)[/red]"
        else:
            pid = "—"
            status = "[dim]stopped[/dim]"
        table.add_row(name, pid, status)
    console.print(table)
