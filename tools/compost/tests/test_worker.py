from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from compost.worker.queue import (
    Job,
    claim_next,
    complete,
    dead_letter,
    enqueue,
    requeue_stuck,
)


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def queue_repo(tmp_path: Path) -> Path:
    """A minimal directory with queue subdirectories. No git needed for queue tests."""
    repo = tmp_path / "wiki"
    for d in ("inbox", "processing", "done", "dead"):
        (repo / ".compost" / "queue" / d).mkdir(parents=True)
    (repo / ".compost" / "shims").mkdir(parents=True)
    (repo / "_plans" / "dead-letter").mkdir(parents=True)
    return repo


# ── enqueue ───────────────────────────────────────────────────────────────────


def test_enqueue_creates_file_in_inbox(queue_repo: Path) -> None:
    job = enqueue(queue_repo, "raw/decisions/foo.md", "raw/2026-05-05-foo", source="cli")

    inbox = queue_repo / ".compost" / "queue" / "inbox"
    files = list(inbox.glob("*.json"))
    assert len(files) == 1

    data = json.loads(files[0].read_text())
    assert data["raw_rel"] == "raw/decisions/foo.md"
    assert data["branch"] == "raw/2026-05-05-foo"
    assert data["source"] == "cli"
    assert data["retry_count"] == 0
    assert data["id"] == job.id


def test_enqueue_returns_job_with_id(queue_repo: Path) -> None:
    job = enqueue(queue_repo, "raw/decisions/bar.md", "raw/branch", source="slack_shim")
    assert len(job.id) == 32  # uuid4 hex, no dashes
    assert job.source == "slack_shim"


# ── claim_next ────────────────────────────────────────────────────────────────


def test_claim_next_returns_none_on_empty_queue(queue_repo: Path) -> None:
    assert claim_next(queue_repo) is None


def test_claim_next_moves_job_to_processing(queue_repo: Path) -> None:
    enqueue(queue_repo, "raw/notes/x.md", "raw/branch-x", source="cli")

    job = claim_next(queue_repo)
    assert job is not None
    assert job.raw_rel == "raw/notes/x.md"

    inbox = queue_repo / ".compost" / "queue" / "inbox"
    processing = queue_repo / ".compost" / "queue" / "processing"
    assert len(list(inbox.glob("*.json"))) == 0
    assert len(list(processing.glob("*.json"))) == 1


def test_claim_next_fifo_order(queue_repo: Path) -> None:
    """Jobs should be claimed oldest-first based on enqueue timestamp in filename."""
    job_a = enqueue(queue_repo, "raw/notes/a.md", "raw/branch-a", source="cli")
    time.sleep(0.02)  # ensure different timestamp in filename
    job_b = enqueue(queue_repo, "raw/notes/b.md", "raw/branch-b", source="cli")

    first = claim_next(queue_repo)
    assert first is not None
    assert first.id == job_a.id


def test_claim_next_skips_job_with_future_retry(queue_repo: Path) -> None:
    """A job with next_retry_at in the future should not be claimed."""
    from datetime import datetime, timedelta, timezone

    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    enqueue(queue_repo, "raw/notes/retry.md", "raw/branch-retry", source="cli")

    # Update the job file to set next_retry_at in the future
    inbox = queue_repo / ".compost" / "queue" / "inbox"
    job_file = next(inbox.glob("*.json"))
    data = json.loads(job_file.read_text())
    data["next_retry_at"] = future
    job_file.write_text(json.dumps(data))

    assert claim_next(queue_repo) is None


# ── complete ──────────────────────────────────────────────────────────────────


def test_complete_moves_job_to_done(queue_repo: Path) -> None:
    enqueue(queue_repo, "raw/notes/done.md", "raw/branch-done", source="cli")
    job = claim_next(queue_repo)
    assert job is not None

    complete(queue_repo, job)

    processing = queue_repo / ".compost" / "queue" / "processing"
    done = queue_repo / ".compost" / "queue" / "done"
    assert len(list(processing.glob("*.json"))) == 0
    assert len(list(done.glob("*.json"))) == 1


