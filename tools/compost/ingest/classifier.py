from __future__ import annotations

import fnmatch
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import yaml

from compost.model.frontmatter import parse_frontmatter


TriggerKind = Literal["source", "semantic", "path", "volume"]


@dataclass(frozen=True)
class Trigger:
    kind: TriggerKind
    pattern: str   # e.g. "incident", "postmortem", "raw/decisions/*", "5 in 7d"


@dataclass(frozen=True)
class ClassifyDecision:
    fired: bool
    triggers: list[Trigger]   # only matched triggers
    rationale: str            # one human-readable sentence


def load_rules(repo: Path) -> dict:
    """Load classifier rules: repo override (.compost/classifier_rules.yaml) first,
    then package-bundled default."""
    repo_override = repo / ".compost" / "classifier_rules.yaml"
    if repo_override.exists():
        with repo_override.open() as f:
            return yaml.safe_load(f)
    default_path = Path(__file__).parent / "classifier_rules.yaml"
    with default_path.open() as f:
        return yaml.safe_load(f)


def classify(
    raw_path: Path,
    repo: Path,
    rules: dict | None = None,
) -> ClassifyDecision:
    """Classify a raw file against the rules. Pure: does not write to disk."""
    if rules is None:
        rules = load_rules(repo)

    fm, body = parse_frontmatter(raw_path)
    source = fm.get("source", "")
    raw_rel_path = raw_path.relative_to(repo)

    triggers: list[Trigger] = []

    t = _check_source(source, rules)
    if t:
        triggers.append(t)

    triggers.extend(_check_semantic(fm, body, rules))

    t = _check_path(raw_rel_path, rules)
    if t:
        triggers.append(t)

    t = _check_volume(repo, source, rules)
    if t:
        triggers.append(t)

    fired = len(triggers) > 0

    if fired:
        parts = [f"{t.kind}:{t.pattern}" for t in triggers]
        n = len(triggers)
        rationale = (
            f"1 matched trigger: {parts[0]}"
            if n == 1
            else f"{n} matched triggers: {', '.join(parts)}"
        )
    else:
        rationale = "no triggers matched"

    return ClassifyDecision(fired=fired, triggers=triggers, rationale=rationale)


def log_decision(repo: Path, raw_rel_path: Path, decision: ClassifyDecision) -> None:
    """Append a classification event to .compost/classifications.jsonl."""
    log_dir = repo / ".compost"
    log_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "raw_path": str(raw_rel_path).replace("\\", "/"),
        "fired": decision.fired,
        "triggers": [{"kind": t.kind, "pattern": t.pattern} for t in decision.triggers],
        "rationale": decision.rationale,
    }
    with (log_dir / "classifications.jsonl").open("a") as f:
        f.write(json.dumps(entry) + "\n")


# ── private helpers ───────────────────────────────────────────────────────────


def _check_source(source: str, rules: dict) -> Trigger | None:
    if source in rules.get("source_triggers", []):
        return Trigger(kind="source", pattern=source)
    return None


def _check_semantic(fm: dict, body: str, rules: dict) -> list[Trigger]:
    intent = str(fm.get("intent", ""))
    text = (body + " " + intent).lower()
    triggers = []
    for kw in rules.get("semantic_keywords", []):
        if kw.lower() in text:
            triggers.append(Trigger(kind="semantic", pattern=kw))
    return triggers


def _check_path(raw_rel_path: Path, rules: dict) -> Trigger | None:
    rel_str = str(raw_rel_path).replace("\\", "/")
    for pattern in rules.get("path_patterns", []):
        if fnmatch.fnmatch(rel_str, pattern):
            return Trigger(kind="path", pattern=pattern)
    return None


def _check_volume(
    repo: Path, source: str, rules: dict
) -> Trigger | None:
    threshold = rules.get("volume", {}).get("threshold", 5)
    window_days = rules.get("volume", {}).get("window_days", 7)

    candidates = _volume_candidates(repo, window_days)
    if candidates is None:
        return None

    count = sum(1 for f in candidates if _source_of(f) == source)

    if count >= threshold:
        return Trigger(kind="volume", pattern=f"{threshold} in {window_days}d")
    return None


def _volume_candidates(repo: Path, window_days: int) -> list[Path] | None:
    """Return raw/*.md files added within window_days. Uses git log if available, else mtime."""
    raw_dir = repo / "raw"
    if not raw_dir.exists():
        return None

    result = subprocess.run(
        [
            "git", "log",
            f"--since={window_days} days ago",
            "--diff-filter=A",
            "--name-only",
            "--format=",
            "--", "raw/",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        paths = [
            repo / p.strip()
            for p in result.stdout.splitlines()
            if p.strip().endswith(".md")
        ]
        return [p for p in paths if p.exists()]

    # Fallback: mtime (non-git repo or no commits yet)
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=window_days)).timestamp()
    return [f for f in raw_dir.rglob("*.md") if f.stat().st_mtime >= cutoff_ts]


def _source_of(f: Path) -> str:
    try:
        fm, _ = parse_frontmatter(f)
        return fm.get("source", "")
    except Exception:
        return ""


def replay_classify(
    repo: Path,
    since_days: int,
) -> list[tuple[Path, ClassifyDecision]]:
    """Classify all raw/*.md files modified within since_days. Pure; does not log.

    Returns list of (absolute_path, ClassifyDecision) sorted newest-first by mtime.
    """
    from datetime import datetime, timedelta, timezone

    raw_dir = repo / "raw"
    if not raw_dir.exists():
        return []

    cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=since_days)).timestamp()
    files = sorted(
        (f for f in raw_dir.rglob("*.md") if f.stat().st_mtime >= cutoff_ts),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    rules = load_rules(repo)
    results: list[tuple[Path, ClassifyDecision]] = []
    for f in files:
        try:
            decision = classify(f, repo, rules=rules)
            results.append((f, decision))
        except Exception:
            continue
    return results
