from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from compost.repo import load_repo_config
from compost.worker.queue import (
    Job,
    _inbox,
    _job_filename,
    _processing,
    claim_next,
    complete,
    dead_letter,
    requeue_stuck,
)


@dataclass
class WorkerConfig:
    poll_interval_s: int = 5
    max_retries: int = 3
    retry_backoff_base_s: int = 30   # delay = base * 2^(retry_count - 1)


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
    stuck = requeue_stuck(repo)
    if stuck:
        print(f"[worker] requeued {stuck} stuck job(s)")
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
    import subprocess as _sp
    from compost.synth.agent import synthesize, load_synth_config
    from compost.synth.diff_writer import apply_diffs, commit_diffs
    from compost.checks.runner import run_checks, load_checks_config
    from compost.ingest.git import default_branch

    print(f"[worker] claim  {job.id[:8]}  {job.source}  {job.raw_rel}")
    raw_path = repo / job.raw_rel
    base = default_branch(repo)

    # Sanitize branch name: git forbids colons and other chars in ref names.
    import re as _re
    git_branch = _re.sub(r"[^a-zA-Z0-9._/-]", "-", job.branch)

    # Switch to the job branch, creating it from base if it doesn't exist yet.
    _sp.run(["git", "checkout", base], cwd=repo, check=True, capture_output=True)
    branch_exists = _sp.run(
        ["git", "branch", "--list", git_branch], cwd=repo, capture_output=True, text=True,
    ).stdout.strip()
    if branch_exists:
        # Branch already has the raw file committed. The untracked copy on main would
        # block the checkout, so remove it first — git restores it from the commit.
        if raw_path.exists():
            raw_path.unlink()
        _sp.run(["git", "checkout", git_branch], cwd=repo, check=True, capture_output=True)
    else:
        _sp.run(["git", "checkout", "-b", git_branch], cwd=repo, check=True, capture_output=True)
    _sp.run(["git", "add", str(raw_path.relative_to(repo))], cwd=repo, check=True)
    staged = _sp.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=repo, capture_output=True, text=True,
    ).stdout.strip()
    if staged:
        raw_commit = _sp.run(
            ["git", "commit", "-m", f"raw: {raw_path.stem}"],
            cwd=repo, capture_output=True, text=True,
        )
        if raw_commit.returncode != 0:
            detail = raw_commit.stderr.strip() or raw_commit.stdout.strip()
            _handle_failure(repo, job, config, f"Failed to commit raw file: {detail}")
            _sp.run(["git", "checkout", base], cwd=repo, capture_output=True)
            return

    try:
        synth_cfg = load_synth_config(repo)
        print(f"[worker] synth  {job.id[:8]}  synthesizing...")
        result = synthesize(raw_path, repo, config=synth_cfg)
        if result.wiki_diffs:
            for diff in result.wiki_diffs:
                print(f"[worker]          {diff.rel_path}")
            written = apply_diffs(repo, result)
            commit_diffs(repo, written, result.run_id)
            checks_cfg = load_checks_config(repo)
            run_checks(repo, git_branch, raw_path, config=checks_cfg)
        else:
            print(f"[worker]          no wiki diffs")

        complete(repo, job)
        print(f"[worker] done   {job.id[:8]}")
        _push_and_pr(repo, git_branch, result)
    except Exception as exc:
        _handle_failure(repo, job, config, str(exc))
    finally:
        _sp.run(["git", "checkout", base], cwd=repo, capture_output=True)


def _push_and_pr(repo: Path, branch: str, result: object) -> None:
    """Push branch to origin and open a Gitea PR. Skips gracefully if not configured."""
    import os
    from compost.ingest.git import push_branch
    from compost.repo import load_repo_config

    config = load_repo_config(repo)
    gitea_cfg = config.get("gitea")
    token = os.environ.get("GITEA_TOKEN", "")
    if not gitea_cfg or not token:
        print(f"[worker] branch {branch}  (no Gitea config — push manually)")
        return

    try:
        from compost.gitea.client import GiteaClient, GiteaError
        from compost.ingest.pr import create_pr

        push_branch(repo, "origin", branch)
        client = GiteaClient(
            url=gitea_cfg["url"],
            owner=gitea_cfg["owner"],
            repo=gitea_cfg["repo"],
            token=token,
        )
        pr = create_pr(repo, branch, client, result=result)
        print(f"[worker] pr     {pr.url}")
    except Exception as exc:
        print(f"[worker] push/PR failed: {exc}  (push manually: git push origin {branch})")


def _handle_failure(repo: Path, job: Job, config: WorkerConfig, error: str) -> None:
    from datetime import datetime, timedelta, timezone
    import os

    if job.retry_count >= config.max_retries:
        dead_letter(repo, job, error)
        print(f"[worker] dead   {job.id[:8]}  {error[:120]}")
        return

    job.retry_count += 1
    backoff_s = config.retry_backoff_base_s * (2 ** (job.retry_count - 1))
    job.next_retry_at = (
        datetime.now(timezone.utc) + timedelta(seconds=backoff_s)
    ).isoformat()
    job.last_error = error

    print(f"[worker] retry  {job.id[:8]}  attempt {job.retry_count}/{config.max_retries}  {error[:80]}")

    # Rewrite job file in processing/ then move back to inbox/
    proc_path = _processing(repo) / _job_filename(job)
    proc_path.write_text(json.dumps(asdict(job)))
    _inbox(repo).mkdir(parents=True, exist_ok=True)
    os.rename(proc_path, _inbox(repo) / _job_filename(job))
