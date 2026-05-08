# Phase 9 Implementation Detail

## § Source plan
`_plans/2026-05-07-2125-phase9-lifecycle-lint-impl.md`

---

## § Implementation Detail

All paths are relative to `/Users/eoin/workspace/compost/tools/compost/` unless noted.

### Step 1 — Add `replay_classify()` to `ingest/classifier.py`

This is the only change to an existing module. The false-negative sampler calls it directly instead of shelling out.

Append the following function at the end of `ingest/classifier.py` (after `_source_of`):

```python
def replay_classify(
    repo: Path,
    since_days: int,
) -> list[tuple[Path, ClassifyDecision]]:
    """Classify all raw/*.md files modified within since_days. Pure; does not log.

    Returns list of (absolute_path, ClassifyDecision) sorted newest-first by mtime.
    """
    from datetime import datetime, timedelta, timezone

    raw_dir = repo / "raw"
    if not raw_dir.exists():
        return []

    cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=since_days)).timestamp()
    files = sorted(
        (f for f in raw_dir.rglob("*.md") if f.stat().st_mtime >= cutoff_ts),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    rules = load_rules(repo)
    results: list[tuple[Path, ClassifyDecision]] = []
    for f in files:
        try:
            decision = classify(f, repo, rules=rules)
            results.append((f, decision))
        except Exception:
            continue
    return results
```

### Step 2 — Create `lint/__init__.py`

```python
# tools/compost/lint/__init__.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class LintFinding:
    linter: str
    severity: Literal["error", "warn"]
    message: str
    path: str | None = None   # repo-relative path; None for repo-wide findings


@dataclass
class LintResult:
    linter: str
    status: Literal["pass", "warn", "error", "skipped"]
    findings: list[LintFinding] = field(default_factory=list)
    duration_s: float = 0.0
    skip_reason: str | None = None
```

### Step 3 — Create `lint/linters/__init__.py`

Empty file.

```python
# tools/compost/lint/linters/__init__.py
```

### Step 4 — Create `lint/linters/stale_citations.py`

```python
# tools/compost/lint/linters/stale_citations.py
from __future__ import annotations

from pathlib import Path

from compost.lint import LintFinding, LintResult
from compost.model.frontmatter import parse_frontmatter

_SKIP_FILES = {"glossary.md", "index.md", "log.md"}


def run(repo: Path) -> LintResult:
    """Check wiki pages for sources: entries pointing to files missing on disk."""
    wiki_dir = repo / "wiki"
    if not wiki_dir.exists():
        return LintResult(linter="stale_citations", status="skipped",
                          skip_reason="no wiki/ directory")

    findings: list[LintFinding] = []
    for page in wiki_dir.rglob("*.md"):
        if page.name in _SKIP_FILES:
            continue
        fm, _ = parse_frontmatter(page)
        sources = fm.get("sources") or []
        rel_page = str(page.relative_to(repo))
        for src in sources:
            resolved = repo / src
            if not resolved.exists():
                findings.append(LintFinding(
                    linter="stale_citations",
                    severity="error",
                    message=f"source '{src}' does not exist on disk",
                    path=rel_page,
                ))

    status = "error" if findings else "pass"
    return LintResult(linter="stale_citations", status=status, findings=findings)
```

### Step 5 — Create `lint/linters/expired_research.py`

```python
# tools/compost/lint/linters/expired_research.py
from __future__ import annotations

import datetime
from pathlib import Path

from compost.lint import LintFinding, LintResult
from compost.model.frontmatter import parse_frontmatter


def run(repo: Path) -> LintResult:
    """Flag research files whose expires_at is in the past."""
    research_dir = repo / "raw" / "research"
    if not research_dir.exists():
        return LintResult(linter="expired_research", status="skipped",
                          skip_reason="no raw/research/ directory")

    today = datetime.date.today()
    findings: list[LintFinding] = []

    for f in research_dir.rglob("*.md"):
        fm, _ = parse_frontmatter(f)
        expires_at = fm.get("expires_at")
        if expires_at is None:
            continue

        exp_date = _to_date(expires_at)
        if exp_date is None or exp_date >= today:
            continue

        promotion_status = fm.get("promotion_status", "not_promoted")
        rel = str(f.relative_to(repo))
        if promotion_status == "not_promoted":
            findings.append(LintFinding(
                linter="expired_research",
                severity="error",
                message=f"expired {exp_date} and not promoted",
                path=rel,
            ))
        else:
            findings.append(LintFinding(
                linter="expired_research",
                severity="warn",
                message=f"expired {exp_date} (promoted; consider archiving)",
                path=rel,
            ))

    status = "pass"
    if any(f.severity == "error" for f in findings):
        status = "error"
    elif findings:
        status = "warn"
    return LintResult(linter="expired_research", status=status, findings=findings)


def _to_date(value) -> datetime.date | None:
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value)
        except ValueError:
            return None
    return None
```

