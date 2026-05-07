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
    """Read shims: block from .compost.yml; fall back to ShimsConfig defaults."""
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
    """Launch shims. Blocks until Ctrl-C."""
    pid_dir = repo / ".compost" / "shims"
    procs: list[subprocess.Popen] = []

    slack_proc = _launch_uvicorn(
        module="compost.shims.slack_shim",
        port=config.slack_port,
        pid_dir=pid_dir,
        name="slack_shim",
        env_extra={
            "COMPOST_REPO": str(repo),
            "COMPOST_WIKI_EMOJI": config.slack_wiki_emoji,
        },
    )
    if slack_proc:
        procs.append(slack_proc)

    gh_proc = _launch_uvicorn(
        module="compost.shims.gh_webhook_shim",
        port=config.gh_webhook_port,
        pid_dir=pid_dir,
        name="gh_webhook",
        env_extra={"COMPOST_REPO": str(repo)},
    )
    if gh_proc:
        procs.append(gh_proc)

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
    module: str,
    port: int,
    pid_dir: Path,
    name: str,
    env_extra: dict[str, str],
) -> subprocess.Popen | None:
    cmd = [sys.executable, "-m", "uvicorn", f"{module}:app", "--port", str(port)]
    env = {**os.environ, **env_extra}
    try:
        proc = subprocess.Popen(cmd, env=env)
        write_pid(pid_dir, name, proc.pid)
        return proc
    except FileNotFoundError:
        return None
