"""
Lifecycle lint runner. Runs all linters in parallel and logs results to JSONL.

To run weekly on macOS, add to crontab (crontab -e):
    0 9 * * 1 cd /path/to/wiki && compost lint run --emit-pr >> ~/.compost/lint-cron.log 2>&1

Or use launchd — save the following to ~/Library/LaunchAgents/com.compost.lint.plist:

    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
        "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0">
    <dict>
        <key>Label</key><string>com.compost.lint</string>
        <key>ProgramArguments</key>
        <array>
            <string>/path/to/venv/bin/compost</string>
            <string>lint</string><string>run</string><string>--emit-pr</string>
        </array>
        <key>StartCalendarInterval</key>
        <dict>
            <key>Weekday</key><integer>1</integer>
            <key>Hour</key><integer>9</integer>
            <key>Minute</key><integer>0</integer>
        </dict>
        <key>WorkingDirectory</key><string>/path/to/wiki</string>
    </dict>
    </plist>

    Load with: launchctl load ~/Library/LaunchAgents/com.compost.lint.plist
"""
from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from compost.lint import LintResult
from compost.lint.linters import (
    confidence_mismatch,
    expired_research,
    false_negative_sampler,
    missing_owners,
    orphaned_raw,
    stale_citations,
)


def run_lint(
    repo: Path,
    since_days: int = 90,
    run_id: str | None = None,
) -> list[LintResult]:
    """Run all linters in parallel, log JSONL, return results sorted by linter name."""
    if run_id is None:
        run_id = uuid.uuid4().hex[:8]

    log_path = _open_log(repo, run_id)
    _write_log(log_path, "lint_start", run_id=run_id, since_days=since_days)

    linter_calls = [
        (stale_citations.run, {"repo": repo}),
        (expired_research.run, {"repo": repo}),
        (confidence_mismatch.run, {"repo": repo}),
        (orphaned_raw.run, {"repo": repo, "since_days": since_days}),
        (missing_owners.run, {"repo": repo}),
        (false_negative_sampler.run, {"repo": repo, "since_days": since_days}),
    ]

    results: list[LintResult] = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(_run_one, fn, kwargs): fn
            for fn, kwargs in linter_calls
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            _write_log(
                log_path, "linter_result",
                linter=result.linter,
                status=result.status,
                findings_count=len(result.findings),
                duration_s=result.duration_s,
            )

    results.sort(key=lambda r: r.linter)

    total = sum(len(r.findings) for r in results)
    _write_log(log_path, "lint_complete", run_id=run_id, total_findings=total)

    return results


def read_runs(repo: Path, last: int = 10) -> list[dict]:
    """Read lint_complete events from the last N log files, newest first."""
    log_dir = repo / ".compost" / "lint-log"
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
            if event.get("event") == "lint_complete":
                summaries.append(event)
                break
    return summaries


# ── private ───────────────────────────────────────────────────────────────────


def _run_one(fn, kwargs: dict) -> LintResult:
    start = time.monotonic()
    try:
        result = fn(**kwargs)
    except Exception as e:
        linter = fn.__module__.split(".")[-1]
        return LintResult(
            linter=linter,
            status="skipped",
            duration_s=round(time.monotonic() - start, 2),
            skip_reason=str(e),
        )
    result.duration_s = round(time.monotonic() - start, 2)
    return result


def _open_log(repo: Path, run_id: str) -> Path:
    log_dir = repo / ".compost" / "lint-log"
    log_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = log_dir / f"{date}-{run_id}.jsonl"
    path.touch()
    return path


def _write_log(log_path: Path, event_type: str, **kwargs) -> None:
    entry = {
        "event": event_type,
        "ts": datetime.now(timezone.utc).isoformat(),
        **kwargs,
    }
    with log_path.open("a") as f:
        f.write(json.dumps(entry) + "\n")
