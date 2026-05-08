"""
Integration tests for the lint module (Phase 9).

All tests use tmp_path-based fixtures. No mocking library — the false_negative_sampler
test uses a hand-crafted synth-log JSONL file as its "mock".
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import time as _time
from pathlib import Path

import pytest


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def lint_repo(tmp_path: Path) -> Path:
    """Minimal wiki repo for lint tests. No git required."""
    repo = tmp_path / "wiki"
    (repo / "wiki" / "services").mkdir(parents=True)
    (repo / "raw" / "decisions").mkdir(parents=True)
    (repo / "raw" / "research").mkdir(parents=True)
    (repo / ".compost.yml").write_text("name: test\nqmd_index: test\n")
    return repo


def _wiki_page(
    repo: Path,
    rel: str,
    *,
    owners: list | None = None,
    confidence: str = "high",
    sources: list | None = None,
) -> Path:
    """Write a minimal valid wiki page. Returns absolute path."""
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    src_yaml = (
        "sources: []\n" if not sources
        else "sources:\n" + "".join(f"  - {s}\n" for s in sources)
    )
    _owners = owners if owners is not None else ["alice"]
    owners_yaml = f"owners: {_owners}\n"
    p.write_text(
        "---\n"
        "type: service\n"
        f"name: {p.stem}\n"
        f"{owners_yaml}"
        "status: active\n"
        "updated: 2026-01-01\n"
        f"confidence: {confidence}\n"
        f"{src_yaml}"
        "---\n\n# content\n"
    )
    return p


def _raw_file(
    repo: Path,
    rel: str,
    *,
    source: str = "decision",
    confidence: str | None = None,
    expires_at: str | None = None,
    promotion_status: str | None = None,
) -> Path:
    """Write a raw file with given frontmatter. Returns absolute path."""
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"source: {source}",
        "captured_at: 2026-01-10T10:00:00Z",
        "captured_by: test",
        "origin: test",
    ]
    if confidence:
        lines.append(f"confidence: {confidence}")
    if expires_at:
        lines.append(f"expires_at: {expires_at}")
    if promotion_status:
        lines.append(f"promotion_status: {promotion_status}")
    lines += ["---", "", "body text"]
    p.write_text("\n".join(lines))
    return p


# ── stale_citations ───────────────────────────────────────────────────────────


def test_stale_citations_pass_when_source_exists(lint_repo):
    from compost.lint.linters import stale_citations
    _raw_file(lint_repo, "raw/decisions/0001.md")
    _wiki_page(lint_repo, "wiki/services/payments.md",
               sources=["raw/decisions/0001.md"])
    result = stale_citations.run(lint_repo)
    assert result.status == "pass"
    assert result.findings == []


def test_stale_citations_error_when_source_missing(lint_repo):
    from compost.lint.linters import stale_citations
    _wiki_page(lint_repo, "wiki/services/payments.md",
               sources=["raw/decisions/missing.md"])
    result = stale_citations.run(lint_repo)
    assert result.status == "error"
    assert len(result.findings) == 1
    assert result.findings[0].severity == "error"
    assert "missing.md" in result.findings[0].message


def test_stale_citations_multiple_missing(lint_repo):
    from compost.lint.linters import stale_citations
    _wiki_page(lint_repo, "wiki/services/payments.md",
               sources=["raw/decisions/a.md", "raw/decisions/b.md"])
    result = stale_citations.run(lint_repo)
    assert result.status == "error"
    assert len(result.findings) == 2


def test_stale_citations_skips_special_pages(lint_repo):
    from compost.lint.linters import stale_citations
    (lint_repo / "wiki" / "log.md").write_text(
        "---\nsources:\n  - raw/decisions/missing.md\n---\nbody\n"
    )
    (lint_repo / "wiki" / "glossary.md").write_text(
        "---\nsources:\n  - raw/decisions/also-missing.md\n---\nbody\n"
    )
    result = stale_citations.run(lint_repo)
    assert result.status == "pass"


def test_stale_citations_skipped_no_wiki_dir(tmp_path):
    from compost.lint.linters import stale_citations
    (tmp_path / ".compost.yml").write_text("name: test\n")
    result = stale_citations.run(tmp_path)
    assert result.status == "skipped"


# ── expired_research ──────────────────────────────────────────────────────────


def test_expired_research_pass_when_not_expired(lint_repo):
    from compost.lint.linters import expired_research
    future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    _raw_file(lint_repo, "raw/research/q1.md",
              expires_at=future, promotion_status="not_promoted")
    result = expired_research.run(lint_repo)
    assert result.status == "pass"


def test_expired_research_error_when_not_promoted_and_expired(lint_repo):
    from compost.lint.linters import expired_research
    past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    _raw_file(lint_repo, "raw/research/q1.md",
              expires_at=past, promotion_status="not_promoted")
    result = expired_research.run(lint_repo)
    assert result.status == "error"
    assert result.findings[0].severity == "error"
    assert "not promoted" in result.findings[0].message


def test_expired_research_warn_when_promoted_and_expired(lint_repo):
    from compost.lint.linters import expired_research
    past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    _raw_file(lint_repo, "raw/research/q1.md",
              expires_at=past, promotion_status="promoted")
    result = expired_research.run(lint_repo)
    assert result.status == "warn"
    assert result.findings[0].severity == "warn"


def test_expired_research_skips_no_expires_at(lint_repo):
    from compost.lint.linters import expired_research
    _raw_file(lint_repo, "raw/research/q1.md")
    result = expired_research.run(lint_repo)
    assert result.status == "pass"
    assert result.findings == []


def test_expired_research_skipped_no_research_dir(tmp_path):
    from compost.lint.linters import expired_research
    (tmp_path / ".compost.yml").write_text("name: test\n")
    result = expired_research.run(tmp_path)
    assert result.status == "skipped"


# ── confidence_mismatch ───────────────────────────────────────────────────────


def test_confidence_mismatch_pass_high_citing_high_research(lint_repo):
    from compost.lint.linters import confidence_mismatch
    _raw_file(lint_repo, "raw/research/q1.md", source="research", confidence="high")
    _wiki_page(lint_repo, "wiki/services/payments.md",
               confidence="high", sources=["raw/research/q1.md"])
    result = confidence_mismatch.run(lint_repo)
    assert result.status == "pass"


def test_confidence_mismatch_warn_high_citing_low_research(lint_repo):
    from compost.lint.linters import confidence_mismatch
    _raw_file(lint_repo, "raw/research/q1.md", source="research", confidence="low")
    _wiki_page(lint_repo, "wiki/services/payments.md",
               confidence="high", sources=["raw/research/q1.md"])
    result = confidence_mismatch.run(lint_repo)
    assert result.status == "warn"
    assert len(result.findings) == 1
    assert result.findings[0].severity == "warn"
    assert "raw/research/q1.md" in result.findings[0].message


def test_confidence_mismatch_ignores_non_research_sources(lint_repo):
    from compost.lint.linters import confidence_mismatch
    _raw_file(lint_repo, "raw/decisions/0001.md")
    _wiki_page(lint_repo, "wiki/services/payments.md",
               confidence="high", sources=["raw/decisions/0001.md"])
    result = confidence_mismatch.run(lint_repo)
    assert result.status == "pass"


def test_confidence_mismatch_ignores_medium_confidence_pages(lint_repo):
    from compost.lint.linters import confidence_mismatch
    _raw_file(lint_repo, "raw/research/q1.md", source="research", confidence="low")
    _wiki_page(lint_repo, "wiki/services/payments.md",
               confidence="medium", sources=["raw/research/q1.md"])
    result = confidence_mismatch.run(lint_repo)
    assert result.status == "pass"


# ── orphaned_raw ──────────────────────────────────────────────────────────────


def test_orphaned_raw_pass_when_cited(lint_repo):
    from compost.lint.linters import orphaned_raw
    _raw_file(lint_repo, "raw/decisions/0001.md")
    _wiki_page(lint_repo, "wiki/services/payments.md",
               sources=["raw/decisions/0001.md"])
    result = orphaned_raw.run(lint_repo, since_days=90)
    assert result.status == "pass"


def test_orphaned_raw_warn_when_not_cited(lint_repo):
    from compost.lint.linters import orphaned_raw
    _raw_file(lint_repo, "raw/decisions/0001.md")
    # No wiki page cites it
    result = orphaned_raw.run(lint_repo, since_days=90)
    assert result.status == "warn"
    assert any("raw/decisions/0001.md" in (f.path or "") for f in result.findings)


def test_orphaned_raw_ignores_old_files(lint_repo):
    from compost.lint.linters import orphaned_raw
    raw = _raw_file(lint_repo, "raw/decisions/old.md")
    old_ts = _time.time() - (200 * 86400)
    os.utime(raw, (old_ts, old_ts))
    result = orphaned_raw.run(lint_repo, since_days=90)
    assert result.status == "pass"


def test_orphaned_raw_skipped_no_raw_dir(tmp_path):
    from compost.lint.linters import orphaned_raw
    (tmp_path / ".compost.yml").write_text("name: test\n")
    result = orphaned_raw.run(tmp_path, since_days=90)
    assert result.status == "skipped"


# ── missing_owners ────────────────────────────────────────────────────────────


def test_missing_owners_pass_when_present(lint_repo):
    from compost.lint.linters import missing_owners
    _wiki_page(lint_repo, "wiki/services/payments.md", owners=["alice"])
    result = missing_owners.run(lint_repo)
    assert result.status == "pass"


def test_missing_owners_error_when_empty_list(lint_repo):
    from compost.lint.linters import missing_owners
    _wiki_page(lint_repo, "wiki/services/payments.md", owners=[])
    result = missing_owners.run(lint_repo)
    assert result.status == "error"
    assert len(result.findings) == 1
    assert result.findings[0].severity == "error"


def test_missing_owners_error_when_field_absent(lint_repo):
    from compost.lint.linters import missing_owners
    p = lint_repo / "wiki" / "services" / "no-owners.md"
    p.write_text("---\ntype: service\nname: no-owners\n---\nbody\n")
    result = missing_owners.run(lint_repo)
    assert result.status == "error"


def test_missing_owners_skips_special_pages(lint_repo):
    from compost.lint.linters import missing_owners
    (lint_repo / "wiki" / "log.md").write_text("# log\n")
    (lint_repo / "wiki" / "glossary.md").write_text("# glossary\n")
    (lint_repo / "wiki" / "index.md").write_text("# index\n")
    result = missing_owners.run(lint_repo)
    assert result.status == "pass"


def test_missing_owners_skipped_no_wiki_dir(tmp_path):
    from compost.lint.linters import missing_owners
    (tmp_path / ".compost.yml").write_text("name: test\n")
    result = missing_owners.run(tmp_path)
    assert result.status == "skipped"


# ── false_negative_sampler ────────────────────────────────────────────────────


def test_false_negative_sampler_no_raw_no_findings(lint_repo):
    from compost.lint.linters import false_negative_sampler
    result = false_negative_sampler.run(lint_repo, since_days=90)
    assert result.status == "pass"
    assert result.findings == []


def test_false_negative_sampler_flags_unsynthesized_fire(lint_repo):
    from compost.lint.linters import false_negative_sampler
    _raw_file(lint_repo, "raw/incidents/inc.md", source="incident")
    result = false_negative_sampler.run(lint_repo, since_days=90)
    assert result.status == "warn"
    assert any("raw/incidents/inc.md" in (f.path or "") for f in result.findings)


def test_false_negative_sampler_no_flag_when_synthesized(lint_repo):
    from compost.lint.linters import false_negative_sampler
    _raw_file(lint_repo, "raw/incidents/inc.md", source="incident")
    log_dir = lint_repo / ".compost" / "synth-log"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "2026-05-07T120000-abc12345.jsonl").write_text(
        json.dumps({
            "event": "run_complete",
            "ts": "2026-05-07T12:00:00",
            "run_id": "abc12345",
            "raw": "raw/incidents/inc.md",
            "total_cost_usd": 0.0,
            "wiki_edits": 1,
            "contradictions": 0,
            "duration_s": 1.0,
        }) + "\n"
    )
    result = false_negative_sampler.run(lint_repo, since_days=90)
    assert result.status == "pass"


def test_false_negative_sampler_no_flag_for_no_fire(lint_repo):
    from compost.lint.linters import false_negative_sampler
    # "note" source with plain body → should not fire default rules
    _raw_file(lint_repo, "raw/notes/standup.md", source="note")
    result = false_negative_sampler.run(lint_repo, since_days=90)
    # Only care that standup.md is NOT in findings (it may not fire)
    firing = [f for f in result.findings if "standup.md" in (f.path or "")]
    assert firing == []


# ── runner ────────────────────────────────────────────────────────────────────


def test_run_lint_returns_six_results(lint_repo):
    from compost.lint.runner import run_lint
    results = run_lint(lint_repo, since_days=90)
    assert len(results) == 6
    names = {r.linter for r in results}
    assert "stale_citations" in names
    assert "expired_research" in names
    assert "confidence_mismatch" in names
    assert "orphaned_raw" in names
    assert "missing_owners" in names
    assert "false_negative_sampler" in names


def test_run_lint_results_sorted_by_name(lint_repo):
    from compost.lint.runner import run_lint
    results = run_lint(lint_repo, since_days=90)
    names = [r.linter for r in results]
    assert names == sorted(names)


def test_run_lint_writes_jsonl_log(lint_repo):
    from compost.lint.runner import run_lint
    run_lint(lint_repo, since_days=90, run_id="test1234")
    log_dir = lint_repo / ".compost" / "lint-log"
    files = list(log_dir.glob("*test1234*.jsonl"))
    assert len(files) == 1
    events = [json.loads(line) for line in files[0].read_text().splitlines()]
    event_types = [e["event"] for e in events]
    assert "lint_start" in event_types
    assert "lint_complete" in event_types
    assert event_types.count("linter_result") == 6


def test_run_lint_log_includes_run_id(lint_repo):
    from compost.lint.runner import run_lint
    run_lint(lint_repo, since_days=90, run_id="myrunid1")
    log_dir = lint_repo / ".compost" / "lint-log"
    files = list(log_dir.glob("*myrunid1*.jsonl"))
    events = [json.loads(l) for l in files[0].read_text().splitlines()]
    start = next(e for e in events if e["event"] == "lint_start")
    assert start["run_id"] == "myrunid1"


def test_read_runs_returns_empty_when_no_logs(lint_repo):
    from compost.lint.runner import read_runs
    assert read_runs(lint_repo) == []


def test_read_runs_returns_summaries(lint_repo):
    from compost.lint.runner import run_lint, read_runs
    run_lint(lint_repo, since_days=90, run_id="aabbccdd")
    summaries = read_runs(lint_repo, last=5)
    assert len(summaries) == 1
    assert summaries[0]["event"] == "lint_complete"
    assert "total_findings" in summaries[0]


# ── report ────────────────────────────────────────────────────────────────────


def test_build_report_contains_all_linter_names(lint_repo):
    from compost.lint.runner import run_lint
    from compost.lint.report import build_report
    results = run_lint(lint_repo)
    report = build_report(results, run_id="test1234")
    for name in ("stale_citations", "expired_research", "confidence_mismatch",
                 "orphaned_raw", "missing_owners", "false_negative_sampler"):
        assert name in report


def test_build_report_shows_total_findings_bold(lint_repo):
    from compost.lint.runner import run_lint
    from compost.lint.report import build_report
    results = run_lint(lint_repo)
    total = sum(len(r.findings) for r in results)
    report = build_report(results, run_id="test1234")
    assert f"**{total}**" in report


def test_build_report_includes_run_id(lint_repo):
    from compost.lint.runner import run_lint
    from compost.lint.report import build_report
    results = run_lint(lint_repo)
    report = build_report(results, run_id="abc99999")
    assert "abc99999" in report


def test_append_to_log_md_creates_file_when_missing(lint_repo):
    from compost.lint.runner import run_lint
    from compost.lint.report import build_report, append_to_log_md
    results = run_lint(lint_repo)
    report = build_report(results, run_id="test1234")
    append_to_log_md(lint_repo, report)
    log_md = lint_repo / "wiki" / "log.md"
    assert log_md.exists()
    assert "Lint Run" in log_md.read_text()


def test_append_to_log_md_prepends_to_existing(lint_repo):
    from compost.lint.runner import run_lint
    from compost.lint.report import build_report, append_to_log_md
    log_md = lint_repo / "wiki" / "log.md"
    log_md.parent.mkdir(parents=True, exist_ok=True)
    log_md.write_text("# Wiki Log\n\n## Old Entry\n\nold content\n")
    results = run_lint(lint_repo)
    report = build_report(results, "new1234")
    append_to_log_md(lint_repo, report)
    content = log_md.read_text()
    assert content.index("new1234") < content.index("Old Entry")


def test_append_to_log_md_preserves_heading(lint_repo):
    from compost.lint.runner import run_lint
    from compost.lint.report import build_report, append_to_log_md
    log_md = lint_repo / "wiki" / "log.md"
    log_md.parent.mkdir(parents=True, exist_ok=True)
    log_md.write_text("# Wiki Log\n\nsome existing content\n")
    results = run_lint(lint_repo)
    report = build_report(results, "new5678")
    append_to_log_md(lint_repo, report)
    content = log_md.read_text()
    assert content.startswith("# Wiki Log\n")


# ── CLI ───────────────────────────────────────────────────────────────────────


def test_cli_lint_run_exits_zero_clean_repo(wiki_repo):
    from click.testing import CliRunner
    from compost.cli import main
    runner = CliRunner()
    result = runner.invoke(main, ["--repo", str(wiki_repo), "lint", "run"])
    assert result.exit_code == 0, result.output


def test_cli_lint_run_exits_one_on_error(lint_repo):
    from click.testing import CliRunner
    from compost.cli import main
    (lint_repo / "wiki" / "services").mkdir(parents=True, exist_ok=True)
    (lint_repo / "wiki" / "services" / "broken.md").write_text(
        "---\ntype: service\nname: broken\nowners: []\n"
        "status: active\nupdated: 2026-01-01\nconfidence: high\nsources: []\n---\nbody\n"
    )
    runner = CliRunner()
    result = runner.invoke(main, ["--repo", str(lint_repo), "lint", "run"])
    assert result.exit_code == 1


def test_cli_lint_run_updates_log_md(lint_repo):
    from click.testing import CliRunner
    from compost.cli import main
    runner = CliRunner()
    runner.invoke(main, ["--repo", str(lint_repo), "lint", "run"])
    log_md = lint_repo / "wiki" / "log.md"
    assert log_md.exists()
    assert "Lint Run" in log_md.read_text()


def test_cli_lint_log_no_runs(lint_repo):
    from click.testing import CliRunner
    from compost.cli import main
    runner = CliRunner()
    result = runner.invoke(main, ["--repo", str(lint_repo), "lint", "log"])
    assert result.exit_code == 0
    assert "No lint runs" in result.output


def test_cli_lint_log_shows_runs(lint_repo):
    from click.testing import CliRunner
    from compost.cli import main
    from compost.lint.runner import run_lint
    run_lint(lint_repo, run_id="deadbeef")
    runner = CliRunner()
    result = runner.invoke(main, ["--repo", str(lint_repo), "lint", "log"])
    assert result.exit_code == 0


def test_emit_pr_creates_branch_pushes_and_opens_pr(compost_git_repo_with_remote, monkeypatch):
    from click.testing import CliRunner
    from compost.cli import main
    from compost.gitea.client import GiteaPR

    repo = compost_git_repo_with_remote
    (repo / "wiki").mkdir(exist_ok=True)
    (repo / "wiki" / "log.md").write_text("# Synthesis Log\n")
    subprocess.run(["git", "add", "wiki/log.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add log"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=repo, check=True, capture_output=True)

    opened: list[tuple[str, str, str]] = []

    class FakeClient:
        def open_pr(self, branch, base, title, body):
            opened.append((branch, base, title))
            return GiteaPR(number=99, url="http://fake/99", state="open")

    monkeypatch.setattr("compost.cli.lint._load_gitea_client", lambda _repo: FakeClient())

    runner = CliRunner()
    result = runner.invoke(main, ["--repo", str(repo), "lint", "run", "--emit-pr"])

    assert result.exit_code == 0, result.output
    assert opened, f"open_pr was never called. Output:\n{result.output}"
    branch_name, base, title = opened[0]
    assert branch_name.startswith("lint/")
    assert base == "main"

    ls = subprocess.run(
        ["git", "ls-remote", "--heads", "origin"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    assert branch_name in ls.stdout, f"Branch '{branch_name}' not found in origin"


# ── replay_classify ───────────────────────────────────────────────────────────


def test_replay_classify_returns_empty_no_raw_dir(tmp_path):
    from compost.ingest.classifier import replay_classify
    (tmp_path / ".compost.yml").write_text("name: test\n")
    results = replay_classify(tmp_path, since_days=90)
    assert results == []


def test_replay_classify_returns_recent_files(tmp_path):
    from compost.ingest.classifier import replay_classify
    (tmp_path / ".compost.yml").write_text("name: test\n")
    (tmp_path / "raw" / "incidents").mkdir(parents=True)
    inc = tmp_path / "raw" / "incidents" / "inc.md"
    inc.write_text("---\nsource: incident\ncaptured_at: 2026-01-01T00:00:00Z\n"
                   "captured_by: test\norigin: test\n---\nbody\n")
    results = replay_classify(tmp_path, since_days=90)
    assert len(results) == 1
    path, decision = results[0]
    assert path == inc
    assert decision.fired


def test_replay_classify_excludes_old_files(tmp_path):
    from compost.ingest.classifier import replay_classify
    (tmp_path / ".compost.yml").write_text("name: test\n")
    (tmp_path / "raw" / "notes").mkdir(parents=True)
    old = tmp_path / "raw" / "notes" / "old.md"
    old.write_text("---\nsource: incident\ncaptured_at: 2026-01-01T00:00:00Z\n"
                   "captured_by: test\norigin: test\n---\nbody\n")
    old_ts = _time.time() - (200 * 86400)
    os.utime(old, (old_ts, old_ts))
    results = replay_classify(tmp_path, since_days=90)
    assert results == []
