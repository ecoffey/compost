from __future__ import annotations

import datetime
import json
from pathlib import Path

from compost.lint import LintFinding, LintResult


def run(repo: Path, since_days: int = 90) -> LintResult:
    """Find raw files that would fire Tier 1 but were never synthesized.

    Cross-references replay_classify() (current rules) against the synth-log
    to identify missed synthesis opportunities.
    """
    from compost.ingest.classifier import replay_classify

    classified = replay_classify(repo, since_days)
    if not classified:
        return LintResult(linter="false_negative_sampler", status="pass")

    synthesized = _synthesized_raws(repo, since_days)

    findings: list[LintFinding] = []
    for raw_path, decision in classified:
        if not decision.fired:
            continue
        rel = str(raw_path.relative_to(repo)).replace("\\", "/")
        if rel not in synthesized:
            trigger_str = ", ".join(f"{t.kind}:{t.pattern}" for t in decision.triggers)
            findings.append(LintFinding(
                linter="false_negative_sampler",
                severity="warn",
                message=f"would fire ({trigger_str}) but no synthesis run found",
                path=rel,
            ))

    status = "warn" if findings else "pass"
    return LintResult(linter="false_negative_sampler", status=status, findings=findings)


def _synthesized_raws(repo: Path, since_days: int) -> set[str]:
    """Return repo-relative raw paths that appear in synth run_complete events within since_days."""
    log_dir = repo / ".compost" / "synth-log"
    if not log_dir.exists():
        return set()

    cutoff = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(days=since_days)
    ).timestamp()

    synthesized: set[str] = set()
    for f in log_dir.glob("*.jsonl"):
        if f.stat().st_mtime < cutoff:
            continue
        for line in f.read_text().splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "run_complete" and "raw" in event:
                synthesized.add(event["raw"])
    return synthesized
