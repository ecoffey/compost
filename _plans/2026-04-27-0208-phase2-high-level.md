# Phase 2: Manual Ingestion CLI + Atomic PR Plumbing

## Starting Prompt

> @_plans/2026-04-23-2149-laptop-steel-thread-high-level.md phase 2. also pull in anything appropriate for this from @_plans/backlog.md

---

## § Context

**Goal:** exercise the atomic PR invariant by hand, before any LLM automation. A developer can capture a Slack thread by hand, land it in `raw/slack/...`, review the "PR" locally, and merge. No LLM yet.

**Backlog items considered:** none of the three backlog entries (persistent qmd process, qmd isolation verification, synthesis observability) are relevant to Phase 2. All remain parked for Phase 8/10.

**Pre-condition:** the test wiki at `~/local-test/compost-test` is not yet a git repository. Phase 2 requires git. The `compost raw add` command will error clearly if the repo has no `.git` — the user runs `git init` once before using the Phase 2 commands.

---

## § High-level plan

### Modules changing

| Module | Status | Change |
|--------|--------|--------|
| `compost/ingest/raw.py` | new | Raw file path resolution, frontmatter generation, file writing |
| `compost/ingest/git.py` | new | Git branch/commit/merge/status operations |
| `compost/ingest/pr.py` | new | Local PR log creation and merge gate |
| `compost/cli.py` | extend | Add `compost raw` and `compost pr` command groups |
| `compost/pyproject.toml` | extend | Add `compost.ingest` to packages list |
| `README.md` | extend | Add Phase 2 commands to CLI reference |

### Data flow

```
tc raw add --source slack --title "Payments standup" --channel eng < body.md
     │
     ▼
ingest/raw.py
  slugify(title) → "payments-standup"
  next_path(repo, "slack", channel="eng", ts=now)
    → raw/slack/2026/04/2026-04-27-eng-payments-standup.md
  build_frontmatter(source, title, captured_by, origin, ts)
  write file (frontmatter + body)
     │
     ▼
ingest/git.py
  assert_git_repo(repo)        # errors if no .git
  branch_name = "raw/2026-04-27-payments-standup"
  create_branch_and_commit(repo, branch_name, [raw_file.path], message)
     │
     ▼
CLI output:
  ✓ raw/slack/2026/04/2026-04-27-eng-payments-standup.md
  branch: raw/2026-04-27-payments-standup
  Review with: compost pr open
```

```
tc pr open
     │
     ▼
ingest/git.py
  current_branch(repo) → "raw/2026-04-27-payments-standup"
  changed_files(repo, branch, base) → ["raw/slack/.../...md"]
     │
     ▼
ingest/pr.py
  write _plans/pr-log/raw-2026-04-27-payments-standup.md
  print path
```

`_plans/` is part of the wiki repo layout (see module layout in the high-level plan). PR logs live there because they are repo-local draft state — the local equivalent of a GitHub PR. In Phase 5, adversarial check results will be written back into this same file before `compost pr merge` is allowed to proceed. Writing to a file (rather than just printing) is what makes that future write-back possible.

```
tc pr merge
     │
     ▼
ingest/git.py + ingest/pr.py
  assert branch is raw/* (error otherwise)
  changed_files → check for wiki/ edits (block if any)
  fast_forward_merge(repo, branch, into=default_branch)
  print "merged"
```

### File naming conventions

All path logic lives exclusively in `ingest/raw.py`.

| Source | Path template |
|--------|---------------|
| `slack` | `raw/slack/{YYYY}/{MM}/{YYYY}-{MM}-{DD}-{channel}-{slug}.md` |
| `incident` | `raw/incidents/{YYYY}/INC-{YYYY}-{MM}-{DD}-{slug}.md` |
| `decision` | `raw/decisions/{YYYY}-{MM}-{DD}-{slug}.md` |
| `note` | `raw/notes/{YYYY}-{MM}-{DD}-{slug}.md` |
| `meeting` | `raw/meetings/{YYYY}-{MM}-{DD}-{slug}.md` |
| `support` | `raw/support/{YYYY}-{MM}-{DD}-{slug}.md` |

All names use the date as the ordering key — no sequence number scanning required. The `INC-` prefix on incidents is kept for easy visual correlation with incident management systems (PagerDuty, etc.). The existing seed file `INC-0001-payment-timeout.md` was hand-authored with a sequence number; new files from `compost raw add` will use the date-based format.

### Raw file frontmatter

Matches the schema already used in the test wiki's seed files:

```yaml
---
source: slack
captured_at: 2026-04-27T10:00:00Z
captured_by: alice          # from --captured-by, else git config user.name
intent: "Payments team standup"
origin: "#eng 2026-04-27"   # from --origin, else empty
---
```

### Core domain objects

```python
# compost/ingest/raw.py

SOURCE_TYPES = Literal["slack", "incident", "decision", "note", "meeting", "support"]

@dataclass(frozen=True)
class RawFile:
    path: Path        # absolute path written
    rel_path: Path    # relative to repo root (used in git commit, PR log)
    source: str

def write_raw(
    repo: Path,
    source: SOURCE_TYPES,
    title: str,
    body: str,
    *,
    captured_by: str | None = None,   # falls back to git config user.name
    origin: str | None = None,
    channel: str | None = None,        # slack only
    ts: datetime | None = None,        # defaults to utcnow
) -> RawFile:
    """Resolve date-based path, write frontmatter + body, return descriptor."""
```

