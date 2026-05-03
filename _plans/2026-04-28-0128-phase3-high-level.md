# Phase 3 High-Level Plan: Tier 1 Rules Classifier + Dry-Run Observability

## Starting Prompt

> @_plans/2026-04-23-2149-laptop-steel-thread-high-level.md lets do phase 3 and make sure to pull anything relevant from @_plans/backlog.md

---

## § Context

Phase 3 adds the Tier 1 classifier described in [team-context-spec.md](team-context-spec.md) §4.1. It is purely rule-based (no LLM): given a newly ingested raw file, decide whether it warrants wiki synthesis. Synthesis itself does not run yet; Phase 3 only produces the fire/no-fire signal and logs it. Phase 4 consumes that signal.

Phase 2 delivered `compost raw add` (captures raw, commits on a branch). Phase 3 hooks into that command to classify after capture and adds standalone classify commands for tuning.

Nothing from the backlog is directly relevant to Phase 3. The "persistent qmd process" and "pipeline observability dashboard" items remain deferred to Phase 8.

---

## § Tier 1 triggers (from spec §4.1)

| Kind | What it checks | Example |
|---|---|---|
| **source** | `source` frontmatter field of the raw file | `incident`, `decision` always fire |
| **semantic** | keywords in raw body + `intent` field | "decision", "deprecate", "breaking" |
| **path** | path of the raw file relative to repo root | `raw/decisions/*`, `raw/incidents/*` |
| **volume** | N+ raw files from the same source type within a rolling window | 5 incidents in 7 days |

Structural triggers (new service dir, new API endpoint) require NLP or cross-referencing wiki entities, which needs synthesis context. Deferred to Phase 4.

---

## § Module layout

```
tools/compost/ingest/
    classifier.py           # new — classification logic
    classifier_rules.yaml   # new — default rules bundled with the package
```

One file for logic, one for the default rules config. No new subpackage.

**Rules override:** `classify()` checks `repo / ".compost" / "classifier_rules.yaml"` first; falls back to the package-bundled default. This lets a wiki repo tune its own rules without touching the source package.

**Classification log:** `repo / ".compost" / "classifications.jsonl"` — one JSONL line per classification event. Written by `log_decision()` in `classifier.py`. Never read by `classify()` itself (except for the volume trigger).

---

## § Data flow

```
compost raw add
    │
    ▼
write_raw() ─────────── RawFile (path + rel_path + source)
    │
    ▼
create_branch_and_commit()
    │
    ▼
classify(raw_file, repo) ─┬─ source rules   ─┐
                           ├─ semantic rules ─┤─► ClassifyDecision(fired, triggers, rationale)
                           ├─ path rules     ─┤
                           └─ volume rules   ─┘
    │
    ├── log_decision(repo, ...) ──► .compost/classifications.jsonl
    ├── print "[green]✓ FIRE[/green]" or "[dim]no-fire[/dim]" to stdout
    │
    ▼
push_branch(origin, raw/...)         ← Gitea integration (already shipped)
    │
    ▼
create_pr(repo, branch, client, decision)  ← decision included in PR body
    │
    └── print "PR #N: http://..."
```

```
compost classify run <raw-path> [--dry-run]
    │
    ├── classify() → ClassifyDecision
    ├── if not --dry-run: log_decision()
    └── print verdict + matched triggers

compost classify replay [--since 7d]
    │
    ├── scan raw/ for files newer than cutoff (by mtime)
    ├── classify() each (dry-run; no logging)
    └── print Rich table: path | fired | triggers
```

---

## § Core domain objects

