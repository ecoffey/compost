# Phase 7 & 8: Shims + Worker — Implementation Detail

## § Implementation Detail

Execution order mirrors TDD: write the failing test, then the production code, then refactor.
Each numbered section is one red-green cycle. Run `just test` after each green phase.

---

### Step 0 — Bootstrap: packages, deps, directories

#### 0a. `pyproject.toml` — register new packages and optional extras

```toml
# Add to [project.optional-dependencies]:
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "fastapi>=0.110"]
shims = ["fastapi>=0.110", "uvicorn[standard]>=0.27"]

# Add to [tool.setuptools] packages list:
"compost.shims",
"compost.worker",
```

Full diff:
```diff
-dev = ["pytest>=8.0", "pytest-asyncio>=0.23"]
+dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "fastapi>=0.110"]
+shims = ["fastapi>=0.110", "uvicorn[standard]>=0.27"]
 
 [tool.setuptools]
 packages = [
     "compost",
     "compost.gitea",
     "compost.model",
     "compost.mcp",
     "compost.ingest",
     "compost.synth",
     "compost.codify",
     "compost.checks",
+    "compost.shims",
+    "compost.worker",
 ]
```

Run: `uv sync --project tools/compost --extra dev`

#### 0b. New package `__init__.py` stubs

Create empty:
- `tools/compost/shims/__init__.py`
- `tools/compost/worker/__init__.py`

#### 0c. `bootstrap.py` — add queue dirs and update `.gitignore`

Add `"raw/commits"` to `_DIRECTORIES`.

After calling `_write_gitignore`, add a call to `_create_queue_dirs`:

```python
def bootstrap_repo(path: Path, name: str) -> None:
    ...
    _write_gitignore(path)
    _create_queue_dirs(path)
    ...
```

```python
def _create_queue_dirs(repo: Path) -> None:
    for d in ("inbox", "processing", "done", "dead"):
        (repo / ".compost" / "queue" / d).mkdir(parents=True, exist_ok=True)
    (repo / ".compost" / "shims").mkdir(parents=True, exist_ok=True)
```

Update `_write_gitignore` to include queue and shims dirs:

```python
def _write_gitignore(repo: Path) -> None:
    (repo / ".gitignore").write_text(
        "# compost generated artifacts — always re-derivable, never commit\n"
        ".compost/codify/\n"
        ".compost/synth-log/\n"
        ".compost/assay-report.md\n"
        ".compost/checks/\n"
        ".compost/queue/\n"
        ".compost/shims/\n"
    )
```

Also update the `doctor` check in `cli.py` (`_REQUIRED_IGNORES`):

```python
_REQUIRED_IGNORES = {
    ".compost/codify/",
    ".compost/synth-log/",
    ".compost/checks/",
    ".compost/queue/",
    ".compost/shims/",
}
```

#### 0d. `ingest/raw.py` — add "checkpoint" and "commits" source types

```python
SOURCE_TYPES = Literal[
    "slack", "incident", "decision", "note", "meeting", "support",
    "checkpoint", "commits",
]
```

In `_resolve_path`:
```python
if source == "checkpoint":
    return Path(f"raw/checkpoints/{yyyy}/{mm}/{dd}/{slug}.md")
if source == "commits":
    return Path(f"raw/commits/{yyyy}/{mm}/{dd}/{slug}.md")
```

Note: `write_raw` does not require `--channel` for "checkpoint" or "commits".
The `channel` guard only applies to `source == "slack"` (already the case).

#### 0e. `cli.py` — add SOURCE_CHOICES update

```python
SOURCE_CHOICES = [
    "slack", "incident", "decision", "note", "meeting", "support",
    "checkpoint", "commits",
]
```

---

### Step 1 — Queue: `worker/queue.py`

#### 1a. Write `test_worker.py` (RED)

File: `tools/compost/tests/test_worker.py`

```python
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
    time.sleep(0.01)  # ensure different timestamp in filename
    job_b = enqueue(queue_repo, "raw/notes/b.md", "raw/branch-b", source="cli")

    first = claim_next(queue_repo)
    assert first is not None
    assert first.id == job_a.id


def test_claim_next_skips_job_with_future_retry(queue_repo: Path) -> None:
    """A job with next_retry_at in the future should not be claimed."""
    from datetime import datetime, timedelta, timezone

    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    job = enqueue(queue_repo, "raw/notes/retry.md", "raw/branch-retry", source="cli")

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


def test_dead_letter_moves_to_dead_and_writes_md(queue_repo: Path, tmp_path: Path) -> None:
    # dead_letter writes _plans/dead-letter/{id}.md relative to the repo
    (queue_repo / "_plans" / "dead-letter").mkdir(parents=True)

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
    processing_file = queue_repo / ".compost" / "queue" / "processing" / f"{job.id}.json"
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
```

Run `just test tests/test_worker.py` — all fail (module not found).

#### 1b. Implement `worker/queue.py` (GREEN)

