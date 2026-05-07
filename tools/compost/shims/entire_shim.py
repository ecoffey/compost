"""Entire shim: polls one or more local git repos and materializes new commits as checkpoints.

No HTTP server — runs as a polling loop in a background thread within the supervisor process.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
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
    """Write a checkpoint raw file for one commit. Returns absolute path, or None if filtered out.

    A commit is filtered out when target.path_filter is set and none of the changed_files
    have that prefix.
    """
    if target.path_filter:
        matches = [f for f in changed_files if f.startswith(target.path_filter)]
        if not matches:
            return None

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


def _new_commits(
    source_repo: Path, since_sha: str | None, branch_prefix: str = ""
) -> Iterator[tuple[str, str, list[str]]]:
    """Yield (sha, message, changed_files) for commits newer than since_sha.

    When branch_prefix is set, only commits reachable from branches whose name
    starts with that prefix are yielded (e.g. branch_prefix="feature/" skips main).
    """
    if branch_prefix:
        rev_args = [f"--branches={branch_prefix}*"]
        if since_sha:
            rev_args = [f"^{since_sha}"] + rev_args
    else:
        rev_args = [f"{since_sha}..HEAD" if since_sha else "HEAD"]

    result = subprocess.run(
        ["git", "log", *rev_args, "--format=%H %s", "--reverse"],
        cwd=source_repo, capture_output=True, text=True,
    )
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
            for sha, message, changed_files in _new_commits(target.repo, last_sha, target.branch_prefix):
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
                    enqueue(
                        compost_repo, str(rel),
                        branch=f"shim/entire/{sha[:8]}",
                        source="entire_shim",
                    )
                new_last_sha = sha
            if new_last_sha != last_sha:
                state[key] = new_last_sha
                _save_state(compost_repo, state)
        time.sleep(config.poll_interval_s)
