from __future__ import annotations

import sys
from pathlib import Path

import click

from compost.cli._helpers import console, _render_check_results


@click.group("checks")
def checks_group() -> None:
    """Tier 2.5 adversarial checks: verify wiki edits before merge."""


@checks_group.command("run")
@click.option("--branch", default=None,
              help="Branch to check. Defaults to current branch.")
@click.option("--raw", "raw_rel", default=None,
              help="Triggering raw file (relative to repo). Inferred from branch commits if omitted.")
@click.pass_context
def checks_run(ctx: click.Context, branch: str | None, raw_rel: str | None) -> None:
    """Run all adversarial checks on a branch and write a report."""
    from compost.checks.runner import run_checks, load_checks_config, infer_raw_path, write_check_report
    from compost.ingest.git import current_branch, default_branch
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    if branch is None:
        branch = current_branch(repo)

    base = default_branch(repo)

    if raw_rel:
        raw_path = repo / raw_rel
    else:
        raw_path = infer_raw_path(repo, branch, base)

    config = load_checks_config(repo)
    results = run_checks(repo, branch, raw_path, config=config)

    _render_check_results(results)

    report_path = write_check_report(repo, branch, results)
    console.print(f"[dim]report → {report_path.relative_to(repo)}[/dim]")

    if any(r.status == "fail" for r in results):
        sys.exit(1)
