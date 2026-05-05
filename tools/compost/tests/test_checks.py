"""Tests for tools/compost/checks/ package.

Strategy: black-box integration tests using real git repos and real file system.
LLM calls are intercepted by replacing module-level functions at the boundary.
qmd_query is intercepted with a hand-written stub (no mocking library).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from compost.checks.runner import (
    CheckResult,
    ChecksConfig,
    Finding,
    _skipped_result,
    infer_raw_path,
    run_checks,
)
from compost.checks.provenance import check_provenance
from compost.checks.human_edit_guard import check_human_edit_guard, _section_headings


# ── fixtures / helpers ────────────────────────────────────────────────────────


def _wiki_file(repo: Path, rel: str, sources: list[str] | None = None) -> Path:
    """Write a minimal wiki page with optional sources. Returns absolute path."""
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if sources is not None:
        src_yaml = "sources:\n" + "".join(f"  - {s}\n" for s in sources)
    else:
        src_yaml = ""
    path.write_text(
        f"---\ntype: service\nname: test\nowners: [alice]\nstatus: active\n"
        f"updated: 2026-01-01\nconfidence: high\n{src_yaml}"
        f"related: []\nsupersedes: []\nsuperseded_by: null\n---\n\n"
        f"# Test\n\n## Summary\nContent.\n## Architecture\nDetails.\n"
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
    assert result.findings[0].page == "wiki/services/auth.md"


def test_provenance_fails_when_sources_absent(wiki_repo):
    page = wiki_repo / "wiki/services/auth.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("---\ntype: service\nname: auth\n---\n\nNo sources field.\n")
    result = check_provenance(wiki_repo, [page])
    assert result.status == "fail"


def test_provenance_passes_multiple_pages_all_with_sources(wiki_repo):
    page1 = _wiki_file(wiki_repo, "wiki/services/auth.md",
                       sources=["raw/decisions/0001-stripe.md"])
    page2 = _wiki_file(wiki_repo, "wiki/concepts/idempotency.md",
                       sources=["raw/decisions/0001-stripe.md"])
    result = check_provenance(wiki_repo, [page1, page2])
    assert result.status == "pass"


def test_provenance_fails_one_page_missing_sources(wiki_repo):
    page1 = _wiki_file(wiki_repo, "wiki/services/auth.md",
                       sources=["raw/decisions/0001-stripe.md"])
    page2 = _wiki_file(wiki_repo, "wiki/concepts/idempotency.md", sources=[])
    result = check_provenance(wiki_repo, [page1, page2])
    assert result.status == "fail"
    assert len(result.findings) == 1
    assert "idempotency" in result.findings[0].page


# ── scope (with qmd stub) ─────────────────────────────────────────────────────


def test_scope_fails_when_page_not_in_qmd_results(wiki_repo):
    from compost.checks.scope import check_scope
    page = _wiki_file(wiki_repo, "wiki/services/auth.md",
                      sources=["raw/decisions/0001-stripe.md"])
    raw_path = wiki_repo / "raw/decisions/0001-stripe.md"
    config = ChecksConfig(scope_min_score=0.15, scope_top_n=5)

    with patch("compost.checks.scope.qmd_query", return_value=[]):
        result = check_scope(wiki_repo, [page], raw_path, config)

    assert result.status == "fail"
    assert any("not in top" in f.message for f in result.findings)


def test_scope_passes_when_page_in_qmd_results(wiki_repo):
    from compost.checks.scope import check_scope
    page = _wiki_file(wiki_repo, "wiki/services/payments.md",
                      sources=["raw/decisions/0001-stripe.md"])
    raw_path = wiki_repo / "raw/decisions/0001-stripe.md"
    config = ChecksConfig(scope_min_score=0.15, scope_top_n=5)

    fake_hits = [
        {"file": "qmd://wiki/services/payments.md", "score": 0.9, "title": "Payments"}
    ]
    with patch("compost.checks.scope.qmd_query", return_value=fake_hits):
        result = check_scope(wiki_repo, [page], raw_path, config)

    assert result.status == "pass"


def test_scope_fails_when_score_below_threshold(wiki_repo):
    from compost.checks.scope import check_scope
    page = _wiki_file(wiki_repo, "wiki/services/payments.md",
                      sources=["raw/decisions/0001-stripe.md"])
    raw_path = wiki_repo / "raw/decisions/0001-stripe.md"
    config = ChecksConfig(scope_min_score=0.5, scope_top_n=5)

    fake_hits = [
        {"file": "qmd://wiki/services/payments.md", "score": 0.1, "title": "Payments"}
    ]
    with patch("compost.checks.scope.qmd_query", return_value=fake_hits):
        result = check_scope(wiki_repo, [page], raw_path, config)

    assert result.status == "fail"


# ── human_edit_guard ─────────────────────────────────────────────────────────


def test_human_edit_guard_passes_when_no_human_commits(compost_git_repo):
    """Only synth-authored commits exist; guard should pass."""
    repo = compost_git_repo
    page = _add_wiki_on_branch(repo, "wiki/services/payments.md",
                               "raw/2026-05-04-payments",
                               sources=["raw/decisions/test.md"])
    config = ChecksConfig(human_edit_days=30)
    result = check_human_edit_guard(repo, [page], config, base_branch="main")
    assert result.status == "pass"
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_human_edit_guard_passes_when_only_synth_commits_in_window(compost_git_repo):
    """synth: commit on main should not trigger the guard."""
    repo = compost_git_repo
    page = _wiki_file(repo, "wiki/services/payments.md",
                      sources=["raw/decisions/test.md"])
    subprocess.run(["git", "add", str(page)], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "synth: add payments wiki page"],
                   cwd=repo, check=True, capture_output=True)

    # Branch with synthesis update — write different content so there's something to commit
    subprocess.run(["git", "checkout", "-b", "raw/2026-05-04-update"],
                   cwd=repo, check=True, capture_output=True)
    page.write_text(
        "---\ntype: service\nname: payments\nowners: [alice]\nstatus: active\n"
        "updated: 2026-01-02\nconfidence: high\nsources:\n  - raw/decisions/test.md\n"
        "related: []\nsupersedes: []\nsuperseded_by: null\n---\n\n# Payments\n\n"
        "## Summary\nUpdated content.\n## Architecture\nDetails.\n"
    )
    subprocess.run(["git", "add", str(page)], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "synth: update payments"],
                   cwd=repo, check=True, capture_output=True)

    config = ChecksConfig(human_edit_days=30)
    result = check_human_edit_guard(repo, [page], config, base_branch="main")
    assert result.status == "pass"
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_human_edit_guard_fails_when_human_commit_overlaps(compost_git_repo):
    """Human commit on main touches same section as synthesis branch."""
    repo = compost_git_repo
    page = _wiki_file(repo, "wiki/services/payments.md", sources=["raw/decisions/test.md"])
    subprocess.run(["git", "add", str(page)], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "fix: update payments architecture section"],
                   cwd=repo, check=True, capture_output=True)

    subprocess.run(["git", "checkout", "-b", "raw/2026-05-04-synth-change"],
                   cwd=repo, check=True, capture_output=True)
    page.write_text(
        "---\ntype: service\nname: payments\nowners: [alice]\nstatus: active\n"
        "updated: 2026-01-02\nconfidence: high\nsources:\n  - raw/decisions/test.md\n"
        "related: []\nsupersedes: []\nsuperseded_by: null\n---\n\n# Payments\n\n"
        "## Summary\nContent.\n## Architecture\nSynth-changed architecture details.\n"
    )
    subprocess.run(["git", "add", str(page)], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "synth: update payments"],
                   cwd=repo, check=True, capture_output=True)

    config = ChecksConfig(human_edit_days=30)
    result = check_human_edit_guard(repo, [page], config, base_branch="main")
    assert result.status == "fail"
    assert any("human commit" in f.message for f in result.findings)
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


def test_section_headings_extracts_from_hunk_context():
    """Changes to section content (not the heading line) are detected via @@ context."""
    diff = (
        "--- a/wiki/services/foo.md\n"
        "+++ b/wiki/services/foo.md\n"
        "@@ -10,3 +10,3 @@ ## Architecture\n"
        "-Old details.\n"
        "+New details.\n"
    )
    headings = _section_headings(diff)
    assert "Architecture" in headings


# ── run_checks short-circuit ──────────────────────────────────────────────────


def test_run_checks_short_circuits_on_kotlin_fail(compost_git_repo):
    repo = compost_git_repo
    branch = "raw/2026-05-04-kotlin-fail"
    _add_wiki_on_branch(repo, "wiki/services/auth.md", branch,
                        sources=["raw/decisions/0001-stripe.md"])

    fail_result = CheckResult(
        name="kotlin_assay", status="fail",
        findings=[Finding(check="kotlin_assay", severity="fail",
                          message="compile error: foo")],
    )

    with patch("compost.checks.kotlin_assay.check_kotlin_assay", return_value=fail_result):
        results = run_checks(repo, branch)

    assert results[0].name == "kotlin_assay"
    assert results[0].status == "fail"
    skipped = [r for r in results if r.status == "skipped"]
    assert len(skipped) == 6
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_run_checks_short_circuits_on_provenance_fail(compost_git_repo):
    repo = compost_git_repo
    branch = "raw/2026-05-04-prov-fail"
    # wiki page with no sources → provenance will fail for real
    _add_wiki_on_branch(repo, "wiki/services/auth.md", branch, sources=[])

    kotlin_pass = CheckResult(name="kotlin_assay", status="skipped",
                              findings=[], skip_reason="kotlinc not found")

    with (
        patch("compost.checks.kotlin_assay.check_kotlin_assay", return_value=kotlin_pass),
        patch("compost.checks.scope.qmd_query", return_value=[]),
    ):
        results = run_checks(repo, branch)

    result_map = {r.name: r for r in results}
    assert result_map["provenance"].status == "fail"
    skipped_names = {r.name for r in results if r.status == "skipped"}
    assert "contradiction_scan" in skipped_names
    assert "citation_faithfulness" in skipped_names
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_run_checks_contradiction_fail_skips_recent_raw_not_citation(compost_git_repo):
    repo = compost_git_repo
    branch = "raw/2026-05-04-contra-fail"
    _add_wiki_on_branch(repo, "wiki/services/auth.md", branch,
                        sources=["raw/decisions/0001-stripe.md"])

    contra_fail = CheckResult(
        name="contradiction_scan", status="fail",
        findings=[Finding(check="contradiction_scan", severity="fail",
                          message="contradiction detected",
                          page="wiki/services/auth.md")],
    )
    cit_pass = CheckResult(name="citation_faithfulness", status="pass", findings=[])

    with (
        patch("compost.checks.kotlin_assay.check_kotlin_assay",
              return_value=CheckResult(name="kotlin_assay", status="skipped",
                                       findings=[], skip_reason="kotlinc not found")),
        patch("compost.checks.provenance.check_provenance",
              return_value=CheckResult(name="provenance", status="pass", findings=[])),
        patch("compost.checks.scope.check_scope",
              return_value=CheckResult(name="scope", status="pass", findings=[])),
        patch("compost.checks.human_edit_guard.check_human_edit_guard",
              return_value=CheckResult(name="human_edit_guard", status="pass", findings=[])),
        patch("compost.checks.contradiction.check_contradiction_scan", return_value=contra_fail),
        patch("compost.checks.citation.check_citation_faithfulness", return_value=cit_pass),
    ):
        results = run_checks(repo, branch)

    result_map = {r.name: r for r in results}
    assert result_map["contradiction_scan"].status == "fail"
    assert result_map["recent_raw_scan"].status == "skipped"
    assert result_map["citation_faithfulness"].status == "pass"
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_run_checks_skips_all_when_no_wiki_changes(compost_git_repo):
    repo = compost_git_repo
    branch = "raw/2026-05-04-raw-only"
    raw_file = repo / "raw/decisions/test-raw.md"
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_text("---\ncaptured_at: 2026-01-01T00:00:00Z\n---\nSome raw content.\n")
    subprocess.run(["git", "checkout", "-b", branch], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", str(raw_file)], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "raw: test raw only"],
                   cwd=repo, check=True, capture_output=True)

    with patch("compost.checks.kotlin_assay.check_kotlin_assay",
               return_value=CheckResult(name="kotlin_assay", status="skipped",
                                        findings=[], skip_reason="kotlinc not found")):
        results = run_checks(repo, branch)

    skipped = [r for r in results if r.status == "skipped"]
    # kotlin_assay skipped (no kotlinc) + 6 remaining checks skipped (no wiki changes)
    assert len(skipped) == 7
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_run_checks_all_pass_returns_pass_list(compost_git_repo):
    repo = compost_git_repo
    branch = "raw/2026-05-04-all-pass"
    _add_wiki_on_branch(repo, "wiki/services/auth.md", branch,
                        sources=["raw/decisions/0001-stripe.md"])

    all_pass = lambda name: CheckResult(name=name, status="pass", findings=[])

    with (
        patch("compost.checks.kotlin_assay.check_kotlin_assay",
              return_value=CheckResult(name="kotlin_assay", status="skipped",
                                       findings=[], skip_reason="kotlinc not found")),
        patch("compost.checks.provenance.check_provenance", return_value=all_pass("provenance")),
        patch("compost.checks.scope.check_scope", return_value=all_pass("scope")),
        patch("compost.checks.human_edit_guard.check_human_edit_guard",
              return_value=all_pass("human_edit_guard")),
        patch("compost.checks.contradiction.check_contradiction_scan",
              return_value=all_pass("contradiction_scan")),
        patch("compost.checks.recent_raw.check_recent_raw_scan",
              return_value=all_pass("recent_raw_scan")),
        patch("compost.checks.citation.check_citation_faithfulness",
              return_value=all_pass("citation_faithfulness")),
    ):
        results = run_checks(repo, branch,
                             raw_path=repo / "raw/decisions/test.md")

    assert all(r.status in ("pass", "skipped") for r in results)
    failed = [r for r in results if r.status == "fail"]
    assert failed == []
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

    fake_codegen = type("CR", (), {
        "page_count": 1, "warnings": [], "dispute_count": 0,
        "kt_path": Path("/tmp/wiki.kt"),
    })()
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


def test_kotlin_assay_fails_on_render_invariant_violation(wiki_repo):
    from compost.checks.kotlin_assay import check_kotlin_assay
    from compost.codify.compiler import CompileResult

    fake_codegen = type("CR", (), {
        "page_count": 1, "warnings": [], "dispute_count": 0,
        "kt_path": Path("/tmp/wiki.kt"),
    })()
    fake_compile = CompileResult(success=True, errors=[], jar_path=Path("/tmp/wiki.jar"))

    with (
        patch("compost.checks.kotlin_assay.shutil.which", return_value="/usr/bin/kotlinc"),
        patch("compost.checks.kotlin_assay.codify", return_value=fake_codegen),
        patch("compost.checks.kotlin_assay.compile_kt", return_value=fake_compile),
        patch("compost.checks.kotlin_assay.render_wiki",
              side_effect=RuntimeError("wiki.jar render failed: invariant violated")),
    ):
        result = check_kotlin_assay(wiki_repo)

    assert result.status == "fail"
    assert any("invariant" in f.message for f in result.findings)


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


# ── Finding structured fields ─────────────────────────────────────────────────


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
