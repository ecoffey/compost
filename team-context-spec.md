# Team-Context: A Spec for Engineering-Scale LLM Knowledge Infrastructure

> *Status: Draft. This document describes a design, not an implementation. It is intended as the basis for a prototype, not a ready-to-deploy system.*

## 1. Motivation

Current LLM coding tools (Claude Code, Cursor, Codex, etc.) optimize the solo developer loop. They make individual work faster by compressing the distance between intent and code. A team using these tools gets N solo loops running in parallel — which is an improvement over no LLMs, but it is not a team-scale improvement. Every developer's agent starts from zero context about the team every session. Lessons are learned in isolation. Dead ends are rediscovered. Architectural reasoning captured in one agent session evaporates when that session ends.

The underlying problem: knowledge produced during agentic work is not accumulating into a shared artifact the team can query. The commit captures what changed; the PR description captures some of why; but the reasoning trace — the dead ends, the alternatives considered, the "why not X" — lives in ephemeral chat history that no one else ever sees.

Solving this requires two things the existing tools don't provide together:

1. **Persistent capture** of the (change + reasoning + outcome) tuple for every unit of work.
2. **A compounding, maintained knowledge base** derived from that capture, queryable by both humans and future agents.

Solving it *at engineering scale* — across an organization of dozens or hundreds of teams — requires a third thing:

3. **Federation and derivation**: team-level knowledge that composes into higher-level views (org architecture, tech radar, service ownership) without the higher-level views becoming separate artifacts that have to be maintained.

This spec describes an architecture that provides all three. It draws on several existing patterns:

- *Karpathy's LLM Wiki pattern:* an LLM-maintained markdown wiki that sits between raw sources and queries, built incrementally as sources arrive rather than re-derived on each query.
- *Entire's checkpoint model:* every commit gets paired with the agent session that produced it, making reasoning traces a first-class artifact linked to git history.
- *qmd (or equivalent):* local hybrid search (BM25 + vector + rerank) with MCP integration, so agents can scope deterministically before reasoning.
- *Thoughtworks' Tech Radar:* adopted here as a worked example of how higher-layer artifacts can be derived from lower-layer decisions rather than maintained separately.

## 2. Goals and non-goals

### Goals

- **Make team knowledge compound.** Every piece of work contributes to a shared, persistent artifact that every team member and every future agent can query.
- **Give every agent the team's accumulated context at session start**, automatically, not as a manual load step.
- **Preserve provenance.** Every claim in the knowledge base traces back to a source (commit, thread, document). Claims without sources are not permitted.
- **Keep humans in the loop where it matters.** Synthesis that could silently drift is reviewed; synthesis that's mechanically safe auto-merges.
- **Scale to engineering-org size** by federating team wikis rather than centralizing content.
- **Make higher-level artifacts derived, not maintained.** Architecture diagrams, Tech Radars, service indexes fall out of lower-layer decisions rather than being separately produced.
- **Run locally.** Nothing in this design requires a vendor-hosted vector DB or an external knowledge service. Data stays with the team.

### Non-goals

- **Replacing coordination tools.** This is not Linear, Graphite, or Slack. It does not handle "who is working on what right now" or task assignment.
- **Replacing the canonical source.** The code repo is still the canonical record of code. Notion/Confluence is still the canonical record for product docs. This system derives from those; it does not replace them.
- **Serving as a customer-facing knowledge base.** Everything here assumes internal team use. Customer-facing documentation has different provenance, review, and tone requirements.
- **Solving access control for highly sensitive content.** The design assumes raw sources can be freely read within the team or org boundary. Teams handling regulated content need additional controls this spec does not address.

## 3. Architecture overview

The system has three conceptual layers, deployed as one, two, or three repository layers depending on company size:

**Team layer.** Every team has a `team-context` repo. Owns services, decisions, runbooks, concepts, customers, people, projects specific to the team. This is where raw ingestion and synthesis happen. The team layer exists in every deployment.

**Org layer (optional).** For companies large enough to have multiple engineering orgs (Infra, Product, ML, etc.), an `org-context` repo per org federates the teams beneath it and holds org-scope decisions, concepts, and org-specific derived artifacts (service index, Tech Radar, dependency graph — see §6.3). Skipped in smaller companies.

**Engineering layer.** An `engineering-context` repo at the top, federating across all orgs (or all teams, if no org layer exists). Holds company-wide concerns: the engineering-wide Tech Radar, the engineering-wide glossary, the master service index, cross-team architecture. Always exists.

### Deployment shapes

- **Default** (one engineering group): `team-context` repos + one `engineering-context`. No org layer.
- **Large company** (multiple engineering orgs): `team-context` repos + `org-context` repos + one `engineering-context`.

The federation, promotion, and derivation mechanics work identically at each layer; only the content and scope differ. The spec describes each layer's role; adapting to your shape means deciding whether the org layer exists.

### The content layers within each repo

Within any team/org/engineering repo, the content is organized the same way:

- **Raw (`raw/`)** — Immutable records. At the team layer, this is where commit checkpoints, Slack threads, incident postmortems, meeting transcripts, etc. land. At higher layers, raw is thinner — mostly promotion events and cross-team incident records.
- **Wiki (`wiki/`)** — Synthesized, LLM-maintained markdown pages (team layer) or human- and bot-authored pages (higher layers) organized by entity type. Every page cites raw sources where claims originate.
- **Schema (`TEAM.md` / `ORG.md` / `ENGINEERING.md`)** — The operating manual for that layer. Declares entity types, frontmatter schema, body templates, ingestion rules, synthesis rules, agent behavior. Read by agents at session start; evolved via PR.

### Search and access

**Search (`qmd` or equivalent)** — Local hybrid retrieval. Each repo has its own search index. Federated queries hit multiple indexes in parallel.

