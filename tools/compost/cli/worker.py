from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.table import Table

from compost.cli._helpers import console


@click.group("worker")
def worker_group() -> None:
    """Async synthesis worker: drains the on-disk job queue."""


@worker_group.command("up")
@click.pass_context
def worker_up(ctx: click.Context) -> None:
    """Start the synthesis worker (blocking; Ctrl-C to stop)."""
    from compost.worker.worker import run_worker, load_worker_config
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    config = load_worker_config(repo)
    console.print(
        f"[dim]worker up — poll every {config.poll_interval_s}s, "
        f"max retries {config.max_retries}[/dim]"
    )
    run_worker(repo, config)


@worker_group.command("status")
@click.pass_context
def worker_status(ctx: click.Context) -> None:
    """Print queue depths (inbox / processing / done / dead)."""
    from compost.worker.queue import _inbox, _processing, _done, _dead
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Queue")
    table.add_column("Count", justify="right")
    for name, fn in (("inbox", _inbox), ("processing", _processing),
                     ("done", _done), ("dead", _dead)):
        d = fn(repo)
        count = len(list(d.glob("*.json"))) if d.exists() else 0
        table.add_row(name, str(count))
    console.print(table)


@worker_group.command("retry")
@click.option("--job-id", required=True, help="Job ID (hex) to move from dead/ back to inbox/.")
@click.pass_context
def worker_retry(ctx: click.Context, job_id: str) -> None:
    """Move a dead-letter job back to inbox/ for reprocessing."""
    import json as _json
    import os as _os
    from compost.worker.queue import _dead, _inbox
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    dead = _dead(repo)
    matches = list(dead.glob(f"*{job_id}*.json"))
    if not matches:
        console.print(f"[red]No dead-letter job matching '{job_id}'[/red]")
        sys.exit(1)
    if len(matches) > 1:
        console.print(f"[red]Ambiguous job ID — {len(matches)} matches[/red]")
        sys.exit(1)

    src = matches[0]
    data = _json.loads(src.read_text())
    data["retry_count"] = 0
    data["next_retry_at"] = None
    data["last_error"] = None
    _inbox(repo).mkdir(parents=True, exist_ok=True)
    dest = _inbox(repo) / src.name
    dest.write_text(_json.dumps(data))
    _os.rename(src, dest)
    console.print(f"[green]✓[/green] job {job_id[:8]} moved to inbox")