```python
# compost/ingest/git.py

def assert_git_repo(repo: Path) -> None:
    """Raise click.UsageError if repo has no .git directory."""

def default_branch(repo: Path) -> str:
    """Return the name of the repo's default branch (main or master)."""

def current_branch(repo: Path) -> str:

def create_branch_and_commit(
    repo: Path,
    branch: str,
    files: list[Path],
    message: str,
) -> None:
    """Create branch off current HEAD, stage files, commit."""

def changed_files(repo: Path, branch: str, base: str) -> list[str]:
    """Return paths changed on branch relative to base."""

def fast_forward_merge(repo: Path, branch: str) -> None:
    """Fast-forward merge branch into current branch. Errors if not FF-able."""
```

```python
# compost/ingest/pr.py

@dataclass(frozen=True)
class PRLog:
    path: Path    # absolute path of the written log file

def open_pr(repo: Path, branch: str) -> PRLog:
    """Write PR log to _plans/pr-log/{branch-slug}.md, return path."""

def merge_pr(repo: Path) -> None:
    """Gate: assert raw/* branch, no wiki/ edits, then fast-forward merge."""
```

### PR log format

```markdown
# PR: raw/2026-04-27-payments-standup

**Branch:** raw/2026-04-27-payments-standup
**Date:** 2026-04-27
**Files changed:**
- raw/slack/2026/04/2026-04-27-eng-payments-standup.md

---
*Review complete? Merge with `compost pr merge`.*
```

Written to `{repo}/_plans/pr-log/raw-2026-04-27-payments-standup.md`.

### CLI surface

```
tc raw add
  --source   TEXT   slack|incident|decision|note|meeting|support  [required]
  --title    TEXT   Human-readable title (used for slug + frontmatter)  [required]
  --captured-by TEXT  Author. Defaults to git config user.name.
  --origin   TEXT   Source reference (e.g. PagerDuty ID, Slack URL).
  --channel  TEXT   Slack channel name. Required when --source=slack.
  Body is read from stdin.

tc pr open
  Opens a local PR log for the current raw/* branch.

tc pr merge
  Merges the current raw/* branch into the default branch (fast-forward only).
  Blocked if any wiki/ files are modified on the branch.
```

### Steel thread

```
cd ~/local-test/compost-test
git init && git add -A && git commit -m "initial wiki content"

echo "Discussed retry ownership. Alice owns it." | \
  COMPOST_REPO=. compost raw add --source note --title "retry ownership discussion"

# → creates raw/notes/2026-04-27-retry-ownership-discussion.md
# → creates branch raw/2026-04-27-retry-ownership-discussion

COMPOST_REPO=. compost pr open
# → writes _plans/pr-log/raw-2026-04-27-retry-ownership-discussion.md

COMPOST_REPO=. compost pr merge
# → fast-forward merges into main
```

### Documentation updates

- **README.md**: add `compost raw add`, `compost pr open`, `compost pr merge` to the CLI reference section. Note the git pre-condition.

---

## § Design notes and red flag checks

**Path formula isolation:** all file naming logic lives in `raw.py`. The CLI and git module never construct raw paths — they receive `RawFile` from `write_raw`. No information leakage.

**git.py depth:** `create_branch_and_commit` bundles three git operations (checkout -b, add, commit) behind one call. This is intentionally deep — the caller (CLI) should not need to know the git step sequence.

**Date-based naming:** all raw file names use the capture date as the ordering key. No sequence number scanning needed. The `INC-` prefix on incidents is kept for visual correlation with external incident systems but carries no numeric sequence.

**`compost pr merge` wiki-edit guard:** `changed_files` diffs the branch against the default branch. If any path starts with `wiki/`, merge is blocked. This guard prevents accidentally auto-merging synthesis output in Phase 4+ before checks have run.

**PR log purpose:** the PR log is the local substitute for a GitHub PR. It records what files are being proposed for merging and gives the developer a file to open and review before running `compost pr merge`. It also serves as the write-back target in Phase 5, when adversarial check results will be appended to this file. `compost pr merge` will be blocked unless checks have passed (Phase 5+); in Phase 2 it merges freely. The log lives in `_plans/pr-log/` inside the wiki repo — `_plans/` is part of the wiki repo layout (see module layout in the high-level plan).

---

### Feedback Log

**Sequence numbers (NNNN) for incidents and decisions**
> Original comment (verbatim): `^^ i think its better to either drop the NNNN (like we don't really need that for incidients) or replace it with timestamping, for each use of NNNN ^^`
>
> Context: appeared after the design note about `_next_seq` scanning for sequence numbers.
>
> Incorporated: replaced `INC-{NNNN}-{slug}` and `{NNNN}-{slug}` with date-based naming (`INC-{YYYY}-{MM}-{DD}-{slug}` and `{YYYY}-{MM}-{DD}-{slug}`). Removed `_next_seq` from the design entirely. The `INC-` prefix is kept for visual correlation with external incident systems.

**PR log location and purpose unclear**
> Original comment 1 (verbatim): `^^ why are we writing to _plans/ in the wiki (e.g. the test wiki)? ^^`
> Original comment 2 (verbatim): `^^ what is the pr log for? ^^`
>
> Context: comments appeared in the `compost pr open` data flow and in the design notes.
>
> Incorporated: added inline explanation of `_plans/` as part of the wiki repo layout (per the high-level plan's module layout), and added a dedicated "PR log purpose" note explaining the Phase 2 use (human review), the Phase 5 use (check results write-back), and why a file rather than stdout.