```python
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
    # Timestamp prefix ensures sorted() gives FIFO ordering.
    ts = job.enqueued_at.replace(":", "").replace("-", "").replace("T", "T")[:15]
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
    os.rename(src, dest)
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
```

Run `just test tests/test_worker.py` — all green.

**Note on `complete()` and `dead_letter()`:** The filename in `processing/` uses the same
`_job_filename()` as when it was enqueued (same `id`, same `enqueued_at`). This is consistent
because the job data does not change between enqueue and complete for non-retry paths.
For retry paths, the worker re-writes the job file in place (see Step 4).

---

### Step 2 — Worker loop: `worker/worker.py`

#### 2a. `conftest.py` — add `compost_git_repo_with_queue` fixture

Append to `tools/compost/tests/conftest.py`:

```python
@pytest.fixture
def compost_git_repo_with_queue(compost_git_repo: Path) -> Path:
    """compost_git_repo with queue directories initialized. HEAD on main."""
    for d in ("inbox", "processing", "done", "dead"):
        (compost_git_repo / ".compost" / "queue" / d).mkdir(parents=True, exist_ok=True)
    (compost_git_repo / ".compost" / "shims").mkdir(parents=True, exist_ok=True)
    (compost_git_repo / "_plans" / "dead-letter").mkdir(parents=True, exist_ok=True)
    return compost_git_repo
```

Also update `.gitignore` in the conftest `_write_gitignore` helper to include the new entries:

```python
def _write_gitignore(repo: Path) -> None:
    (repo / ".gitignore").write_text(
        ".compost/codify/\n"
        ".compost/synth-log/\n"
        ".compost/assay-report.md\n"
        ".compost/checks/\n"
        ".compost/queue/\n"
        ".compost/shims/\n"
    )
```

#### 2b. `worker/worker.py` (write production code directly — no direct-test for the loop)

The loop itself is tested indirectly via integration tests in Step 7. For now, implement
the config loading and the retry path, which have unit-testable logic.

```python
from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from compost.repo import load_repo_config
from compost.worker.queue import Job, claim_next, complete, dead_letter, requeue_stuck, _processing, _job_filename


@dataclass
class WorkerConfig:
    poll_interval_s: int = 5
    max_retries: int = 3
    retry_backoff_base_s: int = 30   # delay = base * 2^retry_count


def load_worker_config(repo: Path) -> WorkerConfig:
    """Read worker: block from .compost.yml; fall back to WorkerConfig defaults."""
    config = load_repo_config(repo)
    w = config.get("worker", {})
    return WorkerConfig(
        poll_interval_s=int(w.get("poll_interval_s", 5)),
        max_retries=int(w.get("max_retries", 3)),
        retry_backoff_base_s=int(w.get("retry_backoff_base_s", 30)),
    )


def worker_mode(repo: Path) -> str:
    """Return 'inline' or 'async' from .compost.yml worker.mode."""
    config = load_repo_config(repo)
    return config.get("worker", {}).get("mode", "inline")


def run_worker(repo: Path, config: WorkerConfig | None = None) -> None:
    """Long-running synthesis + checks loop. Ctrl-C exits cleanly."""
    if config is None:
        config = load_worker_config(repo)
    requeue_stuck(repo)
    # TODO(backlog): explore starting a persistent qmd mcp process here
    #   to eliminate per-query model warmup.
    try:
        while True:
            job = claim_next(repo)
            if job is None:
                time.sleep(config.poll_interval_s)
                continue
            _process_job(repo, job, config)
    except KeyboardInterrupt:
        pass


def _process_job(repo: Path, job: Job, config: WorkerConfig) -> None:
    from compost.synth.agent import synthesize, load_synth_config
    from compost.synth.diff_writer import apply_diffs, commit_diffs
    from compost.checks.runner import run_checks, load_checks_config, infer_raw_path

    raw_path = repo / job.raw_rel
    try:
        synth_cfg = load_synth_config(repo)
        result = synthesize(raw_path, repo, config=synth_cfg)
        if result.wiki_diffs:
            written = apply_diffs(repo, result)
            commit_diffs(repo, written, result.run_id)

        checks_cfg = load_checks_config(repo)
        run_checks(repo, job.branch, raw_path, config=checks_cfg)
        complete(repo, job)
    except Exception as exc:
        _handle_failure(repo, job, config, str(exc))


def _handle_failure(repo: Path, job: Job, config: WorkerConfig, error: str) -> None:
    from datetime import datetime, timedelta, timezone

    if job.retry_count >= config.max_retries:
        dead_letter(repo, job, error)
        return

    job.retry_count += 1
    backoff_s = config.retry_backoff_base_s * (2 ** (job.retry_count - 1))
    job.next_retry_at = (
        datetime.now(timezone.utc) + timedelta(seconds=backoff_s)
    ).isoformat()
    job.last_error = error

    # Rewrite job file in processing/ then move back to inbox/
    import os
    from compost.worker.queue import _inbox
    proc_path = _processing(repo) / _job_filename(job)
    proc_path.write_text(json.dumps(asdict(job)))
    os.rename(proc_path, _inbox(repo) / _job_filename(job))
```

