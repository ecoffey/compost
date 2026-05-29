# Backlog Catchup — Implementation Detail

## § Source plan

[2026-05-23-1706-backlog-catchup-high-level.md](2026-05-23-1706-backlog-catchup-high-level.md)

---

## § TDD order

1. Write `test_checks.py` additions (red) → extend `runner.py` (green)
2. Write `test_claims.py` in full (red) → implement `cli/claims.py` (green)
3. Install hybrid skill (no tests needed)
4. Update `backlog.md`

---

## § Step 1 — Install hybrid skill

```bash
ln -s ~/workspace/hybrid/skills/hybrid-loops ~/.claude/skills/hybrid-loops
```

Verify: `ls ~/.claude/skills/` shows `hybrid-loops`.

---

## § Step 2 — Extract `branch_slug` from `runner.py` and add JSONL sidecar

### 2a. Add tests to `tools/compost/tests/test_checks.py`

Import additions at top of file (after existing imports):

```python
import json
from compost.checks.runner import write_check_report, branch_slug
```

Add these tests at the end of the file:

```python
# ── write_check_report ────────────────────────────────────────────────────────

def test_branch_slug_replaces_slashes_and_colons():
    assert branch_slug("raw/2026-05-04-payments") == "raw-2026-05-04-payments"
    assert branch_slug("feat:auth") == "featauth"
    assert branch_slug("main") == "main"


def test_write_check_report_emits_jsonl(wiki_repo):
    results = [
        CheckResult(
            name="contradiction_scan", status="fail",
            findings=[
                Finding(
                    check="contradiction_scan", severity="fail",
                    message="conflicting claims detected",
                    page="wiki/services/payments.md",
                    conflicting_page="wiki/decisions/0001.md",
                    claim_text="uses JWT",
                    conflicting_claim_text="uses sessions",
                )
            ],
        ),
        CheckResult(name="provenance", status="pass", findings=[]),
    ]
    write_check_report(wiki_repo, "raw/2026-05-04-test", results)

    jsonl_path = wiki_repo / ".compost" / "checks" / "raw-2026-05-04-test.jsonl"
    assert jsonl_path.exists(), "JSONL sidecar not written"

    lines = [l for l in jsonl_path.read_text().splitlines() if l.strip()]
    # Only findings are written, not results with empty findings
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["check"] == "contradiction_scan"
    assert record["page"] == "wiki/services/payments.md"
    assert record["claim_text"] == "uses JWT"
    assert record["conflicting_page"] == "wiki/decisions/0001.md"
    assert record["conflicting_claim_text"] == "uses sessions"


def test_write_check_report_jsonl_empty_when_no_findings(wiki_repo):
    results = [
        CheckResult(name="provenance", status="pass", findings=[]),
        CheckResult(name="scope", status="skipped", findings=[], skip_reason="no raw"),
    ]
    write_check_report(wiki_repo, "main", results)

    jsonl_path = wiki_repo / ".compost" / "checks" / "main.jsonl"
    assert jsonl_path.exists()
    lines = [l for l in jsonl_path.read_text().splitlines() if l.strip()]
    assert lines == []


def test_write_check_report_jsonl_all_fields_present(wiki_repo):
    """Every Finding field is serialised — None values included."""
    results = [
        CheckResult(
            name="provenance", status="fail",
            findings=[
                Finding(
                    check="provenance", severity="fail",
                    message="missing sources",
                    page="wiki/services/auth.md",
                )
            ],
        )
    ]
    write_check_report(wiki_repo, "main", results)
    jsonl_path = wiki_repo / ".compost" / "checks" / "main.jsonl"
    record = json.loads(jsonl_path.read_text().strip())
    assert "check" in record
    assert "severity" in record
    assert "message" in record
    assert "page" in record
    assert "conflicting_page" in record   # present even if None
    assert "claim_text" in record
    assert "conflicting_claim_text" in record
```

