# Backlog Catchup — High-Level Plan

## Starting Prompt

> Triage and address overdue backlog items from _plans/backlog.md. These items were flagged for past phases but never pulled in: (1) single persistent qmd process [flagged Phase 8], (2) hybrid skill install [flagged "install now"], (3) winze/metabolism phases + compost claims suggest [flagged Phase 9]. Also triage: (4) structured TheoryOf claims, (5) initialize from existing docs, (6) feedback on PRs — decide pull in or formally defer with reasoning. Read _plans/backlog.md in full before planning. Do NOT include Phase 11 derived artifacts.

---

## § Triage decisions

### ✅ Pull in — hybrid skill install

The hybrid repo exists at `~/workspace/hybrid/`. The backlog explicitly says "install now" and the skill is a symlink. One command. No reason to defer further.

**Action:** `ln -s ~/workspace/hybrid/skills/hybrid-loops ~/.claude/skills/hybrid-loops`

### ✅ Pull in — `compost claims suggest`

Phase 5 adversarial checks already produce structured `Finding` objects with `conflicting_page`, `claim_text`, and `conflicting_claim_text`. The missing piece is persisting those findings in a queryable form and a CLI command to convert contradiction findings into draft `@Contested TheoryOf` Kotlin stubs for `wiki/claims.kt`.

This is concrete, well-scoped, and the substrate (Finding types, codify schema with TheoryOf/Disputes) is already in place.

### ❌ Defer — single persistent qmd process

The `_warmup()` call in `run_server` already resolves the latency symptom. The architectural change (pointing at `qmd mcp` as a persistent process) would be significant and the benefit on a laptop with 2-3 repos is marginal. The right moment is after Phase 11 when the full multi-repo query pattern is understood.

**Backlog update:** change "When to revisit: Phase 8" to "When to revisit: Phase 12+ — warmup mitigation is sufficient for laptop; revisit when serving multiple concurrent sessions."

### ❌ Defer — winze metabolism phases (dream / bias-audit / calibrate)

These require designing background worker phases and a formal calibration tracking schema. `compost lint` (Phase 9) already covers the structural health portion. The metabolism phases need their own design phase once `claims.kt` entries accumulate and the patterns become clear.

**Backlog update:** note that `compost claims suggest` (this plan) is the prerequisite; metabolism scheduling is Phase 12+.

### ❌ Defer — structured TheoryOf claims (typed predicate slots)

Explicitly blocked on having real `claims.kt` entries to see which predicates recur. `compost claims suggest` (this plan) creates those entries. Revisit after several weeks of real data.

**Backlog update:** note dependency on this plan.

### ❌ Defer — initialize from existing docs

Too large for a backlog catchup plan. Needs its own design phase covering batch ingest, timestamp preservation, async processing, and progress tracking.

**Backlog update:** add "When to revisit: Phase 12+ — needs dedicated design phase."

### ❌ Defer — feedback on PRs

No design exists. Needs its own phase.

**Backlog update:** add "When to revisit: Phase 12+ — needs dedicated design phase."

---

## § High-level plan

### Work item 1: Install hybrid skill

One symlink. Creates `~/.claude/skills/hybrid-loops` pointing at `~/workspace/hybrid/skills/hybrid-loops`.

No code changes. No tests needed.

### Work item 2: `compost claims suggest`

#### The problem

Contradiction findings from `compost checks run` exist only in the markdown check report (`.compost/checks/{slug}.md`). There is no structured, queryable record of them. `compost claims suggest` needs to read contradiction findings and emit `@Contested TheoryOf` Kotlin stubs.

#### Design

**2a. Persist findings as JSONL alongside the markdown report.**

`write_check_report` in `tools/compost/checks/runner.py` currently writes only a markdown file. Extend it to also write a JSONL sidecar at `.compost/checks/{slug}.jsonl` — one JSON object per `Finding`.

```python
# .compost/checks/{slug}.jsonl  — one line per Finding
{"check": "contradiction_scan", "severity": "fail", "message": "...",
 "page": "wiki/services/payments.md", "conflicting_page": "wiki/decisions/0012.md",
 "claim_text": "uses JWT for auth", "conflicting_claim_text": "uses session cookies"}
```

This is small: `Finding` is already a frozen dataclass with scalar fields; `dataclasses.asdict` does the work.

**2b. New module: `tools/compost/cli/claims.py`**

```python
@click.group("claims")
def claims_group() -> None:
    """Work with contested claims extracted from check findings."""

@claims_group.command("suggest")
@click.option("--branch", default=None,
              help="Branch slug to read findings from. Defaults to current branch.")
@click.option("--dry-run", is_flag=True,
              help="Print stubs to stdout instead of writing wiki/claims.kt.")
@click.pass_context
def claims_suggest(ctx, branch, dry_run): ...
```

**Logic:**

