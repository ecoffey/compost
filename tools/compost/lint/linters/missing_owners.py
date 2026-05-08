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
        if not fm.get("owners"):
            findings.append(LintFinding(
                linter="missing_owners",
                severity="error",
                message="owners: is absent or empty",
                path=str(page.relative_to(repo)),
            ))

    status = "error" if findings else "pass"
    return LintResult(linter="missing_owners", status=status, findings=findings)
