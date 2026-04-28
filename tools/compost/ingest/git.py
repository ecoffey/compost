from __future__ import annotations

import subprocess
from pathlib import Path

import click


def assert_git_repo(repo: Path) -> None:
    """Raise click.UsageError if repo has no .git directory."""
    if not (repo / ".git").exists():
        raise click.UsageError(
            f"{repo} is not a git repository. Run `git init` first."
        )


def get_git_user_name(repo: Path) -> str:
    """Return git config user.name, or empty string if not set."""
    result = subprocess.run(
        ["git", "config", "user.name"],
        cwd=repo, capture_output=True, text=True,
    )
    return result.stdout.strip()


def default_branch(repo: Path) -> str:
    """Return the repo's default branch name (main or master)."""
    for branch in ("main", "master"):
        result = subprocess.run(
            ["git", "rev-parse", "--verify", branch],
            cwd=repo, capture_output=True,
        )
        if result.returncode == 0:
            return branch
    return current_branch(repo)


def current_branch(repo: Path) -> str:
    """Return the current branch name."""
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def create_branch_and_commit(
    repo: Path,
    branch: str,
    files: list[Path],
    message: str,
) -> None:
    """Create branch off current HEAD, stage files, commit."""
    result = subprocess.run(
        ["git", "checkout", "-b", branch],
        cwd=repo, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise click.UsageError(
            f"Cannot create branch '{branch}': {result.stderr.strip()}"
        )

    for f in files:
        subprocess.run(["git", "add", str(f)], cwd=repo, check=True)

    result = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise click.UsageError(f"Commit failed: {result.stderr.strip()}")


def changed_files(repo: Path, branch: str, base: str) -> list[str]:
    """Return paths changed on branch relative to base (three-dot diff)."""
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{branch}"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def fast_forward_merge(repo: Path, branch: str) -> None:
    """Fast-forward merge branch into current branch. Errors if not FF-able."""
    result = subprocess.run(
        ["git", "merge", "--ff-only", branch],
        cwd=repo, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise click.UsageError(
            f"Cannot fast-forward merge '{branch}': {result.stderr.strip()}"
        )