Add test for retry backoff calculation to `test_worker.py`:

```python
def test_retry_backoff_delay(queue_repo: Path) -> None:
    """Verify exponential backoff: base * 2^(retry_count-1)."""
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

    # Verify delay is approximately 60s (base * 2^0 = 60)
    from datetime import timedelta
    retry_at = datetime.fromisoformat(data["next_retry_at"])
    now = datetime.now(timezone.utc)
    delay = (retry_at - now).total_seconds()
    assert 55 <= delay <= 65  # within 5s of expected


def test_retry_exhausted_goes_to_dead_letter(queue_repo: Path) -> None:
    from compost.worker.worker import WorkerConfig, _handle_failure

    (queue_repo / "_plans" / "dead-letter").mkdir(parents=True, exist_ok=True)
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
```

Run `just test tests/test_worker.py` — all green.

---

### Step 3 — CLI: `worker` commands in `cli.py`

Add after the existing `checks_group`:

```python
# ── worker commands ───────────────────────────────────────────────────────────


@main.group("worker")
def worker_group() -> None:
    """Async synthesis worker: drains the on-disk job queue."""


@worker_group.command("up")
@click.pass_context
def worker_up(ctx: click.Context) -> None:
    """Start the synthesis worker (blocking; Ctrl-C to stop)."""
    from compost.worker.worker import run_worker, load_worker_config

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    config = load_worker_config(repo)
    console.print(
        f"[dim]worker up — poll every {config.poll_interval_s}s, "
        f"max retries {config.max_retries}[/dim]"
    )
    run_worker(repo, config)


@worker_group.command("status")
@click.pass_context
def worker_status(ctx: click.Context) -> None:
    """Print queue depths (inbox / processing / done / dead)."""
    from compost.worker.queue import _inbox, _processing, _done, _dead

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Queue")
    table.add_column("Count", justify="right")
    for name, fn in (("inbox", _inbox), ("processing", _processing),
                     ("done", _done), ("dead", _dead)):
        d = fn(repo)
        count = len(list(d.glob("*.json"))) if d.exists() else 0
        table.add_row(name, str(count))
    console.print(table)


@worker_group.command("retry")
@click.option("--job-id", required=True, help="Job ID (hex) to move from dead/ back to inbox/.")
@click.pass_context
def worker_retry(ctx: click.Context, job_id: str) -> None:
    """Move a dead-letter job back to inbox/ for reprocessing."""
    import os
    from compost.worker.queue import _dead, _inbox

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    dead = _dead(repo)
    matches = list(dead.glob(f"*{job_id}*.json"))
    if not matches:
        console.print(f"[red]No dead-letter job matching '{job_id}'[/red]")
        sys.exit(1)
    if len(matches) > 1:
        console.print(f"[red]Ambiguous job ID — {len(matches)} matches[/red]")
        sys.exit(1)

    src = matches[0]
    import json
    data = json.loads(src.read_text())
    data["retry_count"] = 0
    data["next_retry_at"] = None
    data["last_error"] = None
    _inbox(repo).mkdir(parents=True, exist_ok=True)
    dest = _inbox(repo) / src.name
    dest.write_text(json.dumps(data))
    os.rename(src, dest)
    console.print(f"[green]✓[/green] job {job_id[:8]} moved to inbox")
```

#### 3a. Integrate async mode into `raw add`

In `raw_add` (after the classification block, before the push), replace the inline synth block
with a mode-aware dispatch:

```python
    # Tier 2 synthesis — inline or async depending on worker.mode
    synth_result = None
    if decision and decision.fired and not no_synth:
        from compost.worker.worker import worker_mode
        mode = worker_mode(repo)
        if mode == "async":
            from compost.worker.queue import enqueue as _enqueue
            _enqueue(repo, str(raw_file.rel_path), branch, source="cli")
            console.print("[dim][Tier 2] queued for async worker[/dim]")
        else:
            # existing inline synth code (unchanged)
            console.print("[dim][Tier 2] synthesizing...[/dim]")
            try:
                from compost.synth.agent import synthesize
                from compost.synth.diff_writer import apply_diffs, commit_diffs
                synth_result = synthesize(raw_file.path, repo)
                ...
```

---

### Step 4 — Entire shim: `shims/entire_shim.py`

#### 4a. Write `test_shims.py` — `entire_shim` tests (RED)

```python
# tools/compost/tests/test_shims.py

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
    (repo / filename).write_text("content")
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

    # commit touches docs/, not src/ — should not be materialized
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
```

Run `just test tests/test_shims.py` — fails (module not found).

#### 4b. Implement `shims/entire_shim.py` (GREEN)