### Step 6 — Create `lint/linters/confidence_mismatch.py`

```python
# tools/compost/lint/linters/confidence_mismatch.py
from __future__ import annotations

from pathlib import Path

from compost.lint import LintFinding, LintResult
from compost.model.frontmatter import parse_frontmatter

_SKIP_FILES = {"glossary.md", "index.md", "log.md"}


def run(repo: Path) -> LintResult:
    """Flag high-confidence wiki pages that cite low-confidence research."""
    wiki_dir = repo / "wiki"
    if not wiki_dir.exists():
        return LintResult(linter="confidence_mismatch", status="skipped",
                          skip_reason="no wiki/ directory")

    findings: list[LintFinding] = []

    for page in wiki_dir.rglob("*.md"):
        if page.name in _SKIP_FILES:
            continue
        fm, _ = parse_frontmatter(page)
        if fm.get("confidence") != "high":
            continue

        rel_page = str(page.relative_to(repo))
        for src in fm.get("sources") or []:
            # Only inspect research raw files
            if not src.startswith("raw/research/"):
                continue
            src_path = repo / src
            if not src_path.exists():
                continue
            src_fm, _ = parse_frontmatter(src_path)
            if src_fm.get("confidence") == "low":
                findings.append(LintFinding(
                    linter="confidence_mismatch",
                    severity="warn",
                    message=(
                        f"high-confidence page cites low-confidence research '{src}'"
                    ),
                    path=rel_page,
                ))

    status = "warn" if findings else "pass"
    return LintResult(linter="confidence_mismatch", status=status, findings=findings)
```

### Step 7 — Create `lint/linters/orphaned_raw.py`

```python
# tools/compost/lint/linters/orphaned_raw.py
from __future__ import annotations

import datetime
from pathlib import Path

from compost.lint import LintFinding, LintResult
from compost.model.frontmatter import parse_frontmatter


def run(repo: Path, since_days: int = 90) -> LintResult:
    """Flag raw files (within since_days) not cited in any wiki page's sources."""
    raw_dir = repo / "raw"
    wiki_dir = repo / "wiki"
    if not raw_dir.exists():
        return LintResult(linter="orphaned_raw", status="skipped",
                          skip_reason="no raw/ directory")

    # Build set of all cited source paths (repo-relative strings)
    cited: set[str] = set()
    if wiki_dir.exists():
        for page in wiki_dir.rglob("*.md"):
            fm, _ = parse_frontmatter(page)
            for src in fm.get("sources") or []:
                cited.add(src.replace("\\", "/"))

    cutoff_ts = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(days=since_days)
    ).timestamp()

    findings: list[LintFinding] = []
    for f in raw_dir.rglob("*.md"):
        if f.stat().st_mtime < cutoff_ts:
            continue
        rel = str(f.relative_to(repo)).replace("\\", "/")
        if rel not in cited:
            findings.append(LintFinding(
                linter="orphaned_raw",
                severity="warn",
                message=f"raw file not cited by any wiki page (within {since_days}d window)",
                path=rel,
            ))

    status = "warn" if findings else "pass"
    return LintResult(linter="orphaned_raw", status=status, findings=findings)
```

### Step 8 — Create `lint/linters/missing_owners.py`

```python
# tools/compost/lint/linters/missing_owners.py
from __future__ import annotations

from pathlib import Path

from compost.lint import LintFinding, LintResult
from compost.model.frontmatter import parse_frontmatter

_SKIP_FILES = {"glossary.md", "index.md", "log.md"}


def run(repo: Path) -> LintResult:
    """Flag wiki pages with absent or empty owners: field."""
    wiki_dir = repo / "wiki"
    if not wiki_dir.exists():
        return LintResult(linter="missing_owners", status="skipped",
                          skip_reason="no wiki/ directory")

    findings: list[LintFinding] = []
    for page in wiki_dir.rglob("*.md"):
        if page.name in _SKIP_FILES:
            continue
        fm, _ = parse_frontmatter(page)
        owners = fm.get("owners")
        if not owners:
            rel = str(page.relative_to(repo))
            findings.append(LintFinding(
                linter="missing_owners",
                severity="error",
                message="owners: is absent or empty",
                path=rel,
            ))

    status = "error" if findings else "pass"
    return LintResult(linter="missing_owners", status=status, findings=findings)
```

### Step 9 — Create `lint/linters/false_negative_sampler.py`

