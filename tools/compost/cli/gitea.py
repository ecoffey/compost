from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click
import yaml

from compost.cli._helpers import console, _run_doctor_checks, _render_checks


@click.group("gitea")
def gitea_group() -> None:
    """Configure and manage the Gitea integration."""


@gitea_group.command("setup")
@click.option("--url", default="http://localhost:3000", show_default=True,
              help="Gitea base URL.")
@click.option("--owner", required=True, help="Gitea user or org login.")
@click.option("--repo", "repo_name", default=None,
              help="Gitea repo name. Defaults to .compost.yml 'name'.")
@click.option("--token", default=None, envvar="GITEA_TOKEN", help="API token.")
@click.option("--remote", default="origin", show_default=True, help="Git remote name.")
@click.option("--ssh", "use_ssh", is_flag=True, default=False,
              help="Use SSH clone URL for the remote (requires Gitea SSH to be running).")
@click.pass_context
def gitea_setup(ctx: click.Context, url: str, owner: str, repo_name: str | None,
                token: str | None, remote: str, use_ssh: bool) -> None:
    """Bootstrap Gitea integration: create repo, set remote, push main, update config."""
    from compost.gitea.client import GiteaClient, GiteaError
    from compost.ingest.git import push_branch, set_remote_url
    from compost.repo import find_repo_root, load_repo_config

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    if not token:
        console.print("[red]GITEA_TOKEN not set. Export it or pass --token.[/red]")
        sys.exit(1)

    config = load_repo_config(repo)
    gitea_repo_name = repo_name or config["name"]

    client = GiteaClient(url=url, owner=owner, repo=gitea_repo_name, token=token)

    try:
        username = client.get_authenticated_user()
        console.print(f"[green]✓[/green] authenticated as {username}")
    except GiteaError as e:
        console.print(f"[red]Auth failed: {e}[/red]")
        sys.exit(1)

    try:
        client.create_repo(gitea_repo_name)
        console.print(f"[green]✓[/green] repo {owner}/{gitea_repo_name}")
    except GiteaError as e:
        if e.status == 403:
            repo_url = f"{url.rstrip('/')}/{owner}/{gitea_repo_name}"
            console.print(f"[yellow]⚠ Could not auto-create repo (token lacks create permission).[/yellow]")
            console.print(f"  Create it manually at {repo_url} then re-run setup.")
            console.print("  Continuing with remote + config setup...")
        else:
            console.print(f"[red]Repo creation failed: {e}[/red]")
            sys.exit(1)

    try:
        clone_url = client.get_clone_url(gitea_repo_name, ssh=use_ssh)
    except GiteaError:
        console.print("[red]Could not fetch repo clone URL — does the repo exist on Gitea?[/red]")
        sys.exit(1)
    set_remote_url(repo, remote, clone_url)
    console.print(f"[green]✓[/green] remote '{remote}' → {clone_url}")

    try:
        push_branch(repo, remote, "main")
        console.print(f"[green]✓[/green] pushed main")
    except click.UsageError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    config["gitea"] = {"url": url, "owner": owner, "repo": gitea_repo_name}
    config_path = repo / ".compost.yml"
    with config_path.open("w") as f:
        yaml.dump(config, f, default_flow_style=False)
    console.print("[green]✓[/green] .compost.yml updated with gitea config")

    subprocess.run(["git", "add", str(config_path)], cwd=repo, check=True, capture_output=True)
    commit = subprocess.run(
        ["git", "commit", "-m", "gitea: configure Gitea integration"],
        cwd=repo, capture_output=True, text=True,
    )
    if commit.returncode == 0:
        console.print("[green]✓[/green] .compost.yml committed")
    else:
        console.print(f"[yellow]⚠ Could not commit .compost.yml: {commit.stderr.strip()}[/yellow]")

    console.print("\n[bold]doctor:[/bold]")
    _render_checks(_run_doctor_checks(repo))