```python
from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from compost.ingest.raw import write_raw


@dataclass
class WatchTarget:
    repo: Path
    branch_prefix: str = ""
    path_filter: str = ""
    gh_name: str = ""   # optional "owner/repo" for gh_webhook_shim mapping
    # Multiple WatchTargets with the same repo but different path_filter values are supported.
    # The shim tracks "last seen" SHA per target independently.
    # A commit touching both watched paths produces two checkpoint files and two synthesis jobs.


@dataclass
class EntireShimConfig:
    targets: list[WatchTarget]
    poll_interval_s: int = 10


def materialize_commit(
    compost_repo: Path,
    source_repo: Path,
    sha: str,
    message: str,
    changed_files: list[str],
    target: WatchTarget,
) -> Path | None:
    """Write a checkpoint raw file for one commit. Returns path, or None if filtered out.

    A commit is filtered out when target.path_filter is set and none of the changed_files
    have that prefix.
    """
    if target.path_filter:
        matches = [f for f in changed_files if f.startswith(target.path_filter)]
        if not matches:
            return None

    slug = _slugify(message)
    raw_file = write_raw(
        compost_repo,
        source="checkpoint",
        title=message,
        body=_checkpoint_body(source_repo, sha, message, changed_files),
        captured_by="git",
        origin=sha,
    )
    return raw_file.path


def _checkpoint_body(
    source_repo: Path, sha: str, message: str, changed_files: list[str]
) -> str:
    lines = [
        f"Repo: {source_repo.name}",
        f"SHA: {sha}",
        f"Message: {message}",
        "",
        "Changed files:",
    ]
    for f in changed_files:
        lines.append(f"- {f}")
    return "\n".join(lines)


def _slugify(text: str) -> str:
    import re
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")[:60]


def _new_commits(
    source_repo: Path, since_sha: str | None, branch_prefix: str
) -> Iterator[tuple[str, str, list[str]]]:
    """Yield (sha, message, changed_files) for commits newer than since_sha."""
    rev_range = f"{since_sha}..HEAD" if since_sha else "HEAD"
    args = ["git", "log", rev_range, "--format=%H %s", "--reverse"]
    result = subprocess.run(args, cwd=source_repo, capture_output=True, text=True)
    if result.returncode != 0:
        return

    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        sha, _, message = line.partition(" ")
        files_result = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "-r", "--name-only", sha],
            cwd=source_repo, capture_output=True, text=True,
        )
        changed = [f for f in files_result.stdout.splitlines() if f]
        yield sha, message, changed


def _state_path(compost_repo: Path) -> Path:
    return compost_repo / ".compost" / "shims" / "entire-state.json"


def _load_state(compost_repo: Path) -> dict:
    path = _state_path(compost_repo)
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save_state(compost_repo: Path, state: dict) -> None:
    path = _state_path(compost_repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def _target_key(target: WatchTarget) -> str:
    return f"{target.repo}:{target.path_filter}"


def run_entire_shim(compost_repo: Path, config: EntireShimConfig) -> None:
    """Blocking poll loop. Run in a thread or call directly."""
    from compost.worker.queue import enqueue

    while True:
        state = _load_state(compost_repo)
        for target in config.targets:
            key = _target_key(target)
            last_sha = state.get(key)
            new_last_sha = last_sha
            for sha, message, changed_files in _new_commits(
                target.repo, last_sha, target.branch_prefix
            ):
                raw_path = materialize_commit(
                    compost_repo=compost_repo,
                    source_repo=target.repo,
                    sha=sha,
                    message=message,
                    changed_files=changed_files,
                    target=target,
                )
                if raw_path is not None:
                    rel = raw_path.relative_to(compost_repo)
                    enqueue(compost_repo, str(rel), "entire-shim", source="entire_shim")
                new_last_sha = sha
            if new_last_sha != last_sha:
                state[key] = new_last_sha
                _save_state(compost_repo, state)
        time.sleep(config.poll_interval_s)
```

Run `just test tests/test_shims.py` — entire_shim tests green.

---

### Step 5 — Slack shim: `shims/slack_shim.py`

#### 5a. Add slack shim tests to `test_shims.py` (RED)

```python
# append to test_shims.py

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

    # Raw file should exist under raw/slack/
    raw_dir = compost_git_repo_with_queue / "raw" / "slack"
    md_files = list(raw_dir.rglob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text()
    assert "We decided to use Stripe for payments." in content

    # Job should be in inbox
    inbox = compost_git_repo_with_queue / ".compost" / "queue" / "inbox"
    assert len(list(inbox.glob("*.json"))) == 1
```

Note: `compost_git_repo_with_queue` does not have `raw/slack/` directory. Add it to the fixture
or create it in the shim's `write_raw` call (which calls `mkdir(parents=True, exist_ok=True)`
already — check `ingest/raw.py`: yes, `abs_path.parent.mkdir(parents=True, exist_ok=True)` is
already there). So no fixture change needed.

#### 5b. Implement `shims/slack_shim.py` (GREEN)

