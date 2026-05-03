import json
from pathlib import Path

import pytest
import yaml

from compost.ingest.classifier import (
    ClassifyDecision,
    Trigger,
    classify,
    load_rules,
    log_decision,
)


# ── helpers ───────────────────────────────────────────────────────────────────


@pytest.fixture
def raw_repo(tmp_path: Path) -> Path:
    """Minimal repo: .compost.yml + raw/. No git required."""
    (tmp_path / ".compost.yml").write_text("name: test\nqmd_index: test\n")
    (tmp_path / "raw").mkdir()
    return tmp_path


def _write_raw(
    repo: Path,
    rel_path: str,
    source: str,
    body: str = "",
    intent: str = "",
) -> Path:
    """Write a raw markdown file with minimal frontmatter. Returns absolute path."""
    f = repo / rel_path
    f.parent.mkdir(parents=True, exist_ok=True)
    fm = (
        f"---\nsource: {source}\ncaptured_at: 2026-04-27T10:00:00Z\n"
        f"captured_by: test\norigin: test\n"
    )
    if intent:
        fm += f"intent: {intent}\n"
    fm += "---\n"
    f.write_text(fm + body)
    return f


# ── load_rules ────────────────────────────────────────────────────────────────


def test_load_rules_falls_back_to_default(raw_repo):
    rules = load_rules(raw_repo)
    assert "incident" in rules["source_triggers"]
    assert "decision" in rules["source_triggers"]
    assert rules["volume"]["threshold"] == 5


def test_load_rules_repo_override(raw_repo):
    override = {"source_triggers": ["support"], "semantic_keywords": [],
                "path_patterns": [], "volume": {"threshold": 2, "window_days": 3}}
    override_path = raw_repo / ".compost" / "classifier_rules.yaml"
    override_path.parent.mkdir(exist_ok=True)
    override_path.write_text(yaml.dump(override))

    rules = load_rules(raw_repo)
    assert rules["source_triggers"] == ["support"]
    assert rules["volume"]["threshold"] == 2


# ── classify: source trigger ──────────────────────────────────────────────────


def test_classify_source_trigger(raw_repo):
    raw_path = _write_raw(raw_repo, "raw/notes/inc.md", "incident")
    decision = classify(raw_path, raw_repo)
    assert decision.fired
    assert any(t.kind == "source" and t.pattern == "incident" for t in decision.triggers)


def test_classify_decision_source_fires(raw_repo):
    raw_path = _write_raw(raw_repo, "raw/decisions/dec.md", "decision")
    decision = classify(raw_path, raw_repo)
    assert decision.fired
    assert any(t.kind == "source" for t in decision.triggers)


# ── classify: semantic trigger ────────────────────────────────────────────────


def test_classify_semantic_trigger(raw_repo):
    raw_path = _write_raw(
        raw_repo, "raw/notes/note.md", "note",
        body="We did a postmortem on the outage.",
    )
    decision = classify(raw_path, raw_repo)
    assert decision.fired
    assert any(t.kind == "semantic" and t.pattern == "postmortem" for t in decision.triggers)


def test_classify_semantic_trigger_on_intent_field(raw_repo):
    raw_path = _write_raw(
        raw_repo, "raw/notes/note2.md", "note",
        body="No keywords here.",
        intent="ownership discussion",
    )
    decision = classify(raw_path, raw_repo)
    assert decision.fired
    assert any(t.kind == "semantic" and t.pattern == "ownership" for t in decision.triggers)


def test_classify_semantic_case_insensitive(raw_repo):
    raw_path = _write_raw(raw_repo, "raw/notes/note3.md", "note", body="We will DEPRECATE this.")
    decision = classify(raw_path, raw_repo)
    assert decision.fired
    assert any(t.kind == "semantic" for t in decision.triggers)


# ── classify: path trigger ────────────────────────────────────────────────────


