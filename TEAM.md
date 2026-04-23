# TEAM.md

This file is the schema and operating manual for the team-context repository. It is read by both agents (at session start, to understand how to query and modify the wiki) and humans (to understand how the system works and how to contribute).

**If you are an agent reading this:** follow the rules in this file exactly. They are not suggestions. When in doubt, prefer the action that preserves wiki integrity (refuse, flag, or ask) over the action that makes a change you're unsure about.

**If you are a human:** this file evolves with the team. Propose changes via PR. The file describes the current contract between the team and the agents.

---

## What this repository is

A structured, LLM-maintained knowledge base for the team. Two layers:

- `raw/` — immutable records of things that happened (commits, Slack threads, incidents, decisions, meetings, support tickets). Source of truth. Append-only.
- `wiki/` — synthesized, maintained knowledge about the team's systems and decisions. Derived from `raw/`. Every claim in the wiki cites a raw source.

The wiki is never authoritative on its own. It is a compounding, cross-referenced *view* over raw. When wiki and raw disagree, raw wins and the wiki gets corrected.

## Directory layout

```
team-context/
├── TEAM.md                    # this file
├── raw/                       # append-only records
│   ├── checkpoints/           # commit + agent session + intent, per commit
│   ├── slack/                 # ingested Slack threads
│   ├── incidents/             # postmortems
│   ├── decisions/             # ADRs, RFCs (human-authored)
│   ├── meetings/              # meeting transcripts
│   ├── support/               # support ticket threads
│   └── notes/                 # ambient "I just learned something" captures
├── wiki/                      # synthesized pages
│   ├── services/              # one page per service
│   ├── modules/               # sub-service components that earned their own page
│   ├── decisions/             # synthesized decision pages (distinct from raw ADRs)
│   ├── runbooks/              # operational procedures
│   ├── concepts/              # cross-cutting ideas (idempotency, rate-limiting, etc.)
│   ├── customers/             # customer-specific knowledge
│   ├── people/                # who knows what
│   ├── projects/              # in-flight work (retires on completion)
│   ├── glossary.md
│   ├── index.md               # catalog of all wiki pages
│   └── log.md                 # append-only log of synthesis events
├── .qmd/                      # qmd search index config
└── .github/
    ├── CODEOWNERS             # wiki ownership (not code ownership)
    └── workflows/             # Tier 1/2/adversarial pipelines
```

## Raw layer conventions

### Organization

Raw is organized by **source and date**, never by topic. Topic is interpretive and belongs in the wiki.

Partition depth matches volume:
- Checkpoints: `raw/checkpoints/YYYY/MM/DD/{sha}-{slug}.md`
- Slack: `raw/slack/YYYY/MM/YYYY-MM-DD-{channel}-{slug}.md`
- Incidents: `raw/incidents/YYYY/INC-NNNN-{slug}.md`
- Meetings: `raw/meetings/YYYY/MM/YYYY-MM-DD-{slug}.md`
- Support: `raw/support/YYYY/MM/{ticket-id}-{slug}.md`
- Notes: `raw/notes/YYYY/MM/YYYY-MM-DD-{slug}.md`
- Decisions: `raw/decisions/NNNN-{slug}.md` (flat, numbered sequentially)

### Raw file frontmatter

Every raw file carries minimal frontmatter:

```yaml
---
source: slack | checkpoint | incident | decision | meeting | support | note
captured_at: 2026-04-20T14:33:00Z
captured_by: {username or "auto"}
intent: {one-line human framing, if push-based ingestion with /wiki-this}
origin: {URL or identifier back to the source — Slack permalink, commit SHA, ticket ID}
---
```

### Raw is append-only and immutable *by convention*