```python
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

from compost.ingest.raw import write_raw
from compost.worker.queue import enqueue


class SlackReactionPayload(BaseModel):
    channel: str
    thread_ts: str
    emoji: str
    text: str
    user: str


def make_app(compost_repo: Path, wiki_emoji: str = "wiki") -> FastAPI:
    """Factory: returns a FastAPI app bound to a specific compost repo and emoji config.

    Using a factory (not module-level app) keeps the repo path injectable for tests.
    """
    app = FastAPI()

    @app.post("/events")
    def events(payload: SlackReactionPayload):
        if payload.emoji != wiki_emoji:
            return {"status": "ignored"}

        raw_file = write_raw(
            compost_repo,
            source="slack",
            title=f"slack-{payload.channel}-{payload.thread_ts}",
            body=payload.text,
            captured_by=payload.user,
            origin=f"slack://{payload.channel}/{payload.thread_ts}",
            channel=payload.channel,
        )
        rel = str(raw_file.rel_path)
        # Branch name is not a real git branch here — the shim doesn't commit.
        # The worker will run synthesis; the branch is recorded for provenance only.
        job = enqueue(compost_repo, rel, branch=f"shim/slack/{payload.thread_ts}",
                      source="slack_shim")
        return {"status": "enqueued", "job_id": job.id}

    return app
```

Note: the slack shim calls `write_raw` but does NOT create a git branch or commit. The raw file
is written directly to disk. The synthesis worker will handle git operations. This is a deliberate
simplification: the shim is a fast receiver, not a git actor.

Run `just test tests/test_shims.py` — slack tests green.

---

### Step 6 — GitHub webhook shim: `shims/gh_webhook_shim.py`

#### 6a. Add gh_webhook tests to `test_shims.py` (RED)

```python
# append to test_shims.py

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

    # A raw/commits file and a queued job should exist
    commits_dir = compost_git_repo_with_queue / "raw" / "commits"
    md_files = list(commits_dir.rglob("*.md")) if commits_dir.exists() else []
    assert len(md_files) == 1

    inbox = compost_git_repo_with_queue / ".compost" / "queue" / "inbox"
    assert len(list(inbox.glob("*.json"))) == 1
```

#### 6b. Implement `shims/gh_webhook_shim.py` (GREEN)

```python
from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from compost.ingest.raw import write_raw
from compost.worker.queue import enqueue

_SYNTH_LABEL = "compost/synth"
_TRIGGER_ACTIONS = {"opened", "labeled"}


class GitHubPRPayload(BaseModel):
    action: str
    pr_number: int
    branch: str
    labels: list[str]
    repo_full_name: str   # e.g. "acme/payments" — identifies source repo


def make_app(compost_repo: Path) -> FastAPI:
    """Factory: returns a FastAPI app bound to a specific compost repo."""
    app = FastAPI()

    @app.post("/webhook")
    def webhook(payload: GitHubPRPayload):
        if payload.action not in _TRIGGER_ACTIONS:
            return {"status": "ignored", "reason": "action"}
        if _SYNTH_LABEL not in payload.labels:
            return {"status": "ignored", "reason": "label"}

        body = (
            f"GitHub PR #{payload.pr_number} on `{payload.branch}`\n"
            f"Repo: {payload.repo_full_name}\n"
            f"Action: {payload.action}\n"
            f"Labels: {', '.join(payload.labels)}\n"
        )
        raw_file = write_raw(
            compost_repo,
            source="commits",
            title=f"pr-{payload.repo_full_name.replace('/', '-')}-{payload.pr_number}",
            body=body,
            captured_by="gh_webhook_shim",
            origin=f"github://{payload.repo_full_name}/pull/{payload.pr_number}",
        )
        rel = str(raw_file.rel_path)
        job = enqueue(
            compost_repo, rel,
            branch=payload.branch,
            source="gh_webhook_shim",
        )
        return {"status": "enqueued", "job_id": job.id}

    return app
```

Run `just test tests/test_shims.py` — all shim tests green.

---

### Step 7 — Supervisor: `shims/supervisor.py`

The supervisor is process-management glue. Tests for PID file writing are lightweight.

#### 7a. Add supervisor tests to `test_shims.py` (RED)

```python
# append to test_shims.py

def test_supervisor_writes_and_clears_pid_files(
    compost_git_repo_with_queue: Path,
    tmp_path: Path,
) -> None:
    """Supervisor writes PID files on start and removes them on stop."""
    from compost.shims.supervisor import write_pid, clear_pid

    pid_dir = compost_git_repo_with_queue / ".compost" / "shims"
    write_pid(pid_dir, "test_shim", 99999)
    pid_file = pid_dir / "test_shim.pid"
    assert pid_file.exists()
    assert pid_file.read_text().strip() == "99999"

    clear_pid(pid_dir, "test_shim")
    assert not pid_file.exists()
```

#### 7b. Implement `shims/supervisor.py` (GREEN)