def test_classify_path_trigger(raw_repo):
    # Use custom rules to isolate path trigger (disable source trigger for "note")
    rules = {
        "source_triggers": [],
        "semantic_keywords": [],
        "path_patterns": ["raw/decisions/*"],
        "volume": {"threshold": 99, "window_days": 7},
    }
    raw_path = _write_raw(raw_repo, "raw/decisions/dec.md", "note")
    decision = classify(raw_path, raw_repo, rules=rules)
    assert decision.fired
    assert any(t.kind == "path" and t.pattern == "raw/decisions/*" for t in decision.triggers)


# ── classify: volume trigger ──────────────────────────────────────────────────


def test_classify_volume_trigger(raw_repo):
    # Use a source type ("note") that doesn't fire on source/path rules.
    # Write 5 note files so the 5th hits threshold=5.
    # raw_repo has no git, so the mtime fallback path is exercised.
    rules = {
        "source_triggers": [],
        "semantic_keywords": [],
        "path_patterns": [],
        "volume": {"threshold": 5, "window_days": 7},
    }
    for i in range(5):
        _write_raw(raw_repo, f"raw/notes/note-{i}.md", "note")

    raw_path = raw_repo / "raw" / "notes" / "note-4.md"
    decision = classify(raw_path, raw_repo, rules=rules)
    assert decision.fired
    assert any(t.kind == "volume" for t in decision.triggers)


def test_classify_volume_below_threshold_no_fire(raw_repo):
    rules = {
        "source_triggers": [],
        "semantic_keywords": [],
        "path_patterns": [],
        "volume": {"threshold": 5, "window_days": 7},
    }
    for i in range(4):
        _write_raw(raw_repo, f"raw/notes/note-{i}.md", "note")

    raw_path = raw_repo / "raw" / "notes" / "note-3.md"
    decision = classify(raw_path, raw_repo, rules=rules)
    assert not decision.fired


# ── classify: no-fire ────────────────────────────────────────────────────────


def test_classify_no_fire(raw_repo):
    raw_path = _write_raw(raw_repo, "raw/notes/standup.md", "note", body="Daily standup notes.")
    decision = classify(raw_path, raw_repo)
    assert not decision.fired
    assert decision.triggers == []
    assert decision.rationale == "no triggers matched"


# ── classify: multi-trigger ───────────────────────────────────────────────────


def test_classify_multi_trigger(raw_repo):
    raw_path = _write_raw(
        raw_repo, "raw/incidents/inc-001.md", "incident",
        body="We made a decision to rollback the deployment.",
    )
    decision = classify(raw_path, raw_repo)
    assert decision.fired
    kinds = {t.kind for t in decision.triggers}
    assert "source" in kinds
    assert "semantic" in kinds
    assert "path" in kinds
    assert len(decision.triggers) >= 3


# ── classify: custom rules ────────────────────────────────────────────────────


def test_classify_custom_rules(raw_repo):
    custom_rules = {
        "source_triggers": ["support"],
        "semantic_keywords": ["escalated"],
        "path_patterns": [],
        "volume": {"threshold": 99, "window_days": 7},
    }
    raw_path = _write_raw(raw_repo, "raw/notes/ticket.md", "support",
                          body="This was escalated to senior support.")
    decision = classify(raw_path, raw_repo, rules=custom_rules)
    assert decision.fired
    kinds = {t.kind for t in decision.triggers}
    assert "source" in kinds
    assert "semantic" in kinds


def test_classify_custom_rules_no_fire_for_incident(raw_repo):
    custom_rules = {
        "source_triggers": ["support"],  # incident not listed
        "semantic_keywords": [],
        "path_patterns": [],
        "volume": {"threshold": 99, "window_days": 7},
    }
    raw_path = _write_raw(raw_repo, "raw/notes/inc.md", "incident")
    decision = classify(raw_path, raw_repo, rules=custom_rules)
    assert not decision.fired


# ── classify: rationale ───────────────────────────────────────────────────────