```python
# tools/compost/ingest/classifier.py

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import yaml

from compost.model.frontmatter import parse_frontmatter


TriggerKind = Literal["source", "semantic", "path", "volume"]


@dataclass(frozen=True)
class Trigger:
    kind: TriggerKind
    pattern: str   # e.g. "incident", "decision", "raw/decisions/*", "5 in 7d"


@dataclass(frozen=True)
class ClassifyDecision:
    fired: bool
    triggers: list[Trigger]   # only populated triggers (matched rules)
    rationale: str            # one human-readable sentence


def classify(
    raw_path: Path,      # absolute path to the raw file
    repo: Path,
    rules: dict | None = None,   # pre-loaded rules; loads from disk if None
) -> ClassifyDecision:
    """Classify a raw file against the rules. Pure: does not write to disk."""
    ...


def log_decision(repo: Path, raw_rel_path: Path, decision: ClassifyDecision) -> None:
    """Append a classification event to .compost/classifications.jsonl."""
    ...


def load_rules(repo: Path) -> dict:
    """Load classifier rules: repo override first, then package default."""
    ...
```

**ClassifyDecision.rationale** is a short human-readable sentence like `"source 'incident' always fires"` or `"3 matched triggers: source, semantic:decision, path:raw/decisions/*"`. Single source of truth for the human-facing explanation.

### `ingest/pr.py` change

`create_pr` gains one optional parameter:

```python
def create_pr(
    repo: Path,
    branch: str,
    client: GiteaClient,
    decision: ClassifyDecision | None = None,
) -> GiteaPR:
```

When `decision` is supplied and `decision.fired` is True, the PR body prepends a `**Tier 1:** FIRE — {triggers}` line. When `decision` is `None` or `fired` is False, the body is unchanged from Phase 2. The caller (`raw_add`) passes the decision; tests inject it directly.

---

## § Rules YAML format

```yaml
# .compost/classifier_rules.yaml (default, bundled in package)

# Source types that always fire Tier 1
source_triggers:
  - incident
  - decision

# Keywords matched against raw body + intent frontmatter field (case-insensitive)
semantic_keywords:
  - "decision"
  - "deprecate"
  - "breaking"
  - "incident"
  - "migration"
  - "postmortem"
  - "ownership"
  - "architecture"

# Path prefix patterns (fnmatch-style, relative to repo root)
path_patterns:
  - "raw/incidents/*"
  - "raw/decisions/*"

# Volume trigger: fire if N+ raw files from the same source type exist
# within a rolling window, by mtime of the file on disk
volume:
  threshold: 5
  window_days: 7
```

Rules are intentionally flat. No nested conditions, no boolean combinators. If a rule fires, the whole decision is `fired=True`. Individual trigger matches are reported so the developer can see exactly which rules hit.

---

## § Classification log format

Each line in `.compost/classifications.jsonl`:

```json
{
  "ts": "2026-04-27T10:00:00Z",
  "raw_path": "raw/decisions/2026-04-27-use-stripe.md",
  "fired": true,
  "triggers": [
    {"kind": "source", "pattern": "decision"},
    {"kind": "semantic", "pattern": "decision"}
  ],
  "rationale": "2 matched triggers: source:decision, semantic:decision"
}
```

The log is append-only. The volume trigger reads it to count recent classifications for the same source type.

---

## § CLI design

```
compost classify run <RAW-PATH> [--dry-run]
```

- `<RAW-PATH>` is the path to the raw file (relative to cwd or absolute).
- Without `--dry-run`: classify, log the result, print verdict.
- With `--dry-run`: classify, print verdict and matched triggers, do not log.
- Output format:

```
✓ FIRE   raw/decisions/2026-04-27-use-stripe.md
  source:decision · semantic:decision
  "2 matched triggers: source:decision, semantic:decision"

or:

  no-fire   raw/notes/2026-04-27-standup.md
  (no triggers matched)
```

```
compost classify replay [--since TEXT]
```

- `--since` accepts `Nd` (e.g. `7d`, `30d`). Default: `7d`.
- Scans `raw/` for `.md` files with mtime newer than the cutoff.
- Runs classify (dry-run; no logging) over each.
- Prints a Rich table:

```
path                                        fired   triggers
raw/decisions/2026-04-27-use-stripe.md      FIRE    source:decision, semantic:decision
raw/notes/2026-04-27-standup.md             -       (none)
raw/incidents/2026/INC-2026-04-26-db.md     FIRE    source:incident, path:raw/incidents/*
```

---

## § Integration: `compost raw add`

