from __future__ import annotations

import sys
from pathlib import Path

import click

from compost.cli._helpers import console


@click.group("codify")
def codify_group() -> None:
    """Generate Kotlin type declarations from wiki frontmatter."""


@codify_group.command("run")
@click.option("--compile", "do_compile", is_flag=True, default=False,
              help="Compile generated Kotlin with kotlinc after codegen.")
@click.option("--wiki-dir", default=None, type=click.Path(resolve_path=True, path_type=Path),
              help="Override wiki directory path.")
@click.pass_context
def codify_run(ctx: click.Context, do_compile: bool, wiki_dir: Path | None) -> None:
    """Generate .compost/codify/generated/wiki.kt from wiki frontmatter."""
    from compost.codify.codegen import codify
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    result = codify(repo, wiki_dir=wiki_dir)
    console.print(
        f"[green]✓[/green] {result.page_count} page(s) codified → {result.kt_path}"
    )
    if result.dispute_count:
        console.print(f"[yellow]⚠ {result.dispute_count} name collision(s) detected (@Contested)[/yellow]")
    for w in result.warnings:
        console.print(f"[dim]  ⚠ {w}[/dim]")

    if not do_compile:
        return

    from compost.codify.compiler import compile_kt

    console.print("[dim]compiling...[/dim]")
    try:
        compile_result = compile_kt(repo)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    if compile_result.success:
        console.print(f"[green]✓[/green] compile passed → {compile_result.jar_path}")
    else:
        console.print("[red]✗ compile failed[/red]")
        for err in compile_result.errors:
            console.print(f"  [red]line {err.kt_line}:[/red] {err.message}")
        sys.exit(1)