- Do not edit raw files to "clean them up." They are records of what was said, committed, or decided at a moment in time.
- Removal is allowed (via `/unwiki` or direct PR) when content is sensitive, wrong-in-a-harmful-way, or explicitly retracted. Removal is a normal `git rm` commit, not history rewriting, unless the content requires a true scrub (credentials, PII).
- When raw is removed, any wiki page citing it is flagged by the next lint pass for reconciliation.

## Wiki layer conventions

### Entity types

Every wiki page is one of these types, declared in frontmatter:

| Type | Directory | Purpose |
|---|---|---|
| `service` | `wiki/services/` | A deployed service the team owns |
| `module` | `wiki/modules/{service}/` | A sub-component of a service that's grown enough to deserve its own page |
| `decision` | `wiki/decisions/` | A synthesized architectural decision (distinct from raw ADRs — this is the *current* state) |
| `runbook` | `wiki/runbooks/` | An operational procedure (incident response, deploy, rollback, etc.) |
| `concept` | `wiki/concepts/` | A cross-cutting idea used in multiple places (idempotency, rate-limiting, etc.) |
| `customer` | `wiki/customers/` | What the team knows about a specific customer |
| `person` | `wiki/people/` | Who on the team knows what |
| `project` | `wiki/projects/` | An in-flight piece of work; retires when done |

A page MUST belong to exactly one type. If something doesn't fit, propose a new type via TEAM.md PR rather than forcing it.

### Page frontmatter

Every wiki page MUST have this frontmatter:

```yaml
---
type: service                  # one of the entity types above
name: payments                 # canonical slug, matches filename
owners: [alice, bob]           # wiki owners (not code owners); at least one
status: active                 # active | deprecated | archived
updated: 2026-04-18            # date of last meaningful edit
confidence: high               # high | medium | low
sources:                       # raw files this page derives from; required, non-empty
  - raw/checkpoints/2026/04/15/def456a-add-retries.md
  - raw/incidents/2026/INC-0038-stripe-timeout.md
related: []                    # paths to related wiki pages
supersedes: []                 # wiki pages this one replaces
superseded_by: null            # set when a newer page replaces this one
---
```

Rules:
- `sources` must be non-empty. A wiki page with no sources is not a synthesis — it's an opinion, and opinions don't belong here.
- `confidence: low` is acceptable and preferred over omitting the page. A low-confidence page with clear "open questions" is more valuable than no page at all.
- `status: deprecated` pages stay in the repo. They are retrieved less (the reranker downweights them via context), but they're the historical record.

### Body templates

Each entity type has a required body template. An agent creating or updating a page MUST preserve the section headers, even if a section is currently empty (use "None known." or similar).

#### Service page template

```markdown
# {Service Name}

## Summary
One paragraph. What this service does, who depends on it, what it depends on.

## Key decisions
Bulleted list of links into wiki/decisions/. Most recent first.

## Architecture
Prose or diagrams. How it's built. What's in it.

## Gotchas
Things that have bitten the team. Each item cites an incident or checkpoint.

## Runbooks
Links into wiki/runbooks/.

## Open questions
Gaps, contradictions surfaced by the lint pass, things the team knows it doesn't know.
```

#### Decision page template

```markdown
# {Decision Title}

## Status
{Active | Superseded by ... | Deprecated}

## Context
What was the situation that required a decision.

## Decision
What was decided, in one paragraph.

## Consequences
What this makes easier, harder, or constrains going forward.

## Open questions
Things left unresolved.
```

#### Runbook template

```markdown
# {Runbook Title}

## When to use this
The symptom or trigger.

## Steps
Numbered, unambiguous. Each step has an expected outcome.

## If this doesn't work
Escalation path.

## Related
Links to incidents this runbook was derived from.
```

(Templates for module, concept, customer, person, project follow similar patterns. Agents should read the existing pages of the same type and match their structure when creating new ones.)

## Ingestion and synthesis pipeline

### Tier 0: capture (no review)

Every source event that matches a configured connector produces a raw file on a branch, which becomes part of a PR (see below). No direct-to-main commits.

