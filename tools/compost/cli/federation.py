from __future__ import annotations

import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table

console = Console()

_DEFAULT_CONFIG = Path.home() / ".compost" / "federation.yaml"


@click.group("federation")
def federation_group() -> None:
    """Manage the multi-repo federation config (~/.compost/federation.yaml)."""


@federation_group.command("list")
def federation_list() -> None:
    """Show all repos registered in the federation config."""
    from compost.mcp.federation import load_federation_config
    repos = load_federation_config()
    if not repos:
        console.print("[dim]No repos registered. Use 'compost federation add' to register one.[/dim]")
        return
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Name")
    table.add_column("Role")
    table.add_column("qmd index")
    table.add_column("Path")
    for r in repos:
        table.add_row(r.name, r.role, r.qmd_index, str(r.path))
    console.print(table)


@federation_group.command("add")
@click.option("--name", required=True, help="Repo identifier (unique).")
@click.option("--path", "repo_path", required=True, type=click.Path(),
              help="Path to the repo.")
@click.option("--role", required=True,
              type=click.Choice(["team", "org", "engineering"]),
              help="Role this repo plays in the federation.")
@click.option("--qmd-index", required=True, help="qmd index name for this repo.")
def federation_add(name: str, repo_path: str, role: str, qmd_index: str) -> None:
    """Register a repo in ~/.compost/federation.yaml."""
    resolved = Path(repo_path).expanduser().resolve()
    if not resolved.exists():
        console.print(f"[red]Path does not exist: {resolved}[/red]")
        sys.exit(1)

    _DEFAULT_CONFIG.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if _DEFAULT_CONFIG.exists():
        existing = yaml.safe_load(_DEFAULT_CONFIG.read_text()) or {}

    repos = existing.get("repos", [])
    if any(r["name"] == name for r in repos):
        console.print(f"[yellow]A repo named '{name}' is already registered.[/yellow]")
        sys.exit(1)

    repos.append({
        "name": name,
        "path": str(resolved),
        "qmd_index": qmd_index,
        "role": role,
    })
    existing["repos"] = repos
    _DEFAULT_CONFIG.write_text(yaml.dump(existing, default_flow_style=False))
    console.print(f"[green]Registered '{name}' ({role}) in {_DEFAULT_CONFIG}[/green]")


@federation_group.command("doctor")
def federation_doctor() -> None:
    """Verify each federated repo: path exists, .compost.yml present, qmd collections indexed."""
    from compost.cli._helpers import _qmd_collections
    from compost.mcp.federation import load_federation_config

    repos = load_federation_config()
    if not repos:
        console.print("[dim]No repos in federation config.[/dim]")
        return

    all_ok = True
    for repo in repos:
        console.print(f"\n[bold]{repo.name}[/bold] ({repo.role})")

        path_ok = repo.path.exists()
        _print_check("path exists", path_ok, str(repo.path))
        if not path_ok:
            all_ok = False
            continue

        compost_yml = (repo.path / ".compost.yml").exists()
        _print_check(".compost.yml", compost_yml, "")
        all_ok = all_ok and compost_yml

        registered = _qmd_collections(repo.qmd_index)
        wiki_ok = "wiki" in registered
        _print_check(
            "qmd wiki collection", wiki_ok,
            f"run: qmd --index {repo.qmd_index} collection add wiki wiki '**/*.md' (from repo root)"
        )
        all_ok = all_ok and wiki_ok

    if not all_ok:
        sys.exit(1)


def _print_check(label: str, ok: bool, msg: str) -> None:
    icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
    detail = f"  [dim]{msg}[/dim]" if msg and not ok else ""
    console.print(f"  {icon} {label}{detail}")
