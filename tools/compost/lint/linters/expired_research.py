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
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value)
        except ValueError:
            return None
    return None
