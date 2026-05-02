from __future__ import annotations

from pathlib import Path

import click

from compost.gitea.client import GiteaClient, GiteaPR
from compost.ingest.git import (
    changed_files,
    current_branch,
    default_branch,
    fetch_and_ff,
)


def create_pr(repo: Path, branch: str, client: GiteaClient) -> GiteaPR:
    """Build PR body from changed files and open a Gitea PR. Returns the new PR."""
    base = default_branch(repo)
    files = changed_files(repo, branch, base)
    files_list = "\n".join(f"- {f}" for f in files) if files else "_(none)_"
    body = (
        f"**Files changed:**\n{files_list}\n\n"
        f"---\n"
        f"*Review and merge with `compost pr merge`.*"
    )
    return client.open_pr(branch, base, f"raw: {branch}", body)


def merge_pr(repo: Path, client: GiteaClient) -> GiteaPR:
    """Validate guards, merge via Gitea API, sync local repo. Returns merged PR."""
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
            "Branch contains wiki/ edits which must not be auto-merged:\n"
            + "\n".join(f"  {f}" for f in wiki_edits)
        )

    pr = client.find_pr(branch)
    if pr is None:
        raise click.UsageError(
            f"No open PR found for '{branch}'. "
            "Was it already merged or not yet pushed?"
        )

    client.merge_pr(pr.number)
    fetch_and_ff(repo, "origin", base)

    return GiteaPR(number=pr.number, url=pr.url, state="merged")
