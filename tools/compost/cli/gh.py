from __future__ import annotations

import sys
from pathlib import Path

import click

from compost.cli._helpers import console


@click.group("gh")
def gh_group() -> None:
    """GitHub webhook shim helpers."""


@gh_group.command("fake-pr")
@click.option("--branch", required=True, help="PR source branch.")
@click.option("--action", default="opened", show_default=True,
              type=click.Choice(["opened", "labeled", "synchronize"]))
@click.option("--pr-number", default=1, type=int, show_default=True)
@click.option("--repo-name", default="", help="GitHub repo full name (e.g. acme/payments).")
@click.option("--labels", default="compost/synth", show_default=True,
              help="Comma-separated labels.")
@click.pass_context
def gh_fake_pr(ctx: click.Context, branch: str, action: str,
               pr_number: int, repo_name: str, labels: str) -> None:
    """POST a fake GitHub PR event to the running gh_webhook_shim."""
    import httpx
    from compost.shims.supervisor import load_shims_config
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    config = load_shims_config(repo)
    url = f"http://localhost:{config.gh_webhook_port}/webhook"
    payload = {
        "action": action,
        "pr_number": pr_number,
        "branch": branch,
        "labels": [lb.strip() for lb in labels.split(",")],
        "repo_full_name": repo_name,
    }
    try:
        resp = httpx.post(url, json=payload, timeout=5)
        console.print(f"[green]✓[/green] {resp.status_code} {resp.json()}")
    except httpx.ConnectError:
        console.print(f"[red]Could not connect to gh_webhook_shim at {url}[/red]")
        console.print("Is [bold]compost shims up[/bold] running?")
        sys.exit(1)
