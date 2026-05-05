# Phase 5: Tier 2.5 Adversarial Checks — Implementation Detail

Derived from: `_plans/2026-05-04-2200-phase5-adversarial-checks-high-level.md`

---

## § Reference Verification Notes

- `qmd query --json` returns list of `{file, title, snippet, score}` where `file` is a `qmd://`
  URI. Verified in Phase 4.
- `git log --diff-filter=A --name-only --pretty=format:` returns only filenames (one per line,
  blank lines between groups). `--format=` is the same as `--pretty=format:`. Strip blank lines.
- `git show {branch}:{rel_path}` returns file content at that branch ref — works even if we are
  not checked out on that branch.
- `compile_kt` raises `RuntimeError` when `kotlinc` is not on PATH. `check_kotlin_assay` must
  catch it and return `status="skipped"`.
- `render_wiki` raises `RuntimeError` on nonzero jar exit (invariant violation). Catch and emit
  Finding.

---

## § Implementation Detail

### Step 1 — Create `tools/compost/checks/__init__.py`

Empty file.

---

### Step 2 — Create `tools/compost/checks/runner.py`

```python
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from compost.ingest.git import changed_files, default_branch
from compost.repo import load_repo_config

if TYPE_CHECKING:
    pass


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

    heg_res = check_human_edit_guard(repo, changed_wiki, config, base_branch=base)
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


def write_check_report(repo: Path, branch: str, results: list[CheckResult]) -> Path:
    """Write a markdown report to .compost/checks/{branch-slug}.md."""
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc)
    slug = branch.replace("/", "-").replace(":", "")
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
        lines += ["", "## Findings"]
        for r in results:
            if not r.findings:
                continue
            lines.append(f"", f"### {r.name}")
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
    return report_path


# ── helpers ───────────────────────────────────────────────────────────────────

def _skipped_result(name: str, reason: str) -> CheckResult:
    return CheckResult(name=name, status="skipped", findings=[], skip_reason=reason)


def _append_skipped(results: list[CheckResult], names: list[str], reason: str) -> None:
    for name in names:
        results.append(_skipped_result(name, reason))
```

Note: `write_check_report` has a bug in the nested list append — fix during implementation: split multiline appends.

---

### Step 3 — Create `tools/compost/checks/_llm.py`

```python
from __future__ import annotations


_PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7": (15.0, 75.0),
    "claude-haiku-4-5-20251001": (0.8, 4.0),
}


def call_llm_anthropic(model: str, system: str, messages: list[dict]) -> tuple[str, dict]:
    """Call Anthropic messages API. Returns (text, usage_dict)."""
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("anthropic package not installed. Run: pip install 'anthropic>=0.40'")
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=system,
        messages=messages,
    )
    text = response.content[0].text if response.content else ""
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
    }
    return text, usage


def compute_cost(model: str, usage: dict) -> float:
    in_rate, out_rate = _PRICING.get(model, (3.0, 15.0))
    input_cost = usage["input_tokens"] / 1_000_000 * in_rate
    output_cost = usage["output_tokens"] / 1_000_000 * out_rate
    cache_read = usage.get("cache_read_input_tokens", 0) / 1_000_000 * (in_rate * 0.1)
    return input_cost + output_cost + cache_read
```

---

### Step 4 — Create `tools/compost/checks/kotlin_assay.py`

```python
from __future__ import annotations

import shutil
import time
from pathlib import Path

from compost.checks.runner import CheckResult, Finding


def check_kotlin_assay(repo: Path) -> CheckResult:
    """Check 0: Kotlin consistency layer. Skipped gracefully when kotlinc absent."""
    start = time.monotonic()

    if not shutil.which("kotlinc"):
        return CheckResult(
            name="kotlin_assay", status="skipped", findings=[],
            skip_reason="kotlinc not found",
        )

    try:
        from compost.codify.codegen import codify
        from compost.codify.compiler import compile_kt
        from compost.codify.renderer import render_wiki
    except ImportError as e:
        return CheckResult(
            name="kotlin_assay", status="skipped", findings=[],
            skip_reason=f"codify module unavailable: {e}",
        )

    findings: list[Finding] = []

    # Codegen
    try:
        codegen_result = codify(repo)
        for w in codegen_result.warnings:
            findings.append(Finding(check="kotlin_assay", severity="warn", message=w))
    except Exception as e:
        return CheckResult(
            name="kotlin_assay", status="fail",
            findings=[Finding(check="kotlin_assay", severity="fail",
                              message=f"codegen error: {e}")],
            duration_s=time.monotonic() - start,
        )

    # Compile
    try:
        compile_result = compile_kt(repo)
    except RuntimeError as e:
        return CheckResult(
            name="kotlin_assay", status="skipped", findings=[],
            skip_reason=str(e), duration_s=time.monotonic() - start,
        )

    if not compile_result.success:
        for err in compile_result.errors:
            findings.append(Finding(
                check="kotlin_assay", severity="fail",
                message=f"compile error: {err.message}",
                page=str(err.source_md) if err.source_md else None,
            ))
        return CheckResult(
            name="kotlin_assay", status="fail", findings=findings,
            duration_s=time.monotonic() - start,
        )

    # Render (invariant check)
    try:
        render_wiki(repo)
    except RuntimeError as e:
        findings.append(Finding(
            check="kotlin_assay", severity="fail",
            message=f"invariant violation: {e}",
        ))
        return CheckResult(
            name="kotlin_assay", status="fail", findings=findings,
            duration_s=time.monotonic() - start,
        )

    status = "fail" if any(f.severity == "fail" for f in findings) else (
        "warn" if findings else "pass"
    )
    return CheckResult(
        name="kotlin_assay", status=status, findings=findings,
        duration_s=time.monotonic() - start,
    )
```

