# compost

Source repo for the `compost` Python package and CLI.

## What this is

`compost` is a toolkit for running a structured, LLM-maintained team knowledge base. It implements the ingestion, synthesis, adversarial check, and MCP access layers described in `team-context-spec.md`.

This repo is the **implementation source**. The actual knowledge wikis (team, org, engineering instances) are separate repos created with `compost init`.

## Setup

```bash
pip install -e tools/compost/
```

Or with uv:

```bash
uv pip install -e tools/compost/
```

Then bootstrap a wiki instance:

```bash
compost init ~/my-team-wiki --name my-team
cd ~/my-team-wiki
compost doctor
```

## Running the MCP server

```bash
cd ~/my-team-wiki
compost mcp
```

Register this in your Claude Code MCP config to give agents access to the wiki:

```json
{
  "mcpServers": {
    "compost": {
      "command": "compost",
      "args": ["mcp"],
      "env": { "COMPOST_REPO": "/path/to/my-team-wiki" }
    }
  }
}
```

## Gitea setup (one-time)

Gitea must be running before using `compost raw add`. Configure the integration from your wiki repo:

```bash
export GITEA_TOKEN=your-token-here
cd ~/my-team-wiki
compost gitea setup --owner myuser
# creates Gitea repo, sets origin remote, pushes main, writes gitea: to .compost.yml
```

## Ingestion commands

### `compost raw add`

Capture a raw source file from stdin, commit it on a new `raw/*` branch, push to Gitea, and open a PR.

```bash
echo "Discussed retry ownership. Alice owns it." | \
  COMPOST_REPO=. compost raw add \
    --source note \
    --title "retry ownership discussion"
```

Output:
```
✓ raw/notes/2026-04-28-retry-ownership-discussion.md
branch: raw/2026-04-28-retry-ownership-discussion
PR #3: http://localhost:3000/myuser/my-team-wiki/pulls/3
```

Options:

| Flag | Description |
|---|---|
| `--source` | `slack\|incident\|decision\|note\|meeting\|support` (required) |
| `--title` | Human-readable title, used for slug and frontmatter (required) |
| `--captured-by` | Author; defaults to `git config user.name` |
| `--origin` | Source reference (e.g. PagerDuty ID, Slack URL) |
| `--channel` | Slack channel name; required when `--source=slack` |

Body is read from stdin.

### `compost pr open`

Print the Gitea PR URL for the current `raw/*` branch.

```bash
COMPOST_REPO=. compost pr open
# PR #3: http://localhost:3000/myuser/my-team-wiki/pulls/3  (open)
```

### `compost pr merge`

Merge the current `raw/*` branch via Gitea PR, then sync the local repo. Blocked if any `wiki/` files were modified on the branch.

```bash
COMPOST_REPO=. compost pr merge
# merged (PR #3)
```

### Steel thread

```bash
# One-time: start Gitea, create a token, then:
export GITEA_TOKEN=your-token
cd ~/local-test/my-wiki
compost gitea setup --owner myuser

# Ingest a raw file:
echo "Discussed retry ownership. Alice owns it." | \
  COMPOST_REPO=. compost raw add --source note --title "retry ownership discussion"
# PR opened at http://localhost:3000/myuser/my-wiki/pulls/1

# Review the PR in browser, then merge:
COMPOST_REPO=. compost pr merge
# merged (PR #1)
```

## Running tests

```bash
cd tools/compost
pip install -e ".[dev]"
pytest
```

## Development notes

- qmd `collection add` resolves paths relative to cwd. `bootstrap_repo` and any code calling `qmd collection add` must set `cwd` to the parent directory of the target collection folder.
- `qmd collection list` does not support `--json`. Parse the text output (lines matching `name (qmd://name/)`) to list collection names.
- `qmd search --json` returns hits with keys: `file` (a `qmd://` URI), `title`, `snippet`, `score`.
