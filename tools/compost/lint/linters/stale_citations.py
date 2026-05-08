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
            if not (repo / src).exists():
                findings.append(LintFinding(
                    linter="stale_citations",
                    severity="error",
                    message=f"source '{src}' does not exist on disk",
                    path=rel_page,
                ))

    status = "error" if findings else "pass"
    return LintResult(linter="stale_citations", status=status, findings=findings)
