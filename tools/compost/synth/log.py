from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def open_run(repo: Path, run_id: str) -> Path:
    """Create .compost/synth-log/{date}T{HHmmss}-{run_id}.jsonl. Return path."""
    log_dir = repo / ".compost" / "synth-log"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
    path = log_dir / f"{ts}-{run_id}.jsonl"
    path.touch()
    return path


def log_event(log_path: Path, event_type: str, **kwargs) -> None:
    """Append one JSONL line: {"event": event_type, "ts": ..., **kwargs}."""
    entry = {
        "event": event_type,
        "ts": datetime.now(timezone.utc).isoformat(),
        **kwargs,
    }
    with log_path.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def read_runs(repo: Path, last: int = 10) -> list[dict]:
    """Read run_complete events from the last N log files, newest first."""
    log_dir = repo / ".compost" / "synth-log"
    if not log_dir.exists():
        return []
    files = sorted(log_dir.glob("*.jsonl"), reverse=True)[:last]
    summaries = []
    for path in files:
        for line in path.read_text().splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "run_complete":
                summaries.append(event)
                break
    return summaries
