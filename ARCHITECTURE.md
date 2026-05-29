# Architecture

Compost is a knowledge-management CLI for engineering teams. It ingests raw signals (Slack threads, incidents, decisions, meeting notes) into a markdown wiki, uses a two-tier classification and synthesis pipeline to keep the wiki current, and exposes the wiki to AI agents via an MCP server.

---

## Repository layout

```
tools/compost/compost/
├── cli/            # Click command groups (entry point: `compost`)
├── ingest/         # Raw source ingestion and git workflow
├── checks/         # Tier 1 rule-based classification
├── synth/          # Tier 2 LLM-driven synthesis
├── model/          # Frontmatter schemas and data models
├── mcp/            # MCP server (stdio transport)
├── worker/         # Async task queue
├── gitea/          # Gitea API client
├── lint/           # Content validation plugins
├── codify/         # Kotlin schema code generation
└── shims/          # FastAPI/Uvicorn compatibility shims
```

Wiki instances (bootstrapped by `compost init`) live in separate repos, not here.

---

## Core pipeline

Raw signals flow through four sequential stages:

```
compost raw add
      │
      ▼
1. INGEST        stdin → markdown file (frontmatter + body) → git branch → Gitea PR
      │
      ▼
2. CLASSIFY      Rule-based: source type + semantic match → FIRE / NO-FIRE decision
      │  (FIRE only)
      ▼
3. SYNTHESIZE    LLM agent: reads wiki, proposes updates to relevant pages
      │
      ▼
4. CHECK         LLM adversarial pass: validates citations, logic, and conflicts
```

Each stage is independently invocable (`compost classify run`, `compost synth run`, `compost checks run`). The worker queue (`compost worker run`) drives the pipeline asynchronously from a filesystem-backed queue at `.compost/queue/`.

---

## Ingestion (`ingest/`)

`compost raw add --source TYPE --title TITLE` reads stdin, creates a markdown file under `raw/<type>/`, fills in frontmatter based on source type, commits to a new branch, and opens a Gitea PR. Source types: `slack`, `incident`, `decision`, `note`, `meeting`, `support`.

Raw docs are immutable once committed. They accumulate as a source-of-truth log that synthesis reads from.

---

## Classification (`checks/`)

Tier 1 is deterministic. Rules live in `ingest/classifier_rules.yaml` and match on source type and keyword/semantic signals. Output is a FIRE or NO-FIRE verdict written into the PR description. Only FIRE docs proceed to synthesis.

---

## Synthesis (`synth/`)

Tier 2 uses the Anthropic API. The agent receives the raw doc, queries the wiki via qmd, and proposes diffs to one or more existing wiki pages (or flags that a new page is needed). Prompts are stored as markdown templates under `synth/prompts/`. Dry-run mode (`--dry-run`) prints proposals without writing.

---

## MCP server (`mcp/`)

`compost mcp` starts a stdio MCP server that exposes the wiki to Claude Code and other MCP clients. Tools:

| Tool | What it does |
|------|-------------|
| `query_wiki` | Full-text and semantic search over `wiki/` |
| `load_service_context` | Returns all frontmatter and body for a named service |
| `query_across_org` | Federated search across registered repos |
| `find_service_owner` | Returns CODEOWNERS entry for a service |

---

## Federation

`~/.compost/federation.yaml` registers multiple repos (team, org, engineering). `query_across_org` and `find_service_owner` fan out to all registered repos and deduplicate results. This lets a single MCP session span the whole org's knowledge.

---

## Data model

Every wiki and raw document has a YAML frontmatter block. Required fields:

| Field | Values |
|-------|--------|
| `type` | `service`, `module`, `decision`, `runbook`, `concept`, `customer`, `person`, `project`, `research` |
| `name` | string |
| `owners` | list of strings |
| `status` | `active`, `deprecated`, `archived` |
| `updated` | ISO date |
| `confidence` | `high`, `medium`, `low` |
| `sources` | non-empty list (links or raw doc paths) |

Optional: `related`, `supersedes`, `depends_on`, `technology`.

Repo config lives in `.compost.yml` at the wiki repo root and declares `name`, `qmd_index`, and optional Gitea coordinates.

---

## External dependencies

| Dependency | Role |
|------------|------|
| Anthropic API | Synthesis and adversarial check agents |
| qmd | Full-text and vector search over markdown (separate binary) |
| Gitea | Remote PR workflow for ingestion |
| MCP (`mcp>=1.9`) | Server protocol for agent tool access |
| Click | CLI framework |
| HTTPX | Async HTTP client for Gitea API |
| python-frontmatter | Frontmatter parsing |
| Rich | Terminal output |
| PyYAML | Config and rules parsing |

---

## Bootstrapping a wiki instance

`compost init PATH --name NAME` creates the directory tree, seeds `.compost.yml`, `CODEOWNERS`, `.gitignore`, and a GitHub Actions workflow, then initializes a qmd index. The result is a standalone git repo ready to receive `compost raw add` calls.
