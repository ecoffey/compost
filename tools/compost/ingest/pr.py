from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import click

from compost.ingest.git import (
    changed_files,
    current_branch,
    default_branch,
    fast_forward_merge,
)


@dataclass(frozen=True)
class PRLog:
    path: Path   # absolute path of the written log file


def open_pr(repo: Path, branch: str) -> PRLog:
    """Write PR log to _plans/pr-log/{branch-slug}.md and return path."""
    base = default_branch(repo)
    files = changed_files(repo, branch, base)

    branch_slug = branch.replace("/", "-")
    log_dir = repo / "_pr-log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{branch_slug}.md"

    today = date.today().isoformat()
    files_list = "\n".join(f"- {f}" for f in files) if files else "_(none)_"

    content = (
        f"# PR: {branch}\n\n"
        f"**Branch:** {branch}\n"
        f"**Date:** {today}\n"
        f"**Files changed:**\n{files_list}\n\n"
        f"---\n"
        f"*Review complete? Merge with `compost pr merge`.*\n"
    )
    log_path.write_text(content)
    return PRLog(path=log_path)


def merge_pr(repo: Path) -> None:
    """Gate: assert raw/* branch, no wiki/ edits, then fast-forward merge."""
    branch = current_branch(repo)
    if not branch.startswith("raw/"):
        raise click.UsageError(
            f"Not on a raw/* branch (current: '{branch}'). "
            "Checkout a raw/* branch before merging."
        )

    base = default_branch(repo)
    files = changed_files(repo, branch, base)

    wiki_edits = [f for f in files if f.startswith("wiki/")]
    if wiki_edits:
        raise click.UsageError(
            "Branch contains wiki/ edits which must not be auto-merged in Phase 2:\n"
            + "\n".join(f"  {f}" for f in wiki_edits)
        )

    result = subprocess.run(
        ["git", "checkout", base],
        cwd=repo, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise click.UsageError(f"Cannot checkout '{base}': {result.stderr.strip()}")

    fast_forward_merge(repo, branch)