---

### Step 5 — Create `tools/compost/checks/provenance.py`

```python
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
```

---

### Step 6 — Create `tools/compost/checks/scope.py`

```python
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
    # Strip qmd:// prefix; results are like qmd://wiki/services/payments.md
    hit_paths = set()
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
                message=f"page not in top-{config.scope_top_n} qmd results for raw content "
                        f"(min score {config.scope_min_score})",
                page=rel,
            ))

    status = "fail" if findings else "pass"
    return CheckResult(
        name="scope", status=status, findings=findings,
        duration_s=time.monotonic() - start,
    )
```

---

### Step 7 — Create `tools/compost/checks/human_edit_guard.py`

```python
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from compost.checks.runner import CheckResult, ChecksConfig, Finding


def check_human_edit_guard(
    repo: Path,
    changed_wiki_files: list[Path],
    config: ChecksConfig,
    base_branch: str = "main",
) -> CheckResult:
    """Check 3: synthesis is not reverting a human edit from the last N days."""
    start = time.monotonic()
    findings: list[Finding] = []

    for abs_path in changed_wiki_files:
        rel = abs_path.relative_to(repo)
        human_shas = _human_commits_on_base(repo, rel, base_branch, config.human_edit_days)
        if not human_shas:
            continue

        branch_diff = _diff_on_branch(repo, base_branch, "HEAD", rel)
        branch_headings = _section_headings(branch_diff)

        for sha in human_shas:
            commit_diff = _diff_of_commit(repo, sha, rel)
            commit_headings = _section_headings(commit_diff)
            overlap = branch_headings & commit_headings
            if overlap:
                findings.append(Finding(
                    check="human_edit_guard", severity="fail",
                    message=f"synthesis changes overlap with human commit {sha[:8]} "
                            f"on section(s): {', '.join(sorted(overlap))}",
                    page=str(rel),
                ))
                break  # one finding per page is enough

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
    """Extract ## section headings from lines added or removed in a diff."""
    headings: set[str] = set()
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+## ") or line.startswith("-## "):
            headings.add(line[4:].strip())
    return headings
```

---

### Step 8 — Create `tools/compost/checks/contradiction.py`

```python
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from compost.checks._llm import call_llm_anthropic, compute_cost
from compost.checks.runner import CheckResult, ChecksConfig, Finding
from compost.qmd import qmd_query
from compost.repo import load_repo_config

_SYSTEM = (
    "You are an adversarial knowledge-base auditor. "
    "Identify claims in a new wiki page that directly contradict claims in existing pages. "
    "Return JSON only — no prose, no markdown fences."
)

_SCHEMA = """{
  "contradictions": [
    {
      "page": "<path of the new/changed page>",
      "claim_text": "<exact or paraphrased claim from the new page>",
      "conflicting_page": "<path of the existing page it contradicts>",
      "conflicting_claim_text": "<claim from the existing page>"
    }
  ]
}"""


def check_contradiction_scan(
    repo: Path,
    changed_wiki_files: list[Path],
    branch: str,
    config: ChecksConfig,
) -> CheckResult:
    """Check 4: new claims don't contradict the rest of the wiki."""
    start = time.monotonic()
    findings: list[Finding] = []
    total_cost = 0.0

    repo_config = load_repo_config(repo)
    index = repo_config["qmd_index"]

    for abs_path in changed_wiki_files:
        rel = str(abs_path.relative_to(repo))

        # Get the page content as it appears on the branch
        new_content = _git_show(repo, branch, rel)
        if not new_content:
            continue

        # Find candidate pages that might conflict (exclude the page itself)
        candidates = [
            h for h in qmd_query(new_content[:300].replace("\n", " "), index, "wiki", limit=5)
            if not h.get("file", "").endswith(rel.lstrip("/"))
        ]
        if not candidates:
            continue

        candidate_texts = []
        for h in candidates:
            cfile = h.get("file", "")
            if cfile.startswith("qmd://"):
                cfile = cfile[len("qmd://"):]
            cpath = repo / cfile
            if cpath.exists():
                candidate_texts.append(f"=== {cfile} ===\n{cpath.read_text()[:2000]}")

        if not candidate_texts:
            continue

        user_msg = (
            f"New/changed page: {rel}\n\n"
            f"{new_content[:3000]}\n\n"
            f"Existing pages to check against:\n\n"
            + "\n\n".join(candidate_texts)
            + f"\n\nReturn contradictions in this JSON schema:\n{_SCHEMA}"
        )

        text, usage = call_llm_anthropic(config.model, _SYSTEM, [{"role": "user", "content": user_msg}])
        total_cost += compute_cost(config.model, usage)

        for c in _parse_contradictions(text):
            findings.append(Finding(
                check="contradiction_scan", severity="fail",
                message="contradiction detected",
                page=c.get("page", rel),
                conflicting_page=c.get("conflicting_page"),
                claim_text=c.get("claim_text"),
                conflicting_claim_text=c.get("conflicting_claim_text"),
            ))

    status = "fail" if findings else "pass"
    return CheckResult(
        name="contradiction_scan", status=status, findings=findings,
        cost_usd=total_cost, duration_s=time.monotonic() - start,
    )


def _git_show(repo: Path, branch: str, rel_path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{branch}:{rel_path}"],
        cwd=repo, capture_output=True, text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def _parse_contradictions(text: str) -> list[dict]:
    try:
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            end = -1 if lines[-1].strip() in ("```", "") else len(lines)
            text = "\n".join(lines[1:end]).strip()
        data = json.loads(text)
        return data.get("contradictions", []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, AttributeError):
        return []
