from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.table import Table

from compost.cli._helpers import console


@click.group("synth")
def synth_group() -> None:
    """Tier 2 synthesis: propose wiki edits from raw files."""


@synth_group.command("run")
@click.option("--raw", "raw_rel", required=True,
              help="Raw file path relative to repo root.")
@click.option("--provider", default=None,
              help="Override .compost.yml synth.provider.")
@click.option("--model", default=None,
              help="Override .compost.yml synth.model.")
@click.option("--dry-run", is_flag=True, default=False,
              help="Propose edits but do not write to disk or git.")
@click.pass_context
def synth_run(ctx: click.Context, raw_rel: str, provider: str | None,
              model: str | None, dry_run: bool) -> None:
    """Run synthesis on a raw file and optionally commit wiki edits to the current branch."""
    from dataclasses import replace
    from compost.synth.agent import synthesize, load_synth_config
    from compost.synth.diff_writer import apply_diffs, commit_diffs
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    raw_path = repo / raw_rel
    if not raw_path.exists():
        console.print(f"[red]Raw file not found: {raw_path}[/red]")
        sys.exit(1)

    cfg = load_synth_config(repo)
    if provider:
        cfg = replace(cfg, provider=provider)
    if model:
        cfg = replace(cfg, model=model)

    result = synthesize(raw_path, repo, config=cfg, dry_run=dry_run)

    if not result.wiki_diffs:
        console.print("[dim][Tier 2] no wiki edits proposed[/dim]")
        return

    if dry_run:
        console.print(f"[dim][Tier 2] dry-run: {len(result.wiki_diffs)} wiki page(s) would be updated[/dim]")
        for d in result.wiki_diffs:
            tag = "new" if d.is_new else "update"
            console.print(f"  {d.rel_path} ({tag})")
        return

    written = apply_diffs(repo, result)
    commit_diffs(repo, written, result.run_id)
    console.print(f"[green][Tier 2][/green] {len(result.wiki_diffs)} wiki page(s) updated")
    for d in result.wiki_diffs:
        tag = "new" if d.is_new else "updated"
        console.print(f"  {d.rel_path} ({tag})")


@synth_group.command("log")
@click.option("--last", default=10, show_default=True,
              help="Number of recent runs to show.")
@click.pass_context
def synth_log(ctx: click.Context, last: int) -> None:
    """Show recent synthesis run history."""
    from compost.synth.log import read_runs
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    runs = read_runs(repo, last=last)
    if not runs:
        console.print("[dim]No synthesis runs found.[/dim]")
        return

    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Run ID", style="cyan")
    table.add_column("Timestamp")
    table.add_column("Raw file")
    table.add_column("Edits", justify="right")
    table.add_column("In tok", justify="right")
    table.add_column("Out tok", justify="right")
    table.add_column("Cost (USD)", justify="right")
    table.add_column("Duration (s)", justify="right")

    for r in runs:
        in_tok = r.get("total_input_tokens", 0)
        out_tok = r.get("total_output_tokens", 0)
        table.add_row(
            (r.get("run_id") or "?")[:8],
            (r.get("ts") or "")[:19].replace("T", " "),
            r.get("raw", "?"),
            str(r.get("wiki_edits", 0)),
            str(in_tok) if in_tok else "-",
            str(out_tok) if out_tok else "-",
            f"{r.get('total_cost_usd', 0):.4f}",
            f"{r.get('duration_s', 0):.1f}",
        )

    console.print(table)