```python
# tools/compost/lint/linters/false_negative_sampler.py
from __future__ import annotations

import datetime
import json
from pathlib import Path

from compost.lint import LintFinding, LintResult


def run(repo: Path, since_days: int = 90) -> LintResult:
    """Find raw files that would fire Tier 1 but were never synthesized.

    Cross-references:
      - replay_classify() for which raws would fire under current rules.
      - .compost/synth-log/ for which raws were actually synthesized.
    """
    from compost.ingest.classifier import replay_classify

    classified = replay_classify(repo, since_days)
    if not classified:
        return LintResult(linter="false_negative_sampler", status="pass")

    synthesized = _synthesized_raws(repo, since_days)

    findings: list[LintFinding] = []
    for raw_path, decision in classified:
        if not decision.fired:
            continue
        rel = str(raw_path.relative_to(repo)).replace("\\", "/")
        if rel not in synthesized:
            trigger_str = ", ".join(f"{t.kind}:{t.pattern}" for t in decision.triggers)
            findings.append(LintFinding(
                linter="false_negative_sampler",
                severity="warn",
                message=f"would fire ({trigger_str}) but no synthesis run found",
                path=rel,
            ))

    status = "warn" if findings else "pass"
    return LintResult(linter="false_negative_sampler", status=status, findings=findings)


def _synthesized_raws(repo: Path, since_days: int) -> set[str]:
    """Return repo-relative raw paths that appear in synth run_complete events within since_days."""
    log_dir = repo / ".compost" / "synth-log"
    if not log_dir.exists():
        return set()

    cutoff = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(days=since_days)
    ).timestamp()

    synthesized: set[str] = set()
    for f in log_dir.glob("*.jsonl"):
        if f.stat().st_mtime < cutoff:
            continue
        for line in f.read_text().splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "run_complete" and "raw" in event:
                synthesized.add(event["raw"])
    return synthesized
```

### Step 10 — Create `lint/runner.py`

```python
# tools/compost/lint/runner.py
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

from compost.lint import LintFinding, LintResult
from compost.lint.linters import (
    stale_citations,
    expired_research,
    confidence_mismatch,
    orphaned_raw,
    missing_owners,
    false_negative_sampler,
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
```

Note: `lint/linters/__init__.py` must export the six submodules so `runner.py` can import them:

```python
# tools/compost/lint/linters/__init__.py
from compost.lint.linters import (  # noqa: F401
    stale_citations,
    expired_research,
    confidence_mismatch,
    orphaned_raw,
    missing_owners,
    false_negative_sampler,
)
```

### Step 11 — Create `lint/report.py`

```python
# tools/compost/lint/report.py
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from compost.lint import LintResult


def build_report(results: list[LintResult], run_id: str) -> str:
    """Return a markdown string: summary table + per-linter finding sections."""
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total = sum(len(r.findings) for r in results)

    lines: list[str] = [
        f"## Lint Run — {date} ({run_id})",
        "",
        "| Linter | Status | Findings |",
        "|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r.linter} | {r.status} | {len(r.findings)} |")
    lines.append(f"| **Total** | | **{total}** |")
    lines.append("")

    for r in results:
        if not r.findings:
            continue
        lines.append(f"### {r.linter}")
        lines.append("")
        for f in r.findings:
            prefix = "- **error**" if f.severity == "error" else "- warn"
            path_note = f" (`{f.path}`)" if f.path else ""
            lines.append(f"{prefix}{path_note}: {f.message}")
        lines.append("")

    lines.append("---")
    return "\n".join(lines)


def append_to_log_md(repo: Path, report: str) -> None:
    """Prepend a dated lint-run block to wiki/log.md (newest entry at top)."""
    log_path = repo / "wiki" / "log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if log_path.exists():
        existing = log_path.read_text()
    else:
        existing = "# Wiki Log\n"

    # Insert after the first heading line so the heading stays at the top.
    if "\n" in existing:
        first_newline = existing.index("\n")
        header = existing[: first_newline + 1]
        rest = existing[first_newline + 1 :].lstrip("\n")
        new_content = header + "\n" + report + "\n\n" + rest
    else:
        new_content = existing + "\n\n" + report + "\n"

    log_path.write_text(new_content)
```

### Step 12 — Create `cli/lint.py`

