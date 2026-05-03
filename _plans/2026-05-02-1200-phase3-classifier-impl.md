# § Implementation Detail: Phase 3 — Tier 1 Rules Classifier

Derived from `_plans/2026-04-28-0128-phase3-high-level.md`.

## § Deferred from high-level plan

Nothing is deferred. All items in the high-level plan are implemented here.

---

## § Files to create / modify

| File | Action |
|---|---|
| `tools/compost/ingest/classifier_rules.yaml` | New — bundled default rules |
| `tools/compost/ingest/classifier.py` | New — classify, log_decision, load_rules |
| `tools/compost/ingest/pr.py` | Modified — `create_pr` gains optional `decision` param |
| `tools/compost/cli.py` | Modified — add `classify` group; hook classify into `raw_add` |
| `tools/compost/pyproject.toml` | Modified — add `package_data` for bundled YAML |
| `tools/compost/tests/test_classifier.py` | New |
| `tools/compost/tests/test_pr.py` | Modified — 2 new tests for Tier 1 in PR body |
| `README.md` | Modified — add `compost classify` section |

---

## § Step-by-step execution (TDD)

All test runs from `tools/compost/`:
```bash
python -m pytest tests/ -x -q
```

---

### Step 1 — `classifier_rules.yaml`

Create `tools/compost/ingest/classifier_rules.yaml`:

```yaml
# Source types that always fire Tier 1.
source_triggers:
  - incident
  - decision

# Keywords matched case-insensitively against raw body and the optional
# `intent` frontmatter field.
semantic_keywords:
  - "decision"
  - "deprecate"
  - "breaking"
  - "incident"
  - "migration"
  - "postmortem"
  - "ownership"
  - "architecture"

# Path prefix patterns (fnmatch-style, relative to repo root).
path_patterns:
  - "raw/incidents/*"
  - "raw/decisions/*"

# Volume trigger: fire if N+ raw files from the same source type were
# added within the rolling window. Uses git log (--diff-filter=A) when
# the repo has git; falls back to mtime when git is unavailable.
volume:
  threshold: 5
  window_days: 7
```

---

### Step 2 — `tests/test_classifier.py` (red) then `classifier.py` (green)

**Write tests first** in `tools/compost/tests/test_classifier.py`:

```python
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
    rules = {
        "source_triggers": [],
        "semantic_keywords": [],
        "path_patterns": [],
        "volume": {"threshold": 5, "window_days": 7},
    }
    for i in range(5):
        _write_raw(raw_repo, f"raw/notes/note-{i}.md", "note")

    # classify the last one: 5 note files now exist with recent mtime
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
    # rationale lists trigger descriptions
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
```

**Run tests:** all fail (module not found). Now implement `tools/compost/ingest/classifier.py`:

```python
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

    t = _check_volume(raw_path, repo, source, rules)
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
    raw_path: Path, repo: Path, source: str, rules: dict
) -> Trigger | None:
    threshold = rules.get("volume", {}).get("threshold", 5)
    window_days = rules.get("volume", {}).get("window_days", 7)

    candidates = _volume_candidates(repo, window_days)
    if candidates is None:
        return None

    count = sum(
        1 for f in candidates
        if _source_of(f) == source
    )

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
        paths = [repo / p.strip() for p in result.stdout.splitlines() if p.strip().endswith(".md")]
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
```

**Run tests:** all pass.

---

### Step 3 — `test_pr.py` additions (red) then `pr.py` change (green)

**Append to `tools/compost/tests/test_pr.py`** (add after the existing `merge_pr` tests):

```python
# ── create_pr: Tier 1 integration ────────────────────────────────────────────


def test_create_pr_body_includes_tier1_on_fire(compost_git_repo):
    from compost.ingest.classifier import ClassifyDecision, Trigger

    repo = compost_git_repo
    branch = "raw/2026-04-28-tier1-fire"
    _add_file_on_branch(repo, "raw/decisions/2026-04-28-tier1-fire.md", branch)

    decision = ClassifyDecision(
        fired=True,
        triggers=[
            Trigger(kind="source", pattern="decision"),
            Trigger(kind="semantic", pattern="decision"),
        ],
        rationale="2 matched triggers: source:decision, semantic:decision",
    )

    received_bodies = []

    @dataclass
    class CapturingClient:
        pr: GiteaPR = GiteaPR(1, "http://gitea/pulls/1", "open")
        def open_pr(self, branch, base, title, body):
            received_bodies.append(body)
            return self.pr
        def find_pr(self, branch): return self.pr
        def merge_pr(self, n): pass

    create_pr(repo, branch, CapturingClient(), decision=decision)
    assert len(received_bodies) == 1
    body = received_bodies[0]
    assert "**Tier 1:** FIRE" in body
    assert "source:decision" in body
    assert "semantic:decision" in body

    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_create_pr_body_omits_tier1_when_no_decision(compost_git_repo):
    repo = compost_git_repo
    branch = "raw/2026-04-28-no-decision"
    _add_file_on_branch(repo, "raw/notes/2026-04-28-no-decision.md", branch)

    received_bodies = []

    @dataclass
    class CapturingClient:
        pr: GiteaPR = GiteaPR(1, "http://gitea/pulls/1", "open")
        def open_pr(self, branch, base, title, body):
            received_bodies.append(body)
            return self.pr
        def find_pr(self, branch): return self.pr
        def merge_pr(self, n): pass

    create_pr(repo, branch, CapturingClient(), decision=None)
    body = received_bodies[0]
    assert "Tier 1" not in body
    assert "**Files changed:**" in body

    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)
```

