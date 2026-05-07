from __future__ import annotations

import sys
from pathlib import Path

import click

from compost.cli._helpers import console


@click.group("slack")
def slack_group() -> None:
    """Slack shim helpers."""


@slack_group.command("fake-react")
@click.option("--channel", required=True)
@click.option("--ts", "thread_ts", required=True, help="Slack message timestamp.")
@click.option("--text", required=True, help="Message body.")
@click.option("--emoji", default="wiki", show_default=True)
@click.option("--user", default="cli-test-user", show_default=True)
@click.pass_context
def slack_fake_react(ctx: click.Context, channel: str, thread_ts: str,
                     text: str, emoji: str, user: str) -> None:
    """POST a fake Slack reaction event to the running slack_shim."""
    import httpx
    from compost.shims.supervisor import load_shims_config
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    config = load_shims_config(repo)
    url = f"http://localhost:{config.slack_port}/events"
    payload = {
        "channel": channel,
        "thread_ts": thread_ts,
        "emoji": emoji,
        "text": text,
        "user": user,
    }
    try:
        resp = httpx.post(url, json=payload, timeout=5)
        console.print(f"[green]✓[/green] {resp.status_code} {resp.json()}")
    except httpx.ConnectError:
        console.print(f"[red]Could not connect to slack_shim at {url}[/red]")
        console.print("Is [bold]compost shims up[/bold] running?")
        sys.exit(1)