```python
# tools/compost/cli/lint.py
from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.table import Table

from compost.cli._helpers import console


@click.group("lint")
def lint_group() -> None:
    """Lifecycle lint: surface stale citations, expired research, and orphaned raw files."""


@lint_group.command("run")
@click.option(
    "--since", "since_days", default=90, show_default=True,
    help="Days back for orphaned-raw and false-negative sampler.",
)
@click.option(
    "--emit-pr", "emit_pr", is_flag=True, default=False,
    help="Create a lint/* branch, commit wiki/log.md, push, and open a PR.",
)
@click.pass_context
def lint_run(ctx: click.Context, since_days: int, emit_pr: bool) -> None:
    """Run all lifecycle linters and print a health report."""
    import uuid
    from compost.lint.runner import run_lint
    from compost.lint.report import build_report, append_to_log_md
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    run_id = uuid.uuid4().hex[:8]
    results = run_lint(repo, since_days=since_days, run_id=run_id)
    _render_lint_results(results)

    report = build_report(results, run_id)

    if emit_pr:
        _emit_lint_pr(repo, report, run_id)
    else:
        append_to_log_md(repo, report)
        console.print("[dim]→ wiki/log.md updated (not committed)[/dim]")

    has_errors = any(r.status == "error" for r in results)
    if has_errors:
        sys.exit(1)


@lint_group.command("log")
@click.option("--last", default=10, show_default=True,
              help="Number of recent runs to show.")
@click.pass_context
def lint_log(ctx: click.Context, last: int) -> None:
    """Show recent lint run history."""
    from compost.lint.runner import read_runs
    from compost.repo import find_repo_root

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    runs = read_runs(repo, last=last)
    if not runs:
        console.print("[dim]No lint runs found.[/dim]")
        return

    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Run ID", style="cyan")
    table.add_column("Timestamp")
    table.add_column("Findings", justify="right")
    for r in runs:
        table.add_row(
            (r.get("run_id") or "?")[:8],
            (r.get("ts") or "")[:19].replace("T", " "),
            str(r.get("total_findings", 0)),
        )
    console.print(table)


# ── private helpers ───────────────────────────────────────────────────────────

def _render_lint_results(results: list) -> None:
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Linter")
    table.add_column("Status")
    table.add_column("Findings", justify="right")
    table.add_column("Duration (s)", justify="right")

    for r in results:
        if r.status == "pass":
            status_str = "[green]✓ pass[/green]"
        elif r.status == "error":
            status_str = "[red]✗ error[/red]"
        elif r.status == "warn":
            status_str = "[yellow]⚠ warn[/yellow]"
        else:
            status_str = "[dim]— skipped[/dim]"

        n = str(len(r.findings)) if r.status != "skipped" else "—"
        dur = f"{r.duration_s:.1f}" if r.status != "skipped" else "—"
        table.add_row(r.linter, status_str, n, dur)

    console.print(table)

    all_findings = [f for r in results for f in r.findings]
    if all_findings:
        console.print(f"\n[bold]{len(all_findings)} finding(s):[/bold]")
        for f in all_findings:
            icon = "[red]✗[/red]" if f.severity == "error" else "[yellow]⚠[/yellow]"
            path_note = f" {f.path}" if f.path else ""
            console.print(f"  {icon} [{f.linter}]{path_note}: {f.message}")


def _emit_lint_pr(repo: Path, report: str, run_id: str) -> None:
    from datetime import datetime, timezone
    from compost.lint.report import append_to_log_md
    from compost.ingest.git import (
        create_branch_and_commit, push_branch, default_branch,
    )

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    branch = f"lint/{date}-{run_id[:4]}"

    # Write log.md update
    append_to_log_md(repo, report)
    log_md = repo / "wiki" / "log.md"

    # Branch + commit
    create_branch_and_commit(
        repo, branch, [log_md],
        f"lint: weekly report {date} ({run_id})",
    )
    console.print(f"[dim]committed to branch {branch}[/dim]")

    # Push
    try:
        push_branch(repo, "origin", branch)
    except Exception as e:
        console.print(f"[yellow]⚠ push failed: {e}[/yellow]")
        console.print(
            f"[yellow]Branch '{branch}' created locally. Push manually and open PR.[/yellow]"
        )
        return

    # Open PR via Gitea
    try:
        from compost.cli._helpers import _load_gitea_client
        client = _load_gitea_client(repo)
        base = default_branch(repo)
        pr = client.open_pr(
            branch, base,
            f"lint: {date}",
            report + "\n\n---\n*Opened by `compost lint run --emit-pr`.*",
        )
        console.print(f"[green]PR opened:[/green] {pr.url}")
    except SystemExit:
        console.print(
            "[yellow]Gitea not configured; branch pushed but no PR created.[/yellow]"
        )
        console.print(
            f"[dim]Run: compost gitea setup, then open a PR for '{branch}'[/dim]"
        )
```

### Step 13 — Register `lint_group` in `cli/__init__.py`

Add to the imports section (after the existing `gitea_group` import):
```python
from compost.cli.lint import lint_group
```

Add at the end of `cli/__init__.py` (after `main.add_command(gitea_group)`):
```python
main.add_command(lint_group)
```

### Step 14 — Add `.compost/lint-log/` to `.gitignore`

