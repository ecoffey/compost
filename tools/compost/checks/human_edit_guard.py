from __future__ import annotations

import subprocess
import time
from pathlib import Path

from compost.checks.runner import CheckResult, ChecksConfig, Finding
from compost.ingest.git import current_branch


def check_human_edit_guard(
    repo: Path,
    changed_wiki_files: list[Path],
    config: ChecksConfig,
    base_branch: str = "main",
    branch: str | None = None,
) -> CheckResult:
    """Check 3: synthesis is not reverting a human edit from the last N days.

    Fails only when a human commit on the base branch touched the same ## section
    headings as the synthesis branch does. File-level overlap alone is not enough.
    """
    start = time.monotonic()
    findings: list[Finding] = []

    if branch is None:
        branch = current_branch(repo)

    for abs_path in changed_wiki_files:
        rel = abs_path.relative_to(repo)
        human_shas = _human_commits_on_base(repo, rel, base_branch, config.human_edit_days)
        if not human_shas:
            continue

        branch_diff = _diff_on_branch(repo, base_branch, branch, rel)
        branch_sections = _section_headings(branch_diff)

        for sha in human_shas:
            commit_diff = _diff_of_commit(repo, sha, rel)
            commit_sections = _section_headings(commit_diff)
            overlap = branch_sections & commit_sections
            if overlap:
                findings.append(Finding(
                    check="human_edit_guard", severity="fail",
                    message=(
                        f"section overlap with human commit {sha[:8]}: "
                        + ", ".join(sorted(overlap))
                    ),
                    page=str(rel),
                ))
                break  # one finding per file is enough

    status = "fail" if findings else "pass"
    return CheckResult(
        name="human_edit_guard", status=status, findings=findings,
        duration_s=time.monotonic() - start,
    )


def _human_commits_on_base(
    repo: Path, rel_path: Path, base: str, since_days: int
) -> list[str]:
    """Return SHAs of non-synth, non-raw commits on base that touched rel_path."""
    result = subprocess.run(
        ["git", "log", "--follow", f"--since={since_days} days ago",
         "--format=%H %s", base, "--", str(rel_path)],
        cwd=repo, capture_output=True, text=True,
    )
    shas = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(None, 1)
        sha = parts[0]
        subject = parts[1] if len(parts) > 1 else ""
        if not subject.startswith("synth:") and not subject.startswith("raw:"):
            shas.append(sha)
    return shas


def _diff_on_branch(repo: Path, base: str, branch: str, rel_path: Path) -> str:
    result = subprocess.run(
        ["git", "diff", f"{base}...{branch}", "--", str(rel_path)],
        cwd=repo, capture_output=True, text=True,
    )
    return result.stdout


def _diff_of_commit(repo: Path, sha: str, rel_path: Path) -> str:
    result = subprocess.run(
        ["git", "show", sha, "--", str(rel_path)],
        cwd=repo, capture_output=True, text=True,
    )
    return result.stdout


def _section_headings(diff_text: str) -> set[str]:
    """Return ## section names that have actual +/- changes in the diff.

    Parses line-by-line, tracking the current ## heading as context lines
    update it. A section is 'touched' when any added or removed line falls
    under it, including the heading line itself being added or removed.
    Also seeds current_section from @@ hunk context when it names a heading.
    """
    current_section: str | None = None
    touched: set[str] = set()

    for line in diff_text.splitlines():
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("@@"):
            # @@ -a,b +c,d @@ ## Heading text
            parts = line.split("@@", 2)
            if len(parts) >= 3:
                ctx = parts[2].strip()
                if ctx.startswith("## "):
                    current_section = ctx[3:].strip()
            continue
        if not line:
            continue

        prefix = line[0]
        content = line[1:]

        if content.startswith("## "):
            heading = content[3:].strip()
            if prefix in ("+", "-"):
                touched.add(heading)
            current_section = heading
        elif prefix in ("+", "-") and current_section is not None:
            touched.add(current_section)

    return touched