```python
"""Supervisor for local shims.

Starts slack_shim and gh_webhook_shim as uvicorn subprocesses.
Runs entire_shim in a background thread within this process.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ShimsConfig:
    slack_port: int = 8421
    slack_wiki_emoji: str = "wiki"
    gh_webhook_port: int = 8422
    entire_targets: list[Any] = field(default_factory=list)  # list[WatchTarget]
    entire_poll_interval_s: int = 10


def load_shims_config(repo: Path) -> ShimsConfig:
    from compost.repo import load_repo_config
    config = load_repo_config(repo)
    s = config.get("shims", {})
    slack = s.get("slack", {})
    gh = s.get("gh_webhook", {})
    entire = s.get("entire", {})

    targets = []
    from compost.shims.entire_shim import WatchTarget
    for t in entire.get("targets", []):
        targets.append(WatchTarget(
            repo=Path(t["repo"]).expanduser(),
            branch_prefix=t.get("branch_prefix", ""),
            path_filter=t.get("path_filter", ""),
            gh_name=t.get("gh_name", ""),
        ))

    return ShimsConfig(
        slack_port=int(slack.get("port", 8421)),
        slack_wiki_emoji=slack.get("wiki_emoji", "wiki"),
        gh_webhook_port=int(gh.get("port", 8422)),
        entire_targets=targets,
        entire_poll_interval_s=int(entire.get("poll_interval_s", 10)),
    )


def write_pid(pid_dir: Path, name: str, pid: int) -> None:
    pid_dir.mkdir(parents=True, exist_ok=True)
    (pid_dir / f"{name}.pid").write_text(str(pid))


def clear_pid(pid_dir: Path, name: str) -> None:
    path = pid_dir / f"{name}.pid"
    path.unlink(missing_ok=True)


def start_shims(repo: Path, config: ShimsConfig) -> None:
    """Launch shims. Blocks until Ctrl-C or SIGTERM."""
    pid_dir = repo / ".compost" / "shims"
    procs: list[subprocess.Popen] = []

    # Launch slack_shim via uvicorn
    slack_proc = _launch_uvicorn(
        repo=repo,
        module="compost.shims.slack_shim",
        factory_args=f"--repo {repo} --wiki-emoji {config.slack_wiki_emoji}",
        port=config.slack_port,
        pid_dir=pid_dir,
        name="slack_shim",
        app_factory=True,
    )
    if slack_proc:
        procs.append(slack_proc)

    # Launch gh_webhook_shim via uvicorn
    gh_proc = _launch_uvicorn(
        repo=repo,
        module="compost.shims.gh_webhook_shim",
        factory_args=f"--repo {repo}",
        port=config.gh_webhook_port,
        pid_dir=pid_dir,
        name="gh_webhook",
        app_factory=True,
    )
    if gh_proc:
        procs.append(gh_proc)

    # Launch entire_shim in a thread
    entire_thread = None
    if config.entire_targets:
        from compost.shims.entire_shim import EntireShimConfig, run_entire_shim
        entire_cfg = EntireShimConfig(
            targets=config.entire_targets,
            poll_interval_s=config.entire_poll_interval_s,
        )
        entire_thread = threading.Thread(
            target=run_entire_shim, args=(repo, entire_cfg), daemon=True
        )
        entire_thread.start()

    try:
        for proc in procs:
            proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for proc in procs:
            _terminate(proc)
        for name in ("slack_shim", "gh_webhook"):
            clear_pid(pid_dir, name)


def stop_shims(repo: Path) -> None:
    """Read PID files and send SIGTERM to running shim processes."""
    pid_dir = repo / ".compost" / "shims"
    for name in ("slack_shim", "gh_webhook"):
        pid_file = pid_dir / f"{name}.pid"
        if not pid_file.exists():
            continue
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            clear_pid(pid_dir, name)
        except (ProcessLookupError, ValueError):
            clear_pid(pid_dir, name)


def _terminate(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


def _launch_uvicorn(
    repo: Path,
    module: str,
    factory_args: str,
    port: int,
    pid_dir: Path,
    name: str,
    app_factory: bool = False,
) -> subprocess.Popen | None:
    # For now: launch uvicorn pointing at the module's `app` object.
    # app_factory=True is noted here for future --factory support.
    cmd = [sys.executable, "-m", "uvicorn", f"{module}:app", "--port", str(port)]
    env = {**os.environ, "COMPOST_REPO": str(repo)}
    try:
        proc = subprocess.Popen(cmd, env=env)
        write_pid(pid_dir, name, proc.pid)
        return proc
    except FileNotFoundError:
        return None
```

Note: the current `slack_shim.py` uses a factory `make_app()`. For uvicorn to serve it, we need
a module-level `app`. Add to the bottom of `slack_shim.py` and `gh_webhook_shim.py`:

```python
# Module-level app for uvicorn. Requires COMPOST_REPO env var.
import os as _os
if _os.environ.get("COMPOST_REPO"):
    app = make_app(
        compost_repo=Path(_os.environ["COMPOST_REPO"]),
        wiki_emoji=_os.environ.get("COMPOST_WIKI_EMOJI", "wiki"),
    )
```

