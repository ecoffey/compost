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