**Access (MCP server)** — Team-shaped tools layered on top of qmd: `load_service_context`, `find_prior_art`, `record_decision`, `query_wiki`, plus cross-layer federation tools (`find_service_owner`, `query_across_org`). Every developer's agent talks to one MCP server that handles routing across layers.

### Visual summary

```
┌──────────────────────────────────────────────────────────────┐
│                    Engineering Layer                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐   │
│  │ Eng-wide    │  │ Master      │  │ Engineering-wide    │   │
│  │ Tech Radar  │  │ Service     │  │ decisions, concepts,│   │
│  │ (derived)   │  │ Index       │  │ workflow diagrams   │   │
│  └─────────────┘  └─────────────┘  └─────────────────────┘   │
└────────────────────────────┬─────────────────────────────────┘
                             │ federation
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
   ┌───────────────┐  ┌───────────────┐  ┌───────────────┐
   │ Org: Infra    │  │ Org: Product  │  │ Org: ML       │
   │ Org Radar,    │  │ Org Radar,    │  │ Org Radar,    │
   │ org services, │  │ org services, │  │ org services, │
   │ org decisions │  │ org decisions │  │ org decisions │
   └───────┬───────┘  └───────┬───────┘  └───────┬───────┘
           │                  │                  │
      ┌────┴────┐         ┌───┴───┐          ┌───┴───┐
      ▼         ▼         ▼       ▼          ▼       ▼
    Team      Team      Team    Team       Team    Team
    wiki      wiki      wiki    wiki       wiki    wiki
      ▲         ▲
      │         │
      │ commits, slack, incidents, etc.
      ▼         ▼
     Raw ingestion
```

In small-company deployments, the middle "Org" row does not exist; the engineering layer federates directly over teams.

## 4. The team layer

This is the core of the system. The team layer is where knowledge is produced and captured. Higher layers derive from it.

### 4.1 The ingestion pipeline

All ingestion — from any source — flows through a four-tier pipeline:

**Tier 0: capture.** A source event (commit, Slack reaction, incident resolution) is materialized as a markdown file in the appropriate `raw/` subdirectory. No LLM is involved. This step is cheap, mechanical, and should not fail for content reasons.

**Tier 1: impact classification.** A cheap classifier — small model, rules, or hybrid — decides whether the new raw warrants wiki synthesis. Triggers include:

- *Path-based:* changes to `ARCHITECTURE.md`, `ADR/*`, `*.proto`, `openapi.yml`, `migrations/*`, `terraform/*`, dependency manifests.
- *Semantic:* commit messages or raw content containing "decision," "deprecate," "breaking," "incident," "migration," "postmortem."
- *Structural:* new service directory, new public API endpoint, schema change.
- *Volume:* N or more raw entries touching the same service/module within 7 days with no wiki update in that window.

Most raw entries do not trigger Tier 2. A bug fix, a refactor, a dependency bump — the commit is captured, but nothing in the wiki needs to change. Aggressive filtering at Tier 1 is critical because synthesis is the expensive step.

**Tier 2: synthesis.** When Tier 1 fires, a synthesis agent:

1. Reads the new raw file.
2. Reads existing wiki pages plausibly affected (scoped via qmd).
3. Proposes wiki changes: update existing pages, create new pages for entities not yet in the wiki, add gotchas, reconcile contradictions.
4. Commits edits to the same branch as the raw file.

Synthesis rules (enforced by adversarial checks):
- Every claim in the diff traces to a source listed in the page's frontmatter.
- Contradictions with existing wiki content are declared, not hidden.
- Cited claims are never silently deleted; supersessions are marked explicitly.

**Tier 2.5: adversarial checks.** Every PR that touches `wiki/` runs a battery of checks designed to find falsifying evidence, not to rubber-stamp the synthesis:

1. **Citation faithfulness.** For each new or modified claim, fetch the cited source and verify it supports the claim. Paraphrase drift fails.
2. **Contradiction scan.** Compare new claims against the rest of the wiki. Undeclared contradictions fail.
3. **Recent-raw scan.** Compare new claims against raw from the last 90 days. Undeclared contradictions with recent raw fail.
4. **Scope check.** Verify edited pages are plausibly connected to the triggering raw. Out-of-scope edits fail.
5. **Provenance check.** Every new/modified page has non-empty `sources`. Every substantive claim traces to at least one. Unsourced claims fail.
6. **Human-edit guard.** If a section was edited by a human in the last 30 days and the agent is reverting or substantively rewriting it, fail.

Check failures are attached to the PR as the review agenda for humans, not just as a gate.

**Tier 3: merge.**
- **Auto-merge** if all adversarial checks pass AND CODEOWNERS for all touched paths allow auto-merge.
- **Human review** otherwise.

Auto-merge is conservative by default. High-stakes areas (payments, auth, customer data) default to human review regardless of check results. The boundary tightens or loosens over time based on observed data.

### 4.2 The atomic PR model

Every ingestion — whether it triggers synthesis or not — flows through a single PR that contains:

1. The new raw file (or files, for batched low-signal ingests).
2. Conditionally, if Tier 1 fires: the wiki edits synthesized from the raw.

Both are merged atomically, or neither is.

**Why atomic:** if raw landed on main before synthesis ran, there would be a window where the raw is searchable but the wiki hasn't been updated yet. An agent doing a query during that window could pull the raw and form an answer that contradicts the wiki it's about to update. Atomic PRs eliminate this window.

**Batching:** pull-based ingests that don't trip Tier 1 are batched into periodic "raw: N ingests from {time} to {time}" PRs that auto-merge after cheap checks. Push-based ingests (human-initiated Slack reactions, slash commands) always get their own PR.

