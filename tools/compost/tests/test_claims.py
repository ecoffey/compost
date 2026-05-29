from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from compost.cli import main


# ── fixtures / helpers ────────────────────────────────────────────────────────

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


# ── _read_contradiction_findings ──────────────────────────────────────────────

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
    assert "TODO" in stub
    assert "Provenance" in stub


def test_render_kotlin_stub_unique_var_name_per_index(wiki_repo):
    from compost.cli.claims import _render_kotlin_stub
    stub0 = _render_kotlin_stub(_CONTRADICTION_FINDING, "2026-05-23", index=0)
    stub1 = _render_kotlin_stub(_CONTRADICTION_FINDING, "2026-05-23", index=1)
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
    output = result.output.lower()
    assert "no findings" in output or "not found" in output


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
