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
                    message=f"high-confidence page cites low-confidence research '{src}'",
                    path=rel_page,
                ))

    status = "warn" if findings else "pass"
    return LintResult(linter="confidence_mismatch", status=status, findings=findings)