The repo's `.gitignore` (at the wiki repo root, not this implementation repo) needs `.compost/lint-log/` added. The `conftest.py` `_write_gitignore` function and `_run_doctor_checks` in `cli/_helpers.py` both reference the required gitignore entries.

**`conftest.py` — update `_write_gitignore`:** add `.compost/lint-log/\n` to the entries.

```python
def _write_gitignore(repo: Path) -> None:
    (repo / ".gitignore").write_text(
        ".compost/codify/\n"
        ".compost/synth-log/\n"
        ".compost/assay-report.md\n"
        ".compost/checks/\n"
        ".compost/queue/\n"
        ".compost/shims/\n"
        ".compost/lint-log/\n"      # ← new
    )
```

**`cli/_helpers.py` — update `_REQUIRED_IGNORES`:**

```python
_REQUIRED_IGNORES = {
    ".compost/codify/", ".compost/synth-log/", ".compost/assay-report.md",
    ".compost/checks/", ".compost/queue/", ".compost/shims/",
    ".compost/lint-log/",   # ← new
}
```

**`bootstrap.py`** — check if it writes `.gitignore` for newly bootstrapped repos. Read it and add the same entry if it does.

### Step 15 — Update `bootstrap.py` if needed

Read `tools/compost/bootstrap.py` to check if it generates a `.gitignore`. If so, add `.compost/lint-log/` to the list there as well.

---

## § Tests

All tests go in `tools/compost/tests/test_lint.py`.

### Test structure

```python
# tools/compost/tests/test_lint.py
"""
Integration tests for the lint module.

All tests use wiki_repo (tmp_path fixture) or raw_repo pattern from conftest.
No mocking except for the false_negative_sampler which has no external system —
  it reads local files — so it uses a real repo fixture.
"""
import datetime
import json
from pathlib import Path

import pytest

from compost.lint import LintFinding, LintResult
from compost.lint.linters import (
    stale_citations,
    expired_research,
    confidence_mismatch,
    orphaned_raw,
    missing_owners,
    false_negative_sampler,
)
from compost.lint.runner import run_lint, read_runs
from compost.lint.report import build_report, append_to_log_md
```

### Fixtures needed (local to test file)

```python
@pytest.fixture
def lint_repo(tmp_path: Path) -> Path:
    """Minimal valid wiki repo for lint tests. Does NOT require git."""
    repo = tmp_path / "wiki"
    (repo / "wiki" / "services").mkdir(parents=True)
    (repo / "raw" / "decisions").mkdir(parents=True)
    (repo / "raw" / "research").mkdir(parents=True)
    (repo / ".compost.yml").write_text("name: test\nqmd_index: test\n")
    return repo


def _wiki_page(repo: Path, rel: str, *, owners: list = None, confidence: str = "high",
               sources: list = None) -> Path:
    """Write a minimal wiki page with given frontmatter. Returns absolute path."""
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    fm_lines = [
        "---",
        "type: service",
        f"name: {p.stem}",
        f"owners: {owners or ['alice']}",
        "status: active",
        "updated: 2026-01-01",
        f"confidence: {confidence}",
        f"sources: {sources or []}",
        "---",
        "",
        "# content",
    ]
    p.write_text("\n".join(fm_lines))
    return p


def _raw_file(repo: Path, rel: str, *, source: str = "decision",
              confidence: str | None = None,
              expires_at: str | None = None,
              promotion_status: str | None = None) -> Path:
    """Write a raw/research file with given frontmatter. Returns absolute path."""
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"source: {source}",
        "captured_at: 2026-01-10T10:00:00Z",
        "captured_by: test",
        "origin: test",
    ]
    if confidence:
        lines.append(f"confidence: {confidence}")
    if expires_at:
        lines.append(f"expires_at: {expires_at}")
    if promotion_status:
        lines.append(f"promotion_status: {promotion_status}")
    lines += ["---", "", "body text"]
    p.write_text("\n".join(lines))
    return p
```

### `stale_citations` tests

```python
def test_stale_citations_pass_when_source_exists(lint_repo):
    _raw_file(lint_repo, "raw/decisions/0001.md")
    _wiki_page(lint_repo, "wiki/services/payments.md",
               sources=["raw/decisions/0001.md"])
    result = stale_citations.run(lint_repo)
    assert result.status == "pass"
    assert result.findings == []


def test_stale_citations_error_when_source_missing(lint_repo):
    # Do NOT write the raw file
    _wiki_page(lint_repo, "wiki/services/payments.md",
               sources=["raw/decisions/missing.md"])
    result = stale_citations.run(lint_repo)
    assert result.status == "error"
    assert len(result.findings) == 1
    assert result.findings[0].severity == "error"
    assert "missing.md" in result.findings[0].message


def test_stale_citations_skips_special_pages(lint_repo):
    # log.md, glossary.md, index.md should be ignored
    (lint_repo / "wiki" / "log.md").write_text(
        "---\nsources: [raw/decisions/missing.md]\n---\nbody"
    )
    result = stale_citations.run(lint_repo)
    assert result.status == "pass"


def test_stale_citations_no_wiki_dir(tmp_path):
    (tmp_path / ".compost.yml").write_text("name: test\n")
    result = stale_citations.run(tmp_path)
    assert result.status == "skipped"
```