# ── dead_letter ───────────────────────────────────────────────────────────────


def test_dead_letter_moves_to_dead_and_writes_md(queue_repo: Path) -> None:
    enqueue(queue_repo, "raw/notes/dead.md", "raw/branch-dead", source="cli")
    job = claim_next(queue_repo)
    assert job is not None

    dead_letter(queue_repo, job, "synthesis exploded")

    processing = queue_repo / ".compost" / "queue" / "processing"
    dead = queue_repo / ".compost" / "queue" / "dead"
    assert len(list(processing.glob("*.json"))) == 0
    assert len(list(dead.glob("*.json"))) == 1

    md = queue_repo / "_plans" / "dead-letter" / f"{job.id}.md"
    assert md.exists()
    content = md.read_text()
    assert "synthesis exploded" in content
    assert job.raw_rel in content


# ── requeue_stuck ─────────────────────────────────────────────────────────────


def test_requeue_stuck_moves_old_processing_jobs_back(queue_repo: Path) -> None:
    enqueue(queue_repo, "raw/notes/stuck.md", "raw/branch-stuck", source="cli")
    job = claim_next(queue_repo)
    assert job is not None

    # Backdate the processing file's mtime by 10 minutes
    processing_dir = queue_repo / ".compost" / "queue" / "processing"
    processing_file = next(processing_dir.glob("*.json"))
    old_time = time.time() - 600
    os.utime(processing_file, (old_time, old_time))

    count = requeue_stuck(queue_repo, older_than_s=300)
    assert count == 1

    inbox = queue_repo / ".compost" / "queue" / "inbox"
    assert len(list(inbox.glob("*.json"))) == 1


def test_requeue_stuck_skips_recent_processing_jobs(queue_repo: Path) -> None:
    enqueue(queue_repo, "raw/notes/fresh.md", "raw/branch-fresh", source="cli")
    job = claim_next(queue_repo)
    assert job is not None  # job is now in processing, mtime = now

    count = requeue_stuck(queue_repo, older_than_s=300)
    assert count == 0

    processing = queue_repo / ".compost" / "queue" / "processing"
    assert len(list(processing.glob("*.json"))) == 1


# ── _process_job git mechanics ────────────────────────────────────────────────
#
# These tests stub out synthesize() so they don't make LLM calls.
# They exist to catch git-mechanics bugs: relative-path add, branch creation,
# untracked-file conflict on retry.


def _make_raw_file(repo: Path, rel: str = "raw/slack/2026/05/msg.md") -> Path:
    """Write an untracked raw file to disk (simulating what the slack shim does)."""
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\nsource: slack\n---\nsome message\n")
    return p


def _stub_synth(monkeypatch, diffs=None):
    """Patch synthesize() and supporting functions to avoid LLM calls."""
    from unittest.mock import MagicMock
    from compost.synth import agent as synth_agent

    result = MagicMock()
    result.wiki_diffs = diffs or []
    result.run_id = "test-run"
    monkeypatch.setattr(synth_agent, "synthesize", lambda *a, **kw: result)
    monkeypatch.setattr(synth_agent, "load_synth_config", lambda repo: MagicMock())
    return result


