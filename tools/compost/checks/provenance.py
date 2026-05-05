from __future__ import annotations

import time
from pathlib import Path

from compost.checks.runner import CheckResult, Finding
from compost.model.frontmatter import parse_frontmatter


def check_provenance(repo: Path, changed_wiki_files: list[Path]) -> CheckResult:
    """Check 1: all changed wiki pages have a non-empty sources list."""
    start = time.monotonic()
    findings: list[Finding] = []

    for abs_path in changed_wiki_files:
        if not abs_path.exists():
            continue
        fm, _ = parse_frontmatter(abs_path)
        rel = str(abs_path.relative_to(repo))
        sources = fm.get("sources")
        if not sources:
            findings.append(Finding(
                check="provenance", severity="fail",
                message="missing or empty 'sources' field",
                page=rel,
            ))

    status = "fail" if findings else "pass"
    return CheckResult(
        name="provenance", status=status, findings=findings,
        duration_s=time.monotonic() - start,
    )
