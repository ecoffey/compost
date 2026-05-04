from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from compost.synth.agent import (
    ContradictionNote,
    FileDiff,
    SynthConfig,
    SynthesisResult,
    _parse_find_affected,
    _parse_propose_edit,
    load_synth_config,
    synthesize,
)
from compost.synth.diff_writer import apply_diffs, commit_diffs
from compost.synth.log import log_event, open_run, read_runs


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def synth_repo(tmp_path: Path) -> Path:
    """Minimal compost repo for synth tests."""
    (tmp_path / ".compost.yml").write_text(yaml.dump({
        "name": "test",
        "qmd_index": "test",
        "synth": {"provider": "anthropic", "model": "claude-sonnet-4-6", "max_candidates": 5},
    }))
    (tmp_path / "wiki" / "services").mkdir(parents=True)
    (tmp_path / "wiki" / "services" / "payments.md").write_text(
        "---\ntype: service\nname: payments\nowners: [alice]\n"
        "status: active\nupdated: 2026-01-15\nconfidence: high\n"
        "sources:\n  - raw/decisions/0001-stripe.md\n---\n\n# Payments\n"
    )
    (tmp_path / "raw" / "decisions").mkdir(parents=True)
    (tmp_path / "raw" / "decisions" / "2026-05-03-drop-postgres.md").write_text(
        "---\nsource: decision\ncaptured_at: 2026-05-03T10:00:00Z\n"
        "captured_by: alice\norigin: internal\n---\n\nWe decided to drop Postgres.\n"
    )
    return tmp_path


# ── load_synth_config ─────────────────────────────────────────────────────────

def test_load_synth_config_reads_yml(synth_repo):
    cfg = load_synth_config(synth_repo)
    assert cfg.provider == "anthropic"
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.max_candidates == 5


def test_load_synth_config_defaults_when_block_absent(tmp_path):
    (tmp_path / ".compost.yml").write_text("name: x\nqmd_index: x\n")
    cfg = load_synth_config(tmp_path)
    assert cfg.provider == "anthropic"
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.max_candidates == 10


# ── _parse_find_affected ──────────────────────────────────────────────────────

def test_parse_find_affected_bare_json():
    text = '[{"page": "wiki/services/p.md", "rationale": "r", "is_new": false}]'
    result = _parse_find_affected(text)
    assert len(result) == 1
    assert result[0]["page"] == "wiki/services/p.md"


def test_parse_find_affected_fenced_code_block():
    text = '```json\n[{"page": "wiki/services/p.md", "rationale": "r", "is_new": false}]\n```'
    result = _parse_find_affected(text)
    assert len(result) == 1


def test_parse_find_affected_empty_array():
    assert _parse_find_affected("[]") == []


def test_parse_find_affected_bad_json_returns_empty():
    assert _parse_find_affected("not json at all") == []


# ── _parse_propose_edit ───────────────────────────────────────────────────────

def test_parse_propose_edit_valid():
    data = {"content": "# page", "cited_sources": ["raw/x.md"], "contradictions": []}
    assert _parse_propose_edit(json.dumps(data))["content"] == "# page"


def test_parse_propose_edit_bad_json_returns_none():
    assert _parse_propose_edit("not json") is None


def test_parse_propose_edit_missing_content_returns_none():
    assert _parse_propose_edit('{"cited_sources": []}') is None


# ── log ───────────────────────────────────────────────────────────────────────

def test_log_open_run_creates_dir_and_file(tmp_path):
    path = open_run(tmp_path, "abc123")
    assert (tmp_path / ".compost" / "synth-log").exists()
    assert path.exists()
    assert "abc123" in path.name


def test_log_event_appends_valid_jsonl(tmp_path):
    path = open_run(tmp_path, "abc")
    log_event(path, "run_start", run_id="abc", raw="raw/x.md")
    log_event(path, "run_complete", run_id="abc", wiki_edits=1)
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    events = [json.loads(l) for l in lines]
    assert events[0]["event"] == "run_start"
    assert events[1]["event"] == "run_complete"
    assert "ts" in events[0]


