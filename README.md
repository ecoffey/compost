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

## Ingestion commands

### `compost raw add`

Capture a raw source file from stdin and commit it on a new `raw/*` branch.

```bash
echo "Discussed retry ownership. Alice owns it." | \
  COMPOST_REPO=. compost raw add \
    --source note \
    --title "retry ownership discussion"
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

Write a local PR log for the current `raw/*` branch.

```bash
COMPOST_REPO=. compost pr open
# → writes _pr-log/raw-YYYY-MM-DD-slug.md
```

### `compost pr merge`

Fast-forward merge the current `raw/*` branch into the default branch. Blocked if any `wiki/` files were modified on the branch.

```bash
COMPOST_REPO=. compost pr merge
```

### Steel thread

```bash
cd ~/local-test/my-wiki
git init && git add -A && git commit -m "initial wiki content"

echo "Discussed retry ownership. Alice owns it." | \
  COMPOST_REPO=. compost raw add --source note --title "retry ownership discussion"

COMPOST_REPO=. compost pr open
# → writes _pr-log/raw-YYYY-MM-DD-retry-ownership-discussion.md
# review the file, then:

COMPOST_REPO=. compost pr merge
# → fast-forward merges into main
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
