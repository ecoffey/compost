import subprocess
from pathlib import Path

import click
import pytest

from compost.ingest.pr import PRLog, merge_pr, open_pr


def _add_file_on_branch(repo: Path, rel_path: str, branch: str, content: str = "body") -> None:
    """Create a file and commit it on a new branch. Leaves HEAD on that branch."""
    f = repo / rel_path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)
    subprocess.run(["git", "checkout", "-b", branch], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", str(f)], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", f"add {rel_path}"],
                   cwd=repo, check=True, capture_output=True)


# ── open_pr ──────────────────────────────────────────────────────────────────

def test_open_pr_creates_log_file(compost_git_repo):
    repo = compost_git_repo
    branch = "raw/2026-04-27-payments-standup"
    _add_file_on_branch(repo, "raw/notes/2026-04-27-payments-standup.md", branch)

    pr_log = open_pr(repo, branch)

    assert pr_log.path.exists()
    assert pr_log.path.name == "raw-2026-04-27-payments-standup.md"
    assert pr_log.path.parent.name == "_pr-log"

    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_open_pr_log_content(compost_git_repo):
    repo = compost_git_repo
    branch = "raw/2026-04-27-retry-ownership"
    _add_file_on_branch(repo, "raw/notes/2026-04-27-retry-ownership.md", branch)

    pr_log = open_pr(repo, branch)
    text = pr_log.path.read_text()

    assert f"# PR: {branch}" in text
    assert f"**Branch:** {branch}" in text
    assert "raw/notes/2026-04-27-retry-ownership.md" in text
    assert "compost pr merge" in text

    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_open_pr_creates_pr_log_dir(compost_git_repo):
    repo = compost_git_repo
    branch = "raw/2026-04-27-dir-test"
    _add_file_on_branch(repo, "raw/notes/2026-04-27-dir-test.md", branch)

    assert not (repo / "_pr-log").exists()
    open_pr(repo, branch)
    assert (repo / "_pr-log").exists()

    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_open_pr_returns_prlog(compost_git_repo):
    repo = compost_git_repo
    branch = "raw/2026-04-27-prlog-type"
    _add_file_on_branch(repo, "raw/notes/2026-04-27-prlog-type.md", branch)

    result = open_pr(repo, branch)
    assert isinstance(result, PRLog)

    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


# ── merge_pr ─────────────────────────────────────────────────────────────────

def test_merge_pr_merges_raw_branch(compost_git_repo):
    repo = compost_git_repo
    branch = "raw/2026-04-27-merge-me"
    _add_file_on_branch(repo, "raw/notes/2026-04-27-merge-me.md", branch)

    merge_pr(repo)

    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "main"
    assert (repo / "raw" / "notes" / "2026-04-27-merge-me.md").exists()


def test_merge_pr_blocked_on_non_raw_branch(compost_git_repo):
    repo = compost_git_repo
    subprocess.run(
        ["git", "checkout", "-b", "feature/something"],
        cwd=repo, check=True, capture_output=True,
    )

    with pytest.raises(click.UsageError, match="Not on a raw/"):
        merge_pr(repo)

    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_merge_pr_blocked_on_wiki_edit(compost_git_repo):
    repo = compost_git_repo
    branch = "raw/2026-04-27-has-wiki-edit"

    raw_f = repo / "raw" / "notes" / "2026-04-27-has-wiki-edit.md"
    raw_f.parent.mkdir(parents=True, exist_ok=True)
    raw_f.write_text("raw content")
    wiki_f = repo / "wiki" / "services" / "payments.md"
    wiki_f.parent.mkdir(parents=True, exist_ok=True)
    wiki_f.write_text("wiki content")

    subprocess.run(["git", "checkout", "-b", branch], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", str(raw_f), str(wiki_f)], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "raw + wiki edit"],
                   cwd=repo, check=True, capture_output=True)

    with pytest.raises(click.UsageError, match="wiki/"):
        merge_pr(repo)

    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)