### Tier 1: impact classification

A cheap classifier decides whether a new raw file warrants wiki synthesis. Triggers include (but are not limited to):

- Path-based: changes to `ARCHITECTURE.md`, `ADR/*`, `*.proto`, `openapi.yml`, `migrations/*`, `terraform/*`, top-level dependency manifests.
- Semantic: commit messages or raw content containing "decision," "deprecate," "breaking," "incident," "migration," "postmortem."
- Structural: new service directory, new public API endpoint, schema change.
- Volume: N or more raw entries touching the same service/module within 7 days with no wiki update in that window.

If Tier 1 fires, Tier 2 runs in the same PR. If not, the PR contains only the raw file (and may be batched with other no-trigger raws for pull-based ingestion).

### Tier 2: synthesis

The synthesis agent:
1. Reads the new raw file.
2. Reads the existing wiki pages the raw plausibly affects (scoped via qmd).
3. Proposes edits: update service pages, add gotchas, create new decision pages, etc.
4. Commits edits to the same branch as the raw file.

Synthesis rules:
- Every claim in the wiki diff must be traceable to a source listed in the page's frontmatter `sources`.
- If the synthesis contradicts an existing wiki claim, the PR description MUST declare the contradiction and either (a) supersede the old claim (updating frontmatter accordingly) or (b) mark the new claim as `confidence: low` and add it to the target page's Open Questions section.
- Synthesis never removes a cited claim without explicitly marking what replaced it. Silent deletion of cited content auto-fails adversarial review.

### Tier 2.5: adversarial checks

Every PR that touches `wiki/` runs these checks:

1. **Citation faithfulness.** For each new or modified claim, fetch the cited source and verify it supports the claim. Paraphrase drift is a fail.
2. **Contradiction scan.** Compare new claims against the rest of the wiki. Undeclared contradictions are a fail.
3. **Recent-raw scan.** Compare new claims against raw from the last 90 days. Contradictions with recent raw that the PR doesn't address are a fail.
4. **Scope check.** Verify that edited pages are plausibly connected to the triggering raw. Edits far from the triggering content are a fail.
5. **Provenance check.** Every new or modified page must have non-empty `sources`. Every substantive claim should be traceable to at least one. Unsourced claims are a fail.
6. **Human-edit guard.** If a section was edited by a human in the last 30 days, and the agent is reverting or substantively rewriting it, fail.

Check failures become the review agenda for the human reviewer, not just a gate.

### Tier 3: merge

- **Auto-merge** if all adversarial checks pass AND CODEOWNERS for every touched path allow auto-merge.
- **Human review** otherwise. The PR is assigned to the CODEOWNERS of the touched paths, with the failed checks attached as annotations.

Auto-merge areas are configured in CODEOWNERS with an `auto-merge: true|false` comment convention. High-stakes areas (payments, auth, customer data) should default to `auto-merge: false` regardless of check results.

## Agent behavior

### On session start

An agent working in a code repository that uses this team-context should:
1. Load `TEAM.md` (this file).
2. Identify the service(s) or module(s) relevant to the task.
3. Call `load_service_context({service})` (or the equivalent MCP tool) to pull:
   - The service's wiki page.
   - Related decision pages.
   - Recent checkpoints touching the service (last 14 days).
   - Open in-flight projects touching the service.
4. Proceed with the task, citing wiki pages in reasoning where relevant.

### When querying the wiki

Use qmd hybrid search (`qmd query` or the MCP equivalent). Do not attempt to list the wiki contents yourself and reason over the list — use the index.

When an answer is produced that would be useful to preserve, the agent should offer to file the answer back into the wiki (as a new page, or an appendix to an existing one). The user accepts or declines; if accepted, the answer enters the normal Tier 2 flow.

### When modifying the wiki

