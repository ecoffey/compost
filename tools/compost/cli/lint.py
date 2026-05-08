from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.table import Table

from compost.cli._helpers import console, _load_gitea_client


@click.group("lint")
def lint_group() -> None:
    """Lifecycle lint: surface stale citations, expired research, and orphaned raw files."""


@lint_group.command("run")
@click.option(
    "--since", "since_days", default=90, show_default=True,
    help="Days back for orphaned-raw and false-negative sampler.",
)
@click.option(
    "--emit-pr", "emit_pr", is_flag=True, default=False,
    help="Create a lint/* branch, commit wiki/log.md, push, and open a PR.",
)
@click.pass_context
def lint_run(ctx: click.Context, since_days: int, emit_pr: bool) -> None:
    """Run all lifecycle linters and print a health report."""
    import uuid
    from compost.lint.runner import run_lint
    from compost.lint.report import build_report, append_to_log_md
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    run_id = uuid.uuid4().hex[:8]
    results = run_lint(repo, since_days=since_days, run_id=run_id)
    _render_lint_results(results)

    report = build_report(results, run_id)

    if emit_pr:
        _emit_lint_pr(repo, report, run_id)
    else:
        append_to_log_md(repo, report)
        console.print("[dim]→ wiki/log.md updated (not committed)[/dim]")

    if any(r.status == "error" for r in results):
        sys.exit(1)


@lint_group.command("log")
@click.option("--last", default=10, show_default=True,
              help="Number of recent runs to show.")
@click.pass_context
def lint_log(ctx: click.Context, last: int) -> None:
    """Show recent lint run history."""
    from compost.lint.runner import read_runs
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    runs = read_runs(repo, last=last)
    if not runs:
        console.print("[dim]No lint runs found.[/dim]")
        return

    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Run ID", style="cyan")
    table.add_column("Timestamp")
    table.add_column("Findings", justify="right")
    for r in runs:
        table.add_row(
            (r.get("run_id") or "?")[:8],
            (r.get("ts") or "")[:19].replace("T", " "),
            str(r.get("total_findings", 0)),
        )
    console.print(table)


# ── private helpers ───────────────────────────────────────────────────────────


def _render_lint_results(results: list) -> None:
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Linter")
    table.add_column("Status")
    table.add_column("Findings", justify="right")
    table.add_column("Duration (s)", justify="right")

    for r in results:
        if r.status == "pass":
            status_str = "[green]✓ pass[/green]"
        elif r.status == "error":
            status_str = "[red]✗ error[/red]"
        elif r.status == "warn":
            status_str = "[yellow]⚠ warn[/yellow]"
        else:
            status_str = "[dim]— skipped[/dim]"

        n = str(len(r.findings)) if r.status != "skipped" else "—"
        dur = f"{r.duration_s:.1f}" if r.status != "skipped" else "—"
        table.add_row(r.linter, status_str, n, dur)

    console.print(table)

    all_findings = [f for r in results for f in r.findings]
    if all_findings:
        console.print(f"\n[bold]{len(all_findings)} finding(s):[/bold]")
        for f in all_findings:
            icon = "[red]✗[/red]" if f.severity == "error" else "[yellow]⚠[/yellow]"
            path_note = f" {f.path}" if f.path else ""
            console.print(f"  {icon} [{f.linter}]{path_note}: {f.message}")


def _emit_lint_pr(repo: Path, report: str, run_id: str) -> None:
    from datetime import datetime, timezone
    from compost.lint.report import append_to_log_md
    from compost.ingest.git import create_branch_and_commit, push_branch, default_branch

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    branch = f"lint/{date}-{run_id[:4]}"

    append_to_log_md(repo, report)
    log_md = repo / "wiki" / "log.md"

    create_branch_and_commit(
        repo, branch, [log_md],
        f"lint: weekly report {date} ({run_id})",
    )
    console.print(f"[dim]committed to branch {branch}[/dim]")

    try:
        push_branch(repo, "origin", branch)
    except Exception as e:
        console.print(f"[yellow]⚠ push failed: {e}[/yellow]")
        console.print(
            f"[yellow]Branch '{branch}' created locally. "
            "Push manually and open PR.[/yellow]"
        )
        return

    try:
        client = _load_gitea_client(repo)
        base = default_branch(repo)
        pr = client.open_pr(
            branch, base,
            f"lint: {date}",
            report + "\n\n---\n*Opened by `compost lint run --emit-pr`.*",
        )
        console.print(f"[green]PR opened:[/green] {pr.url}")
    except SystemExit:
        console.print(
            "[yellow]Gitea not configured; branch pushed but no PR created.[/yellow]"
        )
        console.print(
            f"[dim]Run: compost gitea setup, then open a PR for '{branch}'[/dim]"
        )