### `expired_research` tests

```python
def test_expired_research_pass_when_not_expired(lint_repo):
    future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    _raw_file(lint_repo, "raw/research/q1.md",
              expires_at=future, promotion_status="not_promoted")
    result = expired_research.run(lint_repo)
    assert result.status == "pass"


def test_expired_research_error_when_not_promoted_and_expired(lint_repo):
    past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    _raw_file(lint_repo, "raw/research/q1.md",
              expires_at=past, promotion_status="not_promoted")
    result = expired_research.run(lint_repo)
    assert result.status == "error"
    f = result.findings[0]
    assert f.severity == "error"
    assert "not promoted" in f.message


def test_expired_research_warn_when_promoted_and_expired(lint_repo):
    past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    _raw_file(lint_repo, "raw/research/q1.md",
              expires_at=past, promotion_status="promoted")
    result = expired_research.run(lint_repo)
    assert result.status == "warn"
    assert result.findings[0].severity == "warn"


def test_expired_research_skips_no_expires_at(lint_repo):
    _raw_file(lint_repo, "raw/research/q1.md")  # no expires_at
    result = expired_research.run(lint_repo)
    assert result.status == "pass"
    assert result.findings == []


def test_expired_research_skipped_no_research_dir(tmp_path):
    (tmp_path / ".compost.yml").write_text("name: test\n")
    result = expired_research.run(tmp_path)
    assert result.status == "skipped"
```

### `confidence_mismatch` tests

```python
def test_confidence_mismatch_pass_high_citing_high_research(lint_repo):
    _raw_file(lint_repo, "raw/research/q1.md", source="research",
              confidence="high")
    _wiki_page(lint_repo, "wiki/services/payments.md",
               confidence="high", sources=["raw/research/q1.md"])
    result = confidence_mismatch.run(lint_repo)
    assert result.status == "pass"


def test_confidence_mismatch_warn_high_citing_low_research(lint_repo):
    _raw_file(lint_repo, "raw/research/q1.md", source="research",
              confidence="low")
    _wiki_page(lint_repo, "wiki/services/payments.md",
               confidence="high", sources=["raw/research/q1.md"])
    result = confidence_mismatch.run(lint_repo)
    assert result.status == "warn"
    assert len(result.findings) == 1
    assert result.findings[0].severity == "warn"


def test_confidence_mismatch_ignores_non_research_sources(lint_repo):
    # Decisions in raw/decisions/ should be ignored even if they had a confidence field
    _raw_file(lint_repo, "raw/decisions/0001.md")
    _wiki_page(lint_repo, "wiki/services/payments.md",
               confidence="high", sources=["raw/decisions/0001.md"])
    result = confidence_mismatch.run(lint_repo)
    assert result.status == "pass"
```

### `orphaned_raw` tests

```python
def test_orphaned_raw_pass_when_cited(lint_repo):
    raw = _raw_file(lint_repo, "raw/decisions/0001.md")
    _wiki_page(lint_repo, "wiki/services/payments.md",
               sources=["raw/decisions/0001.md"])
    result = orphaned_raw.run(lint_repo, since_days=90)
    assert result.status == "pass"


def test_orphaned_raw_warn_when_not_cited(lint_repo):
    _raw_file(lint_repo, "raw/decisions/0001.md")
    # No wiki page cites it
    result = orphaned_raw.run(lint_repo, since_days=90)
    assert result.status == "warn"
    assert any("raw/decisions/0001.md" in f.path for f in result.findings)


def test_orphaned_raw_ignores_old_files(lint_repo):
    raw = _raw_file(lint_repo, "raw/decisions/old.md")
    # Set mtime to 200 days ago
    import os, time as _t
    old_ts = _t.time() - (200 * 86400)
    os.utime(raw, (old_ts, old_ts))
    result = orphaned_raw.run(lint_repo, since_days=90)
    assert result.status == "pass"
```

### `missing_owners` tests