After `create_branch_and_commit`, `raw_add` calls classify and prints the result. Then it pushes and opens the Gitea PR as before (Gitea integration already shipped). No changes to the return value or exit code — classify failure never blocks ingestion:

```
✓ raw/decisions/2026-04-27-use-stripe.md
branch: raw/2026-04-27-use-stripe
[Tier 1] FIRE — source:decision, semantic:decision
PR #3: http://localhost:3000/myteam/my-team-wiki/pulls/3
```

If classify raises unexpectedly, `raw_add` prints a dim warning and continues. Classification is never a hard gate at Phase 3.

### Classify result in PR body

`create_pr` gains an optional `decision: ClassifyDecision | None = None` parameter. When provided, the PR body includes a Tier 1 section:

```
**Tier 1:** FIRE — source:decision, semantic:decision

**Files changed:**
- raw/decisions/2026-04-27-use-stripe.md

---
*Review and merge with `compost pr merge`.*
```

When `decision` is `None` or classify failed silently, the PR body omits the Tier 1 section (backward-compatible).

---

## § Files to create / modify

| File | Action |
|---|---|
| `tools/compost/ingest/classifier.py` | New |
| `tools/compost/ingest/classifier_rules.yaml` | New (default rules, bundled) |
| `tools/compost/ingest/pr.py` | Modified — `create_pr` gains optional `decision` param; includes Tier 1 in PR body |
| `tools/compost/cli.py` | Modified — add `classify` group + hook in `raw_add`; pass `decision` to `create_pr` |
| `tools/compost/pyproject.toml` | Modified — add `package_data` for the YAML |
| `tools/compost/tests/test_classifier.py` | New |
| `tools/compost/tests/test_pr.py` | Modified — add test that classify result appears in PR body |
| `README.md` | Modified — add `compost classify` section |

---

## § pyproject.toml change

Add `package_data` so the bundled YAML ships with the package:

```toml
[tool.setuptools.package-data]
"compost.ingest" = ["classifier_rules.yaml"]
```

---

## § Test surface

- `test_classify_source_trigger`: `source=incident` fires
- `test_classify_semantic_trigger`: body containing "postmortem" fires
- `test_classify_path_trigger`: file under `raw/decisions/` fires from path rule
- `test_classify_volume_trigger`: 5+ files from same source type in window fires
- `test_classify_no_fire`: a boring `source=note` with no keywords does not fire
- `test_classify_multi_trigger`: multiple rules match, all reported in triggers list
- `test_classify_custom_rules`: passing a custom rules dict overrides defaults
- `test_log_decision_appends`: calling `log_decision` twice produces 2 JSONL lines
- `test_log_decision_creates_dir`: `.compost/` dir created if missing
- `test_load_rules_falls_back_to_default`: no repo override → loads package default
- `test_load_rules_repo_override`: repo-level YAML overrides package default
- `test_replay_scan`: `replay` finds files by mtime within window
- `test_create_pr_body_includes_tier1_on_fire`: `create_pr` with a FIRE decision includes Tier 1 section in PR body
- `test_create_pr_body_omits_tier1_when_no_decision`: `create_pr` with `decision=None` omits the Tier 1 section (backward-compatible)

---

## § Red flags anticipated

| Risk | Mitigation |
|---|---|
| Volume trigger using mtime is inaccurate (file may have been touched by editor, git checkout, etc.) | Document clearly in `classifier_rules.yaml` comments. Phase 8 can switch to git-log-based dating when the daemon runs. |
| Rules YAML path leaks into CLI, classifier, and test code | A single `load_rules(repo)` function owns the lookup; everything else calls that. |
| `classify()` doing too much (reading files, scanning dirs, reading log) | Keep each trigger kind in its own private `_check_{kind}` function; `classify()` fans out and collects. This keeps complexity local. |

---

## § Documentation updates

- **README.md**: add `compost classify` section after `compost pr merge`, with the `--dry-run` and `--replay` examples and a note about the rules override.
- **No changes to AGENTS.md or spec files** at this phase.