### 2b. Extend `tools/compost/checks/runner.py`

**Add `branch_slug` as a module-level function** (before `write_check_report`):

```python
def branch_slug(branch: str) -> str:
    """Convert a branch name to a filesystem-safe slug (/ → -, : removed)."""
    return branch.replace("/", "-").replace(":", "")
```

**Update `write_check_report`** — replace the inline slug line and add JSONL write:

Replace:
```python
def write_check_report(repo: Path, branch: str, results: list[CheckResult]) -> Path:
    """Write a markdown report to .compost/checks/{branch-slug}.md."""
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc)
    slug = branch.replace("/", "-").replace(":", "")
```

With:
```python
def write_check_report(repo: Path, branch: str, results: list[CheckResult]) -> Path:
    """Write a markdown report and JSONL findings sidecar to .compost/checks/."""
    import dataclasses
    import json
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc)
    slug = branch_slug(branch)
```

Then at the end of `write_check_report`, **after** `report_path.write_text(...)` and before `return report_path`, add:

```python
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
```

Full revised function for clarity:

```python
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
```

Also update the import at the top of `runner.py` — add `write_check_report` to the imports already exported. Nothing needed; `branch_slug` is already accessible as a module-level function after this change.

---

## § Step 3 — Create `tools/compost/tests/test_claims.py` (red phase first)