```

---

### Step 9 — Create `tools/compost/checks/recent_raw.py`

```python
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from compost.checks._llm import call_llm_anthropic, compute_cost
from compost.checks.runner import CheckResult, ChecksConfig, Finding
from compost.model.frontmatter import parse_frontmatter
from compost.repo import load_repo_config

_SYSTEM = (
    "You are an adversarial knowledge-base auditor. "
    "Identify whether recent raw source files contain claims that contradict the new wiki content. "
    "Return JSON only — no prose, no markdown fences."
)

_SCHEMA = """{
  "contradictions": [
    {
      "page": "<path of the changed wiki page>",
      "claim_text": "<claim from the wiki page>",
      "conflicting_page": "<path of the raw file>",
      "conflicting_claim_text": "<contradicting claim from the raw file>"
    }
  ]
}"""


def check_recent_raw_scan(
    repo: Path,
    changed_wiki_files: list[Path],
    branch: str,
    raw_path: Path,
    config: ChecksConfig,
) -> CheckResult:
    """Check 5: new wiki claims don't contradict raw files from the last N days."""
    start = time.monotonic()
    findings: list[Finding] = []
    total_cost = 0.0

    recent_raws = _recent_raw_files(repo, config.recent_raw_days, exclude=raw_path)[:10]
    if not recent_raws:
        return CheckResult(
            name="recent_raw_scan", status="pass", findings=[],
            skip_reason="no recent raw files to compare",
            duration_s=time.monotonic() - start,
        )

    raw_texts = []
    for rp in recent_raws:
        _, body = parse_frontmatter(rp)
        raw_texts.append(f"=== {rp.relative_to(repo)} ===\n{body[:1500]}")

    for abs_path in changed_wiki_files:
        rel = str(abs_path.relative_to(repo))
        new_content = _git_show(repo, branch, rel)
        if not new_content:
            continue

        user_msg = (
            f"Changed wiki page: {rel}\n\n"
            f"{new_content[:3000]}\n\n"
            f"Recent raw files (last {config.recent_raw_days} days):\n\n"
            + "\n\n".join(raw_texts)
            + f"\n\nReturn contradictions in this JSON schema:\n{_SCHEMA}"
        )

        text, usage = call_llm_anthropic(config.model, _SYSTEM, [{"role": "user", "content": user_msg}])
        total_cost += compute_cost(config.model, usage)

        for c in _parse_contradictions(text):
            findings.append(Finding(
                check="recent_raw_scan", severity="fail",
                message="recent raw contradicts wiki claim",
                page=c.get("page", rel),
                conflicting_page=c.get("conflicting_page"),
                claim_text=c.get("claim_text"),
                conflicting_claim_text=c.get("conflicting_claim_text"),
            ))

    status = "fail" if findings else "pass"
    return CheckResult(
        name="recent_raw_scan", status=status, findings=findings,
        cost_usd=total_cost, duration_s=time.monotonic() - start,
    )


def _recent_raw_files(repo: Path, days: int, exclude: Path | None = None) -> list[Path]:
    """Return paths of raw/ files added in the last N days on the default branch."""
    result = subprocess.run(
        ["git", "log", "--diff-filter=A", f"--since={days} days ago",
         "--name-only", "--pretty=format:", "main", "--", "raw/"],
        cwd=repo, capture_output=True, text=True,
    )
    paths = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("raw/"):
            continue
        p = repo / line
        if p.exists() and p != exclude:
            paths.append(p)
    return paths


