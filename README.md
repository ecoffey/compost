# team-context

Source repo for the `team_context` Python package and `tc` CLI.

## What this is

`team_context` is a toolkit for running a structured, LLM-maintained team knowledge base. It implements the ingestion, synthesis, adversarial check, and MCP access layers described in `team-context-spec.md`.

This repo is the **implementation source**. The actual knowledge wikis (team, org, engineering instances) are separate repos created with `tc init`.

## Setup

```bash
pip install -e tools/team_context/
```

Or with uv:

```bash
uv pip install -e tools/team_context/
```

Then bootstrap a wiki instance:

```bash
tc init ~/my-team-wiki --name my-team
cd ~/my-team-wiki
tc doctor
```

## Running the MCP server

```bash
cd ~/my-team-wiki
tc mcp
```

Register this in your Claude Code MCP config to give agents access to the wiki:

```json
{
  "mcpServers": {
    "team-context": {
      "command": "tc",
      "args": ["mcp"],
      "env": { "TC_REPO": "/path/to/my-team-wiki" }
    }
  }
}
```

## Running tests

```bash
cd tools/team_context
pip install -e ".[dev]"
pytest
```

## Development notes

- qmd `collection add` resolves paths relative to cwd. `bootstrap_repo` and any code calling `qmd collection add` must set `cwd` to the parent directory of the target collection folder.
- `qmd collection list` does not support `--json`. Parse the text output (lines matching `name (qmd://name/)`) to list collection names.
- `qmd search --json` returns hits with keys: `file` (a `qmd://` URI), `title`, `snippet`, `score`.
