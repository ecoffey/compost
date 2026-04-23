# ENGINEERING.md

This file is the schema and operating manual for the engineering-context repository — the top layer of the knowledge hierarchy. It is read by agents (during federated queries that cross orgs or teams) and humans (to understand what engineering-wide knowledge looks like and how it's maintained).

**If you are an agent reading this:** follow the rules in this file exactly. You do not write at the engineering layer. The rules below tell you how to query engineering-layer content and how routing works across the full hierarchy.

**If you are a human:** this file evolves with engineering as a whole. Propose changes via PR, reviewed by engineering-layer owners. This is the broadest coordination surface in the system.

---

## What this repository is

The engineering-context. The top of the hierarchy; there is nothing above it. In a deployment with no org layer, it federates directly over team-context repos. In a deployment with an org layer, it federates over org-context repos, which federate over teams.

This repo is the thinnest content layer in the system. It exists to answer questions no individual org or team can answer alone and to host commitments that bind all of engineering.

Two content layers:

- `raw/` — Very thin. Promotion records from lower layers.
- `wiki/` — Engineering-wide decisions, concepts, glossary, the master service index, the engineering Tech Radar, cross-org architecture, the engineering-wide incident index, and (optionally) ways-of-working meta content.

If this file conflicts with anything at the org or team layer, ENGINEERING.md wins.

## Directory layout

```
engineering-context/
├── ENGINEERING.md              # this file
├── orgs/                       # manifests of orgs (or teams, if no org layer)
│   ├── infra-org.yaml
│   ├── product-org.yaml
│   └── ml-org.yaml
├── wiki/
│   ├── decisions/              # engineering-wide decisions
│   ├── concepts/               # engineering-wide concepts
│   ├── glossary.md             # company-wide canonical terms
│   ├── services.md             # master service index
│   ├── architecture/           # cross-org dependency graph + workflow diagrams
│   ├── incidents/              # engineering-wide incident index (cross-org/cross-team postmortems)
│   ├── radar/                  # engineering Tech Radar (descriptive + prescriptive)
│   ├── meta/                   # engineering-layer ways of working (optional)
│   ├── index.md
│   └── log.md
├── raw/
│   └── promotions/             # promotion records from org and team layers
├── .qmd/
└── .github/
    └── CODEOWNERS
```

## What lives at the engineering layer

- **Engineering-wide decisions.** Decisions that constrain every team: platform standards, security policies, compliance requirements, cross-cutting architectural commitments. Sparse by design.
- **Engineering-wide concepts and glossary.** Terms with one canonical meaning across all of engineering. If a term has different meanings in different orgs, it doesn't belong here — it belongs in the glossaries of each org.
- **The master service index.** Every service in the company, with owning team and link to that team's wiki page.
- **The engineering-wide Tech Radar.** Technologies that cross orgs or are standardized for the whole company. Languages, core platforms, CI/CD, observability, SSO, cross-cutting security tools.
- **Cross-org architecture.** Dependency graphs and workflow diagrams that span orgs.
- **Engineering-wide incident index.** Pointers to team postmortems for incidents that crossed orgs (or crossed teams in no-org-layer deployments). The most consequential incidents are usually the ones that span multiple owners; a single index lets engineering reason across them rather than per-team. The index lives here, not in raw, because it is curated (which incidents are engineering-wide is editorial) and citation-bearing (each entry points to one or more team postmortems as primary sources).
- **Org (or team) directory.** Manifests of which orgs or teams exist and where their repos are.
- **Engineering-layer meta (optional).** Ways-of-working notes for how engineering-layer owners make decisions, how the Radar is curated, etc. Thin and bounded.

## What does NOT live at the engineering layer

- **Anything that's really org-specific.** If only one org cares, it's org-scope or team-scope content, not engineering.
- **Service details.** Master service index is pointers only.
- **Narrative content that duplicates what's in org or team wikis.** The engineering layer doesn't summarize lower layers; it hosts things that are intrinsically engineering-wide.
- **Commit checkpoints, Slack threads, postmortems.** All raw of that kind is team-layer.
- **Projects.** Projects belong to teams.
- **People pages.** People belong to teams.

**Anti-pattern restated:** the engineering layer is a derivation engine and a thin curation surface. If `wiki/` is growing a 40-page architecture document, the content should be restructured: a derived service dependency graph + a few narrative workflow diagrams + service pages pushed back to team wikis.

## Entity types

| Type | Directory | Purpose |
|---|---|---|
| `decision` | `wiki/decisions/` | An engineering-wide decision |
| `concept` | `wiki/concepts/` | A cross-cutting idea used across all of engineering |
| `workflow` | `wiki/architecture/` | Cross-org narrative describing an end-to-end process |
| `radar-entry` | `wiki/radar/prescriptive/` | A prescriptive engineering Tech Radar placement (authored) |
| `service-index-entry` | `wiki/services.md` | Pointer to a team service page (derived) |
| `incident-index-entry` | `wiki/incidents/` | Pointer to one or more team postmortems for an incident that crossed orgs or teams |
| `meta` | `wiki/meta/` | Ways-of-working notes for engineering-layer operations |

New entity types require an ENGINEERING.md update via PR, reviewed by engineering-layer owners.

## Frontmatter

```yaml
---
type: decision
name: aws-as-cloud-platform
owners: [architecture-group]
status: active
updated: 2026-04-18
confidence: high
sources:                        # cites org decisions, team decisions, research records, or external refs
  - orgs/infra-org/wiki/decisions/0007-cloud-platform.md
  - raw/promotions/2025-09-10-aws-standardization.md
related: []
supersedes: []
superseded_by: null
technology: [aws]               # for the Radar
---
```

Rules:
- `sources` is required and non-empty. Engineering decisions cite org decisions or team evidence that informed them.
- `owners` must include at least one named engineering-layer owner or the name of the engineering-layer owner group (e.g., `architecture-group`).
- Pages with no owner are a lint failure.

## Body templates

### Decision

```markdown
# {Decision Title}

## Status
{Active | Superseded by ... | Deprecated}

## Context
What situation required an engineering-wide decision.

## Decision
What was decided.

## Scope
All of engineering, unless explicit exceptions (listed).

## Consequences
What this makes easier, harder, or constrains going forward.

## Compliance and audit
If this decision relates to regulatory or security compliance, cite the specific requirement.

## Open questions
Unresolved aspects.
```

### Concept

```markdown
# {Concept Name}

## Definition
The engineering-wide canonical meaning.

## Why this definition
Why this framing across all of engineering; what confusions it resolves.

## Usage
Pointers to places (org or team wikis) where this concept is applied.
```

### Workflow

```markdown
# {Workflow Name}

## Summary
One paragraph description of the end-to-end flow.

## Steps
Numbered. Each step cites the specific team service(s) and, if relevant, org.

## Failure modes
What goes wrong and where.

## Related incidents
Pointers to cross-org postmortems that stressed this flow.
```

### Incident index entry

```markdown
# {Incident Title}

## Summary
One paragraph describing what happened, scope, and customer/business impact.

## Owning postmortems
Links to the team postmortem(s) in `raw/incidents/` of each team that participated. The team postmortems remain the source of truth for facts and timelines; this entry does not duplicate them.

## Cross-org or cross-team scope
Which orgs or teams were involved and what each owned during the incident.

## Engineering-wide lessons
Patterns or decisions that fall out of this incident at engineering scope (e.g., a missing standard, a cross-cutting runbook gap). Cite related engineering decisions or workflow narratives.

## Status
{Open lessons | All lessons addressed | Superseded by INC-XXXX}
```

### Radar entry (prescriptive)

```markdown
# {Technology Name}

## Ring
{Adopt | Trial | Assess | Hold}

## Quadrant
{Techniques | Tools | Platforms | Languages & Frameworks}

## Rationale
Why engineering is moving toward or away from this technology.

## Timeline
When we expect teams to be on / off this.

## Relationship to org Radars
If specific orgs disagree, note the disagreement and any reconciliation plan.
```

## Authorship model

**Synthesis agents:** not permitted at the engineering layer.

**Derivation bots:** allowed for specific paths.
- `services-bot` owns `wiki/services.md`.
- `radar-bot` owns `wiki/radar/descriptive.md`.
- `dependency-graph-bot` owns `wiki/architecture/dependencies.md`.

Each runs on schedule, produces PRs, auto-merges after validation. Bots never touch paths outside their ownership.

**Humans:** engineering-layer owners author decisions, prescriptive Radar entries, glossary, concepts, workflow narratives, and `meta/` content. All writes go through PR with CODEOWNERS review.

## Engineering-layer owners

A small group of humans responsible for engineering-layer curation. Responsibilities:

- Author engineering-wide decisions.
- Maintain the prescriptive engineering Radar.
- Write and maintain cross-org workflow diagrams.
- Adjudicate promotion PRs from the org layer (and from teams in no-org-layer deployments).
- Tag technologies with their Radar quadrant.
- Own ENGINEERING.md changes.
- Review and resolve prescriptive Radar conflicts across orgs.

Staffing models (pick what fits the company's culture):

- **Standing architecture group.** A named team whose remit includes engineering-layer curation. If they also have team-shaped work (services they own, projects they run), those belong in a team-context repo separate from this one.
- **Rotating tech leads.** 2-4 senior engineers from different orgs, rotating every 6-12 months.
- **Hybrid.** Small standing team plus rotating representation from orgs.

The mechanics of this spec don't require a specific model. They require that the role exists, is staffed, and is named in CODEOWNERS. The engineering layer decays without this.

## Federation

The MCP server treats the engineering layer as the top of the routing tree:

- Questions about services → consult the master service index, route to owning team's wiki.
- Questions about technology adoption → consult the engineering Radar; if the question is org-specific, route to the relevant org Radar.
- Questions about cross-cutting concerns (security, compliance, platform standards) → engineering decisions.
- Questions about cross-org workflows → engineering architecture narratives.

The engineering layer fans out to orgs (or teams, if no org layer) in parallel for queries that span the org/team boundary.

## Promotion from org to engineering

When an org decision is promoted to engineering scope (via `engineering_scope: true` in the org decision's frontmatter):

1. A promotion record is auto-created in `raw/promotions/`.
2. Engineering-layer owners evaluate: is this genuinely engineering-wide, or is it still really just this org's concern?
3. If yes to engineering-wide: owners author an engineering decision citing the org decision as primary source.
4. If no: the promotion is declined with a note in the log.

Engineering promotions are rarer than org promotions — most decisions don't need to bind all of engineering, and over-promoting dilutes the signal that engineering-wide decisions carry.

## Tech Radar composition

The engineering Radar is authoritative for technologies that appear on it. Org Radars can:

- Add technologies that only their org cares about.
- Add context ("we use Python for X workload in ML specifically").
- Use prescriptive to push for change.

They cannot contradict engineering ring placements. A prescriptive org entry wanting to Hold something engineering has Adopted surfaces as a declared conflict and is an input to engineering-layer owners' periodic Radar review. Such conflicts are informative — they're often where a shift is about to happen.

See §6.3 of the main spec for the full composition rules.

## qmd configuration

Collections at the engineering layer:

- `engineering-wiki` — `wiki/**/*.md`, contexted as "Engineering-wide knowledge: decisions, concepts, architecture, Radar, services index."
- `engineering-raw` — `raw/**/*.md`, contexted as "Engineering-layer raw: promotion records and cross-org incident index."

Lower-layer repos are federated over, not merged into this index.

## Agent behavior

Agents do not load ENGINEERING.md at session start by default. They load their team's TEAM.md. Engineering content is queried on demand when a question escalates to engineering scope (unrecognized service, technology question, cross-org workflow).

When loaded, agents read engineering content with higher priority than org or team content on conflicts — engineering wins.

Agents never write to the engineering wiki. If an agent concludes that an engineering-level change is needed, it should surface that to the human (e.g., "this task suggests we may need an engineering-wide decision on X; recommend raising this with engineering-layer owners").

## Changing this file

ENGINEERING.md changes go through PR, reviewed by engineering-layer owners named in CODEOWNERS. Changes affecting agent behavior (new entity type, frontmatter change, new rule) require a migration plan for existing engineering-layer content and a notice to all org-layer and team-layer owners (since changes here cascade down).

---

*Last updated: {date}. Feedback and proposed changes: open a PR.*