def _git_show(repo: Path, branch: str, rel_path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{branch}:{rel_path}"],
        cwd=repo, capture_output=True, text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def _parse_contradictions(text: str) -> list[dict]:
    try:
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            end = -1 if lines[-1].strip() in ("```", "") else len(lines)
            text = "\n".join(lines[1:end]).strip()
        data = json.loads(text)
        return data.get("contradictions", []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, AttributeError):
        return []
```

---

### Step 10 — Create `tools/compost/checks/citation.py`

```python
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from compost.checks._llm import call_llm_anthropic, compute_cost
from compost.checks.runner import CheckResult, ChecksConfig, Finding
from compost.model.frontmatter import parse_frontmatter

_SYSTEM = (
    "You are an adversarial knowledge-base auditor. "
    "Check whether the claims in a wiki page are supported by its cited sources. "
    "Flag claims that are not supported or that contradict the source text. "
    "Return JSON only — no prose, no markdown fences."
)

_SCHEMA = """{
  "unfaithful_claims": [
    {
      "claim_text": "<claim from the wiki page>",
      "reason": "<why it is not supported or contradicted by sources>"
    }
  ]
}"""


def check_citation_faithfulness(
    repo: Path,
    changed_wiki_files: list[Path],
    branch: str,
    config: ChecksConfig,
) -> CheckResult:
    """Check 6: cited sources actually support the claims they back."""
    start = time.monotonic()
    findings: list[Finding] = []
    total_cost = 0.0

    for abs_path in changed_wiki_files:
        rel = str(abs_path.relative_to(repo))
        new_content = _git_show(repo, branch, rel)
        if not new_content:
            continue

        fm, _ = parse_frontmatter(abs_path)
        sources = fm.get("sources") or []
        if not sources:
            continue

        source_texts = []
        for src in sources:
            src_path = repo / src
            if src_path.exists():
                _, body = parse_frontmatter(src_path)
                source_texts.append(f"=== {src} ===\n{body[:2000]}")

        if not source_texts:
            continue

        user_msg = (
            f"Wiki page: {rel}\n\n"
            f"{new_content[:3000]}\n\n"
            f"Cited sources:\n\n"
            + "\n\n".join(source_texts)
            + f"\n\nReturn unfaithful claims in this JSON schema:\n{_SCHEMA}"
        )

        text, usage = call_llm_anthropic(config.model, _SYSTEM, [{"role": "user", "content": user_msg}])
        total_cost += compute_cost(config.model, usage)

        for c in _parse_unfaithful(text):
            findings.append(Finding(
                check="citation_faithfulness", severity="fail",
                message=c.get("reason", "claim not supported by sources"),
                page=rel,
                claim_text=c.get("claim_text"),
            ))

    status = "fail" if findings else "pass"
    return CheckResult(
        name="citation_faithfulness", status=status, findings=findings,
        cost_usd=total_cost, duration_s=time.monotonic() - start,
    )


def _git_show(repo: Path, branch: str, rel_path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{branch}:{rel_path}"],
        cwd=repo, capture_output=True, text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def _parse_unfaithful(text: str) -> list[dict]:
    try:
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            end = -1 if lines[-1].strip() in ("```", "") else len(lines)
            text = "\n".join(lines[1:end]).strip()
        data = json.loads(text)
        return data.get("unfaithful_claims", []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, AttributeError):
        return []
```

---

### Step 11 — Update `tools/compost/pyproject.toml`

Add `"compost.checks"` to the packages list:

```toml
packages = [
    "compost",
    "compost.gitea",
    "compost.model",
    "compost.mcp",
    "compost.ingest",
    "compost.synth",
    "compost.codify",
    "compost.checks",      # ← add this
]
```

---

### Step 12 — Update `tools/compost/cli.py`: add `checks` group

Add after the `codify` group section and before `gitea` group section:

```python
# ── checks commands ───────────────────────────────────────────────────────────


@main.group("checks")
def checks_group() -> None:
    """Tier 2.5 adversarial checks: verify wiki edits before merge."""


@checks_group.command("run")
@click.option("--branch", default=None, help="Branch to check. Defaults to current branch.")
@click.option("--raw", "raw_rel", default=None,
              help="Triggering raw file (relative to repo). Inferred from branch commits if omitted.")
@click.pass_context
def checks_run(ctx: click.Context, branch: str | None, raw_rel: str | None) -> None:
    """Run all adversarial checks on a branch and write a report."""
    from compost.checks.runner import run_checks, load_checks_config, infer_raw_path, write_check_report
    from compost.ingest.git import current_branch, default_branch

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    if branch is None:
        branch = current_branch(repo)

    base = default_branch(repo)

    if raw_rel:
        raw_path = repo / raw_rel
    else:
        raw_path = infer_raw_path(repo, branch, base)

    config = load_checks_config(repo)
    results = run_checks(repo, branch, raw_path, config=config)

    _render_check_results(results)

    report_path = write_check_report(repo, branch, results)
    console.print(f"[dim]report → {report_path.relative_to(repo)}[/dim]")

    failed = [r for r in results if r.status == "fail"]
    if failed:
        sys.exit(1)
```

Add helper `_render_check_results` in the helpers section at the bottom of `cli.py`:

```python
def _render_check_results(results: list) -> None:
    from rich.table import Table as RichTable
    table = RichTable(show_header=True, box=None, padding=(0, 2))
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Findings", justify="right")
    table.add_column("Cost (USD)", justify="right")
    table.add_column("Duration (s)", justify="right")

    for r in results:
        if r.status == "pass":
            status_str = "[green]✓ pass[/green]"
        elif r.status == "fail":
            status_str = "[red]✗ FAIL[/red]"
        elif r.status == "warn":
            status_str = "[yellow]⚠ warn[/yellow]"
        else:
            status_str = "[dim]— skipped[/dim]"

        n = str(len(r.findings)) if r.status != "skipped" else "—"
        cost = f"{r.cost_usd:.4f}" if r.status != "skipped" else "—"
        dur = f"{r.duration_s:.1f}" if r.status != "skipped" else "—"
        table.add_row(r.name, status_str, n, cost, dur)

    console.print(table)

    all_findings = [f for r in results for f in r.findings]
    if all_findings:
        console.print(f"\n[red]✗ {len(all_findings)} finding(s):[/red]")
        for f in all_findings:
            console.print(f"  [bold]{f.check}[/bold]: {f.page or '(general)'}")
            console.print(f"    {f.message}")
            if f.claim_text:
                console.print(f'    claim: "{f.claim_text}"')
            if f.conflicting_page:
                console.print(f"    conflicts with {f.conflicting_page}")
                if f.conflicting_claim_text:
                    console.print(f'    "{f.conflicting_claim_text}"')
```

---

### Step 13 — Update `cli.py` `pr_merge` command

Replace the current `pr_merge` command with:

```python
@pr_group.command("merge")
@click.option("--override", is_flag=True, default=False,
              help="Merge despite failing checks. Requires --reason.")
@click.option("--reason", default=None,
              help="Override reason, logged to wiki/log.md. Required with --override.")
@click.option("--no-checks", "skip_checks", is_flag=True, default=False,
              help="Skip all adversarial checks (for raw-only PRs with no wiki edits).")
@click.pass_context
def pr_merge(ctx: click.Context, override: bool, reason: str | None, skip_checks: bool) -> None:
    """Merge the current raw/* branch via Gitea PR, then sync local repo."""
    from compost.ingest.pr import merge_pr
    from compost.gitea.client import GiteaError
    from compost.ingest.git import current_branch, default_branch
    from compost.checks.runner import run_checks, load_checks_config, infer_raw_path, write_check_report

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    if override and not reason:
        console.print("[red]--override requires --reason.[/red]")
        sys.exit(1)

    branch = current_branch(repo)
    base = default_branch(repo)

    if not skip_checks:
        raw_path = infer_raw_path(repo, branch, base)
        config = load_checks_config(repo)
        console.print("[dim]running checks...[/dim]")
        results = run_checks(repo, branch, raw_path, config=config)

        _render_check_results(results)
        write_check_report(repo, branch, results)

        failed = [r for r in results if r.status == "fail"]
        if failed and not override:
            names = ", ".join(r.name for r in failed)
            console.print(
                f"\n[red]✗ checks failed: {names}[/red]\n"
                f"Use [bold]--override --reason \"...\"[/bold] to bypass."
            )
            sys.exit(1)

    client = _load_gitea_client(repo)

    try:
        pr = merge_pr(repo, client)
        console.print(f"[green]merged[/green] (PR #{pr.number})")
    except GiteaError as e:
        console.print(f"[red]Gitea error: {e}[/red]")
        sys.exit(1)

    if override and reason and not skip_checks:
        _log_override(repo, branch, reason, [r.name for r in results if r.status == "fail"])
```

Add the `_log_override` helper in the helpers section:

```python
def _log_override(repo: Path, branch: str, reason: str, failed_checks: list[str]) -> None:
    """Append an override entry to wiki/log.md and commit it on main."""
    from datetime import datetime, timezone
    from compost.ingest.git import stage_and_commit

    ts = datetime.now(timezone.utc)
    log_path = repo / "wiki" / "log.md"
    entry = (
        f"\n## Override: {branch} ({ts:%Y-%m-%d %H:%M UTC})\n"
        f"Reason: {reason}\n"
        f"Findings bypassed: {', '.join(failed_checks) or 'none'}\n"
    )
    if log_path.exists():
        existing = log_path.read_text()
        log_path.write_text(existing + entry)
    else:
        log_path.write_text(f"# Override Log\n{entry}")

    try:
        stage_and_commit(repo, [log_path],
                         f"override: {branch} — {reason[:60]}")
        console.print(f"[dim]override logged → wiki/log.md[/dim]")
    except Exception as e:
        console.print(f"[yellow]⚠ could not commit override log: {e}[/yellow]")
```

---

### Step 14 — Update `.gitignore` in `bootstrap.py`

Add `.compost/checks/` to the generated `.gitignore`:

```python
(path / ".gitignore").write_text(
    "# compost generated artifacts — always re-derivable, never commit\n"
    ".compost/codify/\n"
    ".compost/synth-log/\n"
    ".compost/assay-report.md\n"
    ".compost/checks/\n"   # ← add this line
)
```

---

### Step 15 — Update `cli.py` `_run_doctor_checks`: add `.compost/checks/` to required ignores

```python
_REQUIRED_IGNORES = {".compost/codify/", ".compost/synth-log/", ".compost/checks/"}
```

---

### Step 16 — Update `tests/conftest.py`: add `.compost/checks/` to gitignore

In `_write_gitignore`:

```python
def _write_gitignore(repo: Path) -> None:
    (repo / ".gitignore").write_text(
        ".compost/codify/\n"
        ".compost/synth-log/\n"
        ".compost/assay-report.md\n"
        ".compost/checks/\n"
    )
```

---

### Step 17 — Create `tools/compost/tests/test_checks.py`

```python
"""Tests for tools/compost/checks/ package.

Strategy: black-box integration tests using real git repos and real file system.
LLM calls are intercepted by replacing _call_llm at the module boundary.
qmd_query is intercepted with a hand-written stub (no mocking library).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from compost.checks.runner import (
    CheckResult, ChecksConfig, Finding,
    run_checks, infer_raw_path, _skipped_result,
)
from compost.checks.provenance import check_provenance
from compost.checks.scope import check_scope
from compost.checks.human_edit_guard import check_human_edit_guard, _section_headings


# ── fixtures ─────────────────────────────────────────────────────────────────


def _wiki_file(repo: Path, rel: str, sources: list[str] | None = None) -> Path:
    """Write a minimal wiki page. Returns absolute path."""
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    src_yaml = ""
    if sources is not None:
        src_yaml = "\n".join(f"  - {s}" for s in sources)
        src_yaml = f"sources:\n{src_yaml}\n"
    path.write_text(
        f"---\ntype: service\nname: test\nowners: [alice]\nstatus: active\n"
        f"updated: 2026-01-01\nconfidence: high\n{src_yaml}related: []\n"
        f"supersedes: []\nsuperseded_by: null\n---\n\n# Test\n\n## Summary\nContent.\n"
        f"## Architecture\nDetails.\n"
    )
    return path


def _add_wiki_on_branch(
    repo: Path, rel: str, branch: str, sources: list[str] | None = None
) -> Path:
    """Create a wiki file and commit it on a new branch. Leaves HEAD on that branch."""
    path = _wiki_file(repo, rel, sources)
    subprocess.run(["git", "checkout", "-b", branch], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", str(path)], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", f"synth: add {rel}"],
                   cwd=repo, check=True, capture_output=True)
    return path


# ── provenance ────────────────────────────────────────────────────────────────


def test_provenance_passes_when_sources_present(wiki_repo):
    page = _wiki_file(wiki_repo, "wiki/services/auth.md",
                      sources=["raw/decisions/0001-stripe.md"])
    result = check_provenance(wiki_repo, [page])
    assert result.status == "pass"
    assert result.findings == []


def test_provenance_fails_when_sources_empty(wiki_repo):
    page = _wiki_file(wiki_repo, "wiki/services/auth.md", sources=[])
    result = check_provenance(wiki_repo, [page])
    assert result.status == "fail"
    assert any("sources" in f.message for f in result.findings)


def test_provenance_fails_when_sources_absent(wiki_repo):
    page = wiki_repo / "wiki/services/auth.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("---\ntype: service\nname: auth\n---\n\nNo sources field.\n")
    result = check_provenance(wiki_repo, [page])
    assert result.status == "fail"


# ── scope ─────────────────────────────────────────────────────────────────────


def test_scope_fails_when_page_not_in_qmd_results(wiki_repo):
    page = _wiki_file(wiki_repo, "wiki/services/auth.md",
                      sources=["raw/decisions/0001-stripe.md"])
    raw_path = wiki_repo / "raw/decisions/0001-stripe.md"
    config = ChecksConfig(scope_min_score=0.15, scope_top_n=5)

    with patch("compost.checks.scope.qmd_query", return_value=[]):
        result = check_scope(wiki_repo, [page], raw_path, config)

    assert result.status == "fail"
    assert any("not in top" in f.message for f in result.findings)


def test_scope_passes_when_page_in_qmd_results(wiki_repo):
    page = _wiki_file(wiki_repo, "wiki/services/payments.md",
                      sources=["raw/decisions/0001-stripe.md"])
    raw_path = wiki_repo / "raw/decisions/0001-stripe.md"
    config = ChecksConfig(scope_min_score=0.15, scope_top_n=5)

    fake_hits = [{"file": "qmd://wiki/services/payments.md", "score": 0.9, "title": "Payments"}]
    with patch("compost.checks.scope.qmd_query", return_value=fake_hits):
        result = check_scope(wiki_repo, [page], raw_path, config)

    assert result.status == "pass"


# ── human_edit_guard ─────────────────────────────────────────────────────────


def test_human_edit_guard_passes_when_no_human_commits(compost_git_repo):
    repo = compost_git_repo
    page = _add_wiki_on_branch(repo, "wiki/services/payments.md",
                               "raw/2026-05-04-payments",
                               sources=["raw/decisions/test.md"])
    config = ChecksConfig(human_edit_days=30)
    result = check_human_edit_guard(repo, [page], config, base_branch="main")
    assert result.status == "pass"
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_human_edit_guard_passes_when_only_synth_commits_in_window(compost_git_repo):
    repo = compost_git_repo
    # Add synth commit on main (should be ignored by guard)
    page = _wiki_file(repo, "wiki/services/payments.md", sources=["raw/decisions/test.md"])
    subprocess.run(["git", "add", str(page)], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "synth: add payments wiki page"],
                   cwd=repo, check=True, capture_output=True)

    # Branch with synthesis update
    page2 = _add_wiki_on_branch(repo, "wiki/services/payments.md",
                                "raw/2026-05-04-update",
                                sources=["raw/decisions/test.md"])
    config = ChecksConfig(human_edit_days=30)
    result = check_human_edit_guard(repo, [page2], config, base_branch="main")
    assert result.status == "pass"
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_human_edit_guard_fails_when_human_commit_overlaps(compost_git_repo):
    repo = compost_git_repo
    # Human edit on main (commit without synth:/raw: prefix)
    page = _wiki_file(repo, "wiki/services/payments.md", sources=["raw/decisions/test.md"])
    subprocess.run(["git", "add", str(page)], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "fix: update payments architecture section"],
                   cwd=repo, check=True, capture_output=True)

    # Synthesis branch that also touches the Architecture section
    subprocess.run(["git", "checkout", "-b", "raw/2026-05-04-synth-change"],
                   cwd=repo, check=True, capture_output=True)
    page.write_text(
        "---\ntype: service\nname: payments\nowners: [alice]\nstatus: active\n"
        "updated: 2026-01-02\nconfidence: high\nsources:\n  - raw/decisions/test.md\n"
        "related: []\nsupersedes: []\nsuperseded_by: null\n---\n\n# Payments\n\n"
        "## Summary\nContent.\n## Architecture\nSynth-changed architecture.\n"
    )
    subprocess.run(["git", "add", str(page)], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "synth: update payments"],
                   cwd=repo, check=True, capture_output=True)

    config = ChecksConfig(human_edit_days=30)
    result = check_human_edit_guard(repo, [page], config, base_branch="main")
    assert result.status == "fail"
    assert any("overlap" in f.message for f in result.findings)
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_section_headings_extracts_from_diff():
    diff = (
        "--- a/wiki/services/foo.md\n"
        "+++ b/wiki/services/foo.md\n"
        " ## Summary\n"
        "+## Architecture\n"
        "-## Old Section\n"
        " some content\n"
    )
    headings = _section_headings(diff)
    assert "Architecture" in headings
    assert "Old Section" in headings
    assert "Summary" not in headings


# ── run_checks short-circuit ──────────────────────────────────────────────────


def test_run_checks_short_circuits_on_kotlin_fail(compost_git_repo):
    repo = compost_git_repo
    branch = "raw/2026-05-04-kotlin-fail"
    _add_wiki_on_branch(repo, "wiki/services/auth.md", branch,
                        sources=["raw/decisions/0001-stripe.md"])

    fail_result = CheckResult(name="kotlin_assay", status="fail",
                              findings=[Finding(check="kotlin_assay", severity="fail",
                                                message="compile error: foo")])

    with patch("compost.checks.runner.check_kotlin_assay", return_value=fail_result):
        results = run_checks(repo, branch)

    assert results[0].name == "kotlin_assay"
    assert results[0].status == "fail"
    skipped = [r for r in results if r.status == "skipped"]
    assert len(skipped) == 6  # all remaining checks skipped
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_run_checks_short_circuits_on_provenance_fail(compost_git_repo):
    repo = compost_git_repo
    branch = "raw/2026-05-04-prov-fail"
    _add_wiki_on_branch(repo, "wiki/services/auth.md", branch, sources=[])

    kotlin_pass = CheckResult(name="kotlin_assay", status="skipped",
                              findings=[], skip_reason="kotlinc not found")
    fail_prov = CheckResult(name="provenance", status="fail",
                            findings=[Finding(check="provenance", severity="fail",
                                              message="missing or empty 'sources' field",
                                              page="wiki/services/auth.md")])

    with (
        patch("compost.checks.runner.check_kotlin_assay", return_value=kotlin_pass),
        patch("compost.checks.runner.check_provenance", return_value=fail_prov),
        patch("compost.checks.scope.qmd_query", return_value=[]),
    ):
        results = run_checks(repo, branch)

    assert any(r.name == "provenance" and r.status == "fail" for r in results)
    skipped = [r for r in results if r.status == "skipped"]
    skipped_names = {r.name for r in skipped}
    assert "contradiction_scan" in skipped_names
    assert "citation_faithfulness" in skipped_names
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_run_checks_contradiction_fail_skips_recent_raw_not_citation(compost_git_repo):
    repo = compost_git_repo
    branch = "raw/2026-05-04-contra-fail"
    _add_wiki_on_branch(repo, "wiki/services/auth.md", branch,
                        sources=["raw/decisions/0001-stripe.md"])

    contra_fail = CheckResult(name="contradiction_scan", status="fail",
                              findings=[Finding(check="contradiction_scan", severity="fail",
                                                message="contradiction detected",
                                                page="wiki/services/auth.md")])
    cit_pass = CheckResult(name="citation_faithfulness", status="pass", findings=[])

    with (
        patch("compost.checks.runner.check_kotlin_assay",
              return_value=CheckResult(name="kotlin_assay", status="skipped",
                                       findings=[], skip_reason="kotlinc not found")),
        patch("compost.checks.runner.check_provenance",
              return_value=CheckResult(name="provenance", status="pass", findings=[])),
        patch("compost.checks.runner.check_scope",
              return_value=CheckResult(name="scope", status="pass", findings=[])),
        patch("compost.checks.runner.check_human_edit_guard",
              return_value=CheckResult(name="human_edit_guard", status="pass", findings=[])),
        patch("compost.checks.runner.check_contradiction_scan", return_value=contra_fail),
        patch("compost.checks.runner.check_citation_faithfulness", return_value=cit_pass),
    ):
        results = run_checks(repo, branch)

    result_map = {r.name: r for r in results}
    assert result_map["contradiction_scan"].status == "fail"
    assert result_map["recent_raw_scan"].status == "skipped"
    assert result_map["citation_faithfulness"].status == "pass"
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_run_checks_skips_all_when_no_wiki_changes(compost_git_repo):
    repo = compost_git_repo
    # Branch with only a raw file (no wiki changes)
    branch = "raw/2026-05-04-raw-only"
    raw_file = repo / "raw/decisions/test-raw.md"
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_text("---\ncaptured_at: 2026-01-01T00:00:00Z\n---\nSome raw content.\n")
    subprocess.run(["git", "checkout", "-b", branch], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", str(raw_file)], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "raw: test raw only"],
                   cwd=repo, check=True, capture_output=True)

    with patch("compost.checks.runner.check_kotlin_assay",
               return_value=CheckResult(name="kotlin_assay", status="skipped",
                                        findings=[], skip_reason="kotlinc not found")):
        results = run_checks(repo, branch)

    skipped = [r for r in results if r.status == "skipped"]
    assert len(skipped) == 6
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


# ── kotlin_assay ─────────────────────────────────────────────────────────────


def test_kotlin_assay_skipped_when_kotlinc_absent():
    from compost.checks.kotlin_assay import check_kotlin_assay
    with patch("compost.checks.kotlin_assay.shutil.which", return_value=None):
        result = check_kotlin_assay(Path("/nonexistent"))
    assert result.status == "skipped"
    assert result.skip_reason == "kotlinc not found"


def test_kotlin_assay_fails_on_compile_error(wiki_repo):
    from compost.checks.kotlin_assay import check_kotlin_assay
    from compost.codify.compiler import CompileResult, CompileError

    fake_codegen = type("CR", (), {"page_count": 1, "warnings": [], "dispute_count": 0,
                                    "kt_path": Path("/tmp/wiki.kt")})()
    fake_compile = CompileResult(
        success=False,
        errors=[CompileError(kt_line=5, message="unresolved reference: foo", source_md=None)],
        jar_path=None,
    )
    with (
        patch("compost.checks.kotlin_assay.shutil.which", return_value="/usr/bin/kotlinc"),
        patch("compost.checks.kotlin_assay.codify", return_value=fake_codegen),
        patch("compost.checks.kotlin_assay.compile_kt", return_value=fake_compile),
    ):
        result = check_kotlin_assay(wiki_repo)

    assert result.status == "fail"
    assert any("compile error" in f.message for f in result.findings)


# ── infer_raw_path ────────────────────────────────────────────────────────────


def test_infer_raw_path_finds_added_raw_file(compost_git_repo):
    repo = compost_git_repo
    raw_file = repo / "raw/decisions/2026-05-04-test.md"
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_text("---\ncaptured_at: 2026-01-01T00:00:00Z\n---\nContent.\n")
    branch = "raw/2026-05-04T120000-test"
    subprocess.run(["git", "checkout", "-b", branch], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", str(raw_file)], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "raw: test"],
                   cwd=repo, check=True, capture_output=True)

    inferred = infer_raw_path(repo, branch, "main")
    assert inferred == raw_file
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_infer_raw_path_returns_none_when_no_raw_added(compost_git_repo):
    repo = compost_git_repo
    inferred = infer_raw_path(repo, "main", "main")
    assert inferred is None


# ── finding contradiction fields ───────────────────────────────────────────────


def test_finding_contradiction_has_structured_fields():
    f = Finding(
        check="contradiction_scan",
        severity="fail",
        message="contradiction detected",
        page="wiki/services/payments.md",
        conflicting_page="wiki/decisions/0001-stripe.md",
        claim_text="Stripe is async",
        conflicting_claim_text="Stripe is synchronous",
    )
    assert f.conflicting_page == "wiki/decisions/0001-stripe.md"
    assert f.claim_text == "Stripe is async"
    assert f.conflicting_claim_text == "Stripe is synchronous"
```

---

### Step 18 — Update `tools/compost/README.md`

Add after "## Tier 2 Synthesis" section:

```markdown
## Tier 2.5 Adversarial Checks

`compost pr merge` runs six checks before merging any branch that touches `wiki/`. Checks run
cheapest-first and short-circuit on deterministic failures.

```bash
# Run checks on the current branch
compost checks run

# Run checks on a specific branch
compost checks run --branch raw/2026-05-04T161353-my-change

# Merge with check gate (runs checks automatically)
compost pr merge

# Override a failing check (reason is logged to wiki/log.md)
compost pr merge --override --reason "contradiction is already declared; merging to unblock"

# Skip checks entirely (raw-only PR, no wiki edits)
compost pr merge --no-checks
```

Configure under `checks:` in `.compost.yml`:

```yaml
checks:
  provider: anthropic
  model: claude-sonnet-4-6
  human_edit_days: 30
  recent_raw_days: 90
  scope_min_score: 0.15
```

`ANTHROPIC_API_KEY` must be set for LLM-based checks (same key as synthesis).
```

---

### Step 19 — Update `AGENTS.md`

Add a note in the "Workflow" or relevant section:

```markdown
### Adversarial checks

`compost pr merge` now runs Tier 2.5 adversarial checks before merging. LLM-based checks
require `ANTHROPIC_API_KEY`. Checks run automatically; use `--no-checks` only for raw-only
branches with no wiki edits. Use `--override --reason "..."` to bypass failing checks — the
override is logged to `wiki/log.md`.
```

---

### Step 20 — Update `_plans/backlog.md`

Update the "Disputes as a first-class predicate" section to add at the end:

```markdown
**Phase 5 status:** adversarial checks now produce structured `Finding` objects with
`conflicting_page`, `claim_text`, and `conflicting_claim_text` fields. The remaining
missing piece is `compost claims suggest` — a command that reads `Finding` objects and
writes draft `@Contested TheoryOf` entries to `wiki/claims.kt`.
```

---

## § Dropped steps (vs high-level plan)

None dropped. All items in the high-level plan are covered above. The `_llm.py` module is
an addition not mentioned in the high-level plan's module layout (added to avoid duplicating
LLM calling code across three check modules without creating coupling to `synth/agent.py`).

---

## § Notes on TDD order

For each component, follow Red → Green → Refactor:

1. Write test(s) for `provenance.py` → implement → run tests
2. Write test(s) for `scope.py` → implement → run tests
3. Write test(s) for `human_edit_guard.py` → implement → run tests
4. Write test(s) for `run_checks` short-circuit → implement `runner.py` → run tests
5. Write test(s) for `kotlin_assay.py` → implement → run tests
6. Write test(s) for `infer_raw_path` → implement → run tests
7. Implement `contradiction.py`, `recent_raw.py`, `citation.py` (LLM integration tests only;
   no unit-test stubs needed for these — behavior is covered by the short-circuit tests)
8. Add CLI commands → run `compost checks run --help` to verify
9. Update `pr merge` → run `compost pr merge --help` to verify
10. Update docs, gitignore, pyproject.toml
