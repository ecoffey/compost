from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.table import Table

from compost.cli._helpers import console, _print_classify_verdict


@click.group("classify")
def classify_group() -> None:
    """Run Tier 1 classifier on raw files."""


@classify_group.command("run")
@click.argument("raw_path", type=click.Path(exists=True, resolve_path=True, path_type=Path))
@click.option("--dry-run", is_flag=True, help="Classify without logging the result.")
@click.pass_context
def classify_run(ctx: click.Context, raw_path: Path, dry_run: bool) -> None:
    """Classify a raw file and print the Tier 1 verdict."""
    from compost.ingest.classifier import classify, log_decision
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    try:
        rel = raw_path.relative_to(repo)
    except ValueError:
        console.print(f"[red]{raw_path} is not under the repo root {repo}[/red]")
        sys.exit(1)

    decision = classify(raw_path, repo)

    if not dry_run:
        log_decision(repo, rel, decision)

    _print_classify_verdict(rel, decision)


@classify_group.command("replay")
@click.option("--since", default="7d", show_default=True,
              help="How far back to scan (e.g. 7d, 30d).")
@click.pass_context
def classify_replay(ctx: click.Context, since: str) -> None:
    """Classify all raw/*.md files modified within a rolling window (dry-run; no logging)."""
    import re
    from datetime import datetime, timedelta, timezone as tz
    from compost.ingest.classifier import classify
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    m = re.fullmatch(r"(\d+)d", since.strip())
    if not m:
        console.print("[red]--since must be in Nd format (e.g. 7d, 30d)[/red]")
        sys.exit(1)

    days = int(m.group(1))
    cutoff_ts = (datetime.now(tz.utc) - timedelta(days=days)).timestamp()

    raw_dir = repo / "raw"
    if not raw_dir.exists():
        console.print("[dim]No raw/ directory found.[/dim]")
        return

    files = sorted(
        (f for f in raw_dir.rglob("*.md") if f.stat().st_mtime >= cutoff_ts),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    if not files:
        console.print("[dim]No files found within window.[/dim]")
        return

    table = Table(show_header=True, box=None, padding=(0, 1))
    table.add_column("path")
    table.add_column("fired")
    table.add_column("triggers")

    for f in files:
        try:
            decision = classify(f, repo)
        except Exception:
            continue
        rel = f.relative_to(repo)
        fired_str = "[green]FIRE[/green]" if decision.fired else "[dim]-[/dim]"
        triggers_str = (
            ", ".join(f"{t.kind}:{t.pattern}" for t in decision.triggers)
            or "(none)"
        )
        table.add_row(str(rel), fired_str, triggers_str)

    console.print(table)