Also add to the imports at the top of `test_pr.py`:
```python
from compost.ingest.pr import create_pr, merge_pr
```
(already present — no change needed)

**Run tests:** 2 new tests fail. Now update `tools/compost/ingest/pr.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click

from compost.gitea.client import GiteaClient, GiteaPR
from compost.ingest.git import (
    changed_files,
    current_branch,
    default_branch,
    fetch_and_ff,
)

if TYPE_CHECKING:
    from compost.ingest.classifier import ClassifyDecision


def create_pr(
    repo: Path,
    branch: str,
    client: GiteaClient,
    decision: "ClassifyDecision | None" = None,
) -> GiteaPR:
    """Build PR body from changed files (and optional Tier 1 result) and open a Gitea PR."""
    base = default_branch(repo)
    files = changed_files(repo, branch, base)
    files_list = "\n".join(f"- {f}" for f in files) if files else "_(none)_"

    tier1 = ""
    if decision is not None and decision.fired:
        trigger_str = ", ".join(f"{t.kind}:{t.pattern}" for t in decision.triggers)
        tier1 = f"**Tier 1:** FIRE — {trigger_str}\n\n"

    body = (
        f"{tier1}"
        f"**Files changed:**\n{files_list}\n\n"
        f"---\n"
        f"*Review and merge with `compost pr merge`.*"
    )
    return client.open_pr(branch, base, f"raw: {branch}", body)


def merge_pr(repo: Path, client: GiteaClient) -> GiteaPR:
    """Validate guards, merge via Gitea API, sync local repo. Returns merged PR."""
    branch = current_branch(repo)
    if not branch.startswith("raw/"):
        raise click.UsageError(
            f"Not on a raw/* branch (current: '{branch}'). "
            "Checkout a raw/* branch before merging."
        )

    base = default_branch(repo)
    files = changed_files(repo, branch, base)
    wiki_edits = [f for f in files if f.startswith("wiki/")]
    if wiki_edits:
        raise click.UsageError(
            "Branch contains wiki/ edits which must not be auto-merged:\n"
            + "\n".join(f"  {f}" for f in wiki_edits)
        )

    pr = client.find_pr(branch)
    if pr is None:
        raise click.UsageError(
            f"No open PR found for '{branch}'. "
            "Was it already merged or not yet pushed?"
        )

    client.merge_pr(pr.number)
    fetch_and_ff(repo, "origin", base)

    return GiteaPR(number=pr.number, url=pr.url, state="merged")
```

**Run tests:** all pass.

---

### Step 4 — `cli.py` additions

Two changes: (a) hook classify into `raw_add`, (b) add `classify` group.

#### 4a — Update `raw_add` in `cli.py`

Replace the existing push/PR block at the end of `raw_add` with this (classify runs before push, decision is passed to `create_pr`):

**Old block (starting at `console.print(f"branch: {branch}"`):**

```python
    console.print(f"[green]✓[/green] {raw_file.rel_path}")
    console.print(f"branch: {branch}")

    try:
        push_branch(repo, "origin", branch)
        client = _load_gitea_client(repo)
        pr = create_pr(repo, branch, client)
        console.print(f"PR #{pr.number}: {pr.url}")
    except (click.UsageError, GiteaError) as e:
        console.print(f"[yellow]⚠ push/PR failed: {e}[/yellow]")
        console.print("Push manually: [bold]git push origin " + branch + "[/bold]")
```

**New block:**

