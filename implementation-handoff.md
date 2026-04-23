# Implementation Plan Handoff

This document is for whoever writes the implementation plan. It is **not** the plan itself. It is a scoping document that says what the plan needs to address, what's already decided, what's deliberately underspecified, and what the first-principles tradeoffs are.

The plan author should read this alongside the three spec documents:

1. `team-context-spec.md` — the full architecture (three layers, federation, derived artifacts, authorship model).
2. `team-context-research-spec.md` — the research capability extension (grounding gate, stub-and-promote).
3. `TEAM.md`, `ORG.md`, `ENGINEERING.md` — the per-layer schema documents (what agents and humans read at session start).

---

## 1. What the plan must produce

A staged implementation that earns team trust incrementally. Skipping stages is tempting and usually wrong — the trust that makes people actually use this system is built by each stage delivering enough value that the next stage is justified.

The main spec §10 sketches six stages. The plan should:

- Keep those six stages' intent (single team → automated ingestion → adversarial checks → second team → derived artifacts → scale out).
- Add concrete milestones, entry/exit criteria, and acceptance tests to each stage.
- Identify which stages are optional for a given deployment shape (e.g., a 50-person company may never need stage 6's org layer).
- Specify what gets built, what gets bought/adopted as open source, and what gets deferred.

The plan does **not** need to solve every open question in the specs. It needs to identify which open questions block which stages, and specify a working decision for each blocker.

## 2. What's already decided (don't re-litigate)

These are committed design decisions. The plan should treat them as fixed constraints, not open questions:

- **Raw is append-only**, at every layer.
- **Atomic PRs** for raw + wiki changes together. Batching allowed for pull-based low-signal ingests.
- **No direct-to-main commits.** All writes go through PR.
- **Three authorship modes.** Synthesis agents write at team layer only. Derivation bots run at org/engineering layers with path-restricted permissions. Humans write anywhere, gated by CODEOWNERS.
- **Frontmatter is required and structured.** Every wiki page has `type`, `sources` (non-empty), `owners`, `status`, `updated`, `confidence`, `supersedes`, `superseded_by`.
- **Federation is query-time, not sync-time.** Team wikis are the source of truth for team content. Higher layers don't replicate content, they point.
- **Derived artifacts compose across layers.** Higher-layer placements are authoritative when entities appear at both.
- **Adversarial checks gate wiki PRs.** Citation faithfulness, contradiction scan, recent-raw scan, scope, provenance, human-edit guard.
- **Auto-merge is opt-in per-area.** High-stakes areas default to human review regardless of check results.
- **Entire CLI is the checkpoint primitive.** Free, open-source, MIT-licensed. The paid service is not required.
- **qmd (or equivalent) is the search layer.** Local, hybrid (BM25 + vector + rerank), MCP-exposed.
- **Research has a grounding gate.** Purely external research sits in `raw/research/` until explicitly grounded, via stub-and-promote if needed.

## 3. What the plan must decide

These are genuinely open questions that need working decisions before implementation. The plan should pick an answer for each, with clear reasoning. Every decision here is reversible, but the plan should commit to something rather than leave it open.

### 3.1 The Tier 1 classifier

The spec names Tier 1 as "a cheap classifier — small model, rules, or hybrid" and lists triggers (path-based, semantic, structural, volume). The plan must decide:

- **What the first-cut classifier is.** Pure rules is simplest to start. A small local model (e.g., a 1-3B parameter classifier) adds cost and complexity but can capture semantic triggers that rules miss. Hybrid is probably the endgame.
- **How the classifier is trained or tuned.** If rules-only, the rules come from the main spec's list and get extended. If a model, what's the training or prompting strategy for v1?
- **How to observe false positives and false negatives.** A classifier that triggers too often creates PR noise; one that triggers too rarely lets the wiki drift. There needs to be a feedback loop. **Working decision:** combine two signals. (1) Log every classification decision so false positives surface from PR review (humans rejecting synthesis). (2) Run a weekly "what-if" job that samples N raw entries from the last 7 days that did *not* trigger Tier 2, runs them through the synthesis agent in dry-run mode, and asks whether they would have produced a non-trivial wiki diff. Hits go into the weekly lint PR as suspected false negatives. Together these give both error directions without needing labeled training data upfront.
- **What happens when Tier 1 is uncertain.** **Working decision:** fire by default and let adversarial checks catch mistakes. The cost asymmetry favors firing: a false positive costs one PR review, a false negative costs an undetected wiki gap that compounds.

### 3.2 The synthesis agent

Tier 2 needs an agent that reads raw, reads relevant wiki, and produces a PR. The plan must decide:

- **Which model(s) are used.** A single model for synthesis, or tiered (small model for simple pages, frontier model for complex cross-page synthesis)?
- **Where synthesis runs.** Locally (developer machine), on a shared runner (CI, dedicated VM), or a hybrid. The atomicity requirement means it can't run per-developer-per-commit; something centralized is required. **Working decision:** GitHub Actions on the team-context repo. The ingestion daemon (see §3.6) opens a PR with the raw file on a fresh branch; a workflow triggered by the PR runs synthesis and pushes wiki edits to the same branch before adversarial checks fire. Atomicity stays intact because raw and wiki land in one merge. Tradeoff to plan around: GH Actions runners are ephemeral, so retry/backoff state must live in the PR or external storage. Open sub-question for the plan: bake the qmd index into the runner image vs. call out to a hosted qmd service. Bake-in is simpler for v1; hosted scales better once indexes grow.
- **How long synthesis can take before it's a problem.** If Tier 2 takes 90 seconds, the user who just `:wiki:`-reacted a Slack thread wants their confirmation in under that window. This is solved by async PR creation (confirm the ingestion immediately; PR completes on its own), but the plan should specify the UX.
- **Failure handling.** What happens if synthesis fails (rate limits, model unavailable, syntax error in output)? The raw ingestion should still land even if synthesis fails; synthesis is retried. **Working decision:** treat the synthesis job as a durable event. Minimal v1: raw lands first (committed to the PR branch immediately), synthesis runs as a separate workflow that retries on failure with exponential backoff, and a permanent failure opens a dead-letter issue tagged `synthesis-failed` for human triage. A real queue (SQS, Temporal) is overkill until multiple teams are running hundreds of jobs/day; defer.
### 3.3 The adversarial check pipeline

Six checks are specified. The plan must decide:

- **Implementation per check.** Citation faithfulness is the hardest: it requires fetching the cited source (which might be a raw file path, an external URL, or a prior wiki page) and verifying support. What tool(s) verify "source supports claim"? LLM-based, embedding similarity, or a combination? **Working decision:** LLM-based check, not embedding similarity alone (too coarse to distinguish faithful synthesis from paraphrase drift). Source retrieval reuses the team's existing qmd collection rather than building a separate `citations` collection (premature for v1). For external URLs cited by research records, archive the fetched content into `raw/research/external-cache/` so the check works the same way as for internal sources; this is the use case that justifies the archival nice-to-have already nodded at in the research spec. A separate qmd collection becomes worth considering at stage 5+ when the main collection is large enough that tuning rerank settings independently for citation checks pays off.
- **Pipeline ordering and short-circuiting.** Some checks are cheap (provenance check is frontmatter validation); some are expensive (citation faithfulness). Run cheap first, short-circuit on failure.
- **How check failures are surfaced.** The spec says failures become "the review agenda for humans". What does the UI for that look like? Annotations on the PR, a structured comment, a separate status page? **Working decision:** PR comments are the primary surface (every wiki change has a PR; the failures belong inline with the diff being reviewed). A separate dashboard is the secondary surface for cross-cutting views the PR can't show: trend in check-pass rate over time, which checks fail most often, classifier accuracy. Build the PR comment integration in stage 3 alongside the check pipeline; defer the dashboard to stage 5 when there is enough data for trends to be meaningful.
- **Check authoring.** Should teams be able to add their own checks? Probably yes eventually; not necessary for v1.

### 3.4 The MCP server

The spec names several MCP tools. The plan must decide:

- **Server architecture.** One MCP server per developer machine (running qmd locally + a team-context router), or a shared team server with developer-machine clients? **Working decision:** per-developer for v1. A shared model (single MCP server on a team VM/container/hosted service that all developers connect to) has real benefits at scale (one index to keep fresh, simpler federation routing, easier to instrument) but carries v1 costs that don't pay off yet: it needs auth (which user can call which tool), uptime guarantees, and per-user query isolation. The per-developer model has none of those: each developer owns a local clone of the team-context repo, qmd indexes locally, and federated queries either rely on additional local clones or proxy to the shared model later. The migration path is clean: per-developer MCP servers can be configured to proxy specific tools (e.g., `query_across_org`) to a shared server once one exists, without changing the tool surface developers see. Build per-developer first; revisit at stage 4+ when federation traffic and index size make local indexing wasteful.
- **Tool schemas in detail.** The spec sketches `load_service_context`, `find_prior_art`, `query_wiki`, `research_question`, `promote_research`, `find_service_owner`, `query_across_org`. Each needs a formal input/output schema.
- **Authentication and scoping.** A developer's MCP can only see their team's content directly; cross-team content is accessed via federation. How is this enforced?
- **Versioning.** When the tool surface changes, how do existing clients cope?

### 3.5 The Slack integration

Push ingestion relies on Slack reactions and slash commands. The plan must decide:

- **Which Slack app implementation to use.** Mintlify KB agent exists and does approximately this; is the right move to fork/adopt it, use it as a dependency, or build fresh?
- **Which channels are pull-enabled.** Specified at config time. The plan should specify how that config is stored (in TEAM.md? in a separate config file? in a Slack admin setting?).
- **How the `:wiki:` reaction UX works end-to-end.** The spec says "confirmation posted in-thread with undo affordance." Concrete implementation: what the bot posts, how undo works, what the 5-minute window is enforced by.

### 3.6 The Entire integration

The plan must decide:

- **Whether Entire is the starting primitive or an option.** Recommend: starting primitive. Don't try to support multiple checkpoint mechanisms in v1.
- **How checkpoints get from code repos into the team-context raw/.** The spec sketches a daemon that fetches `entire/checkpoints/v1` branches and materializes them. What runs this daemon? How often? Is it per-team or per-engineer? **Working decision:** a small per-team ingestion daemon (one per team-context repo, not per engineer). It (1) reads the team manifest to know which code repos to watch, (2) polls or webhook-subscribes for new commits on `entire/checkpoints/v1`, (3) opens a synthesis PR against the team-context repo for each new checkpoint, which hands off to the GH Actions synthesis flow in §3.2. Hosting options ranked by simplicity: a scheduled GitHub Action on the team-context repo running every N minutes (zero external infra, credentials already present), a small container on team infrastructure, a Lambda. Start with the GH Action; graduate to a long-running process if poll latency becomes a problem. **Forward-looking design constraint:** make the daemon's "destination" a configurable output rather than hardcoded "the team repo". Personal knowledge wikis are out of scope for v1, but the same checkpoint stream should fan out to (team-context, person-context) destinations later with different ingestion rules. Designing the daemon's output as a list of sinks now avoids a rewrite later.
- **How to handle code repos without Entire.** In a large eng org, adoption of Entire will be incremental. The team-context system should work for teams that have it and for teams that don't (with fewer raw sources, less automatic capture). **Working decision:** ship a thinner fallback connector that pulls from `git log`, PR titles/descriptions, and PR review comments. The raw entry is less rich (no agent reasoning, no dead-end exploration) but commit messages and PR descriptions still capture the *what* and some of the *why*. Materialize these into a separate `raw/commits/` directory (distinct from `raw/checkpoints/`) so consumers can tell which kind of source they are reading and so confidence and triggers can be tuned per source. Tier 1 semantic triggers fall back to commit-message and PR-description keyword matching only; structural and path-based triggers work the same for both sources.

### 3.7 Lifecycle automation

The lint pass is specified (main spec §4.8, research spec §6) but not implemented. The plan must decide:

- **Scheduling.** Weekly is mentioned. Concrete schedule (which day, what time, on what runner).
- **Output format.** A single PR, or multiple? How are findings grouped?
- **Who acts on the lint output.** CODEOWNERS? A rotating duty? An org-layer owner?

### 3.8 Deployment model

The plan must decide, for the target deployment:

- **Small-company shape (team + engineering) or large-company shape (team + org + engineering).** The answer depends on the specific company.
- **Bootstrapping order.** Which team goes first? See §4 below.
- **Repo hosting.** Assumed to be GitHub based on the PR/CODEOWNERS language, but the plan should confirm. GitLab works too with minor adjustments.

## 4. Bootstrapping strategy

The plan needs to pick a starting team. Criteria for a good first team:

- **Pain that's solvable.** The team has a specific knowledge problem (onboarding is slow, a legacy service's history is in people's heads, incidents keep referencing the same gaps). The system's value is measurable against this pain.
- **Willing senior engineers.** The team has at least one engineer who's enthusiastic about the system and will champion it internally. Without this, adoption stalls.
- **A code surface where agents are already being used.** Teams that already use Claude Code or similar tools will get more out of checkpoint capture immediately.
- **Tolerance for rough edges.** Early stages have rough edges. Don't pick a mission-critical team that can't afford friction.
- **Not too small.** A 3-person team produces too little content for the synthesis value to show. Aim for 5-15 engineers.

Expansion after the first team:

- **Second team pulls, doesn't pushed.** When another team sees the first team's results and asks for the system, that's the right time.
- **Federation value unlocks at team #2.** Don't build the engineering-layer artifacts until there are at least two teams in the system; otherwise there's nothing to federate.
- **Avoid a rollout schedule.** The system's value is pull-driven; a push rollout (IT mandates everyone use it) will produce compliance, not adoption.

## 5. What the plan can defer

These are identified as real concerns but not v1 blockers:

- **Non-text raw.** Diagrams, screenshots, meeting recordings. Text only for v1; multimodal later.
- **Confidence calibration.** The `high | medium | low` field is a vibes call. Deriving it more rigorously (from citation count, source age, supersession history) is deferred.
- **Cross-company deployment.** Acquisitions, contractors, vendor access. Not needed for internal-only v1.
- **Prescriptive Radar conflict resolution mechanics.** The spec says conflicts across layers are "informative tensions"; how exactly they get resolved in practice is deferred to running experience.
- **External source archival for research.** The research spec recommends archiving external URLs at research time. Nice-to-have; can be added once the research capability is in use.
- **Multi-agent mesh sync.** Mentioned in some prior-art references (Rohit G.'s LLM Wiki v2). Not in scope.

## 6. What the plan must not do

A few failure modes the plan should actively avoid:

- **Don't over-automate early.** Ship with everything going to human review first. Auto-merge comes after human-review data shows what's safe to automate, per category.
- **Don't centralize to solve latency.** If federated queries feel slow, the instinct will be to centralize content at the org or engineering layer. Resist. The spec's federation model is there for good reasons (ownership, staleness, access control). Solve slowness with caching or better indexing, not centralization.
- **Don't skip the stub-and-promote friction.** The 30 seconds it takes a user to create a stub page before promoting external research is doing real work — it forces explicit commitment. Making it frictionless will cause the wiki to fill with unreviewed external research.
- **Don't let the engineering layer grow content.** The temptation to write a big "engineering architecture overview" at the engineering layer is strong and almost always wrong. The engineering layer hosts derived artifacts + a small amount of authored prescriptive content. That's it.
- **Don't treat research as a Q&A log.** Research is invoked explicitly for questions worth capturing. If it gets used as a general-purpose Q&A capture mechanism, the sign-to-noise ratio collapses.
- **Don't under-staff the engineering-layer owner role.** The engineering layer decays without owners. If the plan doesn't identify who plays this role before stage 5, stage 5 shouldn't start.

## 7. Success criteria

How to know each stage worked.

**Stage 1 (single-team prototype):**
- At least one engineer on the target team uses `load_service_context` daily.
- At least five wiki pages exist with real content derived from real raw.
- At least one question the team would previously have had to ask a senior engineer has been answered via the wiki.

**Stage 2 (automated ingestion):**
- Wiki stays current without manual maintenance for 2+ weeks.
- At least one synthesis-generated wiki update correctly surfaces a contradiction with prior content.
- Slack `:wiki:` reactions are being used by multiple team members without prompting.

**Stage 3 (adversarial checks and auto-merge):**
- Adversarial check pipeline has run on 20+ wiki PRs.
- Measured: what fraction of synthesis PRs pass all checks? How often do humans change things after the checks pass?
- At least one category of wiki change has been promoted to auto-merge based on observed safety.

**Stage 4 (second team):**
- Federation queries work — agents on team B can find and cite content from team A's wiki.
- The master service index (in the minimum engineering layer) is populated and accurate.
- At least one cross-team question has been answered via federation that wouldn't have been possible without it.

**Stage 5 (derived artifacts):**
- Service dependency graph is generated nightly and matches reality (spot-checked).
- Descriptive Tech Radar reflects real team decisions.
- At least one engineering-layer conversation (prescriptive Radar entry, architecture review, etc.) references the derived artifacts as inputs.

**Stage 6 (scale out):**
- Three or more teams using the system.
- Org-layer artifacts (if the deployment includes an org layer) are being used in at least one org.
- A new team can be onboarded in under a week.

## 8. Ongoing evaluation

The plan should specify how the system is evaluated over time, not just at stage transitions. Candidates:

- **Agent query log analysis.** Every `query_wiki` or `load_service_context` call is logged. What fraction find useful content? What fraction return nothing, indicating a gap?
- **Wiki PR outcomes.** What fraction of synthesis PRs get merged as-is vs. substantially edited vs. rejected? By category?
- **Raw-to-wiki conversion rate.** How many raw ingests per wiki edit? Too high means synthesis is triggering too eagerly; too low means the Tier 1 classifier is missing triggers.
- **Staleness metrics.** Wiki pages where the cited raw is >90 days old and unchanged. Pages where the effective confidence has drifted below the stated confidence. Research records past `expires_at`.
- **Usage breadth.** What fraction of team members have invoked the system in the last 30 days? Usage concentrated in one or two enthusiasts is a warning sign; broad usage is a healthy signal.

These metrics should be visible to the engineering-layer owners and the team-layer maintainers, not just to the plan's implementer.

## 9. Minimum viable deployment checklist

To get started, the plan needs to cover at minimum:

- [ ] One team-context repo, bootstrapped with TEAM.md.
- [ ] One engineering-context repo, bootstrapped with ENGINEERING.md (even if almost empty at stage 1).
- [ ] Entire CLI enabled on the target team's code repos.
- [ ] qmd (or equivalent) installed and indexing the team-context repo.
- [ ] MCP server with at minimum `load_service_context` and `query_wiki` tools.
- [ ] At least five hand-written wiki pages to seed the system (don't start from an empty wiki — the synthesis pipeline has nothing to synthesize against).
- [ ] CODEOWNERS configured with real names.
- [ ] At least one person identified as the team-layer wiki maintainer.
- [ ] At least one engineer on the team willing to be the daily user and feedback source.

This is the minimum. Everything else in the spec is additive from here.

---

## Pointers back into the specs

When the plan needs to reference design decisions, these are the key sections:

- **Three-tier ingestion pipeline:** main spec §4.1.
- **Adversarial checks:** main spec §4.1 (Tier 2.5).
- **Atomic PR model:** main spec §4.2.
- **Frontmatter schema:** main spec §4.4.
- **Federation mechanics:** main spec §7.
- **Authorship modes:** main spec §8.3.
- **Engineering-layer owner role:** main spec §8.4.
- **Derived artifacts (Radar, service index, dependency graph):** main spec §6.3.
- **Research grounding gate:** research spec §4.3.
- **Stub-and-promote:** research spec §4.4.
- **Per-layer schemas:** TEAM.md, ORG.md, ENGINEERING.md.

---

*Last updated: {date}. This document itself goes in the engineering-context repo once the system bootstraps, under `wiki/meta/implementation-handoff.md`.*
