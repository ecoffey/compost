from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def source_git_repo(tmp_path: Path) -> Path:
    """A local git repo to act as the watched source (e.g. a service repo)."""
    repo = tmp_path / "source-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"],
                   cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"],
                   cwd=repo, check=True, capture_output=True)
    return repo


def _add_commit(repo: Path, filename: str, message: str) -> str:
    """Add a file and commit in the source repo. Returns the new SHA."""
    path = repo / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("content")
    subprocess.run(["git", "add", filename], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", message],
                   cwd=repo, check=True, capture_output=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


# ── entire_shim ───────────────────────────────────────────────────────────────


def test_entire_shim_materializes_new_commit(
    compost_git_repo_with_queue: Path,
    source_git_repo: Path,
) -> None:
    from compost.shims.entire_shim import WatchTarget, materialize_commit

    sha = _add_commit(source_git_repo, "service.py", "feat: add service")
    target = WatchTarget(repo=source_git_repo)

    raw_path = materialize_commit(
        compost_repo=compost_git_repo_with_queue,
        source_repo=source_git_repo,
        sha=sha,
        message="feat: add service",
        changed_files=["service.py"],
        target=target,
    )

    assert raw_path is not None
    assert raw_path.exists()
    assert "checkpoints" in str(raw_path)
    content = raw_path.read_text()
    assert sha in content
    assert "feat: add service" in content


def test_entire_shim_respects_path_filter(
    compost_git_repo_with_queue: Path,
    source_git_repo: Path,
) -> None:
    from compost.shims.entire_shim import WatchTarget, materialize_commit

    sha = _add_commit(source_git_repo, "docs/readme.md", "docs: update readme")
    target = WatchTarget(repo=source_git_repo, path_filter="src/")

    raw_path = materialize_commit(
        compost_repo=compost_git_repo_with_queue,
        source_repo=source_git_repo,
        sha=sha,
        message="docs: update readme",
        changed_files=["docs/readme.md"],
        target=target,
    )
    assert raw_path is None


def test_entire_shim_multiple_targets_same_repo(
    compost_git_repo_with_queue: Path,
    source_git_repo: Path,
) -> None:
    """Two targets for the same repo with different path_filter values each see matching commits."""
    from compost.shims.entire_shim import WatchTarget, materialize_commit

    sha = _add_commit(source_git_repo, "src/payments/pay.py", "feat: payments")
    target_payments = WatchTarget(repo=source_git_repo, path_filter="src/payments/")
    target_auth = WatchTarget(repo=source_git_repo, path_filter="src/auth/")

    path_payments = materialize_commit(
        compost_repo=compost_git_repo_with_queue,
        source_repo=source_git_repo,
        sha=sha,
        message="feat: payments",
        changed_files=["src/payments/pay.py"],
        target=target_payments,
    )
    path_auth = materialize_commit(
        compost_repo=compost_git_repo_with_queue,
        source_repo=source_git_repo,
        sha=sha,
        message="feat: payments",
        changed_files=["src/payments/pay.py"],
        target=target_auth,
    )

    assert path_payments is not None
    assert path_auth is None


# ── slack_shim ────────────────────────────────────────────────────────────────


def test_slack_shim_ignores_non_wiki_emoji(
    compost_git_repo_with_queue: Path,
) -> None:
    from fastapi.testclient import TestClient
    from compost.shims.slack_shim import make_app

    app = make_app(compost_repo=compost_git_repo_with_queue, wiki_emoji="wiki")
    client = TestClient(app)

    resp = client.post("/events", json={
        "channel": "eng",
        "thread_ts": "1234567890.000",
        "emoji": "thumbsup",
        "text": "some message",
        "user": "U12345",
    })
    assert resp.status_code == 200

    inbox = compost_git_repo_with_queue / ".compost" / "queue" / "inbox"
    assert len(list(inbox.glob("*.json"))) == 0


def test_slack_shim_writes_raw_and_enqueues_on_wiki_emoji(
    compost_git_repo_with_queue: Path,
) -> None:
    from fastapi.testclient import TestClient
    from compost.shims.slack_shim import make_app

    app = make_app(compost_repo=compost_git_repo_with_queue, wiki_emoji="wiki")
    client = TestClient(app)

    resp = client.post("/events", json={
        "channel": "eng",
        "thread_ts": "1234567890.000",
        "emoji": "wiki",
        "text": "We decided to use Stripe for payments.",
        "user": "U12345",
    })
    assert resp.status_code == 200

    raw_dir = compost_git_repo_with_queue / "raw" / "slack"
    md_files = list(raw_dir.rglob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text()
    assert "We decided to use Stripe for payments." in content

    inbox = compost_git_repo_with_queue / ".compost" / "queue" / "inbox"
    assert len(list(inbox.glob("*.json"))) == 1


# ── gh_webhook_shim ───────────────────────────────────────────────────────────


def test_gh_webhook_shim_ignores_pr_without_synth_label(
    compost_git_repo_with_queue: Path,
) -> None:
    from fastapi.testclient import TestClient
    from compost.shims.gh_webhook_shim import make_app

    app = make_app(compost_repo=compost_git_repo_with_queue)
    client = TestClient(app)

    resp = client.post("/webhook", json={
        "action": "opened",
        "pr_number": 1,
        "branch": "feature/foo",
        "labels": ["bug"],
        "repo_full_name": "acme/payments",
    })
    assert resp.status_code == 200
    inbox = compost_git_repo_with_queue / ".compost" / "queue" / "inbox"
    assert len(list(inbox.glob("*.json"))) == 0


def test_gh_webhook_shim_enqueues_on_synth_label(
    compost_git_repo_with_queue: Path,
) -> None:
    from fastapi.testclient import TestClient
    from compost.shims.gh_webhook_shim import make_app

    app = make_app(compost_repo=compost_git_repo_with_queue)
    client = TestClient(app)

    resp = client.post("/webhook", json={
        "action": "labeled",
        "pr_number": 42,
        "branch": "raw/2026-05-05T1234-my-thing",
        "labels": ["compost/synth"],
        "repo_full_name": "acme/payments",
    })
    assert resp.status_code == 200

    commits_dir = compost_git_repo_with_queue / "raw" / "commits"
    md_files = list(commits_dir.rglob("*.md")) if commits_dir.exists() else []
    assert len(md_files) == 1

    inbox = compost_git_repo_with_queue / ".compost" / "queue" / "inbox"
    assert len(list(inbox.glob("*.json"))) == 1


# ── supervisor ────────────────────────────────────────────────────────────────


def test_supervisor_writes_and_clears_pid_files(
    compost_git_repo_with_queue: Path,
) -> None:
    from compost.shims.supervisor import write_pid, clear_pid

    pid_dir = compost_git_repo_with_queue / ".compost" / "shims"
    write_pid(pid_dir, "test_shim", 99999)
    pid_file = pid_dir / "test_shim.pid"
    assert pid_file.exists()
    assert pid_file.read_text().strip() == "99999"

    clear_pid(pid_dir, "test_shim")
    assert not pid_file.exists()
