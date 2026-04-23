# ORG.md

This file is the schema and operating manual for this org-context repository. It is read by both agents (at session start, when operating in federated queries that touch this org) and humans (to understand how the org layer works and what belongs here).

**If you are an agent reading this:** follow the rules in this file exactly. You do not write at the org layer directly. The rules below tell you how to query org-layer content and route federated queries.

**If you are a human:** this file evolves with the org. Propose changes via PR. The file describes the current contract between the org, its teams, and the engineering layer above.

---

## What this repository is

The org-context for {org name}. It is one of several org-context repositories that together compose the engineering-context layer above, and above the team-context repositories of teams within this org.

This repo is a thin coordination layer. It does not own detailed knowledge. It federates teams, hosts org-scope decisions and concepts, and hosts org-specific derived artifacts (Tech Radar, service index, dependency graph) scoped to domains the rest of engineering does not need visibility into.

Two content layers:

- `raw/` — Thin. Promotion events (when a team's content moves to org scope) and the org-scoped cross-team incident index. No commit checkpoints, Slack threads, etc. — that's team-layer concern.
- `wiki/` — Org-scope decisions, concepts, glossary, services index, Radar, architecture. All authored by humans (org-layer owners) or derived by bots (see §authorship).

If this file's rules conflict with ENGINEERING.md at the layer above, ENGINEERING.md wins.

## Directory layout

```
org-context/
├── ORG.md                      # this file
├── teams/                      # one manifest per team in this org
│   ├── payments-team.yaml
│   └── identity-team.yaml
├── wiki/
│   ├── decisions/              # org-scope decisions
│   ├── concepts/               # org-scope concepts
│   ├── glossary.md
│   ├── incidents/              # cross-team incident index (pointers to team postmortems)
│   ├── services.md             # service index for this org
│   ├── radar/                  # org Tech Radar (descriptive + prescriptive)
│   ├── architecture/           # org-scoped dependency graph and workflow diagrams
│   ├── index.md                # catalog of all org-layer pages
│   └── log.md                  # chronological event log
├── raw/
│   └── promotions/             # record of team→org promotion events
├── .qmd/
└── .github/
    └── CODEOWNERS
```

## What lives at the org layer

Things that belong here:

- **Cross-team decisions.** Decisions binding multiple teams within the org (this org standardizes on Kafka for internal messaging, this org uses the same observability stack).
- **Cross-team concepts.** Terms with one canonical meaning across the org's teams (what "P0" means here, the on-call escalation policy, the security review process within this org).
- **Team directory.** Manifests of which teams exist, their team-context repo URLs, their services, their CODEOWNERS.
- **Org-scope glossary.** Terms that mean the same thing across this org's teams but may mean something different elsewhere in engineering.
- **Cross-team incident index.** Pointers to team-layer postmortems for incidents that spanned multiple teams in this org.
- **Org-specific derived artifacts.** Service index, Tech Radar (both descriptive and prescriptive), dependency graph — each scoped to services and decisions within this org. These are layered on top of, not instead of, the engineering-wide versions.
- **Org-scoped workflow diagrams.** Cross-team flows internal to the org.

## What does NOT live at the org layer

- **Service details.** Those live in the owning team's wiki. This repo's service index just points.
- **Project pages.** Projects belong to teams.
- **People pages.** People belong to teams.
- **Team-specific runbooks, decisions, concepts.** Team-local.
- **Commit checkpoints, Slack threads, meeting transcripts.** Team-layer raw. Nothing at the org layer ingests these.

**Anti-pattern:** a central team writing narrative content at the org layer that duplicates or summarizes team content. If `wiki/` is growing prose unrelated to org-specific derived artifacts or org-scope decisions, something is miscategorized. Move it back to the owning team.

## Entity types

Wiki pages at the org layer are one of:

| Type | Directory | Purpose |
|---|---|---|
| `decision` | `wiki/decisions/` | An org-scope architectural or process decision |
| `concept` | `wiki/concepts/` | A cross-cutting idea used across this org's teams |
| `workflow` | `wiki/architecture/` | A narrative describing a cross-team process within this org |
| `radar-entry` | `wiki/radar/prescriptive/` | A prescriptive Tech Radar placement (authored) |
| `service-index-entry` | `wiki/services.md` (single file) | A pointer to a team's service page (derived) |
| `incident-index-entry` | `wiki/incidents/` | A pointer to a team postmortem |

New entity types require an ORG.md update via PR.

## Frontmatter

Every wiki page has structured YAML frontmatter:

```yaml
---
type: decision                  # one of the entity types above
name: kafka-for-internal-messaging
owners: [alice-tl, bob-tl]      # org-layer owners
status: active                  # active | deprecated | archived
updated: 2026-04-18
confidence: high                # high | medium | low
sources:                        # cites team wikis, other org decisions, or research records
  - orgs/payments-team/wiki/decisions/0032-kafka-migration.md
  - orgs/identity-team/wiki/decisions/0018-event-bus-choice.md
  - raw/promotions/2026-03-15-payments-kafka.md
related: []
supersedes: []
superseded_by: null

# Fields for engineering-layer derivation:
engineering_scope: false        # promote to engineering layer?
technology: [kafka]             # for the Radar
---
```

Rules:
- `sources` is required and non-empty. Org decisions cite the team evidence that informed them (promotion records and team decision pages).
- `owners` must include at least one named org-layer owner.
- Pages with no human owner are a lint failure.

## Body templates

### Decision

```markdown
# {Decision Title}

## Status
{Active | Superseded by ... | Deprecated}

## Context
What situation required this decision at org scope.

## Decision
What was decided.

## Scope
Which teams this applies to, any exceptions.

## Consequences
What this makes easier, harder, or constrains going forward.

## Open questions
Unresolved aspects.
```

### Concept

```markdown
# {Concept Name}

## Definition
The org's canonical meaning.

## Why this definition
Why we chose this framing; what confusions it resolves.

## Usage across teams
Pointers to team wikis where this concept is applied in specific contexts.

## Glossary cross-references
Related terms.
```

### Workflow

```markdown
# {Workflow Name}

## Summary
One paragraph description of the end-to-end flow.

## Steps
Numbered. Each step cites the specific team service(s) involved.

## Failure modes
What goes wrong and where.

## Related incidents
Pointers to cross-team incidents that stressed or exposed this flow.
```

### Radar entry (prescriptive)

```markdown
# {Technology Name}

## Ring
{Adopt | Trial | Assess | Hold}

## Quadrant
{Techniques | Tools | Platforms | Languages & Frameworks}

## Rationale
Why the org is moving toward or away from this technology.

## Timeline
When we expect teams to be on / off this.

## Relationship to engineering Radar
Whether this conflicts with, extends, or aligns with the engineering-wide Radar.
```

## Authorship model

Three modes, different permissions at this layer:

**Synthesis agents:** not permitted at the org layer. No agent synthesizes content into the org wiki. Agents can read org content and use it in reasoning.

**Derivation bots:** allowed for specific paths only.
- `services-bot` owns `wiki/services.md`.
- `radar-bot` owns `wiki/radar/descriptive.md`.
- `dependency-graph-bot` owns `wiki/architecture/dependencies.md`.

Each bot runs on schedule (nightly), produces a PR, and auto-merges after cheap validation. Bots never touch paths outside their ownership.

**Humans:** can write anywhere at this layer, subject to CODEOWNERS. Org-layer owners (see below) are the primary authors of decisions, prescriptive Radar entries, glossary, concepts, and workflow narratives.

## Org-layer owners

The org layer requires named humans to own its curation. Their responsibilities:

- Author org-scope decisions.
- Maintain the prescriptive Radar for this org.
- Write and maintain org-scoped workflow diagrams.
- Adjudicate team→org promotion and demotion PRs.
- Tag technologies with their Radar quadrant.
- Own ORG.md changes.

Typical staffing: 2-4 tech leads from teams within the org, rotating every 6-12 months. Alternatively, a named architecture group for the org. Pick what fits the org's culture, but the role must be staffed. Under-staffing this role is the most common reason org-layer content decays.

CODEOWNERS for this repo should reflect who the current org-layer owners are.

## Federation

The MCP server federates queries across layers. At the org layer:

- Queries received from agents in teams within this org consult `teams/*.yaml` manifests to identify relevant peer teams.
- Queries are fanned out to team qmd indexes in parallel, scoped to the service index's declared ownership.
- Results are returned with per-team citations preserved.

Cross-org queries go through the engineering layer, which federates over orgs. Orgs do not directly query each other.

## Promotion from teams

When a team marks a decision `org_scope: true`:

1. A promotion record is auto-created in `raw/promotions/YYYY-MM-DD-{team}-{decision-slug}.md` pointing back at the team wiki page.
2. The record appears in the org's `log.md` as a "recently promoted" entry.
3. Tier 1 classification runs at the org layer: does this promotion warrant an authored org-layer decision page, or is the team decision sufficient as-is?
4. If yes, an org-layer owner writes (or proposes) the org decision page. The org page cites the team decision as its primary source.
5. If a team's decision directly contradicts an existing org-scope decision, the promotion is flagged for owner review before acceptance.

## Promotion to engineering

Org decisions can be promoted to engineering scope by setting `engineering_scope: true` in frontmatter. The same model applies one level up: the engineering-layer owners decide whether to author an engineering-wide decision, or whether the org decision is sufficient scope.

## Demotion

If an org decision is no longer appropriate at org scope (e.g., the context has narrowed and it's really just one team's concern now), it can be demoted:

1. Open a demotion PR updating frontmatter: `status: deprecated`, add `Deprecation` section explaining.
2. If another team still relies on the decision, they're tagged for review.
3. After merge, the relevant team wiki may want to absorb the decision content (which is now just their team's decision).

Demotion is rare and should be explicit, not inferred.

## qmd configuration

`.qmd/config.yml` collections at the org layer:

- `org-wiki` — `wiki/**/*.md`, contexted as "Synthesized org-scope knowledge: decisions, concepts, architecture, Radar."
- `org-raw` — `raw/**/*.md`, contexted as "Org-layer raw: promotion records and cross-team incident index."

The team-layer repos' qmd indexes are consulted separately via federation; they are not merged into this repo's index.

## Agent behavior

When an agent operates at the org layer (typically during federated queries rather than session-start context loads):

- Read org decisions, concepts, and glossary when relevant.
- Use the service index to route service-specific questions.
- Use the Radar when answering technology questions.
- Do not attempt to write to the org wiki.
- For questions the org layer can't answer, escalate to engineering via the MCP server's routing.

Agents do not load ORG.md at session start by default. They load their team's TEAM.md; ORG.md is loaded only if a query escalates to org scope.

## Changing this file

ORG.md changes go through PR, reviewed by the org-layer owners named in CODEOWNERS. Changes that affect agent behavior (new entity type, frontmatter change, new rule) should include a migration plan for existing content.

---

*Last updated: {date}. Feedback and proposed changes: open a PR.*