def test_read_runs_returns_newest_first(tmp_path):
    import time as _time
    p1 = open_run(tmp_path, "run1")
    log_event(p1, "run_complete", run_id="run1", wiki_edits=0, total_cost_usd=0.0, duration_s=1.0)
    _time.sleep(0.02)
    p2 = open_run(tmp_path, "run2")
    log_event(p2, "run_complete", run_id="run2", wiki_edits=1, total_cost_usd=0.005, duration_s=2.0)

    runs = read_runs(tmp_path, last=10)
    assert len(runs) == 2
    assert runs[0]["run_id"] == "run2"


def test_read_runs_empty_when_no_logs(tmp_path):
    assert read_runs(tmp_path) == []


def test_read_runs_respects_last_limit(tmp_path):
    import time as _time
    for i in range(4):
        p = open_run(tmp_path, f"run{i}")
        log_event(p, "run_complete", run_id=f"run{i}", wiki_edits=0, total_cost_usd=0.0, duration_s=0.0)
        _time.sleep(0.02)
    assert len(read_runs(tmp_path, last=2)) == 2


# ── apply_diffs / commit_diffs ────────────────────────────────────────────────

def test_apply_diffs_creates_new_page(tmp_path):
    result = SynthesisResult(
        run_id="abc",
        wiki_diffs=[FileDiff(rel_path=Path("wiki/services/new.md"), content="# New", is_new=True)],
        pr_description="",
        cited_sources=[],
        declared_contradictions=[],
    )
    written = apply_diffs(tmp_path, result)
    assert (tmp_path / "wiki" / "services" / "new.md").read_text() == "# New"
    assert len(written) == 1


def test_apply_diffs_overwrites_existing_page(tmp_path):
    page = tmp_path / "wiki" / "services" / "payments.md"
    page.parent.mkdir(parents=True)
    page.write_text("old")
    result = SynthesisResult(
        run_id="abc",
        wiki_diffs=[FileDiff(rel_path=Path("wiki/services/payments.md"), content="new", is_new=False)],
        pr_description="",
        cited_sources=[],
        declared_contradictions=[],
    )
    apply_diffs(tmp_path, result)
    assert page.read_text() == "new"


def test_apply_diffs_returns_absolute_paths(tmp_path):
    result = SynthesisResult(
        run_id="abc",
        wiki_diffs=[FileDiff(rel_path=Path("wiki/services/new.md"), content="x", is_new=True)],
        pr_description="",
        cited_sources=[],
        declared_contradictions=[],
    )
    written = apply_diffs(tmp_path, result)
    assert all(p.is_absolute() for p in written)


def test_apply_diffs_empty_result_writes_nothing(tmp_path):
    result = SynthesisResult(
        run_id="abc", wiki_diffs=[], pr_description="",
        cited_sources=[], declared_contradictions=[],
    )
    assert apply_diffs(tmp_path, result) == []


# ── synthesize (LLM mocked) ───────────────────────────────────────────────────

_CANDIDATE = [{"file": "wiki/services/payments.md", "snippet": "payments", "title": "Payments", "score": 0.9}]

_FIND_RESP = json.dumps([
    {"page": "wiki/services/payments.md", "rationale": "related", "is_new": False}
])

_PROPOSE_RESP = json.dumps({
    "content": "---\ntype: service\nname: payments\n---\n\n# Payments\n\nUpdated.",
    "cited_sources": ["raw/decisions/2026-05-03-drop-postgres.md"],
    "contradictions": [],
})