**Invariants:**
- No direct-to-main commits. Every change is a PR.
- Any synthesis is in the same PR as its triggering raw.
- Raw removal is cheap (normal `git rm` commit) and triggers reconciliation of any wiki pages citing the removed raw.

### 4.3 Physical structure

```
team-context/
├── TEAM.md                    # the schema
├── raw/                       # immutable records
│   ├── checkpoints/YYYY/MM/DD/{sha}-{slug}.md
│   ├── slack/YYYY/MM/YYYY-MM-DD-{channel}-{slug}.md
│   ├── incidents/YYYY/INC-NNNN-{slug}.md
│   ├── decisions/NNNN-{slug}.md
│   ├── meetings/YYYY/MM/YYYY-MM-DD-{slug}.md
│   ├── support/YYYY/MM/{ticket-id}-{slug}.md
│   └── notes/YYYY/MM/YYYY-MM-DD-{slug}.md
├── wiki/                      # synthesized pages
│   ├── services/
│   ├── modules/{service}/
│   ├── decisions/
│   ├── runbooks/
│   ├── concepts/
│   ├── customers/
│   ├── people/
│   ├── projects/
│   ├── glossary.md
│   ├── index.md
│   └── log.md
├── .qmd/                      # search config
└── .github/
    ├── CODEOWNERS
    └── workflows/
```

**Raw is organized by source and date, never by topic.** Topic is interpretive and belongs in the wiki. Date partitioning keeps leaf directories manageable.

**Wiki is organized by entity type.** Entity types are defined in TEAM.md; agents do not invent new types. Flat over nested: `wiki/services/payments.md` is better than `wiki/services/payments/index.md` unless genuinely needed.

**`index.md` and `log.md` are first-class.** `index.md` is the deterministic catalog the agent consults before reranking. `log.md` is the append-only timeline of synthesis events.

**Repository strategy.** Default: `raw/` and `wiki/` live in one repo. This is required for the atomic PR model. If the repo grows large, sparse checkout (`git sparse-checkout add wiki/`) gives developers a lightweight local clone while the synthesis and indexing machinery runs against the full repo. Separate repos only if access-control requirements force it. Submodules are not recommended — the failure modes outweigh the benefits.

### 4.4 Frontmatter

Every wiki page has structured YAML frontmatter:

```yaml
---
type: service
name: payments
owners: [alice, bob]           # wiki owners, not code owners; at least one
status: active                 # active | deprecated | archived
updated: 2026-04-18
confidence: high               # high | medium | low
sources:                       # raw files this page derives from; required, non-empty
  - raw/checkpoints/2026/04/15/def456a-add-retries.md
  - raw/incidents/2026/INC-0038-stripe-timeout.md
related: []
supersedes: []
superseded_by: null

# Fields that enable higher-layer derivation (all optional):
depends_on: [auth, notifications]       # for dependency graph
depended_on_by: [checkout, admin-api]   # for dependency graph
technology: [postgres, grpc]            # for Tech Radar
data_classifications: [pii, payment]    # for data flow maps
systems_of_record: [transactions]       # for system-of-record map
org_registered: true                    # publish to org/engineering service index
org_scope: false                        # promote decision to org layer
engineering_scope: false                # promote decision to engineering layer
---
```

Frontmatter is the deterministic layer agents read before any LLM reasoning. `sources` is what the citation-faithfulness check reads. `confidence` lets the reranker downweight uncertain pages. `supersedes`/`superseded_by` make contradictions explicit.

The higher-layer-derivation fields are optional — teams add them as they become useful — but once populated, they enable org-level and engineering-level artifacts without any additional authoring.

### 4.5 Body templates

Each entity type has a required body template. Example for a service page:

```markdown
# {Service Name}

## Summary
## Key decisions
## Architecture
## Gotchas
## Runbooks
## Open questions
```

The "Open questions" section is where the lint pass logs unresolved contradictions and where humans can see what the wiki knows it doesn't know.

### 4.6 Ingestion mechanisms

**Push (human-initiated).** Curation paid at the moment of realization. Near-zero friction is essential:

- Slack reaction `:wiki:` on any message → ingests the thread.
- Slash command `/wiki-this {intent}` → ingests with human-authored intent.
- CLI `team-context note "{text}"` → ambient "I just learned something" capture.
- Agent "save to wiki" affordance on useful answers.

Each push ingestion produces its own PR.

**Pull (automatic).** Curation paid at configuration time:

- Git commits (via Entire CLI or equivalent) → `raw/checkpoints/`.
- Designated Slack channels (`#incidents`, `#architecture-decisions`, `#postmortems`, `#oncall`) → `raw/slack/`, filtered for signal.
- Support tickets that escalated to engineering → `raw/support/`.
- PagerDuty incidents after resolution → `raw/incidents/`.
- Designated Notion/Confluence trees (ADRs, runbooks, customer profiles) → appropriate raw subdirectory.

Pull ingestions that don't trip Tier 1 are batched. Trips get individual PRs.

**What not to pull.** Email (mostly noise; use forward-to-address push instead). All Slack channels (signal-to-noise too low). All meetings (push only; the meeting owner decides if the transcript matters).

**Undo.** Any ingested raw can be removed within 5 minutes via `:wiki-undo:` or CLI. After the window, removal requires a normal PR. Removal triggers reconciliation in the next lint pass.

### 4.7 Agent behavior at the team layer

**On session start**, an agent working in a code repo linked to a team-context:

1. Loads `TEAM.md`.
2. Identifies the service(s) or module(s) relevant to the task.
3. Calls `load_service_context({service})` to pull the service's wiki page, related decisions, recent checkpoints, open projects.
4. Proceeds with the task, citing wiki pages where relevant.

