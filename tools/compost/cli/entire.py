from __future__ import annotations

import sys
from pathlib import Path

import click

from compost.cli._helpers import console


@click.group("entire")
def entire_group() -> None:
    """Entire shim helpers."""


@entire_group.command("seed")
@click.option("--repo", "source_repo", required=True,
              type=click.Path(resolve_path=True, path_type=Path),
              help="Local git repo to read the commit from.")
@click.option("--sha", required=True, help="Commit SHA to materialize.")
@click.option("--path", "path_filter", default="",
              help="Only materialize if commit touches this path prefix.")
@click.pass_context
def entire_seed(ctx: click.Context, source_repo: Path, sha: str, path_filter: str) -> None:
    """Materialize one commit as a checkpoint raw file (no watcher needed)."""
    import subprocess as _subprocess
    from compost.shims.entire_shim import WatchTarget, materialize_commit
    from compost.worker.queue import enqueue as _enqueue
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    msg_result = _subprocess.run(
        ["git", "log", "-1", "--format=%s", sha],
        cwd=source_repo, capture_output=True, text=True, check=True,
    )
    message = msg_result.stdout.strip()

    files_result = _subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "-r", "--name-only", sha],
        cwd=source_repo, capture_output=True, text=True, check=True,
    )
    changed_files = [f for f in files_result.stdout.splitlines() if f]

    target = WatchTarget(repo=source_repo, path_filter=path_filter)
    raw_path = materialize_commit(
        compost_repo=repo,
        source_repo=source_repo,
        sha=sha,
        message=message,
        changed_files=changed_files,
        target=target,
    )
    if raw_path is None:
        console.print(f"[dim]filtered out (path_filter={path_filter!r} not matched)[/dim]")
        return

    rel = raw_path.relative_to(repo)
    job = _enqueue(repo, str(rel), branch=f"shim/entire/{sha[:8]}", source="entire_shim")
    console.print(f"[green]✓[/green] {rel}")
    console.print(f"job: {job.id[:8]}")
