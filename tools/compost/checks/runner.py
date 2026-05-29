from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from compost.ingest.git import changed_files, default_branch
from compost.repo import load_repo_config


@dataclass(frozen=True)
class Finding:
    check: str
    severity: Literal["fail", "warn"]
    message: str
    page: str | None = None
    conflicting_page: str | None = None
    claim_text: str | None = None
    conflicting_claim_text: str | None = None


@dataclass
class CheckResult:
    name: str
    status: Literal["pass", "fail", "warn", "skipped"]
    findings: list[Finding]
    cost_usd: float = 0.0
    duration_s: float = 0.0
    skip_reason: str | None = None


@dataclass
class ChecksConfig:
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    human_edit_days: int = 30
    recent_raw_days: int = 90
    scope_min_score: float = 0.15
    scope_top_n: int = 10


def load_checks_config(repo: Path) -> ChecksConfig:
    """Read checks: block from .compost.yml; fall back to ChecksConfig defaults."""
    config = load_repo_config(repo)
    c = config.get("checks", {})
    return ChecksConfig(
        provider=c.get("provider", "anthropic"),
        model=c.get("model", "claude-sonnet-4-6"),
        human_edit_days=int(c.get("human_edit_days", 30)),
        recent_raw_days=int(c.get("recent_raw_days", 90)),
        scope_min_score=float(c.get("scope_min_score", 0.15)),
        scope_top_n=int(c.get("scope_top_n", 10)),
    )


def run_checks(
    repo: Path,
    branch: str,
    raw_path: Path | None = None,
    *,
    config: ChecksConfig | None = None,
) -> list[CheckResult]:
    """Run all seven checks in order, short-circuiting on failures per plan rules.

    Never raises on check failure; only raises on infrastructure errors (git not found).
    Returns results for all checks including skipped ones with skip_reason set.
    """
    from compost.checks.kotlin_assay import check_kotlin_assay
    from compost.checks.provenance import check_provenance
    from compost.checks.scope import check_scope
    from compost.checks.human_edit_guard import check_human_edit_guard
    from compost.checks.contradiction import check_contradiction_scan
    from compost.checks.recent_raw import check_recent_raw_scan
    from compost.checks.citation import check_citation_faithfulness

    if config is None:
        config = load_checks_config(repo)

    base = default_branch(repo)
    results: list[CheckResult] = []

    # Check 0: Kotlin assay (skip if kotlinc absent; fail short-circuits all)
    kotlin_res = check_kotlin_assay(repo)
    results.append(kotlin_res)
    if kotlin_res.status == "fail":
        _append_skipped(results, [
            "provenance", "scope", "human_edit_guard",
            "contradiction_scan", "recent_raw_scan", "citation_faithfulness",
        ], "kotlin_assay failed")
        return results

    # Gather changed wiki files
    all_changed = changed_files(repo, branch, base)
    changed_wiki = [
        repo / f for f in all_changed
        if f.startswith("wiki/") and f.endswith(".md")
    ]
    if not changed_wiki:
        _append_skipped(results, [
            "provenance", "scope", "human_edit_guard",
            "contradiction_scan", "recent_raw_scan", "citation_faithfulness",
        ], "no wiki files changed on branch")
        return results

    # Checks 1-3: deterministic
    prov_res = check_provenance(repo, changed_wiki)
    results.append(prov_res)

    if raw_path is not None:
        scope_res = check_scope(repo, changed_wiki, raw_path, config)
    else:
        scope_res = _skipped_result("scope", "no triggering raw")
    results.append(scope_res)

    heg_res = check_human_edit_guard(repo, changed_wiki, config, base_branch=base, branch=branch)
    results.append(heg_res)

    det_failed = any(r.status == "fail" for r in [prov_res, scope_res, heg_res])
    if det_failed:
        _append_skipped(results, [
            "contradiction_scan", "recent_raw_scan", "citation_faithfulness",
        ], "deterministic check failed")
        return results

    # Check 4: contradiction scan
    contra_res = check_contradiction_scan(repo, changed_wiki, branch, config)
    results.append(contra_res)

    # Check 5: recent raw scan (skip if check 4 failed)
    if contra_res.status == "fail":
        results.append(_skipped_result("recent_raw_scan", "contradiction_scan failed"))
    elif raw_path is not None:
        recent_res = check_recent_raw_scan(repo, changed_wiki, branch, raw_path, config)
        results.append(recent_res)
    else:
        results.append(_skipped_result("recent_raw_scan", "no triggering raw"))

    # Check 6: citation faithfulness (always runs if deterministic checks passed)
    cit_res = check_citation_faithfulness(repo, changed_wiki, branch, config)
    results.append(cit_res)

    return results