**When querying**, agents use hybrid search rather than listing the wiki and reasoning over it. Reranking is the deterministic scoping layer.

**When producing useful answers**, the agent offers to file them back. If the user accepts, the answer enters the Tier 2 flow as a draft page.

**When modifying the wiki**, agents never commit directly. All changes go through PRs with updated frontmatter, stated triggering raw, declared contradictions, declared supersessions.

**Forbidden actions:** creating pages with empty `sources`, paraphrasing cited content in a way that changes meaning, deleting cited claims without marking supersession, modifying raw files, bypassing the PR flow, inventing entity types.

### 4.8 Lifecycle rules

**Projects** retire when complete. Decisions produced are distilled into `wiki/decisions/` pages; affected service pages are updated; the project page moves to `wiki/projects/archived/`.

**Deprecated services** keep their page with `status: deprecated` and a `Deprecation` section until fully removed from production, then move to `wiki/services/archived/`.

**People pages** are opt-in. They describe expertise and historical context, not personal information. Edited only by the person or with explicit consent. Agents do not write to people pages automatically.

**The lint pass** runs weekly: flags contradictions between wiki and recent raw, pages not updated despite high raw activity, orphan pages, low-confidence pages with no recent activity. Output is a single weekly "wiki health" PR.

## 5. The org layer (optional)

The org layer exists for companies large enough to have multiple engineering orgs. It federates teams beneath it, promotes team-level decisions to org scope when they set precedent for the org, and hosts org-specific derived artifacts (see §6.3).

### 5.1 What lives at the org layer

- **Cross-team decisions.** Decisions that bind multiple teams within the org (this org standardizes on Kafka for internal messaging, this org's teams all use the same observability stack).
- **Cross-team concepts.** Things that have one canonical meaning across the org (what "P0" means in this org, the on-call escalation policy, the security review process).
- **Team directory.** Which teams exist in the org, their wiki repo URLs, their CODEOWNERS.
- **Org-scope glossary.** Terms that mean the same thing across the org's teams.
- **Cross-team incident index.** Links to team postmortems for incidents that spanned multiple teams.
- **Org-specific derived artifacts.** Service index scoped to the org's services, org Tech Radar (both descriptive and prescriptive), org-scoped dependency graph. Covered in §6.3.

### 5.2 What does NOT live at the org layer

- Service *details*. Those live in the owning team's wiki; the org just points.
- Project pages. Projects belong to teams.
- People pages. People belong to teams.
- Team-specific runbooks, decisions, concepts.

**Anti-pattern to avoid:** a central team writing prose content at the org layer that duplicates what's in team wikis. If the org layer is growing narrative content unrelated to org-specific derived artifacts or org-specific decisions, something is miscategorized. Org-layer maintainers are curators, not authors of derivative narrative.

### 5.3 Physical structure

```
org-context/
├── ORG.md                      # org-level schema
├── teams/                      # one manifest per team
│   ├── payments-team.yaml
│   └── identity-team.yaml
├── wiki/
│   ├── decisions/              # org-scope decisions only
│   ├── concepts/               # org-scope concepts only
│   ├── glossary.md
│   ├── incidents/              # cross-team incident index
│   ├── services.md             # service index for this org
│   ├── radar/                  # org Tech Radar (descriptive + prescriptive)
│   ├── architecture/           # org-scoped dependency graph and diagrams
│   ├── index.md
│   └── log.md
├── raw/
│   └── promotions/             # record of team→org promotions
└── .github/
    └── CODEOWNERS
```

Raw at the org layer is thin. The main inflow is promotion events: when a team marks a decision `org_scope: true`, a small raw record is created at the org layer pointing back at the team wiki. This is enough for derivation jobs to work without duplicating content.

### 5.4 Federation

Federation happens at query time, not sync time. Team wikis remain the source of truth for team content.

An agent asking a question that crosses team boundaries:

1. The MCP server's `query_across_org` tool receives the question.
2. It consults the org-layer service index and concept glossary to identify which team wikis are likely relevant.
3. It queries those team wikis in parallel (via their qmd indexes).
4. It synthesizes the results, preserving per-team citations.

For "who owns service X" questions, the tool hits the service index directly and returns the team + link to their wiki page. No federation needed.

### 5.5 Promotion model

The promotion of team-layer content to org-layer scope uses a **hybrid model**:

**Usage threshold (descriptive).** Decisions and concepts aggregate into descriptive org-level views automatically when usage thresholds are crossed. No human gate — the views simply reflect reality.

**Self-promotion (individual).** A team marks a decision `org_scope: true` in frontmatter. It appears in the org-layer's "recently promoted" feed. Lightweight-but-visible: anyone can open a demotion PR if they disagree. This matches how precedent-setting actually works at most orgs.

**Owner approval (prescriptive).** For explicit top-down commitments ("the org has decided Y"), an org-layer owner writes a decision at the org layer directly (see §8.3 on authorship). This is governance-heavy because it's setting direction.

### 5.6 Cross-team activity

When a developer from team A works in team B's codebase:

1. The agent detects the code repo's team via a `.team-context-link` file pointing at team B's team-context repo.
2. It loads team B's TEAM.md and service pages — not team A's.
3. It loads the developer's team identity for authorship.

Commits route checkpoints to team B's raw (the owning team's). The author's team can optionally reference these in their own log but does not duplicate them.

## 6. The engineering layer

The engineering layer is the top of the hierarchy. It exists in every deployment. Its job is to answer the questions no individual org or team can answer alone.

### 6.1 What lives at the engineering layer