def test_classify_rationale_single_trigger(raw_repo):
    rules = {
        "source_triggers": ["incident"],
        "semantic_keywords": [],
        "path_patterns": [],
        "volume": {"threshold": 99, "window_days": 7},
    }
    raw_path = _write_raw(raw_repo, "raw/notes/inc.md", "incident")
    decision = classify(raw_path, raw_repo, rules=rules)
    assert "1 matched trigger" in decision.rationale


def test_classify_rationale_multi_trigger(raw_repo):
    raw_path = _write_raw(raw_repo, "raw/incidents/inc.md", "incident")
    decision = classify(raw_path, raw_repo)
    assert "matched triggers" in decision.rationale
    assert "source:incident" in decision.rationale


# ── log_decision ─────────────────────────────────────────────────────────────


def test_log_decision_creates_dir(raw_repo):
    assert not (raw_repo / ".compost").exists()
    raw_path = _write_raw(raw_repo, "raw/notes/note.md", "note")
    decision = classify(raw_path, raw_repo)
    log_decision(raw_repo, Path("raw/notes/note.md"), decision)
    assert (raw_repo / ".compost" / "classifications.jsonl").exists()


def test_log_decision_appends(raw_repo):
    raw_path = _write_raw(raw_repo, "raw/notes/note.md", "note")
    decision = classify(raw_path, raw_repo)
    rel = Path("raw/notes/note.md")

    log_decision(raw_repo, rel, decision)
    log_decision(raw_repo, rel, decision)

    lines = (raw_repo / ".compost" / "classifications.jsonl").read_text().splitlines()
    assert len(lines) == 2
    entry = json.loads(lines[0])
    assert "ts" in entry
    assert entry["raw_path"] == "raw/notes/note.md"
    assert isinstance(entry["fired"], bool)
    assert isinstance(entry["triggers"], list)
    assert "rationale" in entry


# ── CLI: classify run ────────────────────────────────────────────────────────


def test_classify_run_prints_verdict(raw_repo):
    from click.testing import CliRunner
    from compost.cli import main

    raw_path = _write_raw(raw_repo, "raw/incidents/inc.md", "incident")
    runner = CliRunner()
    result = runner.invoke(main, ["--repo", str(raw_repo), "classify", "run", str(raw_path)])
    assert result.exit_code == 0, result.output
    assert "FIRE" in result.output
    assert "source:incident" in result.output


def test_classify_run_dry_run_does_not_log(raw_repo):
    from click.testing import CliRunner
    from compost.cli import main

    raw_path = _write_raw(raw_repo, "raw/incidents/inc.md", "incident")
    runner = CliRunner()
    runner.invoke(
        main,
        ["--repo", str(raw_repo), "classify", "run", "--dry-run", str(raw_path)],
    )
    assert not (raw_repo / ".compost" / "classifications.jsonl").exists()


def test_classify_run_logs_when_not_dry_run(raw_repo):
    from click.testing import CliRunner
    from compost.cli import main

    raw_path = _write_raw(raw_repo, "raw/incidents/inc.md", "incident")
    runner = CliRunner()
    runner.invoke(main, ["--repo", str(raw_repo), "classify", "run", str(raw_path)])
    assert (raw_repo / ".compost" / "classifications.jsonl").exists()


# ── CLI: classify replay ──────────────────────────────────────────────────────


def test_classify_replay_shows_files_in_window(raw_repo):
    from click.testing import CliRunner
    from compost.cli import main

    _write_raw(raw_repo, "raw/incidents/recent.md", "incident")

    runner = CliRunner()
    result = runner.invoke(
        main, ["--repo", str(raw_repo), "classify", "replay", "--since", "7d"]
    )
    assert result.exit_code == 0, result.output
    assert "raw/incidents/recent.md" in result.output
    assert "FIRE" in result.output


def test_classify_replay_empty_when_no_files(raw_repo):
    from click.testing import CliRunner
    from compost.cli import main

    runner = CliRunner()
    result = runner.invoke(
        main, ["--repo", str(raw_repo), "classify", "replay", "--since", "7d"]
    )
    assert result.exit_code == 0, result.output
    assert "No files found" in result.output
