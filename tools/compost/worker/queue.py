from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Job:
    id: str                      # uuid4 hex
    raw_rel: str                 # relative to repo root
    branch: str                  # git branch the raw was committed on
    enqueued_at: str             # ISO 8601
    source: str                  # "cli" | "slack_shim" | "entire_shim" | "gh_webhook_shim"
    retry_count: int = 0
    next_retry_at: str | None = None
    last_error: str | None = None


# ── path helpers ──────────────────────────────────────────────────────────────


def _inbox(repo: Path) -> Path:
    return repo / ".compost" / "queue" / "inbox"


def _processing(repo: Path) -> Path:
    return repo / ".compost" / "queue" / "processing"


def _done(repo: Path) -> Path:
    return repo / ".compost" / "queue" / "done"


def _dead(repo: Path) -> Path:
    return repo / ".compost" / "queue" / "dead"


def _job_filename(job: Job) -> str:
    # Include microseconds so sorted() gives FIFO order even for sub-second enqueues.
    # isoformat() → "2026-05-05T16:05:23.456789+00:00"; we keep up to microseconds.
    ts = job.enqueued_at[:26].replace(":", "").replace("-", "").replace(".", "")
    return f"{ts}-{job.id}.json"


# ── public API ────────────────────────────────────────────────────────────────


def enqueue(repo: Path, raw_rel: str, branch: str, source: str = "cli") -> Job:
    """Write a new job to inbox/. Returns the Job."""
    job = Job(
        id=uuid.uuid4().hex,
        raw_rel=raw_rel,
        branch=branch,
        enqueued_at=datetime.now(timezone.utc).isoformat(),
        source=source,
    )
    _inbox(repo).mkdir(parents=True, exist_ok=True)
    path = _inbox(repo) / _job_filename(job)
    path.write_text(json.dumps(asdict(job)))
    return job


def claim_next(repo: Path) -> Job | None:
    """Atomically move the oldest ready inbox job to processing/. Returns Job or None.

    A job is "ready" if next_retry_at is None or is in the past.
    """
    now = datetime.now(timezone.utc).isoformat()
    _processing(repo).mkdir(parents=True, exist_ok=True)

    for path in sorted(_inbox(repo).glob("*.json")):
        data = json.loads(path.read_text())
        next_retry = data.get("next_retry_at")
        if next_retry and next_retry > now:
            continue  # not yet ready
        target = _processing(repo) / path.name
        try:
            os.rename(path, target)
            return Job(**data)
        except FileNotFoundError:
            continue  # claimed concurrently (future-proofing)
    return None


def complete(repo: Path, job: Job) -> None:
    """Move a processing job to done/."""
    src = _processing(repo) / _job_filename(job)
    _done(repo).mkdir(parents=True, exist_ok=True)
    os.rename(src, _done(repo) / _job_filename(job))


def dead_letter(repo: Path, job: Job, error: str) -> None:
    """Move a processing job to dead/ and write a dead-letter markdown file."""
    job.last_error = error
    src = _processing(repo) / _job_filename(job)
    _dead(repo).mkdir(parents=True, exist_ok=True)
    dest = _dead(repo) / _job_filename(job)
    dest.write_text(json.dumps(asdict(job)))
    try:
        os.rename(src, dest)
    except FileNotFoundError:
        pass  # already moved by the write above on same path
    _write_dead_letter_md(repo, job, error)


def requeue_stuck(repo: Path, older_than_s: int = 300) -> int:
    """On worker startup: move processing/ jobs older than threshold back to inbox/.

    Returns the number of jobs requeued.
    """
    cutoff = time.time() - older_than_s
    count = 0
    _inbox(repo).mkdir(parents=True, exist_ok=True)
    for path in _processing(repo).glob("*.json"):
        if path.stat().st_mtime < cutoff:
            try:
                os.rename(path, _inbox(repo) / path.name)
                count += 1
            except FileNotFoundError:
                pass
    return count


# ── internal helpers ──────────────────────────────────────────────────────────


def _write_dead_letter_md(repo: Path, job: Job, error: str) -> None:
    dl_dir = repo / "_plans" / "dead-letter"
    dl_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    content = (
        f"# Dead Letter: {job.id}\n\n"
        f"**Date:** {ts}\n"
        f"**Raw:** `{job.raw_rel}`\n"
        f"**Branch:** `{job.branch}`\n"
        f"**Source:** {job.source}\n"
        f"**Retries:** {job.retry_count}\n\n"
        f"## Error\n\n```\n{error}\n```\n\n"
        f"To retry: `compost worker retry --job-id {job.id}`\n"
    )
    (dl_dir / f"{job.id}.md").write_text(content)
