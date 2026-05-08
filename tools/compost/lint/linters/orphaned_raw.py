from __future__ import annotations

import datetime
from pathlib import Path

from compost.lint import LintFinding, LintResult
from compost.model.frontmatter import parse_frontmatter


def run(repo: Path, since_days: int = 90) -> LintResult:
    """Flag raw files (within since_days) not cited in any wiki page's sources."""
    raw_dir = repo / "raw"
    if not raw_dir.exists():
        return LintResult(linter="orphaned_raw", status="skipped",
                          skip_reason="no raw/ directory")

    cited: set[str] = set()
    wiki_dir = repo / "wiki"
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
                message=f"not cited by any wiki page (within {since_days}d window)",
                path=rel,
            ))

    status = "warn" if findings else "pass"
    return LintResult(linter="orphaned_raw", status=status, findings=findings)
