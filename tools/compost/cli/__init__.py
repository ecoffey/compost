from __future__ import annotations

import sys
from pathlib import Path

import click

from compost.cli._helpers import console, _run_doctor_checks, _render_checks
from compost.cli.session import session_group
from compost.cli.raw import raw_group
from compost.cli.pr import pr_group
from compost.cli.classify import classify_group
from compost.cli.synth import synth_group
from compost.cli.codify import codify_group
from compost.cli.assay import assay_cmd
from compost.cli.checks import checks_group
from compost.cli.worker import worker_group
from compost.cli.shims import shims_group
from compost.cli.slack import slack_group
from compost.cli.entire import entire_group
from compost.cli.gh import gh_group
from compost.cli.gitea import gitea_group


@click.group()
@click.option("--repo", envvar="COMPOST_REPO", default=None,
              help="Path to wiki repo. Falls back to COMPOST_REPO env var then cwd walk.")
@click.pass_context
def main(ctx: click.Context, repo: str | None) -> None:
    ctx.ensure_object(dict)
    ctx.obj["repo"] = Path(repo).resolve() if repo else None


@main.command()
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Verify repo health: qmd collections, frontmatter, CODEOWNERS, Gitea."""
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found in cwd or any parent.[/red]")
        sys.exit(1)

    checks = _run_doctor_checks(repo)
    _render_checks(checks)
    if any(not ok for _, ok, _ in checks):
        sys.exit(1)


@main.command("mcp")
@click.pass_context
def mcp_serve(ctx: click.Context) -> None:
    """Start the MCP server (stdio transport)."""
    import asyncio
    from compost.mcp.server import run_server
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found. Run compost init first.[/red]")
        sys.exit(1)
    asyncio.run(run_server(repo))


@main.command()
@click.argument("path", default=".", type=click.Path())
@click.option("--name", required=True, help="Team name (used as qmd index name).")
def init(path: str, name: str) -> None:
    """Bootstrap a new compost wiki repo at PATH."""
    from compost.bootstrap import bootstrap_repo
    bootstrap_repo(Path(path).resolve(), name)


main.add_command(session_group)
main.add_command(raw_group)
main.add_command(pr_group)
main.add_command(classify_group)
main.add_command(synth_group)
main.add_command(codify_group)
main.add_command(assay_cmd)
main.add_command(checks_group)
main.add_command(worker_group)
main.add_command(shims_group)
main.add_command(slack_group)
main.add_command(entire_group)
main.add_command(gh_group)
main.add_command(gitea_group)