Agents never commit directly to main. All wiki changes go through PRs. An agent editing the wiki:
1. Makes the change on a branch.
2. Updates frontmatter (`sources`, `updated`, `confidence`, `supersedes` as needed).
3. Writes a PR description that:
   - States the triggering raw.
   - Lists affected pages.
   - Declares any contradictions with existing content.
   - Declares any supersessions.
4. Opens the PR. The Tier 2.5 pipeline takes over.

### Things agents must not do

- Create wiki pages with empty `sources`.
- Paraphrase cited content in a way that changes its meaning.
- Delete cited claims without marking the supersession.
- Modify `raw/` files (beyond the initial creation, which is done by connectors, not agents).
- Bypass the PR flow, even for "small" changes.
- Invent entity types. If something doesn't fit, flag it, don't force it.

## Ingestion mechanisms

### Push (human-initiated)

- Slack reaction `:wiki:` on a message — ingests the thread.
- Slash command `/wiki-this {intent}` on a Slack thread — ingests the thread with the given intent as frontmatter.
- CLI `team-context note "{text}"` — creates an ambient note in `raw/notes/`.
- "Save this to wiki" affordance on agent-produced answers — files the answer as a draft wiki page.

Each push ingestion produces one PR.

### Pull (automatic)

Configured pulls include:
- Git commits → `raw/checkpoints/`, with agent session and intent.
- Designated Slack channels (`#incidents`, `#architecture-decisions`, `#postmortems`, `#oncall`) → `raw/slack/`, filtered for thread length and signal.
- Support tickets that involve engineering escalation → `raw/support/`.
- PagerDuty incidents after resolution → `raw/incidents/`.
- Designated Notion/Confluence trees (ADRs, runbooks, customer profiles) → `raw/{appropriate-directory}/`.

Pull ingestions that don't trip Tier 1 are batched into periodic PRs to keep PR volume sane. Pulls that do trip Tier 1 get individual PRs.

### Undo

`:wiki-undo:` reaction (or equivalent CLI command) within 5 minutes of ingestion removes the file. After the 5-minute window, removal requires a normal PR.

## Lifecycle rules

### Projects

Projects retire when complete. On retirement:
- If the project produced decisions, those are distilled into `wiki/decisions/` pages.
- If the project affected services, the affected service pages are updated.
- The project page is moved to `wiki/projects/archived/` with `status: archived` and `updated: {retirement date}`.

### Deprecated services

When a service is deprecated:
- `status: deprecated` in frontmatter.
- A `Deprecation` section is added to the page explaining when and why.
- `superseded_by` points at the replacement (service page, migration project, or decision page).
- The page stays in `wiki/services/` until it's fully removed from production, then moves to `wiki/services/archived/`.

### People pages

People pages are optional and opt-in. They should describe areas of expertise and historical context (what someone built, what they remember), not personal information. People pages are edited only by the person themselves or with their explicit consent.

## qmd configuration

The repo ships with `.qmd/config.yml` defining collections and contexts. Collections:

- `raw` — all of `raw/**/*.md`, contexted as "Immutable records — commits, Slack threads, incidents, meetings. Source of truth."
- `wiki` — all of `wiki/**/*.md`, contexted as "Synthesized, maintained knowledge. Derived from raw. Pages cite sources in frontmatter."
- `decisions` — `raw/decisions/**/*.md`, contexted as "Authoritative architectural decisions (ADRs, RFCs)."
- `incidents` — `raw/incidents/**/*.md`, contexted as "Postmortems. Canonical record of what went wrong and what was learned."

Agents should prefer `wiki` for "what does the team believe," `decisions` for "what was decided," `incidents` for "what has gone wrong," and `raw` when looking for evidence of something specific that happened.

## Changing this file

TEAM.md changes go through PR, reviewed by a named set of owners in CODEOWNERS. A change to TEAM.md that affects agent behavior (new entity type, frontmatter change, new rule) should include a migration plan for existing content.

---

*Last updated: {date}. Feedback and proposed changes: open a PR.*