Similarly for `gh_webhook_shim.py`:
```python
import os as _os
if _os.environ.get("COMPOST_REPO"):
    app = make_app(compost_repo=Path(_os.environ["COMPOST_REPO"]))
```

Run `just test tests/test_shims.py` — all green.

---

### Step 8 — CLI: `shims`, `slack`, `entire`, `gh` command groups in `cli.py`

Add after `worker_group`:

```python
# ── shims commands ────────────────────────────────────────────────────────────


@main.group("shims")
def shims_group() -> None:
    """Manage local ingestion shims (Slack, Entire, GitHub webhook)."""


@shims_group.command("up")
@click.pass_context
def shims_up(ctx: click.Context) -> None:
    """Start all configured shims (blocking; Ctrl-C to stop)."""
    from compost.shims.supervisor import load_shims_config, start_shims

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    config = load_shims_config(repo)
    console.print(
        f"[dim]shims up — slack :{config.slack_port}, "
        f"gh_webhook :{config.gh_webhook_port}[/dim]"
    )
    start_shims(repo, config)


@shims_group.command("down")
@click.pass_context
def shims_down(ctx: click.Context) -> None:
    """Send SIGTERM to running shims (reads PID files)."""
    from compost.shims.supervisor import stop_shims

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    stop_shims(repo)
    console.print("[green]✓[/green] shims stopped")


@shims_group.command("status")
@click.pass_context
def shims_status(ctx: click.Context) -> None:
    """Print per-shim PID and port."""
    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    pid_dir = repo / ".compost" / "shims"
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Shim")
    table.add_column("PID")
    table.add_column("Status")
    for name in ("slack_shim", "gh_webhook"):
        pid_file = pid_dir / f"{name}.pid"
        if pid_file.exists():
            pid = pid_file.read_text().strip()
            import os
            try:
                os.kill(int(pid), 0)
                status = "[green]running[/green]"
            except ProcessLookupError:
                status = "[red]dead (stale PID)[/red]"
        else:
            pid = "—"
            status = "[dim]stopped[/dim]"
        table.add_row(name, pid, status)
    console.print(table)


# ── slack commands ────────────────────────────────────────────────────────────


@main.group("slack")
def slack_group() -> None:
    """Slack shim helpers."""


@slack_group.command("fake-react")
@click.option("--channel", required=True)
@click.option("--ts", "thread_ts", required=True, help="Slack message timestamp.")
@click.option("--text", required=True, help="Message body.")
@click.option("--emoji", default="wiki", show_default=True)
@click.option("--user", default="cli-test-user", show_default=True)
@click.pass_context
def slack_fake_react(ctx: click.Context, channel: str, thread_ts: str,
                     text: str, emoji: str, user: str) -> None:
    """POST a fake Slack reaction event to the running slack_shim."""
    import httpx
    from compost.shims.supervisor import load_shims_config

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    config = load_shims_config(repo)
    url = f"http://localhost:{config.slack_port}/events"
    payload = {
        "channel": channel,
        "thread_ts": thread_ts,
        "emoji": emoji,
        "text": text,
        "user": user,
    }
    try:
        resp = httpx.post(url, json=payload, timeout=5)
        console.print(f"[green]✓[/green] {resp.status_code} {resp.json()}")
    except httpx.ConnectError:
        console.print(f"[red]Could not connect to slack_shim at {url}[/red]")
        console.print("Is [bold]compost shims up[/bold] running?")
        sys.exit(1)


# ── entire commands ───────────────────────────────────────────────────────────


@main.group("entire")
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
def entire_seed(ctx: click.Context, source_repo: Path,
                sha: str, path_filter: str) -> None:
    """Materialize one commit as a checkpoint raw file (no watcher needed)."""
    import subprocess
    from compost.shims.entire_shim import WatchTarget, materialize_commit
    from compost.worker.queue import enqueue

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    # Get commit message and changed files
    msg_result = subprocess.run(
        ["git", "log", "-1", "--format=%s", sha],
        cwd=source_repo, capture_output=True, text=True, check=True,
    )
    message = msg_result.stdout.strip()

    files_result = subprocess.run(
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
    job = enqueue(repo, str(rel), branch=f"shim/entire/{sha[:8]}", source="entire_shim")
    console.print(f"[green]✓[/green] {rel}")
    console.print(f"job: {job.id[:8]}")


# ── gh commands ───────────────────────────────────────────────────────────────


@main.group("gh")
def gh_group() -> None:
    """GitHub webhook shim helpers."""


@gh_group.command("fake-pr")
@click.option("--branch", required=True, help="PR source branch.")
@click.option("--action", default="opened", show_default=True,
              type=click.Choice(["opened", "labeled", "synchronize"]))
@click.option("--pr-number", default=1, type=int, show_default=True)
@click.option("--repo-name", default="",
              help="GitHub repo full name (e.g. acme/payments).")
@click.option("--labels", default="compost/synth", show_default=True,
              help="Comma-separated labels.")
@click.pass_context
def gh_fake_pr(ctx: click.Context, branch: str, action: str,
               pr_number: int, repo_name: str, labels: str) -> None:
    """POST a fake GitHub PR event to the running gh_webhook_shim."""
    import httpx
    from compost.shims.supervisor import load_shims_config

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    config = load_shims_config(repo)
    url = f"http://localhost:{config.gh_webhook_port}/webhook"
    payload = {
        "action": action,
        "pr_number": pr_number,
        "branch": branch,
        "labels": [l.strip() for l in labels.split(",")],
        "repo_full_name": repo_name,
    }
    try:
        resp = httpx.post(url, json=payload, timeout=5)
        console.print(f"[green]✓[/green] {resp.status_code} {resp.json()}")
    except httpx.ConnectError:
        console.print(f"[red]Could not connect to gh_webhook_shim at {url}[/red]")
        console.print("Is [bold]compost shims up[/bold] running?")
        sys.exit(1)
```