```python
def test_missing_owners_pass_when_owners_present(lint_repo):
    _wiki_page(lint_repo, "wiki/services/payments.md", owners=["alice"])
    result = missing_owners.run(lint_repo)
    assert result.status == "pass"


def test_missing_owners_error_when_empty(lint_repo):
    _wiki_page(lint_repo, "wiki/services/payments.md", owners=[])
    result = missing_owners.run(lint_repo)
    assert result.status == "error"
    assert len(result.findings) == 1
    assert result.findings[0].severity == "error"


def test_missing_owners_error_when_absent(lint_repo):
    p = lint_repo / "wiki" / "services" / "no-owners.md"
    p.write_text("---\ntype: service\nname: no-owners\n---\nbody\n")
    result = missing_owners.run(lint_repo)
    assert result.status == "error"


def test_missing_owners_skips_special_pages(lint_repo):
    (lint_repo / "wiki" / "log.md").write_text("# log\n")
    (lint_repo / "wiki" / "glossary.md").write_text("# glossary\n")
    result = missing_owners.run(lint_repo)
    assert result.status == "pass"
```

### `false_negative_sampler` tests

```python
def test_false_negative_sampler_no_raw_no_findings(lint_repo):
    result = false_negative_sampler.run(lint_repo, since_days=90)
    assert result.status == "pass"
    assert result.findings == []


def test_false_negative_sampler_flags_unsynthesized_fire(lint_repo):
    # Write a raw file that will fire (source: incident)
    _raw_file(lint_repo, "raw/incidents/inc.md", source="incident")
    # No synth-log entries → not synthesized
    result = false_negative_sampler.run(lint_repo, since_days=90)
    assert result.status == "warn"
    assert any("raw/incidents/inc.md" in f.path for f in result.findings)


def test_false_negative_sampler_no_flag_when_synthesized(lint_repo):
    _raw_file(lint_repo, "raw/incidents/inc.md", source="incident")
    # Write a fake synth-log entry saying it was synthesized
    log_dir = lint_repo / ".compost" / "synth-log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "2026-05-07T120000-abc12345.jsonl"
    log_file.write_text(
        json.dumps({
            "event": "run_complete",
            "ts": "2026-05-07T12:00:00",
            "run_id": "abc12345",
            "raw": "raw/incidents/inc.md",
            "total_cost_usd": 0.0,
            "wiki_edits": 1,
            "contradictions": 0,
            "duration_s": 1.0,
        }) + "\n"
    )
    result = false_negative_sampler.run(lint_repo, since_days=90)
    assert result.status == "pass"


def test_false_negative_sampler_no_flag_when_no_fire(lint_repo):
    # Write a raw file that does NOT fire (source: note, no keywords)
    _raw_file(lint_repo, "raw/notes/standup.md", source="note")
    result = false_negative_sampler.run(lint_repo, since_days=90)
    # standup.md fires on nothing by default rules — no findings
    assert all("standup.md" not in (f.path or "") for f in result.findings)
```

### `runner` tests

```python
def test_run_lint_returns_six_results(lint_repo):
    results = run_lint(lint_repo, since_days=90)
    assert len(results) == 6
    names = {r.linter for r in results}
    assert "stale_citations" in names
    assert "missing_owners" in names


def test_run_lint_writes_jsonl_log(lint_repo):
    run_lint(lint_repo, since_days=90, run_id="test1234")
    log_dir = lint_repo / ".compost" / "lint-log"
    files = list(log_dir.glob("*test1234*.jsonl"))
    assert len(files) == 1
    events = [json.loads(l) for l in files[0].read_text().splitlines()]
    event_types = [e["event"] for e in events]
    assert "lint_start" in event_types
    assert "lint_complete" in event_types
    assert event_types.count("linter_result") == 6


def test_run_lint_results_sorted_by_name(lint_repo):
    results = run_lint(lint_repo, since_days=90)
    names = [r.linter for r in results]
    assert names == sorted(names)


def test_read_runs_returns_empty_when_no_logs(lint_repo):
    assert read_runs(lint_repo) == []


def test_read_runs_returns_summaries(lint_repo):
    run_lint(lint_repo, since_days=90, run_id="aabbccdd")
    summaries = read_runs(lint_repo, last=5)
    assert len(summaries) == 1
    assert summaries[0]["event"] == "lint_complete"
    assert "total_findings" in summaries[0]
```

### `report` tests