```python
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from compost.cli import main


# ── helpers ───────────────────────────────────────────────────────────────────

def _write_findings_jsonl(repo: Path, branch: str, findings: list[dict]) -> Path:
    from compost.checks.runner import branch_slug
    slug = branch_slug(branch)
    p = repo / ".compost" / "checks" / f"{slug}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(f) for f in findings) + "\n")
    return p


_CONTRADICTION_FINDING = {
    "check": "contradiction_scan",
    "severity": "fail",
    "message": "conflicting claims detected",
    "page": "wiki/services/payments.md",
    "conflicting_page": "wiki/decisions/0001.md",
    "claim_text": "uses JWT for auth",
    "conflicting_claim_text": "uses session cookies",
}

_PROVENANCE_FINDING = {
    "check": "provenance",
    "severity": "fail",
    "message": "missing sources",
    "page": "wiki/services/auth.md",
    "conflicting_page": None,
    "claim_text": None,
    "conflicting_claim_text": None,
}


# ── _read_contradiction_findings ─────────────────────────────────────────────

def test_read_contradiction_findings_filters_to_contradiction_only(wiki_repo):
    from compost.cli.claims import _read_contradiction_findings
    p = _write_findings_jsonl(
        wiki_repo, "raw/test",
        [_CONTRADICTION_FINDING, _PROVENANCE_FINDING],
    )
    results = _read_contradiction_findings(p)
    assert len(results) == 1
    assert results[0]["check"] == "contradiction_scan"


def test_read_contradiction_findings_skips_missing_claim_text(wiki_repo):
    from compost.cli.claims import _read_contradiction_findings
    finding_no_claim = {**_CONTRADICTION_FINDING, "claim_text": None}
    p = _write_findings_jsonl(wiki_repo, "raw/test2", [finding_no_claim])
    results = _read_contradiction_findings(p)
    assert results == []


def test_read_contradiction_findings_missing_file_returns_empty(wiki_repo):
    from compost.cli.claims import _read_contradiction_findings
    p = wiki_repo / ".compost" / "checks" / "nonexistent.jsonl"
    assert _read_contradiction_findings(p) == []


# ── _render_kotlin_stub ───────────────────────────────────────────────────────

def test_render_kotlin_stub_contains_required_elements(wiki_repo):
    from compost.cli.claims import _render_kotlin_stub
    stub = _render_kotlin_stub(_CONTRADICTION_FINDING, "2026-05-23", index=0)
    assert "@Contested" in stub
    assert "TheoryOf" in stub
    assert "wiki/services/payments.md" in stub
    assert "uses JWT for auth" in stub
    assert "wiki/decisions/0001.md" in stub
    assert "uses session cookies" in stub
    assert "TODO" in stub  # subject placeholder
    assert "Provenance" in stub


def test_render_kotlin_stub_unique_var_name_per_index(wiki_repo):
    from compost.cli.claims import _render_kotlin_stub
    stub0 = _render_kotlin_stub(_CONTRADICTION_FINDING, "2026-05-23", index=0)
    stub1 = _render_kotlin_stub(_CONTRADICTION_FINDING, "2026-05-23", index=1)
    # Extract variable names — they must differ
    import re
    names = [re.search(r'val (\w+):', s).group(1) for s in (stub0, stub1)]
    assert names[0] != names[1]


def test_render_kotlin_stub_no_conflicting_claim_text(wiki_repo):
    from compost.cli.claims import _render_kotlin_stub
    finding = {**_CONTRADICTION_FINDING, "conflicting_claim_text": None}
    stub = _render_kotlin_stub(finding, "2026-05-23", index=0)
    assert "@Contested" in stub
    assert "wiki/decisions/0001.md" in stub


# ── CLI: compost claims suggest ───────────────────────────────────────────────

def test_claims_suggest_dry_run_prints_stubs(wiki_repo):
    _write_findings_jsonl(wiki_repo, "raw/test-branch", [_CONTRADICTION_FINDING])
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--repo", str(wiki_repo), "claims", "suggest",
         "--branch", "raw/test-branch", "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert "@Contested" in result.output
    assert "TheoryOf" in result.output
    claims_kt = wiki_repo / "wiki" / "claims.kt"
    assert not claims_kt.exists(), "dry-run must not write to disk"


def test_claims_suggest_writes_claims_kt(wiki_repo):
    _write_findings_jsonl(wiki_repo, "raw/test-branch", [_CONTRADICTION_FINDING])
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--repo", str(wiki_repo), "claims", "suggest",
         "--branch", "raw/test-branch"],
    )
    assert result.exit_code == 0, result.output
    claims_kt = wiki_repo / "wiki" / "claims.kt"
    assert claims_kt.exists()
    content = claims_kt.read_text()
    assert "@Contested" in content
    assert "TheoryOf" in content
    assert "uses JWT for auth" in content


def test_claims_suggest_appends_on_second_run(wiki_repo):
    _write_findings_jsonl(wiki_repo, "raw/branch-a", [_CONTRADICTION_FINDING])
    finding_b = {**_CONTRADICTION_FINDING, "claim_text": "uses OAuth"}
    _write_findings_jsonl(wiki_repo, "raw/branch-b", [finding_b])
    runner = CliRunner()

    runner.invoke(main, ["--repo", str(wiki_repo), "claims", "suggest",
                         "--branch", "raw/branch-a"])
    runner.invoke(main, ["--repo", str(wiki_repo), "claims", "suggest",
                         "--branch", "raw/branch-b"])

    content = (wiki_repo / "wiki" / "claims.kt").read_text()
    assert "uses JWT for auth" in content
    assert "uses OAuth" in content


def test_claims_suggest_no_findings_file_exits_gracefully(wiki_repo):
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--repo", str(wiki_repo), "claims", "suggest",
         "--branch", "raw/no-such-branch"],
    )
    assert result.exit_code == 0
    assert "no findings" in result.output.lower() or "not found" in result.output.lower()


def test_claims_suggest_no_contradiction_findings_reports_zero(wiki_repo):
    _write_findings_jsonl(wiki_repo, "raw/clean", [_PROVENANCE_FINDING])
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--repo", str(wiki_repo), "claims", "suggest",
         "--branch", "raw/clean"],
    )
    assert result.exit_code == 0
    assert "0" in result.output
```

---

## § Step 4 — Create `tools/compost/cli/claims.py`