- **The engineering-wide Tech Radar.** Technologies that meaningfully cross orgs or are standardized for the whole company.
- **The master service index.** Every service across engineering, with owning team and link to team wiki.
- **Engineering-wide decisions.** Decisions that bind all of engineering (the company uses AWS, Postgres is the default OLTP store, SSO goes through Okta).
- **Engineering-wide concepts and glossary.** Terms with one canonical meaning across engineering.
- **Cross-org architecture.** High-level dependency graphs and workflow documents spanning multiple orgs.
- **Org (or team) directory.** Which orgs or teams exist and their context repos.

The engineering layer is thinnest of all. In a deployment with no org layer, it federates directly over teams and is the only layer above them. In a deployment with an org layer, it federates over orgs, which themselves federate over teams.

### 6.2 Physical structure

```
engineering-context/
├── ENGINEERING.md
├── orgs/                       # one manifest per org (or team, if no org layer)
│   ├── infra-org.yaml
│   ├── product-org.yaml
│   └── ml-org.yaml
├── wiki/
│   ├── decisions/              # engineering-wide decisions
│   ├── concepts/               # engineering-wide concepts
│   ├── glossary.md
│   ├── services.md             # master service index
│   ├── architecture/           # cross-org architecture docs
│   ├── radar/                  # engineering Tech Radar (descriptive + prescriptive)
│   ├── meta/                   # engineering-layer ways of working (optional)
│   ├── index.md
│   └── log.md
├── raw/
│   └── promotions/
└── .github/
    └── CODEOWNERS
```

### 6.3 Derived artifacts

This is where the "derive, don't maintain" principle pays off. Several artifacts are produced mechanically from lower-layer content.

**Derived artifacts can exist at multiple layers.** An artifact at a higher layer does not supersede the equivalent artifact at a lower layer; they *compose*. The ML Org legitimately has its own Radar, its own service index, its own dependency graph — they cover ML-specific concerns (JAX adoption, training-service dependencies) at a resolution that would be noise at the engineering layer. The engineering layer's equivalents cover what crosses orgs.

**Consistency rule.** When a given entity appears at multiple layers (e.g., Python is on both the ML Org Radar and the engineering Radar), the higher-layer placement is authoritative for that entity. Lower layers cannot disagree with higher-layer placements, only:
- Add entities the higher layer doesn't track (ML-specific tools only the ML Org cares about).
- Add context specific to their scope ("used for X workload in our case").
- Use the prescriptive Radar to push for a change.

Prescriptive conflict across layers is possible and informative — the ML Org's prescriptive wanting to move off TensorFlow while the engineering descriptive still shows it as Adopt is exactly the kind of tension worth surfacing. Such conflicts should be declared explicitly in the lower-layer Radar's frontmatter or body, not silently.

The artifacts described below all follow this pattern: they exist at the engineering layer by default, and additionally at the org layer when an org has domain-specific scope that justifies a local version.

#### 6.3.1 Service index

A map of every service to its owning team, maintained as a derivation from team wikis that have `org_registered: true` in their service page frontmatter.