```python
def test_build_report_contains_all_linters(lint_repo):
    results = run_lint(lint_repo)
    report = build_report(results, run_id="test1234")
    assert "stale_citations" in report
    assert "missing_owners" in report
    assert "Lint Run" in report


def test_build_report_shows_total_findings(lint_repo):
    results = run_lint(lint_repo)
    total = sum(len(r.findings) for r in results)
    report = build_report(results, run_id="test1234")
    assert f"**{total}**" in report


def test_append_to_log_md_creates_file(lint_repo):
    results = run_lint(lint_repo)
    report = build_report(results, run_id="test1234")
    append_to_log_md(lint_repo, report)
    log_md = lint_repo / "wiki" / "log.md"
    assert log_md.exists()
    content = log_md.read_text()
    assert "Lint Run" in content


def test_append_to_log_md_prepends_to_existing(lint_repo):
    log_md = lint_repo / "wiki" / "log.md"
    log_md.parent.mkdir(parents=True, exist_ok=True)
    log_md.write_text("# Wiki Log\n\n## Old Entry\n\nold content\n")
    results = run_lint(lint_repo)
    report = build_report(results, "new1234")
    append_to_log_md(lint_repo, report)
    content = log_md.read_text()
    # New entry should appear before old entry
    new_pos = content.index("new1234")
    old_pos = content.index("Old Entry")
    assert new_pos < old_pos
```

### CLI integration tests

```python
def test_cli_lint_run_exits_zero_when_no_errors(lint_repo, wiki_repo):
    from click.testing import CliRunner
    from compost.cli import main
    # Use wiki_repo which has valid pages with owners
    runner = CliRunner()
    result = runner.invoke(main, ["--repo", str(wiki_repo), "lint", "run"])
    assert result.exit_code == 0, result.output


def test_cli_lint_run_exits_one_on_error(lint_repo):
    from click.testing import CliRunner
    from compost.cli import main
    # Write a page with no owners (triggers error)
    (lint_repo / "wiki" / "services").mkdir(parents=True, exist_ok=True)
    (lint_repo / "wiki" / "services" / "broken.md").write_text(
        "---\ntype: service\nname: broken\nowners: []\n"
        "status: active\nupdated: 2026-01-01\nconfidence: high\nsources: []\n---\nbody\n"
    )
    runner = CliRunner()
    result = runner.invoke(main, ["--repo", str(lint_repo), "lint", "run"])
    assert result.exit_code == 1


def test_cli_lint_log_no_runs(lint_repo):
    from click.testing import CliRunner
    from compost.cli import main
    runner = CliRunner()
    result = runner.invoke(main, ["--repo", str(lint_repo), "lint", "log"])
    assert result.exit_code == 0
    assert "No lint runs" in result.output
```

---

## § Files modified summary

| File | Change |
|---|---|
| `ingest/classifier.py` | Add `replay_classify()` function |
| `cli/__init__.py` | Import and register `lint_group` |
| `cli/_helpers.py` | Add `".compost/lint-log/"` to `_REQUIRED_IGNORES` |
| `tests/conftest.py` | Add `".compost/lint-log/\n"` to `_write_gitignore()` |
| `bootstrap.py` | Add `".compost/lint-log/"` to gitignore if it writes one |

## § New files summary

| File | Purpose |
|---|---|
| `lint/__init__.py` | `LintFinding`, `LintResult` types |
| `lint/runner.py` | `run_lint()`, `read_runs()`, JSONL log |
| `lint/report.py` | `build_report()`, `append_to_log_md()` |
| `lint/linters/__init__.py` | Re-export linter submodules |
| `lint/linters/stale_citations.py` | Linter: missing source files |
| `lint/linters/expired_research.py` | Linter: past `expires_at` |
| `lint/linters/confidence_mismatch.py` | Linter: high-conf page citing low-conf research |
| `lint/linters/orphaned_raw.py` | Linter: uncited raw files |
| `lint/linters/missing_owners.py` | Linter: empty owners field |
| `lint/linters/false_negative_sampler.py` | Linter: would-fire raws not synthesized |
| `cli/lint.py` | `lint_group`, `lint run`, `lint log` commands |
| `tests/test_lint.py` | All lint tests |

---

## § Cross-check against high-level plan

| High-level deliverable | Covered? |
|---|---|
| Six linters (stale citations, expired research, confidence mismatch, orphaned raw, missing owners, false-negative sampler) | Yes — one file per linter |
| `compost lint run [--emit-pr] [--since DAYS]` | Yes |
| `wiki/log.md` updated on every run | Yes — `append_to_log_md()` always called |
| Scheduling docs in module docstring (launchd + crontab) | Yes — in `lint/runner.py` docstring |
| False-negative sampler tied to Phase 3 classifier replay | Yes — calls `replay_classify()` |
| `compost lint log` view command | Yes |
| `--emit-pr` opens a proper PR via Gitea | Yes — calls `client.open_pr()` |

---

## § Verify steps post-implementation

```bash
cd /Users/eoin/workspace/compost/tools/compost
python -m pytest tests/test_lint.py -v
python -m pytest tests/ -v --tb=short   # no regressions
```

Then in a local wiki repo:
```bash
compost lint run --since 30
compost lint log
```