```python
    console.print(f"[green]✓[/green] {raw_file.rel_path}")
    console.print(f"branch: {branch}")

    decision = None
    try:
        from compost.ingest.classifier import classify, log_decision
        decision = classify(raw_file.path, repo)
        log_decision(repo, raw_file.rel_path, decision)
        if decision.fired:
            trigger_str = ", ".join(f"{t.kind}:{t.pattern}" for t in decision.triggers)
            console.print(f"[green][Tier 1] FIRE[/green] — {trigger_str}")
        else:
            console.print("[dim][Tier 1] no-fire[/dim]")
    except Exception as exc:
        console.print(f"[dim]⚠ classify failed: {exc}[/dim]")

    try:
        push_branch(repo, "origin", branch)
        client = _load_gitea_client(repo)
        pr = create_pr(repo, branch, client, decision)
        console.print(f"PR #{pr.number}: {pr.url}")
    except (click.UsageError, GiteaError) as e:
        console.print(f"[yellow]⚠ push/PR failed: {e}[/yellow]")
        console.print("Push manually: [bold]git push origin " + branch + "[/bold]")
```

#### 4b — Add `classify` group to `cli.py`

Add after the `pr_group` section and before the `gitea_group` section:

```python
# ── classify commands ─────────────────────────────────────────────────────────


@main.group("classify")
def classify_group() -> None:
    """Run Tier 1 classifier on raw files."""


@classify_group.command("run")
@click.argument("raw_path", type=click.Path(exists=True, resolve_path=True, path_type=Path))
@click.option("--dry-run", is_flag=True, help="Classify without logging the result.")
@click.pass_context
def classify_run(ctx: click.Context, raw_path: Path, dry_run: bool) -> None:
    """Classify a raw file and print the Tier 1 verdict."""
    from compost.ingest.classifier import classify, log_decision

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    try:
        rel = raw_path.relative_to(repo)
    except ValueError:
        console.print(f"[red]{raw_path} is not under the repo root {repo}[/red]")
        sys.exit(1)

    decision = classify(raw_path, repo)

    if not dry_run:
        log_decision(repo, rel, decision)

    _print_classify_verdict(rel, decision)


@classify_group.command("replay")
@click.option("--since", default="7d", show_default=True,
              help="How far back to scan (e.g. 7d, 30d).")
@click.pass_context
def classify_replay(ctx: click.Context, since: str) -> None:
    """Classify all raw/*.md files modified within a rolling window (dry-run; no logging)."""
    import re
    from datetime import datetime, timedelta, timezone as tz
    from compost.ingest.classifier import classify

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    m = re.fullmatch(r"(\d+)d", since.strip())
    if not m:
        console.print("[red]--since must be in Nd format (e.g. 7d, 30d)[/red]")
        sys.exit(1)

    days = int(m.group(1))
    cutoff_ts = (datetime.now(tz.utc) - timedelta(days=days)).timestamp()

    raw_dir = repo / "raw"
    if not raw_dir.exists():
        console.print("[dim]No raw/ directory found.[/dim]")
        return

    files = sorted(
        (f for f in raw_dir.rglob("*.md") if f.stat().st_mtime >= cutoff_ts),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    if not files:
        console.print("[dim]No files found within window.[/dim]")
        return

    table = Table(show_header=True, box=None, padding=(0, 1))
    table.add_column("path")
    table.add_column("fired")
    table.add_column("triggers")

    for f in files:
        try:
            decision = classify(f, repo)
        except Exception:
            continue
        rel = f.relative_to(repo)
        fired_str = "[green]FIRE[/green]" if decision.fired else "[dim]-[/dim]"
        triggers_str = (
            ", ".join(f"{t.kind}:{t.pattern}" for t in decision.triggers)
            or "(none)"
        )
        table.add_row(str(rel), fired_str, triggers_str)

    console.print(table)
```

Also add the `_print_classify_verdict` helper at the end of `cli.py` (before or after `_render_checks`):

```python
def _print_classify_verdict(rel: Path, decision: "ClassifyDecision") -> None:
    if decision.fired:
        trigger_str = " · ".join(f"{t.kind}:{t.pattern}" for t in decision.triggers)
        console.print(f"[green]✓ FIRE[/green]   {rel}")
        console.print(f"  {trigger_str}")
        console.print(f'  "{decision.rationale}"')
    else:
        console.print(f"[dim]  no-fire[/dim]   {rel}")
        console.print("  (no triggers matched)")
```

Add the `TYPE_CHECKING` import for the type hint on `_print_classify_verdict`. Since this is a private function with a string annotation used only at runtime, no import is needed — just keep the annotation as a string: `decision: "ClassifyDecision"`.