```python
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import click
from rich.console import Console

console = Console()


@click.group("claims")
def claims_group() -> None:
    """Work with contested claims extracted from adversarial check findings."""


@claims_group.command("suggest")
@click.option("--branch", default=None,
              help="Branch slug to read findings from. Defaults to current git branch.")
@click.option("--dry-run", is_flag=True,
              help="Print stubs to stdout instead of writing wiki/claims.kt.")
@click.pass_context
def claims_suggest(ctx: click.Context, branch: str | None, dry_run: bool) -> None:
    """Generate @Contested TheoryOf stubs from contradiction check findings."""
    import subprocess
    from datetime import date
    from compost.checks.runner import branch_slug
    from compost.repo import find_repo_root

    repo = ctx.obj.get("repo") or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found. Run from inside a compost wiki repo.[/red]")
        sys.exit(1)

    if branch is None:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo, capture_output=True, text=True,
        )
        branch = result.stdout.strip() if result.returncode == 0 else "main"

    slug = branch_slug(branch)
    findings_path = repo / ".compost" / "checks" / f"{slug}.jsonl"
    findings = _read_contradiction_findings(findings_path)

    if not findings_path.exists():
        console.print(f"[dim]No findings file found for branch '{branch}' ({findings_path.name}).[/dim]")
        console.print("[dim]Run 'compost checks run' on the branch first.[/dim]")
        return

    if not findings:
        console.print(f"[dim]0 contradiction findings for branch '{branch}'.[/dim]")
        return

    date_str = date.today().isoformat()
    stubs = [_render_kotlin_stub(f, date_str, index=i) for i, f in enumerate(findings)]
    combined = "\n\n".join(stubs)

    if dry_run:
        console.print(combined)
        return

    claims_kt = repo / "wiki" / "claims.kt"
    if not claims_kt.exists():
        claims_kt.write_text(
            "// claims.kt — Contested theories extracted from compost check findings.\n"
            "// Each entry was generated by `compost claims suggest` and requires human review.\n"
            "// Edit freely: add context, promote to Disputes, or delete if resolved.\n\n"
        )

    existing = claims_kt.read_text()
    claims_kt.write_text(existing + combined + "\n")
    console.print(f"[green]{len(findings)} stub(s) written to wiki/claims.kt[/green]")


def _read_contradiction_findings(findings_path: Path) -> list[dict]:
    """Read JSONL sidecar; return only contradiction_scan findings with claim_text set."""
    if not findings_path.exists():
        return []
    results = []
    for line in findings_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            record.get("check") == "contradiction_scan"
            and record.get("claim_text")
        ):
            results.append(record)
    return results


def _render_kotlin_stub(finding: dict, date_str: str, *, index: int) -> str:
    """Render a single @Contested TheoryOf Kotlin stub from a contradiction finding."""
    page = finding.get("page", "unknown")
    var_name = "theory_" + re.sub(r"[^a-zA-Z0-9]", "_", page) + f"_{index}"
    claim = finding.get("claim_text", "")
    conflicting_page = finding.get("conflicting_page") or ""
    conflicting_claim = finding.get("conflicting_claim_text") or ""

    lines = [
        f"// Suggested by `compost claims suggest` on {date_str} — review before committing",
        "@Contested",
        f"val {var_name}: TheoryOf = TheoryOf(",
        f'    subject = TODO("resolve page: {page}"),',
        f'    claim = "{_escape_kt(claim)}",',
        f'    prov = Provenance(origin = "compost-checks", ingestedAt = "{date_str}", ingestedBy = "compost"),',
        ")",
    ]
    if conflicting_page:
        lines.append(f"// Conflicts with: {conflicting_page}")
    if conflicting_claim:
        lines.append(f'// Conflicting claim: "{_escape_kt(conflicting_claim)}"')

    return "\n".join(lines)


def _escape_kt(s: str) -> str:
    """Escape double quotes and backslashes for Kotlin string literals."""
    return s.replace("\\", "\\\\").replace('"', '\\"')
```