def infer_raw_path(repo: Path, branch: str, base: str | None = None) -> Path | None:
    """Return the raw/ file added on branch relative to base, or None."""
    if base is None:
        base = default_branch(repo)
    result = subprocess.run(
        ["git", "log", f"{base}...{branch}",
         "--diff-filter=A", "--name-only", "--pretty=format:"],
        cwd=repo, capture_output=True, text=True,
    )
    files = [line for line in result.stdout.splitlines()
             if line.strip() and line.startswith("raw/")]
    if not files:
        return None
    return repo / files[0]


def branch_slug(branch: str) -> str:
    """Convert a branch name to a filesystem-safe slug (/ → -, : removed)."""
    return branch.replace("/", "-").replace(":", "")


def write_check_report(repo: Path, branch: str, results: list[CheckResult]) -> Path:
    """Write a markdown report and JSONL findings sidecar to .compost/checks/."""
    import dataclasses
    import json
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc)
    slug = branch_slug(branch)
    report_dir = repo / ".compost" / "checks"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{slug}.md"

    lines = [
        f"# Checks: {branch}",
        f"Generated: {ts:%Y-%m-%d %H:%M UTC}",
        "",
        "| Check | Status | Findings | Cost | Duration |",
        "|-------|--------|----------|------|----------|",
    ]
    for r in results:
        status_icon = {"pass": "✓ pass", "fail": "✗ FAIL", "warn": "⚠ warn",
                       "skipped": "— skipped"}.get(r.status, r.status)
        n = str(len(r.findings)) if r.status != "skipped" else "—"
        cost = f"${r.cost_usd:.4f}" if r.status != "skipped" else "—"
        dur = f"{r.duration_s:.1f}s" if r.status != "skipped" else "—"
        lines.append(f"| {r.name} | {status_icon} | {n} | {cost} | {dur} |")

    skip_notes = [(r.name, r.skip_reason) for r in results
                  if r.status == "skipped" and r.skip_reason]
    if skip_notes:
        lines.append("")
        for name, reason in skip_notes:
            lines.append(f"**Short-circuit:** {reason} → skipped: {name}")

    all_findings = [f for r in results for f in r.findings]
    if all_findings:
        lines.append("")
        lines.append("## Findings")
        for r in results:
            if not r.findings:
                continue
            lines.append("")
            lines.append(f"### {r.name}")
            for f in r.findings:
                lines.append(f"**{f.page or '(general)'}** ({f.severity})")
                lines.append(f"- {f.message}")
                if f.claim_text:
                    lines.append(f'  Claim: "{f.claim_text}"')
                if f.conflicting_page:
                    lines.append(f"  Conflicts with `{f.conflicting_page}`:")
                    if f.conflicting_claim_text:
                        lines.append(f'  "{f.conflicting_claim_text}"')

    report_path.write_text("\n".join(lines) + "\n")

    # Write JSONL sidecar: one line per Finding across all results.
    jsonl_path = report_dir / f"{slug}.jsonl"
    all_findings_flat = [f for r in results for f in r.findings]
    try:
        jsonl_path.write_text(
            "\n".join(json.dumps(dataclasses.asdict(f)) for f in all_findings_flat)
            + ("\n" if all_findings_flat else "")
        )
    except OSError:
        pass  # Non-fatal: markdown report is the authoritative output.

    return report_path


# ── helpers ───────────────────────────────────────────────────────────────────

def _skipped_result(name: str, reason: str) -> CheckResult:
    return CheckResult(name=name, status="skipped", findings=[], skip_reason=reason)


def _append_skipped(results: list[CheckResult], names: list[str], reason: str) -> None:
    for name in names:
        results.append(_skipped_result(name, reason))