**Run tests:** all pass (classify commands don't have tests yet — see step 5).

---

### Step 5 — `tests/test_classifier.py` additions: replay and classify run CLI tests

**Append to `test_classifier.py`** (after the existing log tests):

```python
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
```

**Run tests:** all pass.

---

### Step 6 — `pyproject.toml` update

Add `package_data` so the bundled YAML ships with the installed package.

Append to `tools/compost/pyproject.toml`:

```toml
[tool.setuptools.package-data]
"compost.ingest" = ["classifier_rules.yaml"]
```

---

### Step 7 — `README.md` update

**Replace** the existing `### Steel thread` section closing with a new `compost classify` section inserted after `### compost pr merge` and before `### Steel thread`.

Specifically, after this paragraph:

```markdown
### `compost pr merge`

Merge the current `raw/*` branch via Gitea PR, then sync the local repo. Blocked if any `wiki/` files were modified on the branch.

```bash
COMPOST_REPO=. compost pr merge
# merged (PR #3)
```
```

Insert:

```markdown
## Tier 1 Classifier

`compost raw add` automatically classifies new raw files using rule-based triggers. FIRE means the file is likely worth synthesizing into the wiki.

```
✓ raw/decisions/2026-04-27-use-stripe.md
branch: raw/2026-04-27-use-stripe
[Tier 1] FIRE — source:decision, semantic:decision
PR #3: http://localhost:3000/myteam/my-team-wiki/pulls/3
```

The classification result is also included in the Gitea PR description.

### `compost classify run`

Classify a single file. Use `--dry-run` to skip logging.

```bash
COMPOST_REPO=. compost classify run raw/decisions/2026-04-27-use-stripe.md
# ✓ FIRE   raw/decisions/2026-04-27-use-stripe.md
#   source:decision · semantic:decision
#   "2 matched triggers: source:decision, semantic:decision"

COMPOST_REPO=. compost classify run --dry-run raw/notes/standup.md
#   no-fire   raw/notes/standup.md
#   (no triggers matched)
```

### `compost classify replay`

Re-classify all raw files modified within a rolling window (dry-run; no logging).

```bash
COMPOST_REPO=. compost classify replay --since 7d
```

### Rules override

Create `.compost/classifier_rules.yaml` in your wiki repo to override the defaults:

```yaml
source_triggers:
  - incident
  - decision
  - support      # add support tickets

semantic_keywords:
  - "decision"
  - "breaking"
  # ... any additional keywords
```
```

Also update the steel thread to include the Tier 1 output line:

```markdown
### Steel thread

```bash
# One-time: start Gitea, create a token, then:
export GITEA_TOKEN=your-token
cd ~/local-test/my-wiki
compost gitea setup --owner myuser

# Ingest a raw file:
echo "Discussed retry ownership. Alice owns it." | \
  COMPOST_REPO=. compost raw add --source note --title "retry ownership discussion"
# ✓ raw/notes/2026-04-28-retry-ownership-discussion.md
# branch: raw/2026-04-28-retry-ownership-discussion
# [Tier 1] no-fire
# PR opened at http://localhost:3000/myuser/my-wiki/pulls/1

# Review the PR in browser, then merge:
COMPOST_REPO=. compost pr merge
# → merged (PR #1)
```
```

---

### Step 8 — Final test run

```bash
cd tools/compost && python -m pytest tests/ -q
```

All tests must pass.

---

## § Design notes

**Volume trigger uses `git log --diff-filter=A` when available, mtime otherwise.** The volume check finds files *added* (not just touched) within the window by querying git history, which is immune to editor saves and checkouts. If the working directory is not a git repo (or has no commits yet, as in most unit tests), the fallback to mtime keeps tests fast and self-contained without a mock. The `raw_repo` test fixture is a plain `tmp_path` (no git), so the volume tests exercise the mtime fallback path; the git path is covered implicitly once the repo has commits.

**`classify()` is pure.** It never writes to disk. `log_decision()` is the only function that writes, and it is always called by the caller (`raw_add`, `classify run`). This makes testing straightforward.

**`create_pr` backward compatibility.** `decision=None` produces the same PR body as before Phase 3. Existing tests pass unchanged.

**`_print_classify_verdict` vs inline code.** Extracted to a helper to avoid duplicating the FIRE/no-fire formatting between `classify_run` and `raw_add`. The output format is slightly different between the two callers: `classify_run` uses the multi-line block format; `raw_add` uses the inline `[Tier 1] FIRE — …` format inline with other output lines.

**TYPE_CHECKING guard on `ClassifyDecision` import in `pr.py`.** The import is under `TYPE_CHECKING` to avoid a runtime circular dependency if `classifier.py` ever imports from `pr.py` in the future. At runtime the annotation is a string; only type checkers resolve it.

---

### Feedback Log

> **Context:** Step 1, volume trigger comment in `classifier_rules.yaml`
>
> ```
> # Volume trigger: fire if N+ raw files from the same source type have an
> # mtime within the rolling window. Note: mtime can be affected by editor
> # saves and git checkouts; a git-log-based approach is deferred to Phase 8.
> ^^ why wait till phase 8? we have git and gitea now right? ^^
> ```
>
> **Resolution:** Implemented `_check_volume` using `git log --diff-filter=A` now, with mtime as fallback for non-git repos. Removed the Phase 8 deferral.