def test_process_job_creates_branch_and_commits_raw(
    compost_git_repo_with_queue: Path, monkeypatch
) -> None:
    """Worker creates the job branch, commits the raw file, ends on main."""
    import subprocess
    from compost.worker.worker import WorkerConfig, _process_job
    from compost.worker.queue import enqueue, claim_next

    _stub_synth(monkeypatch)
    # Also stub run_checks so no checks config is needed
    from compost.checks import runner as checks_runner
    monkeypatch.setattr(checks_runner, "run_checks", lambda *a, **kw: [])
    monkeypatch.setattr(checks_runner, "load_checks_config", lambda repo: None)

    repo = compost_git_repo_with_queue
    rel = "raw/slack/2026/05/msg.md"
    _make_raw_file(repo, rel)
    job = enqueue(repo, rel, branch="shim/slack/2026-05-05T15-30-00", source="slack_shim")
    job = claim_next(repo)
    assert job is not None

    _process_job(repo, job, WorkerConfig())

    # Job should be done
    done = repo / ".compost" / "queue" / "done"
    assert len(list(done.glob("*.json"))) == 1

    # Raw file should be committed on the job branch
    result = subprocess.run(
        ["git", "log", "--oneline", "shim/slack/2026-05-05T15-30-00"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    assert rel in subprocess.run(
        ["git", "show", "--stat", "shim/slack/2026-05-05T15-30-00"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout

    # Worker should return HEAD to main
    head = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert head == "main"


def test_process_job_handles_existing_branch_on_retry(
    compost_git_repo_with_queue: Path, monkeypatch
) -> None:
    """Retry succeeds when branch already exists and raw file is committed there."""
    import subprocess
    from compost.worker.worker import WorkerConfig, _process_job
    from compost.worker.queue import enqueue, claim_next

    _stub_synth(monkeypatch)
    from compost.checks import runner as checks_runner
    monkeypatch.setattr(checks_runner, "run_checks", lambda *a, **kw: [])
    monkeypatch.setattr(checks_runner, "load_checks_config", lambda repo: None)

    repo = compost_git_repo_with_queue
    rel = "raw/slack/2026/05/msg.md"
    git_branch = "shim/slack/2026-05-05T15-30-00"

    # Simulate state after a first attempt: branch exists with raw committed,
    # raw file is back as untracked on main (which blocks a naive checkout).
    _make_raw_file(repo, rel)
    subprocess.run(["git", "checkout", "-b", git_branch], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", rel], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "raw: msg"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)
    # Raw file is now gone from working tree; re-write it as untracked (as the shim would)
    _make_raw_file(repo, rel)

    job = enqueue(repo, rel, branch=git_branch, source="slack_shim")
    job = claim_next(repo)
    assert job is not None

    _process_job(repo, job, WorkerConfig())

    done = repo / ".compost" / "queue" / "done"
    assert len(list(done.glob("*.json"))) == 1


# ── retry / backoff ───────────────────────────────────────────────────────────


def test_retry_backoff_delay(queue_repo: Path) -> None:
    """First retry backoff = base * 2^0 = base seconds."""
    from datetime import datetime, timezone
    from compost.worker.worker import WorkerConfig, _handle_failure

    enqueue(queue_repo, "raw/notes/retry.md", "raw/branch-retry", source="cli")
    job = claim_next(queue_repo)
    assert job is not None

    config = WorkerConfig(poll_interval_s=1, max_retries=3, retry_backoff_base_s=60)
    _handle_failure(queue_repo, job, config, "transient error")

    # Job should be back in inbox with retry_count=1 and next_retry_at set
    inbox = queue_repo / ".compost" / "queue" / "inbox"
    files = list(inbox.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["retry_count"] == 1
    assert data["next_retry_at"] is not None

    retry_at = datetime.fromisoformat(data["next_retry_at"])
    now = datetime.now(timezone.utc)
    delay = (retry_at - now).total_seconds()
    assert 55 <= delay <= 65, f"expected ~60s backoff, got {delay:.1f}s"


def test_retry_exhausted_goes_to_dead_letter(queue_repo: Path) -> None:
    from compost.worker.worker import WorkerConfig, _handle_failure

    enqueue(queue_repo, "raw/notes/exhaust.md", "raw/branch-ex", source="cli")
    job = claim_next(queue_repo)
    assert job is not None
    job.retry_count = 3  # already at max

    config = WorkerConfig(max_retries=3)
    _handle_failure(queue_repo, job, config, "permanent failure")

    dead = queue_repo / ".compost" / "queue" / "dead"
    assert len(list(dead.glob("*.json"))) == 1
    md = queue_repo / "_plans" / "dead-letter" / f"{job.id}.md"
    assert md.exists()