_EMPTY_USAGE = {"input_tokens": 100, "output_tokens": 50,
                "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


@patch("compost.synth.agent.qmd_query")
@patch("compost.synth.agent._call_anthropic")
def test_synthesize_populates_result(mock_llm, mock_qmd, synth_repo):
    mock_qmd.return_value = _CANDIDATE
    mock_llm.side_effect = [
        (_FIND_RESP, _EMPTY_USAGE),
        (_PROPOSE_RESP, _EMPTY_USAGE),
    ]
    raw_path = synth_repo / "raw" / "decisions" / "2026-05-03-drop-postgres.md"
    result = synthesize(raw_path, synth_repo)

    assert result.run_id
    assert len(result.wiki_diffs) == 1
    assert result.wiki_diffs[0].rel_path == Path("wiki/services/payments.md")
    assert not result.wiki_diffs[0].is_new
    assert "raw/decisions/2026-05-03-drop-postgres.md" in result.cited_sources
    assert result.declared_contradictions == []


@patch("compost.synth.agent.qmd_query")
@patch("compost.synth.agent._call_anthropic")
def test_synthesize_no_candidates_returns_empty(mock_llm, mock_qmd, synth_repo):
    mock_qmd.return_value = []
    raw_path = synth_repo / "raw" / "decisions" / "2026-05-03-drop-postgres.md"
    result = synthesize(raw_path, synth_repo)
    assert result.wiki_diffs == []
    mock_llm.assert_not_called()


@patch("compost.synth.agent.qmd_query")
@patch("compost.synth.agent._call_anthropic")
def test_synthesize_llm_finds_no_affected_returns_empty(mock_llm, mock_qmd, synth_repo):
    mock_qmd.return_value = _CANDIDATE
    mock_llm.return_value = ("[]", _EMPTY_USAGE)
    raw_path = synth_repo / "raw" / "decisions" / "2026-05-03-drop-postgres.md"
    result = synthesize(raw_path, synth_repo)
    assert result.wiki_diffs == []
    assert mock_llm.call_count == 1  # only Phase A


@patch("compost.synth.agent.qmd_query")
@patch("compost.synth.agent._call_anthropic")
def test_synthesize_two_affected_pages_two_propose_calls(mock_llm, mock_qmd, synth_repo):
    mock_qmd.return_value = [
        {"file": "wiki/services/payments.md", "snippet": "x", "title": "P", "score": 0.9},
        {"file": "wiki/concepts/idempotency.md", "snippet": "y", "title": "I", "score": 0.8},
    ]
    two_affected = json.dumps([
        {"page": "wiki/services/payments.md", "rationale": "r1", "is_new": False},
        {"page": "wiki/concepts/idempotency.md", "rationale": "r2", "is_new": False},
    ])
    (synth_repo / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
    (synth_repo / "wiki" / "concepts" / "idempotency.md").write_text("---\ntype: concept\n---\n")
    mock_llm.side_effect = [
        (two_affected, _EMPTY_USAGE),
        (_PROPOSE_RESP, _EMPTY_USAGE),
        (_PROPOSE_RESP, _EMPTY_USAGE),
    ]
    raw_path = synth_repo / "raw" / "decisions" / "2026-05-03-drop-postgres.md"
    result = synthesize(raw_path, synth_repo)
    assert mock_llm.call_count == 3
    assert len(result.wiki_diffs) == 2


@patch("compost.synth.agent.qmd_query")
@patch("compost.synth.agent._call_anthropic")
def test_synthesize_dry_run_skips_log_file(mock_llm, mock_qmd, synth_repo):
    mock_qmd.return_value = _CANDIDATE
    mock_llm.side_effect = [
        (_FIND_RESP, _EMPTY_USAGE),
        (_PROPOSE_RESP, _EMPTY_USAGE),
    ]
    raw_path = synth_repo / "raw" / "decisions" / "2026-05-03-drop-postgres.md"
    result = synthesize(raw_path, synth_repo, dry_run=True)

    assert not (synth_repo / ".compost" / "synth-log").exists()
    assert len(result.wiki_diffs) == 1


@patch("compost.synth.agent.qmd_query")
@patch("compost.synth.agent._call_anthropic")
def test_synthesize_bad_propose_json_gracefully_skips(mock_llm, mock_qmd, synth_repo):
    mock_qmd.return_value = _CANDIDATE
    mock_llm.side_effect = [
        (_FIND_RESP, _EMPTY_USAGE),
        ("not valid json at all", _EMPTY_USAGE),
    ]
    raw_path = synth_repo / "raw" / "decisions" / "2026-05-03-drop-postgres.md"
    result = synthesize(raw_path, synth_repo)
    assert result.wiki_diffs == []


# ── qmd refactor: mcp tools still work ───────────────────────────────────────

def test_qmd_module_has_expected_functions():
    from compost import qmd
    assert callable(qmd.qmd_query)
    assert callable(qmd.qmd_get)


@patch("compost.qmd.qmd_query")
def test_mcp_query_wiki_uses_qmd_module(mock_qmd_query):
    mock_qmd_query.return_value = [
        {"file": "wiki/services/p.md", "snippet": "s", "title": "P", "score": 0.9}
    ]
    from compost.mcp.tools import query_wiki
    result = query_wiki("payments", "test-index", scope="team", limit=3)
    mock_qmd_query.assert_called_once()
    assert "payments" in result or "Results" in result or "p.md" in result
