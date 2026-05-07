# compost

A CLI for maintaining a team knowledge wiki. Raw source documents are ingested, classified, synthesized into wiki pages, and served via an MCP server for use in Claude Code sessions.

## Installation

```bash
cd tools/compost
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Quick start

```bash
# Bootstrap a new wiki repo
compost init --name my-team /path/to/my-team-wiki

# Add a raw source document
echo "We decided to use Stripe." | COMPOST_REPO=/path/to/my-team-wiki compost raw add \
  --source decision --title "Use Stripe for payments"

# Verify repo health
COMPOST_REPO=/path/to/my-team-wiki compost doctor

# Start the MCP server (point Claude Code at this)
COMPOST_REPO=/path/to/my-team-wiki compost mcp
```

## Commands

### `compost init`

Bootstrap a new wiki repo with `.compost.yml`, seed directories, and qmd configuration.

### `compost raw add`

Ingest a raw source document from stdin. Commits the file on a new branch, runs the Tier 1 classifier, and (if Tier 1 fires) synthesizes wiki edits via the Tier 2 agent.

```bash
echo "Postmortem: payments service was down for 2 hours." | \
  COMPOST_REPO=. compost raw add --source incident --title "Payments outage 2026-05-03"
```

Output:
```
✓ raw/incidents/2026/INC-2026-05-03-payments-outage-2026-05-03.md
branch: raw/2026-05-03-payments-outage-2026-05-03
[Tier 1] FIRE — source:incident, semantic:postmortem
[Tier 2] synthesizing...
  wiki/services/payments.md (updated)
