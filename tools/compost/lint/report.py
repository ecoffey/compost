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

    existing = log_path.read_text() if log_path.exists() else "# Wiki Log\n"

    # Insert after the first heading line so the heading stays at the top.
    if "\n" in existing:
        first_newline = existing.index("\n")
        header = existing[: first_newline + 1]
        rest = existing[first_newline + 1 :].lstrip("\n")
        new_content = header + "\n" + report + "\n\n" + rest
    else:
        new_content = existing + "\n\n" + report + "\n"

    log_path.write_text(new_content)
