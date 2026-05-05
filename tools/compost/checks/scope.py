from __future__ import annotations

import time
from pathlib import Path

from compost.checks.runner import CheckResult, ChecksConfig, Finding
from compost.model.frontmatter import parse_frontmatter
from compost.qmd import qmd_query
from compost.repo import load_repo_config


def check_scope(
    repo: Path,
    changed_wiki_files: list[Path],
    raw_path: Path,
    config: ChecksConfig,
) -> CheckResult:
    """Check 2: changed wiki pages are semantically connected to the triggering raw."""
    start = time.monotonic()
    findings: list[Finding] = []

    repo_config = load_repo_config(repo)
    index = repo_config["qmd_index"]

    _, raw_body = parse_frontmatter(raw_path)
    query_text = (raw_body or raw_path.read_text()).replace("\n", " ")[:300]

    hits = qmd_query(query_text, index, "wiki", limit=config.scope_top_n)
    hit_paths: set[str] = set()
    for h in hits:
        file_key = h.get("file", "")
        if file_key.startswith("qmd://"):
            file_key = file_key[len("qmd://"):]
        if h.get("score", 0.0) >= config.scope_min_score:
            hit_paths.add(file_key)

    for abs_path in changed_wiki_files:
        rel = str(abs_path.relative_to(repo))
        if rel not in hit_paths:
            findings.append(Finding(
                check="scope", severity="fail",
                message=(
                    f"page not in top-{config.scope_top_n} qmd results for raw content "
                    f"(min score {config.scope_min_score})"
                ),
                page=rel,
            ))

    status = "fail" if findings else "pass"
    return CheckResult(
        name="scope", status=status, findings=findings,
        duration_s=time.monotonic() - start,
    )