---

### Step 9 — Documentation: `README.md`

Append to `tools/compost/README.md` after the existing `## Commands` section:

```markdown
### `compost worker up`

Start the async synthesis worker. When `.compost.yml` has `worker: {mode: async}`, raw
ingestion enqueues jobs instead of running synthesis inline. The worker drains the queue:
synthesizes wiki edits, runs adversarial checks, then marks jobs done or dead.

```bash
COMPOST_REPO=/path/to/wiki compost worker up
# Ctrl-C to stop

COMPOST_REPO=/path/to/wiki compost worker status
# Shows queue depths: inbox / processing / done / dead

COMPOST_REPO=/path/to/wiki compost worker retry --job-id <hex>
# Move a dead-letter job back to inbox
```

Config in `.compost.yml`:
```yaml
worker:
  mode: async          # inline (default) or async
  poll_interval_s: 5
  max_retries: 3
  retry_backoff_base_s: 30
```

### `compost shims up`

Start local ingestion shims that mimic Slack, Entire, and GitHub webhooks on localhost.

```bash
COMPOST_REPO=/path/to/wiki compost shims up
# Ctrl-C to stop

# Trigger a fake Slack reaction
compost slack fake-react --channel eng --ts 1234567890.000 --text "We chose Stripe"

# Seed one checkpoint from a local repo
compost entire seed --repo ~/workspace/payments --sha abc123

# Simulate a GitHub PR webhook
compost gh fake-pr --branch raw/2026-05-05T1234-foo --repo-name acme/payments
```

Config in `.compost.yml`:
```yaml
shims:
  slack:
    port: 8421
    wiki_emoji: wiki
  gh_webhook:
    port: 8422
  entire:
    poll_interval_s: 10
    targets:
      - repo: ~/workspace/payments-service
        path_filter: ""
      - repo: ~/workspace/auth-service
        path_filter: "src/"
```

Install shim dependencies: `pip install "compost[shims]"` or `uv pip install ".[shims]"`.
```

---

### Step 10 — AGENTS.md update

Append to `AGENTS.md`:

```markdown
## Worker and shim test isolation

Tests for `worker/queue.py` use a plain `tmp_path` fixture (no git needed — the queue is
pure filesystem). Tests for `shims/` use `compost_git_repo_with_queue` from `conftest.py`.

Shim tests use `fastapi.testclient.TestClient` (synchronous, no real uvicorn port). The
`fastapi` package is a `dev` extra — it is available in `just test` without needing `[shims]`.

The entire_shim's `materialize_commit()` writes raw files directly to disk (no git commit).
The git branch recorded in the enqueued job is a synthetic `shim/entire/{sha}` string for
provenance only. The synthesis worker handles git operations.
```

---

### Step 11 — Run full test suite

```bash
just test
```

All existing tests must remain green. The new tests must all pass.

---

## § Verification checklist

- [ ] `just test` passes with no failures
- [ ] `compost worker status` prints four queue rows
- [ ] `compost worker up` starts and exits cleanly on Ctrl-C
- [ ] `compost entire seed --repo <local-repo> --sha <sha>` writes a file under `raw/checkpoints/`
  and enqueues a job (visible in `compost worker status`)
- [ ] `compost shims up` prints port info without error (even if uvicorn not installed, should
  degrade gracefully with a clear message)
- [ ] Dead-letter markdown files land in `_plans/dead-letter/`
- [ ] `compost worker retry --job-id <hex>` moves a dead job back to inbox

---

## § Dropped / deferred from high-level plan

Nothing dropped. The `compost shims status` command checks liveness via `os.kill(pid, 0)`,
which is a clean POSIX signal check with no extra dependencies. The supervisor runs the
FastAPI shims as uvicorn subprocesses (one process each) rather than in-process threads,
matching the high-level plan's "launched as uvicorn subprocesses" spec. The entire_shim
runs in a thread within the supervisor process as specified.
