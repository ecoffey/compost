import subprocess
from pathlib import Path

import click
import pytest

from compost.ingest.git import (
    assert_git_repo,
    changed_files,
    create_branch_and_commit,
    current_branch,
    default_branch,
    fast_forward_merge,
    get_git_user_name,
)


# ── assert_git_repo ──────────────────────────────────────────────────────────

def test_assert_git_repo_passes(git_repo):
    assert_git_repo(git_repo)  # no exception


def test_assert_git_repo_fails_on_plain_dir(tmp_path):
    with pytest.raises(click.UsageError, match="not a git repository"):
        assert_git_repo(tmp_path)


# ── current_branch ───────────────────────────────────────────────────────────

def test_current_branch_main(git_repo):
    assert current_branch(git_repo) == "main"


def test_current_branch_after_checkout(git_repo):
    subprocess.run(
        ["git", "checkout", "-b", "raw/2026-04-27-test"],
        cwd=git_repo, check=True, capture_output=True,
    )
    assert current_branch(git_repo) == "raw/2026-04-27-test"
    subprocess.run(["git", "checkout", "main"], cwd=git_repo, check=True, capture_output=True)


# ── default_branch ───────────────────────────────────────────────────────────

def test_default_branch_returns_main(git_repo):
    assert default_branch(git_repo) == "main"


# ── get_git_user_name ────────────────────────────────────────────────────────

def test_get_git_user_name(git_repo):
    assert get_git_user_name(git_repo) == "Test User"


def test_get_git_user_name_unset_returns_str(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    name = get_git_user_name(tmp_path)
    assert isinstance(name, str)


# ── create_branch_and_commit ─────────────────────────────────────────────────

def test_create_branch_and_commit(git_repo):
    new_file = git_repo / "raw" / "notes" / "test.md"
    new_file.parent.mkdir(parents=True, exist_ok=True)
    new_file.write_text("hello")

    create_branch_and_commit(
        git_repo,
        "raw/2026-04-27-test-note",
        [new_file],
        "raw: test note",
    )

    result = subprocess.run(
        ["git", "rev-parse", "--verify", "raw/2026-04-27-test-note"],
        cwd=git_repo, capture_output=True,
    )
    assert result.returncode == 0

    log = subprocess.run(
        ["git", "show", "--name-only", "--format=", "HEAD"],
        cwd=git_repo, capture_output=True, text=True,
    )
    assert "raw/notes/test.md" in log.stdout

    subprocess.run(["git", "checkout", "main"], cwd=git_repo, check=True, capture_output=True)


def test_create_branch_fails_if_branch_exists(git_repo):
    subprocess.run(
        ["git", "checkout", "-b", "raw/2026-04-27-existing"],
        cwd=git_repo, check=True, capture_output=True,
    )
    subprocess.run(["git", "checkout", "main"], cwd=git_repo, check=True, capture_output=True)

    with pytest.raises(click.UsageError, match="Cannot create branch"):
        create_branch_and_commit(git_repo, "raw/2026-04-27-existing", [], "msg")


# ── changed_files ────────────────────────────────────────────────────────────

def test_changed_files(git_repo):
    new_file = git_repo / "raw" / "notes" / "change.md"
    new_file.parent.mkdir(parents=True, exist_ok=True)
    new_file.write_text("content")

    subprocess.run(
        ["git", "checkout", "-b", "raw/2026-04-27-change"],
        cwd=git_repo, check=True, capture_output=True,
    )
    subprocess.run(["git", "add", str(new_file)], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "add file"],
                   cwd=git_repo, check=True, capture_output=True)

    files = changed_files(git_repo, "raw/2026-04-27-change", "main")
    assert "raw/notes/change.md" in files

    subprocess.run(["git", "checkout", "main"], cwd=git_repo, check=True, capture_output=True)


def test_changed_files_empty_on_same_ref(git_repo):
    assert changed_files(git_repo, "main", "main") == []


# ── fast_forward_merge ───────────────────────────────────────────────────────

def test_fast_forward_merge(git_repo):
    new_file = git_repo / "raw" / "notes" / "merged.md"
    new_file.parent.mkdir(parents=True, exist_ok=True)
    new_file.write_text("merged content")

    subprocess.run(
        ["git", "checkout", "-b", "raw/2026-04-27-merge-test"],
        cwd=git_repo, check=True, capture_output=True,
    )
    subprocess.run(["git", "add", str(new_file)], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "add merged file"],
                   cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=git_repo, check=True, capture_output=True)

    fast_forward_merge(git_repo, "raw/2026-04-27-merge-test")

    assert new_file.exists()