PR #4: http://localhost:3000/myteam/wiki/pulls/4
```

Use `--no-synth` to skip synthesis even when Tier 1 fires.

### `compost pr merge`

Merge the current `raw/*` branch via Gitea PR, then sync the local repo.

```bash
COMPOST_REPO=. compost pr merge
# merged (PR #4)
```

## Tier 1 Classifier

`compost raw add` automatically classifies new raw files using rule-based triggers. FIRE means the file is likely worth synthesizing into the wiki.

```
[Tier 1] FIRE — source:decision, semantic:decision
```

### `compost classify run`

Classify a single file without adding it:

```bash
COMPOST_REPO=. compost classify run raw/decisions/2026-04-27-use-stripe.md
# ✓ FIRE   raw/decisions/2026-04-27-use-stripe.md
#   source:decision · semantic:decision
```

### `compost classify replay`

Re-run the classifier across recent raw files to tune rules:

```bash
COMPOST_REPO=. compost classify replay --since 7d
```

## Tier 2 Synthesis

When Tier 1 fires, the synthesis agent queries the wiki for candidate pages, identifies which ones need updating, and proposes new content. This runs automatically from `compost raw add`, or manually:

```bash
COMPOST_REPO=. compost synth run --raw raw/decisions/2026-05-03-drop-postgres.md
COMPOST_REPO=. compost synth run --raw raw/decisions/... --dry-run  # inspect without writing
```

### `compost synth log`

View recent synthesis runs:

```bash
COMPOST_REPO=. compost synth log --last 5
```

## Tier 2.5 Adversarial Checks

`compost pr merge` runs six checks before merging any branch that touches `wiki/`. Checks run
cheapest-first and short-circuit on deterministic failures.

```bash
# Run checks on the current branch
compost checks run

# Run checks on a specific branch
compost checks run --branch raw/2026-05-04T161353-my-change

# Merge with check gate (runs checks automatically)
compost pr merge

# Override a failing check (reason is logged to wiki/log.md)
compost pr merge --override --reason "contradiction is already declared; merging to unblock"

# Skip checks entirely (raw-only PR, no wiki edits)
compost pr merge --no-checks
```

Configure under `checks:` in `.compost.yml`:

```yaml
checks:
  provider: anthropic
  model: claude-sonnet-4-6
  human_edit_days: 30
  recent_raw_days: 90
  scope_min_score: 0.15
```

`ANTHROPIC_API_KEY` must be set for LLM-based checks (same key as synthesis).

## Kotlin Consistency Layer

Compost generates a typed Kotlin representation of the wiki and uses `kotlinc` as a consistency
oracle. The markdown frontmatter is always primary; Kotlin is always derived.

**Prerequisites:** install Kotlin via SDKMAN (`sdk install kotlin`).

```bash
# Generate wiki.kt from wiki frontmatter
compost codify run

# Generate and compile (kotlinc must be on PATH)
compost codify run --compile

# Full round-trip: codify → compile → render → fuzzy compare
compost assay

# Assay with markdown report
compost assay --report

# Validate wiki edits produced by synthesis before pushing
compost raw add --source decision --title "..." --assay < content.md
```

A compile pass means all wiki frontmatter is structurally consistent with the schema. `compost
assay` also verifies the codegen is lossless: the compiled Kotlin, when run, produces output that
matches the original frontmatter for all key fields.

### Human-authored claims

Create `wiki/claims.kt` to write typed assertions that reference generated page variables:

```kotlin
// wiki/claims.kt — hand-authored; committed to version control
// References variables declared in the generated wiki.kt.

@Contested val jwtPositionByPlatform = TheoryOf(
    subject = authService,   // compile error if authService doesn't exist in wiki.kt
    claim = "JWT is stateless and scales horizontally",
    prov = Provenance(origin = "internal-discussion", ingestedAt = "2026-04-15", ingestedBy = "eoin")
)

@Contested val sessionPositionByAlice = TheoryOf(
    subject = authService,
    claim = "Session cookies are simpler and immediately revocable",
    prov = Provenance(origin = "raw/slack/2026-04-14-eng.md", ingestedAt = "2026-04-14", ingestedBy = "alice")
)
```

`claims.kt` is included in the compile automatically when present. Two or more `@Contested
TheoryOf` sharing the same subject cause codegen to auto-generate a `Disputes` record and mark
that page as `contested = true`. A contested page with `confidence > 0.8` fails `compost assay`.

| Command | Description |
|---|---|
| `compost codify run [--compile]` | Generate wiki.kt; optionally compile |
| `compost assay [--report]` | Full round-trip validation |

## Shims

Shims are local listeners that bridge async ingestion sources (Slack, GitHub, git checkpoints) into the compost queue. Run them alongside the synthesis worker during development.

```bash
# Start all shims (Slack on :8421, GitHub webhook on :8422, git poller in-process)
COMPOST_REPO=. compost shims up

# Stop shims started by the above
COMPOST_REPO=. compost shims down

# Show running shim PIDs
COMPOST_REPO=. compost shims status
```

### Slack shim

Simulates a Slack `reaction_added` event. The shim listens on `localhost:8421/events`.

```bash
# Fake a wiki reaction on a Slack thread
COMPOST_REPO=. compost slack fake-react \
  --channel C12345 --ts 1234567890.000001 \
  --text "We decided to switch to Postgres." \
  --user U99
```

### GitHub webhook shim

Simulates a GitHub PR event with the `compost/synth` label. Listens on `localhost:8422/webhook`.

```bash
# Fake a PR opened event
COMPOST_REPO=. compost gh fake-pr \
  --repo acme/payments --pr 42 \
  --branch feature/add-stripe \
  --action opened \
  --label compost/synth
```

### Entire shim (git checkpoint poller)

Polls local git repos for new commits and materializes them as checkpoint raw files. Seed the
poller's "seen" state from the current HEAD (so only future commits are materialized):

```bash
COMPOST_REPO=. compost entire seed --repo /path/to/source-repo
```

### Configuration

Add a `shims:` block to `.compost.yml`:

```yaml
shims:
  slack:
    port: 8421
    wiki_emoji: wiki
  gh_webhook:
    port: 8422
  entire:
    poll_interval_s: 10
    targets:
      - repo: ~/work/my-service
        branch_prefix: feature/
        path_filter: src/
        gh_name: acme/my-service
```

## Worker

The synthesis worker drains the on-disk queue and runs synthesis for each job. Enable async mode
in `.compost.yml` so that `compost raw add` enqueues jobs rather than synthesizing inline:

```yaml
worker:
  mode: async          # inline (default) | async
  poll_interval_s: 5
  max_retries: 3
  retry_backoff_base_s: 30
```

```bash
# Start the worker (blocks; Ctrl-C to stop)
COMPOST_REPO=. compost worker up

# Show queue depth and recent job states
COMPOST_REPO=. compost worker status

# Move one dead-letter job back to inbox by ID
COMPOST_REPO=. compost worker retry --job-id <hex>
```

The queue lives under `.compost/queue/{inbox,processing,done,dead}/`. Jobs transition atomically
via `os.rename()`. Failed jobs are retried up to `max_retries` times with exponential backoff
(`retry_backoff_base_s * 2^(retry_count - 1)` seconds). After exhausting retries the job is
moved to `dead/` with a human-readable `.md` companion explaining the failure.

## Gitea integration

Configure Gitea as the PR backend:

```bash
export GITEA_TOKEN=your-token
COMPOST_REPO=. compost gitea setup --url http://localhost:3000 --owner myteam
```

## MCP server

Start the MCP server for use in Claude Code:

```bash
COMPOST_REPO=/path/to/my-team-wiki compost mcp
```

Tools available to Claude Code:
- `load_service_context(service)` — load wiki context for a service
- `query_wiki(query, scope)` — hybrid search over wiki, decisions, or incidents