- **Engineering-layer index:** all registered services across the company.
- **Org-layer index (optional):** services within a specific org, potentially with org-specific categorization (e.g., the ML Org's index might categorize services as "training," "inference," "feature store").

Provides:
- "Who owns service X?" — direct lookup.
- "What services depend on X?" — derivable from the dependency graph (next section).
- "What services handle PII?" — derivable from `data_classifications` frontmatter across teams.

Regenerated nightly from the underlying team wikis.

#### 6.3.2 Tech Radar

Tech Radars exist wherever the scope is meaningful — always at the engineering layer, and additionally at the org layer when an org has domain-specific technologies the rest of engineering doesn't care about (ML tooling being the clearest example).

Each Radar has two complementary views that are explicitly tracked separately.

**Descriptive Radar (auto-derived).** What's actually in use at that scope. Built nightly by aggregating `technology:` frontmatter and decision status from the relevant teams:

| Signal | Ring placement |
|--------|----------------|
| `status: active` + `{layer}_scope: true` + cited by multiple teams | **Adopt** |
| `status: active` + `{layer}_scope: true` + cited by one team | **Trial** |
| `status: exploring` or `confidence: low` | **Assess** |
| `status: deprecated` or supersedes a prior adoption | **Hold** |

Where `{layer}` is `org` for the org Radar and `engineering` for the engineering Radar.

Quadrant (Techniques, Tools, Platforms, Languages & Frameworks, or whatever taxonomy the engineering group prefers) is editorial — the layer's maintainers tag each technology with its quadrant once, and it's stable.

**Prescriptive Radar (human-maintained).** Where that layer's leadership wants teams to move. Written directly by layer owners as explicit decisions: "we want Rust in Trial for systems work," "we want to move off MongoDB." Uses the same ring/quadrant structure as the descriptive Radar but is authored, not derived.

**The divergence view.** The most valuable artifact of this split is the diff. A page titled "Tech Radar: Descriptive vs. Prescriptive" highlights where the two disagree:

- Technologies in descriptive-Adopt but prescriptive-Hold → migration energy needed.
- Technologies in prescriptive-Adopt but descriptive-Assess → adoption isn't happening; why?
- Technologies in prescriptive-Trial but descriptive-Adopt → success; formalize.

This is the Radar view that drives real conversation at leadership reviews. It's a computed artifact: it stays current because both sides stay current.

**Frontmatter conventions for the Radar.** Team-layer decision pages opt in by declaring:

```yaml
technology: [dynamodb, aws-lambda]
org_scope: true           # include in org Radar
engineering_scope: false  # don't include in engineering Radar
```

That's all the team has to do. The Radar-generation jobs at each layer handle the rest.

#### 6.3.3 Service dependency graph

The high-level architecture diagram, done right. Most orgs have this artifact produced manually, outdated within a quarter, and trusted by no one. Deriving it from team wikis solves that.

**Mechanism.** Every team's service page declares dependencies in frontmatter:

```yaml
depends_on: [auth-service, payments-api, notifications]
depended_on_by: [checkout, admin-ui]
```

Nightly jobs at each layer (org and engineering) union these declarations into directed graphs rendered as D2 or Mermaid:
- **Engineering-layer graph:** all services across the company, filterable by org or domain.
- **Org-layer graph (optional):** zoomed to the org's services, potentially with org-specific annotations (data contracts flowing between services, model artifacts produced, etc.).

**Queries enabled:**
- "Show me the blast radius of auth-service going down."
- "Show me services with no declared dependencies" (probably missing data).
- "Show me circular dependencies" (probably a design problem).
- "Show me the subgraph under the checkout service."

**Data quality loop.** Services with no declared dependencies show up in the lint pass as incomplete. Over time, teams close the gaps because the dependency graph becomes a useful artifact and being a gap in it is visible.

#### 6.3.4 Workflow and process diagrams

Cross-team flows — "what happens when a customer checks out," "what happens when an alert fires," "what happens in our deploy pipeline." These span teams, so no team's wiki can own them.

**Mechanism.** Each workflow is a human-authored narrative wiki page at the engineering layer (or org layer for org-internal flows). The narrative cites specific services in team wikis as step anchors. Mechanical freshness: the page's frontmatter lists referenced services; a lint job detects when a referenced service's wiki page has substantively changed and flags the workflow page for review. Narrative updates are human; freshness detection is automatic.

This is a semi-derived artifact: the narrative is authored, but its decay is mechanically detectable.

### 6.4 Engineering-layer decisions and concepts

Decisions and concepts at the engineering layer are authored by engineering-layer owners (for prescriptive top-down commitments) or promoted from lower layers via the descriptive path (usage threshold).

Engineering-wide decisions are sparse by design — most decisions are team-scope or org-scope. Engineering decisions are the ones that constrain everyone: platform standards, security policies, compliance requirements, cross-cutting architectural commitments.

### 6.5 The anti-pattern restated

At every layer above team, the temptation is to let the layer grow arbitrary narrative content. Resist this. The higher layers are derivation engines, thin curation surfaces, and hosts for scope-appropriate authored content (prescriptive decisions, workflow narratives, glossary). If the engineering wiki has a 40-page architecture document, that's a signal the content should be restructured as: a derived service dependency graph + a few narrative workflow diagrams + service pages in team wikis.

## 7. Federation mechanics across layers

### 7.1 Publication contract

For lower layers to federate into higher layers, a few things must be published in a standard way.

**Team → Org (or Engineering, in the no-org-layer case):**

*Service ownership manifest.* When a team owns a service, they publish a small manifest to the org/engineering service index:

```yaml
# org-context/teams/payments-team.yaml (or engineering-context/...)
team: payments
wiki_url: https://github.com/example/payments-team-context
services:
  - name: payments-api
    wiki_path: wiki/services/payments-api.md
    org_registered: true
  - name: refund-processor
    wiki_path: wiki/services/refund-processor.md
    org_registered: true
codeowners:
  - alice
  - bob
```

This is the minimum interop surface. It's generated automatically from the team wiki's service pages that have `org_registered: true`.

**Org → Engineering (only if org layer exists):**

Orgs roll up team manifests into org manifests, which engineering reads. Same mechanism, one level up.

### 7.2 Query routing

An agent's MCP server routes queries based on what's being asked:

- **Local queries** (single team's content): hit the team qmd index directly.
- **Cross-team queries within an org:** hit the org layer's service index to identify relevant teams, then fan out to team qmd indexes in parallel.
- **Cross-org queries:** hit the engineering layer's master service index, fan out to org indexes, which fan out to teams.

The fan-out is bounded by the service index — you don't query every team; you query the ones the index says might be relevant. This keeps federated queries cheap.

### 7.3 Promotion events

Promotion events (team decision → org scope, team or org decision → engineering scope) produce small raw entries at the higher layer. This is the derivation substrate for the Tech Radar and other aggregate views. A promotion raw entry is minimal:

```yaml
---
source: promotion
promoted_at: 2026-04-20
promoted_by: alice
source_layer: team
source_wiki: payments-team
source_path: wiki/decisions/0042-dynamodb-for-sessions.md
technology: [dynamodb]
---

Team payments has promoted decision "Use DynamoDB for sessions" to org scope.
```

The Radar jobs at each layer read these and aggregate.

## 8. Agent and human behavior across layers

### 8.1 Session start

When an agent starts a session in a code repo:

1. Find the `.team-context-link` file in the repo root.
2. Load that team's TEAM.md.
3. Load the relevant service pages for the task.
4. Identify the parent org and engineering context (via team manifest → org manifest → engineering manifest).
5. Keep those higher-layer schemas available for federated queries but don't load their content upfront.

The default is: team-layer context is loaded; higher layers are queryable on demand.

### 8.2 Cross-layer queries

When an agent encounters a question that can't be answered from the local team context, it escalates:

- Service it doesn't recognize → `find_service_owner(name)` at the engineering layer.
- Technology adoption question → query the relevant Radar (team's org if the tech is org-scope, engineering if cross-cutting).
- Cross-team workflow question → query org or engineering workflow diagrams.
- Decision that might have precedent → `find_prior_art(intent)` across federated indexes.

These calls should feel like the agent consulting a map, not like it's loading a new context. The MCP server handles routing transparently.

### 8.3 Authorship model

There are three distinct authorship modes, with different permissions at different layers:

**Synthesis agents.** Write at the team layer only. Run through the Tier 1/2/2.5 pipeline (classifier → synthesizer → adversarial checks → merge). Cannot modify raw files (only connectors do that). Cannot write at org or engineering layers.

**Derivation bots.** Run at the org and engineering layers. Each bot has a specific identity (e.g., `radar-bot`, `service-index-bot`, `dependency-graph-bot`) and is authorized to modify specific paths only (`wiki/radar/descriptive.md`, `wiki/services.md`, `wiki/architecture/dependencies.md`, etc.). They produce PRs that auto-merge after cheap validation. They never touch paths outside their authorization.

**Humans.** Can write at any layer, subject to CODEOWNERS review. This is how:
- Teams correct or augment their own wikis when the agent got something wrong.
- Org and engineering layer owners author prescriptive Radar entries, workflow diagrams, glossary entries, and prescriptive decisions.
- Promotion/demotion PRs get adjudicated.
- TEAM.md / ORG.md / ENGINEERING.md schemas evolve.

Human PRs at the team layer go through the same adversarial checks as agent PRs (citation faithfulness, contradiction scan, etc.) — the checks aren't about who authored the content, they're about whether the content is consistent with the rest of the wiki. Human PRs at higher layers have lighter checks because the content at those layers is not claiming to be derived from raw (it's authored), but CODEOWNERS review becomes the primary gate.

**The one thing humans cannot do by convention:** write synthesized content at higher layers. If a human wants to produce "a summary of all team postmortems from this quarter," that's a legitimate engineering-layer artifact, but the authorship should be explicit (authored by a named human with the analysis caveated as their interpretation), not laundered as an auto-derived view. Keeping the distinction between *derived* (computed, reproducible), *authored* (explicit human opinion), and *synthesized* (agent-generated from raw) clear is important to the system's trustworthiness.

### 8.4 The engineering-layer owners

The engineering layer requires a specific human role: a small group responsible for curating the engineering-wide wiki. This group:

- Authors prescriptive Radar entries.
- Writes engineering-wide decisions.
- Maintains the engineering glossary.
- Authors cross-org workflow diagrams.
- Adjudicates promotion and demotion PRs.
- Tags technologies with their Radar quadrant.
- Owns `ENGINEERING.md` changes.

Possible staffing models:
- **Architecture/platform standards team** — a named team whose job includes this curation. They may also have team-shaped work (projects they run, services they own) in which case they have a team-context repo too. Their engineering-layer work happens in `engineering-context` directly, not in their own team repo.
- **Rotating tech leads** — 2-4 senior engineers from different orgs, rotating every N months. Lightweight, avoids the centralization concerns of a standing team.
- **Hybrid** — a small standing team plus rotating lead representation.

The mechanics of the spec don't require a specific model; what they require is that the role exists and is staffed. Engineering layers decay when no one owns them. The same pattern applies at the org layer: org-layer owners exist for each org, often tech leads within that org.

### 8.5 Summary of who writes what, where

| Layer | Synthesis agents | Derivation bots | Humans |
|---|---|---|---|
| Team | Wiki pages (via Tier 2 pipeline) | — | Wiki pages (corrective / additive), TEAM.md |
| Org | Not permitted | Service index, Radar-descriptive, dependency graph | Org decisions, Radar-prescriptive, glossary, workflow narratives, ORG.md |
| Engineering | Not permitted | Service index, Radar-descriptive, dependency graph | Engineering decisions, Radar-prescriptive, glossary, workflow narratives, ENGINEERING.md |

## 9. The Entire CLI as the checkpoint primitive

The design treats git commit checkpoints as a first-class raw source. Entire (entire.io, open source, MIT, github.com/entireio/cli) provides exactly this primitive and is the recommended foundation:

- Installs git hooks that capture the full agent session (prompts, responses, files modified, timestamps) on every commit.
- Stores session metadata on a separate `entire/checkpoints/v1` branch — main branch history stays clean.
- Works with Claude Code, Gemini CLI, Cursor, OpenCode, GitHub Copilot CLI, OpenAI Codex.
- Fully local; checkpoints live in the repo itself.
- Push-sessions flag auto-publishes the checkpoints branch alongside normal `git push`.

Integration into the team-context ingestion pipeline:

1. Each code repo runs `entire enable --strategy manual-commit`.
2. The team-context ingestion daemon periodically does `git fetch origin entire/checkpoints/v1` across the team's repos.
3. For each new checkpoint, it reads the session file, runs it through Tier 1, and materializes it into `raw/checkpoints/YYYY/MM/DD/{sha}-{slug}.md`.
4. The normal pipeline takes over.

Entire's free open-source CLI does everything needed for Tier 0. The paid entire.io service adds hosted search/dashboard features that this design replaces with the team-context system itself.

## 10. Prototyping path

The system is large enough that building it all at once is the wrong move. A staged path:

**Stage 1: Single-team prototype.** One team-context repo for one team. Entire CLI for checkpoints. qmd for search. MCP server exposing `load_service_context` and `query_wiki`. Manual synthesis (a human runs a script to update the wiki after interesting commits; no Tier 1/2/2.5 pipeline yet). Goal: prove the wiki is more useful than no wiki, for one team.

**Stage 2: Automated ingestion.** Add connectors (Slack, incident tool). Add Tier 1 classification and Tier 2 synthesis. Still single-team. Goal: prove the wiki stays current without human maintenance overhead.

**Stage 3: Adversarial checks and auto-merge.** Add the check pipeline. Start with everything going to human review; promote categories to auto-merge as data justifies. Goal: prove synthesis can be trusted.

**Stage 4: Second team.** Add one more team. Federate queries between them. Build the minimum engineering-layer index. Goal: prove federation works and produces value neither team could achieve alone.

**Stage 5: Derived artifacts.** Build the service dependency graph. Build the descriptive Tech Radar at the engineering layer. Goal: prove derivation is cheaper and more accurate than maintained alternatives.

**Stage 6: Scale out.** More teams. Add org layer if the company is large enough to need one. Add prescriptive Radars. Add org-level Radars and dependency graphs where scope justifies. Add cross-org architecture.

Skipping stages is tempting and almost always wrong. The trust in the system is built incrementally by each stage earning it; skipping ahead produces a system people don't use because they don't trust it yet.

## 11. Adoption

The architecture works only if it's used. Usage generates queries, queries surface gaps, gaps drive synthesis, synthesis makes the wiki more useful, which drives more usage. If the loop stalls early, the wiki ossifies and people stop trusting it.

Early moves that help:

- **Start narrow.** Pick one team, one painful domain (onboarding, or a hairy legacy service), and make the agent demonstrably faster than asking a senior person. Expansion after that is pull rather than push.
- **Make agent reasoning visible.** When `load_service_context` fires, surface "I loaded these pages from the wiki" so people can see what the agent is working from. Builds trust; surfaces "that page is wrong" feedback.
- **Close the loop on queries.** Log every query where the wiki didn't have the answer. Those are the highest-value synthesis targets.
- **Don't auto-merge early.** Run the full adversarial pipeline, but send everything to human review for the first weeks. Use that period to calibrate what humans consistently approve without changes.
- **Build higher layers only when teams pull for them.** If two teams both want cross-team queries, that's the signal. Building engineering-layer artifacts before any team has traction is the classic failure mode of enterprise knowledge systems.
- **Staff the curator roles explicitly.** The engineering layer (and any org layer) requires named humans to own prescriptive decisions, Radar curation, workflow diagrams, and promotion adjudication. Under-staffing this role is the most common reason higher-layer artifacts decay.

## 12. Known limits and open questions

### What this doesn't solve

- **Team coordination.** This does not tell you what someone else is working on right now.
- **Wiki usefulness feedback.** Hard to measure. Proxies: agent queries resolving without escalation, reduced repeat questions in Slack, faster onboarding. All noisy.
- **Long-horizon drift.** Even with citation checks and lint passes, a wiki maintained for years accumulates errors. Periodic human-led audits of high-stakes pages are the backstop.
- **Cross-company knowledge sharing.** If vendors, contractors, or acquired teams need partial access, this design doesn't handle it.

### What this trades off

- **Compute cost vs. human time.** The adversarial pipeline is not free. Bets that compute is cheaper than senior-eng time. True for most teams; not true for tiny ones.
- **Friction vs. noise.** Push ingestion is low-friction but people forget. Pull ingestion is automatic but noisy. The split-by-source approach (pull where curation is structural, push where it isn't) has no free lunch.
- **Atomicity vs. PR volume.** Atomic PRs create a lot of PRs. Batching low-signal pulls helps but doesn't eliminate. Teams uncomfortable with PR volume may relax atomicity; this usually ends badly.
- **Federation vs. centralization.** Federation keeps ownership distributed but makes cross-team queries slower and more complex. Centralizing would be faster but recreates the ownership problems at scale.

### Open design questions

- **The Tier 1 classifier.** Rules, small model, or fine-tuned on the team's acceptance/rejection data. All three eventually; unclear starting point.
- **Non-text raw.** Diagrams, screenshots, recordings. LFS for storage; multimodal agents for synthesis. Punt for v1.
- **Confidence calibration.** `confidence: high|medium|low` is a vibes call. Deriving it from citation count, source age, and supersession history is probably better but not urgent.
- **The right shape for prescriptive Radar governance.** Rotating tech leads vs. standing architecture team vs. hybrid. Depends on org culture.
- **Cross-company deployment.** If Company A acquires Company B, how do their engineering-context repos merge? Open question.
- **When the org layer actually earns its keep.** Some multi-org companies may find team + engineering sufficient; some may need an intermediate layer. The threshold is a cultural/coordination question more than a technical one.
- **Handling prescriptive conflict across layers.** An org wanting to Hold something engineering considers Adopt produces an informative tension. The exact mechanism for surfacing and resolving such conflicts is sketched in §6.3 but would need sharpening in practice.

## 13. Prior art and acknowledgments

This design is a synthesis of several existing ideas:

- **Karpathy's LLM Wiki gist** (April 2026): the pattern of LLM-maintained markdown wikis with a schema file, index, and log. This spec extends that pattern to engineering scale.
- **Entire** (entireio/cli, MIT): the checkpoint model — every commit paired with its agent session — is the foundational Tier 0 mechanism.
- **qmd** (Tobi Lütke): local hybrid search with BM25, vector, and LLM reranking, already designed for agentic workflows via MCP.
- **Mintlify KB agent**: the closest existing product, combining Slack-based push ingestion with version-controlled wiki output via GitHub PRs. This spec incorporates similar push mechanics.
- **ThoughtWorks Tech Radar**: the pattern of technology-adoption views used here as a worked example of derived higher-layer artifacts. The descriptive/prescriptive split and the multi-layer Radar composition are extensions that become possible only when the underlying team decisions are captured systematically.
- **Vannevar Bush's Memex** (1945): the distant ancestor — a personal, curated knowledge store with associative trails, whose central unsolved problem was who does the maintenance. LLMs finally answer that.
- **Discussion on Karpathy's gist**: several critiques this spec tries to address — synthesis-time drift (Ranjan Kumar), retrieval-as-reasoning failure modes at scale (gulliveruk), and the librarian-who-writes-books-vs-index-cards framing (foundanand). The adversarial checks, explicit citation, and scoping-vs-reasoning separation are responses to those critiques.

---

*This spec is a starting point, not a finished design. It describes an architecture that has been reasoned about but not deployed; real deployment will surface issues this document cannot anticipate. Treat it as a basis for prototyping, not a blueprint for production.*