---

## § Step 5 — Register `claims_group` in `tools/compost/cli/__init__.py`

Add import after the `federation_group` import:

```python
from compost.cli.claims import claims_group
```

Add after `main.add_command(federation_group)`:

```python
main.add_command(claims_group)
```

---

## § Step 6 — Update `_plans/backlog.md`

Replace the entire backlog file content. Changes:
- `Verify: qmd --index per-index isolation` — mark **DONE** (Phase 10 integration tests)
- `single persistent qmd process` — update "When to revisit" to Phase 12+
- `hybrid skill` — mark **DONE** (this plan)
- `winze metabolism` — note `compost claims suggest` ships in this plan; full metabolism deferred Phase 12+
- `structured TheoryOf claims` — note dependency on `claims.kt` entries accumulating
- `initialize from existing docs` — add "When to revisit: Phase 12+ — needs dedicated design phase"
- `feedback on PRs` — add "When to revisit: Phase 12+ — needs dedicated design phase"

Specific edits by section:

**`Verify: qmd --index per-index isolation`** — prepend:
```
> **DONE (Phase 10):** Verified by integration tests in `tests/test_federation.py`.
```

**`hybrid — install the Claude Code skill`** — prepend:
```
> **DONE (Phase backlog-catchup):** Symlinked at `~/.claude/skills/hybrid-loops`.
```

**`single persistent qmd/compost process`** — change "When to revisit: Phase 8" to:
```
**When to revisit:** Phase 12+. The `_warmup()` call in `run_server` mitigates latency sufficiently for laptop use. Revisit when serving multiple concurrent sessions where per-session warmup becomes measurable.
```

**`winze — metabolism and epistemic discipline patterns`** — update Phase 5 status note and add at bottom:
```
**Phase backlog-catchup status:** `compost claims suggest` ships in this plan, writing structured `@Contested TheoryOf` stubs to `wiki/claims.kt` from contradiction check findings. Full metabolism phases (dream/bias-audit/calibrate) deferred to Phase 12+.
```

**`Structured TheoryOf claims`** — update "When to revisit":
```
**When to revisit:** After several weeks of `claims.kt` entries from `compost claims suggest`. The recurring predicate patterns will surface the right vocabulary.
```

**`Initialize from existing documentation`** — add:
```
**When to revisit:** Phase 12+ — needs dedicated design phase covering batch ingest, timestamp preservation, async processing, and progress tracking.
```

**`Feedback on PRs`** — add:
```
**When to revisit:** Phase 12+ — needs dedicated design phase.
```

---

## § Cross-check: high-level plan vs. this impl plan

| High-level item | Impl plan coverage |
|---|---|
| hybrid symlink | Step 1 ✓ |
| `branch_slug` exported from runner.py | Step 2b ✓ |
| JSONL sidecar in `write_check_report` | Step 2b ✓ |
| JSONL write failure is non-fatal | Step 2b ✓ (OSError caught, pass) |
| `_read_contradiction_findings` hides file format from CLI | Step 4 ✓ |
| `claims suggest --dry-run` prints, does not write | Step 4 ✓ |
| `claims suggest` appends to existing `wiki/claims.kt` | Step 4 ✓ |
| `wiki/claims.kt` created with header on first run | Step 4 ✓ |
| `claims_group` registered in `cli/__init__.py` | Step 5 ✓ |
| `backlog.md` updated with triage decisions | Step 6 ✓ |
| All 8 planned tests present | Steps 2a + 3 ✓ |

**One ambiguity resolved:** the high-level plan listed `test_claims_suggest_missing_branch` as a test name but defined it as "graceful error when branch slug resolves to no JSONL file" — this is implemented as `test_claims_suggest_no_findings_file_exits_gracefully` (same behaviour, clearer name).

**No items dropped.**
