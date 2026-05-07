from __future__ import annotations

import sys
from pathlib import Path

import click

from compost.cli._helpers import console, _load_gitea_client, _render_check_results, _log_override


@click.group("pr")
def pr_group() -> None:
    """Manage Gitea PRs for raw/* branches."""


@pr_group.command("open")
@click.pass_context
def pr_open(ctx: click.Context) -> None:
    """Print the Gitea PR URL for the current branch."""
    from compost.ingest.git import current_branch
    from compost.gitea.client import GiteaError
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    client = _load_gitea_client(repo)
    branch = current_branch(repo)

    try:
        pr = client.find_pr(branch)
    except GiteaError as e:
        console.print(f"[red]Gitea error: {e}[/red]")
        sys.exit(1)

    if pr is None:
        console.print(f"[red]No open PR for '{branch}'. Did `compost raw add` succeed?[/red]")
        sys.exit(1)

    console.print(f"PR #{pr.number}: {pr.url}  ({pr.state})")


@pr_group.command("merge")
@click.option("--override", is_flag=True, default=False,
              help="Merge despite failing checks. Requires --reason.")
@click.option("--reason", default=None,
              help="Override reason, logged to wiki/log.md. Required with --override.")
@click.option("--no-checks", "skip_checks", is_flag=True, default=False,
              help="Skip all adversarial checks (for raw-only PRs with no wiki edits).")
@click.pass_context
def pr_merge(ctx: click.Context, override: bool, reason: str | None, skip_checks: bool) -> None:
    """Merge the current raw/* branch via Gitea PR, then sync local repo."""
    from compost.ingest.pr import merge_pr
    from compost.gitea.client import GiteaError
    from compost.ingest.git import current_branch, default_branch
    from compost.checks.runner import run_checks, load_checks_config, infer_raw_path, write_check_report
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    if override and not reason:
        console.print("[red]--override requires --reason.[/red]")
        sys.exit(1)

    branch = current_branch(repo)
    base = default_branch(repo)

    failed_check_names: list[str] = []
    if not skip_checks:
        raw_path = infer_raw_path(repo, branch, base)
        config = load_checks_config(repo)
        console.print("[dim]running checks...[/dim]")
        results = run_checks(repo, branch, raw_path, config=config)

        _render_check_results(results)
        write_check_report(repo, branch, results)

        failed = [r for r in results if r.status == "fail"]
        failed_check_names = [r.name for r in failed]
        if failed and not override:
            names = ", ".join(r.name for r in failed)
            console.print(
                f"\n[red]✗ checks failed: {names}[/red]\n"
                "Use [bold]--override --reason \"...\"[/bold] to bypass."
            )
            sys.exit(1)

    client = _load_gitea_client(repo)

    try:
        pr = merge_pr(repo, client)
        console.print(f"[green]merged[/green] (PR #{pr.number})")
    except GiteaError as e:
        console.print(f"[red]Gitea error: {e}[/red]")
        sys.exit(1)

    if override and reason and not skip_checks and failed_check_names:
        _log_override(repo, branch, reason, failed_check_names)