1. Resolve branch (from `--branch` or `git rev-parse --abbrev-ref HEAD`).
2. Find `.compost/checks/{slug}.jsonl` (slug = branch name with `/` → `-`).
3. Filter findings where `check == "contradiction_scan"` and both `claim_text` and `conflicting_page` are set.
4. For each, render a Kotlin stub:

```kotlin
// Suggested by compost claims suggest — review before committing
@Contested
val theory_<slug>: TheoryOf<WikiPage> = TheoryOf(
    subject = TODO("resolve: <page>"),
    claim = "<claim_text>",
    prov = Provenance(origin = "compost-checks", ingestedAt = "<date>", ingester = "compost"),
)
// Conflicts with: <conflicting_page>
// Conflicting claim: "<conflicting_claim_text>"
```

5. If `--dry-run`: print to stdout. Otherwise: append to `wiki/claims.kt` (create the file if absent, with a header comment).
6. Print a summary: N stubs written (or would be written).

**2c. Register `claims_group` in `cli/__init__.py`.**

#### Data flow

```
compost checks run
  └─► .compost/checks/{slug}.md  (existing)
  └─► .compost/checks/{slug}.jsonl  (new)

compost claims suggest [--branch BRANCH] [--dry-run]
  └─► reads .compost/checks/{slug}.jsonl
  └─► filters contradiction findings
  └─► appends to wiki/claims.kt
```

#### Key function signatures

```python
# tools/compost/checks/runner.py — extend write_check_report
def write_check_report(repo: Path, branch: str, results: list[CheckResult]) -> Path:
    """Write markdown report and JSONL findings sidecar. Returns markdown path."""
    ...

# tools/compost/cli/claims.py
def _branch_slug(branch: str) -> str:
    """Convert branch name to filesystem-safe slug (/ → -)."""
    ...

def _render_kotlin_stub(finding: dict, date_str: str) -> str:
    """Render a single @Contested TheoryOf stub from a contradiction finding."""
    ...

def _read_contradiction_findings(findings_path: Path) -> list[dict]:
    """Read JSONL, filter to contradiction_scan findings with claim_text set."""
    ...
```

### Work item 3: Update `backlog.md`

Mark the resolved items, add formal defer notes for the others.

---

## § Tests

| Test | File | What it covers |
|---|---|---|
| `test_write_check_report_emits_jsonl` | `tests/test_checks.py` | JSONL sidecar written alongside markdown; valid JSON per line |
| `test_jsonl_contains_all_findings` | `tests/test_checks.py` | All Finding fields serialised correctly; no fields dropped |
| `test_read_contradiction_findings_filters` | `tests/test_claims.py` | Filters to contradiction_scan only; skips findings without claim_text |
| `test_render_kotlin_stub_format` | `tests/test_claims.py` | Output contains @Contested, TheoryOf, subject TODO, claim_text, conflicting refs |
| `test_claims_suggest_dry_run` | `tests/test_claims.py` | Dry run prints to stdout, does not write claims.kt |
| `test_claims_suggest_writes_file` | `tests/test_claims.py` | Creates claims.kt on first run; appends on subsequent runs |
| `test_claims_suggest_no_findings` | `tests/test_claims.py` | Graceful message when no JSONL file or no contradiction findings |
| `test_claims_suggest_missing_branch` | `tests/test_claims.py` | Graceful error when branch slug resolves to no JSONL file |

---

## § Documentation updates

No separate doc files. `compost claims suggest --help` serves as the primary documentation (Click docstrings). `backlog.md` is updated as part of this plan (Work item 3).

---

## § Cross-check against PHILOSOPHY.md Red Flags

| Red Flag | Applies? | Mitigation |
|---|---|---|
| **Information Leakage** | Mild: `cli/claims.py` needs to know the JSONL slug format. Mitigate: centralise `_branch_slug()` in `runner.py` (or a shared helper) so the CLI doesn't hardcode the format. | Centralise slug logic in `runner.py`, export it for CLI use. |
| **Shallow Module** | Risk: `_read_contradiction_findings` is 3 lines. Acceptable if it's the only caller-facing API into the findings data; caller doesn't need to know about JSONL format. | Keep it — it hides the file format from the CLI. |
| **Pass-Through Method** | `claims_suggest` could become thin delegation to helpers. Acceptable: CLI layer owns option parsing + output routing (stdout vs file); the helpers own data logic. | Ensure `claims_suggest` does more than pass args through — it owns branch resolution, dry-run routing, and summary printing. |
| **Temporal Decomposition** | No: JSONL write is tied to check run (same function), not a separate step. | N/A |

### Correctness

`write_check_report` currently returns the markdown path. Extending it to write JSONL must not change the return value or break callers. The JSONL write is additive — failure to write JSONL (e.g. permission error) should log a warning but not fail the check run.

### Data Integrity

The JSONL sidecar is in `.compost/checks/` which is already gitignored. No risk of committing generated artifacts.

### Performance

JSONL write is synchronous but trivially fast (scalar fields, small file). No concern.
