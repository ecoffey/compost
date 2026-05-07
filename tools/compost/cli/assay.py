from __future__ import annotations

import sys
from pathlib import Path

import click

from compost.cli._helpers import console, _write_assay_report


@click.command("assay")
@click.option("--wiki-dir", default=None, type=click.Path(resolve_path=True, path_type=Path),
              help="Override wiki directory path.")
@click.option("--report", is_flag=True, default=False,
              help="Write markdown report to .compost/assay-report.md.")
@click.pass_context
def assay_cmd(ctx: click.Context, wiki_dir: Path | None, report: bool) -> None:
    """Round-trip validation: codify → compile → render → fuzzy compare."""
    from compost.codify.codegen import codify
    from compost.codify.compiler import compile_kt
    from compost.codify.renderer import render_wiki
    from compost.codify.compare import compare
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    console.print("[dim]codifying...[/dim]")
    codify(repo, wiki_dir=wiki_dir)

    console.print("[dim]compiling...[/dim]")
    try:
        compile_result = compile_kt(repo)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    if not compile_result.success:
        console.print("[red]✗ compile failed — assay aborted[/red]")
        for err in compile_result.errors:
            console.print(f"  [red]line {err.kt_line}:[/red] {err.message}")
        sys.exit(1)

    console.print("[dim]rendering...[/dim]")
    try:
        rendered = render_wiki(repo)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    console.print("[dim]comparing...[/dim]")
    result = compare(repo, rendered, wiki_dir=wiki_dir)

    if result.passed:
        console.print(
            f"[green]✓ ASSAY PASS[/green] — {result.checked} page(s) round-tripped cleanly"
        )
    else:
        console.print(f"[red]✗ ASSAY FAIL[/red] — {len(result.failures)} failure(s)")
        for f in result.failures:
            console.print(f"  [red]{f.page_id}[/red] ({f.source_md.relative_to(repo)})")
            for field in f.missing_fields:
                console.print(f"    missing field: {field}")
            for field, orig, rend in f.changed_fields:
                console.print(f"    changed: {field} = {orig!r} → {rend!r}")

    if report:
        _write_assay_report(repo, result)
        console.print(f"[dim]report → {repo / '.compost' / 'assay-report.md'}[/dim]")

    if not result.passed:
        sys.exit(1)
