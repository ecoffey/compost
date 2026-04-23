# Team-Context: Research Capability Spec

> *Status: Draft. Companion document to the main team-context spec. Describes an extension, not a standalone system.*

## 1. Motivation

The main team-context spec describes a system that captures knowledge produced *during work* — commit checkpoints, Slack threads, incidents, decisions. That captures the (change + reasoning + outcome) triple of team activity, but it misses a complementary class of knowledge production: **questions people deliberately investigate rather than things they do**.

Teams routinely need to answer questions like:

- "What's the current best practice for handling webhook retry storms?"
- "How does Stripe handle idempotency on their webhooks?"
- "Have we tried this approach before in the codebase?"
- "Given how we currently handle X, and given current external best practices for X, what should we do?"

These questions get answered today by someone spending 20 minutes on Google, or by asking a senior engineer, or by an agent session that gets thrown away when the chat window closes. The resulting knowledge — carefully gathered external references, a specific synthesis relevant to the team's context, a connection made between internal practice and external standard — evaporates.

This spec describes a research capability that treats deliberately-invoked research as a first-class raw source, feeds grounded research through the normal synthesis pipeline, and keeps purely external research searchable but quarantined from the wiki until a human says it applies.

## 2. What counts as research

Three kinds of research, distinguished by where the sources come from:

**External research.** The answer is synthesized from sources outside the team's knowledge base. "What's the industry consensus on distributed tracing in 2026?" The agent reads external documentation, blog posts, specs, papers. The output is information about the world.

**Internal research.** The answer is synthesized from the team's own wiki and raw, possibly across teams via federation. "What have we decided about rate limiting across our services?" The agent gathers and synthesizes what the team (or org, or engineering) already knows but hasn't assembled in one place.

**Hybrid research.** Both. "Given how we currently handle webhooks (internal) and current best practices (external), what should we change?" This is where most genuinely useful research happens — the team's context meets the outside world.

The `research_kind` field in the record frontmatter (see §3) distinguishes these. Internal and hybrid research are *grounded*: they cite at least one team-layer wiki page or raw source. External research is *ungrounded*: it cites only external sources. This grounding distinction drives the pipeline behavior in §4.

### 2.1 What research is not

Research is not the default Q&A mode. People ask agents lots of questions in normal work; most of those answers are useful in the moment but not worth preserving. Research is an *explicit invocation* for questions the user recognizes as non-trivial, open-ended, or worth capturing. The act of invocation is the curation signal.

Research is also not a replacement for decisions. A research output may recommend a decision, but the decision itself still has to be made by a human (or agent with explicit authorization) and captured as a decision page in the normal way. Confusing research with decisions is a failure mode: "we researched X" is not the same as "we decided X."

## 3. The `raw/research/` schema

Research records live in `raw/research/` in the team-context repo where they were invoked. Filename convention: `raw/research/YYYY/MM/YYYY-MM-DD-{slug}.md`.

Frontmatter:

```yaml
---
source: research
captured_at: 2026-04-20T14:33:00Z
asked_by: alice                  # requester (human)
authored_by: agent               # synthesizer (agent identity)
research_kind: external          # external | internal | hybrid
question: "What's the current best practice for handling webhook retry storms?"
agent_session: {path or link}    # to the originating agent session/checkpoint
external_sources:                # URLs, for external/hybrid
  - https://docs.stripe.com/webhooks/best-practices
  - https://example.com/post-about-retries
internal_sources:                # team paths, for internal/hybrid
  - raw/incidents/2025/INC-0033-webhook-storm.md
  - wiki/services/notifications.md
confidence: medium               # high | medium | low
expires_at: 2026-10-20           # optional; when to re-evaluate
status: active                   # active | superseded | stale
superseded_by: null
promotion_status: not_promoted   # not_promoted | proposed | accepted | rejected
---
```

Body template:

```markdown
# Question
{verbatim question}

## Context
{what prompted the question, if the requester provided framing}

# Answer
{the agent's synthesized answer, with inline citations to sources}

# Recommended actions
{any concrete suggestions for the team — "we should add a runbook for this,"
 "we should reconsider our retry policy." Explicitly separated from the answer
 so it's clear what's synthesis vs. what's recommendation.}

# Caveats
{what the agent isn't confident about, what would change the answer, known gaps}
```

### 3.1 Authorship

Research records have split authorship:

- **`asked_by`** is the human who invoked the research. Their name records the intent and the accountability for having asked.
- **`authored_by`** is the agent identity that produced the synthesis. This is typically `agent` but could identify a specific model or tool version when that matters.
- **`external_sources` / `internal_sources`** are what the synthesis cites.

The split matters because if the synthesis turns out to be wrong, "the agent synthesized this in response to Alice's question" is more accurate than "Alice claimed this." Agents can be wrong in specific, model-shaped ways that are different from how humans are wrong. Keeping the attribution honest preserves that signal.

### 3.2 Confidence

Research is rarely high-confidence. Most research records should be `medium` or `low`.

- **`high`:** the answer is well-established, the sources are authoritative, and the internal grounding (if any) is solid. Rare.
- **`medium`:** the answer is reasonable but relies on interpretation, the sources disagree in places, or the internal grounding is partial. Most research.
- **`low`:** the answer is a best-effort synthesis but there's genuine uncertainty — sources are thin, the question is cutting-edge, or the agent couldn't find authoritative references.

Low-confidence research is searchable but blocked from driving wiki auto-merges regardless of other signals. A wiki page that derives from low-confidence research inherits that confidence.

### 3.3 The `expires_at` field

External research has a half-life. The agent estimates an expiration date based on domain volatility:

- Security best practices: 6 months.
- LLM tooling: 3 months.
- Mature database tuning: 2 years.
- Published RFCs: rarely expire unless superseded.

The requester can override the agent's estimate. When the date passes, the lint pass (§6) flags the record for re-running. Expired research records stay `active` until re-run; the staleness is flagged as a lint issue, not silently applied, because "stale" is itself a judgment call.

Internal research also benefits from `expires_at` but on a different logic — internal research is stale when the underlying team wiki pages or raw it cites have substantively changed. The lint pass detects this by comparing citation freshness, independent of the `expires_at` clock.

## 4. The research pipeline

Research records enter the normal raw ingestion pipeline, with one modification based on grounding.

### 4.1 Capture (Tier 0)

A user invokes research via:

- `/research {question}` slash command in the agent UI.
- `research_question(question, scope)` MCP tool (see §7).
- CLI: `team-context research "{question}"`.

The agent does the research (web fetches, internal queries, or both), then writes `raw/research/{date}-{slug}.md` with the schema from §3. This is a normal raw capture — atomic PR, same mechanics as any other ingestion.

### 4.2 Grounded research → Tier 1/2 normally

If the research is **grounded** (`research_kind: internal` or `hybrid`, with at least one `internal_source` that's a real path in the team-layer repo), it enters Tier 1 classification like any other raw. Grounded research is anchored in what the team already tracks — it references services, decisions, incidents the team already has wiki pages for. Tier 1's existing triggers work as specified in the main spec, though research-specific triggers may also apply:

- References a wiki page where the research contradicts an existing claim.
- References a service page with open questions the research answers.
- References a decision page where the research provides new evidence.

If Tier 1 fires, Tier 2 runs, producing a wiki PR with adversarial checks. The research record itself is a cited source in the wiki page's frontmatter, same as any other raw. `promotion_status` moves to `accepted` on merge.

Confidence propagates. A wiki change derived from `confidence: low` research inherits `confidence: low`, which blocks auto-merge and routes to human review. This is the mechanism that keeps tentative research from hardening prematurely into wiki-authoritative claims.

### 4.3 Ungrounded (purely external) research → quarantine

If the research is **purely external** (`research_kind: external`, no `internal_sources`), it does not automatically enter Tier 2. The record lands in `raw/research/` with `promotion_status: not_promoted`. It is searchable (qmd indexes it like any other raw) and citable in ad-hoc queries, but it does not trigger synthesis into the wiki.

The rationale: purely external research is information about the world, not about the team. Whether it applies to the team's situation is a judgment that requires team context. Letting external research auto-synthesize into the wiki means the agent would have to decide that applicability, and it usually can't — it doesn't have enough context about what matters to this specific team.

### 4.4 Promoting external research into the wiki

Two mechanisms.

**Explicit promotion.** The requester (or anyone) can flag the research for promotion:

- `team-context promote-research {path}` CLI command.
- MCP tool `promote_research(record_path)`.
- Agent UI affordance after research completes ("promote this to the wiki?").

Promotion flips `promotion_status` to `proposed` and sends the record through Tier 1. But there's a problem: if the research has no internal sources, Tier 1 has nothing to anchor it to. The synthesis agent doesn't know which wiki pages to update.

**Stub-and-promote.** The user creates a stub wiki page (or picks an existing one) that the research should flow into, then promotes the research. Mechanically:

1. User (or agent, at user's direction) creates `wiki/concepts/webhook-retry-storms.md` as a stub — just a title, frontmatter with empty `sources`, and a "## Open questions" section saying "we don't have a practice here yet."
2. User edits the research record to add the stub page path to `internal_sources`.
3. User promotes the research. The research is now technically grounded (it cites an internal wiki page), so Tier 2 runs normally.
4. Tier 2 synthesizes the research into the stub, filling out the page. Adversarial checks apply. PR is opened.

This preserves the invariant that Tier 2 only synthesizes into pages that exist and that someone (a human) has decided should exist. The human's act of creating the stub is the commitment "yes, this topic belongs in our wiki." The research then provides the content.

The stub-and-promote flow is deliberately slightly friction-heavy. The friction is the point: it forces the user to make the commitment explicit. If they won't take 30 seconds to create a stub page, the research probably isn't important enough to add to the wiki.

### 4.5 Summary of the decision tree

```
Research invoked
    │
    ▼
Record created in raw/research/
    │
    ├── Grounded (internal/hybrid)?
    │       │
    │       └── Yes → Tier 1 classification → Tier 2 if triggered → wiki PR → adversarial checks → merge
    │
    └── Ungrounded (purely external)?
            │
            └── Yes → promotion_status: not_promoted → sits in raw, searchable but inert
                    │
                    ├── Later: user explicitly promotes + ensures internal grounding
                    │     (either by pointing at an existing wiki page or creating a stub)
                    │         │
                    │         └── Now grounded → Tier 1 → Tier 2 → wiki PR → merge
                    │
                    └── Or: record stays in raw indefinitely, useful for search
                            but never influences wiki until grounded+promoted
```

## 5. Cross-layer research

Research records default to the team where the question was invoked. Internal research can and often should query *across* teams (using federation from the main spec §7), but the record itself lives at the invoking team's layer.

### 5.1 When research matters beyond the team

Sometimes research done at the team layer turns out to be broadly relevant — a team researched a pattern that multiple teams would benefit from, or a team's research on external best practices aligns with something the engineering layer is trying to set direction on.

Such research can be promoted to higher layers through the same mechanisms as any other content promotion (main spec §5.5 and §6.3). A team sets `org_scope: true` or `engineering_scope: true` on the research record's frontmatter (same convention as decisions), and the normal promotion flow applies. The higher layer may then cite the research in its own authored content (e.g., a prescriptive Tech Radar entry that cites a team's external research as evidence).

### 5.2 Research commissioned at higher layers

Org and engineering-layer owners sometimes commission research themselves — "what's the state of the art on distributed tracing?" as input to a prescriptive Tech Radar entry. This research is invoked at the higher layer and lands in that layer's `raw/research/`. The pipeline behavior is the same as at the team layer, except:

- There's no Tier 2 synthesis agent operating at higher layers (per main spec §8.3). Higher-layer wiki content is human-authored.
- Research at higher layers is typically cited by authored content rather than feeding a synthesis pipeline.

In practice: an engineering-layer owner invokes research on distributed tracing, reads the resulting record, and writes a prescriptive Radar entry that cites the research in its `sources` frontmatter. The research informs the human-authored decision. No pipeline automation, just citation discipline.

## 6. Lifecycle and lint

The lint pass (main spec §4.8) gains new responsibilities for research:

- **Expired research.** Records past `expires_at` are flagged for re-running. The lint pass doesn't re-run them automatically — the requester or a CODEOWNER decides whether the question is still relevant.
- **Stale internal research.** Records whose cited `internal_sources` have substantively changed since the research was done are flagged. The research may have been correct when done but the underlying context has moved.
- **Wiki pages citing superseded research.** If a research record is superseded, wiki pages that derived from it get flagged for reconciliation, same as any other raw removal/supersession.
- **Low-confidence research driving wiki content.** If a wiki page's effective confidence is higher than the confidence of the research records it derives from, flag — this usually means the research got promoted more aggressively than its evidence warranted.
- **Repeated research in the same area.** If multiple research records within a short window (say, 30 days) cover overlapping topics that don't have wiki pages, flag as a wiki gap. This is a hint that the team is re-researching the same thing because the knowledge isn't captured, and promoting one of the records (via stub-and-promote) would close the gap.
- **Old unpromoted external research that's been read/cited frequently.** Indicator that it's genuinely useful but nobody has taken the step to promote it. Worth nudging the requester or a relevant CODEOWNER.

### 6.1 Re-running research

When research is re-run (because it expired, or because the user thinks it may be stale), the new run:

1. Produces a new record in `raw/research/` with a fresh capture date.
2. Sets `supersedes: [old/path/here]` in its frontmatter.
3. Sets the old record's `superseded_by` and `status: superseded`.
4. If the new answer differs substantively from the old, flags the difference for review. If the old research had been promoted and was driving wiki content, the new divergence triggers a wiki reconciliation PR.

Re-running is cheap and should be encouraged. Research that doesn't get re-run goes stale silently, which is the failure mode.

## 7. MCP tool surface

The MCP server (main spec §3) gains research-specific tools:

**`research_question(question, scope, expected_kind)`**
Invokes research. `scope` is `local` (this team), `org`, `engineering`, or `external`. `expected_kind` is `external`, `internal`, or `hybrid`, as a hint to the agent about where to look. Returns the path to the newly-created research record plus the synthesized answer inline so the user doesn't have to go read the file.

**`promote_research(record_path, grounding_paths)`**
Promotes a research record. `grounding_paths` is required for purely external research and specifies the wiki pages the research should be anchored to (which must exist or be created — see stub-and-promote). For grounded research, omit; the research is already anchored.

**`re_run_research(record_path, updated_question)`**
Re-runs research. Optional updated question (for when the original was imprecise). Produces a new record that supersedes the old.

**`search_research(query, include_unpromoted)`**
Finds research records matching a query. Defaults to including unpromoted records (they're searchable by design). Returns records sorted by relevance + recency, with confidence and promotion_status surfaced.

### 7.1 Agent usage

An agent in a normal work session should proactively invoke `research_question` when it doesn't have local context for something. Two patterns:

**Proactive external research.** "I don't see anything in this team's wiki about webhook retry storms; let me research this externally." The research runs, the agent uses the answer for the current task, and the record is captured for later. The agent does *not* promote the research — that's a user decision. The agent may mention the research record path in its response so the user can read and optionally promote.

**Proactive internal research.** "This team's wiki doesn't directly address this, but let me search across the org to see if another team has handled something similar." Uses federated search, produces a hybrid or internal research record citing any relevant other-team wiki pages. Again: capture, use, let the user decide on promotion.

## 8. Visibility and social mechanics

Research is team-visible by default (it's in the team repo) but not broadcast. The act of invoking research doesn't notify anyone; the record is findable via search but doesn't appear in a feed.

Optional broadcast: a `--share` flag on research invocation (or equivalent in the agent UI) posts a notification to a configured channel. Useful when the requester actively wants input or wants others to know they're investigating an area. Broadcast is opt-in because default-broadcast would create social pressure on invocation ("everyone will see I had to research this basic thing") that would discourage capture.

Research records do not have "authorship status" in the social sense. `asked_by` records who invoked; `authored_by` records the synthesizer. There is no "reviewed by" or "approved by" field — research is not a claim that someone signed off on, it's a capture of work done.

## 9. Failure modes and mitigations

**Research-driven synthesis drift.** An agent does research, gets something subtly wrong (source updated, misread, confused two similar things), and if the research gets promoted quickly that wrong fact lands in the wiki. Mitigation: the standard adversarial check pipeline still applies to wiki PRs driven by research. Citation faithfulness catches when the research's stated citations don't support its claims. The confidence-inherit rule means low-confidence research can't auto-merge.

**External source rot.** A URL cited in a research record changes, disappears, or paywalls. The research record becomes uncheckable. Mitigation: the agent should archive snapshots of external sources at research time (Internet Archive submission, or a local snapshot cached in the repo). Without this, 2026 research becomes unverifiable in 2028. This is a real operational cost and worth paying.

**Research as procrastination.** Teams produce lots of beautiful research but no decisions or actions. Mitigation: the lint pass flags research older than 90 days that's been read/cited but never promoted and never cited in a decision. This surfaces the pattern without blocking it — some research genuinely doesn't produce action, and that's okay.

**Invocation friction.** If the research invocation is too heavy, people won't use it; they'll just chat with the agent and the knowledge evaporates. Mitigation: make invocation cheap. A slash command or single CLI call. The richer structure happens after invocation, mostly behind the scenes.

**Question-privacy leaks.** External research by an agent sends the question to whatever external service does the retrieval. "How do we handle the Acme Corp acquisition?" reveals the acquisition exists. Mitigation: the same controls that govern any agent-external-service interaction. Audit logs of external queries. Warnings on sensitive-looking questions. Org policy for what topics can't be externally researched.

**Stub-and-promote abuse.** Someone creates a one-line stub just to unblock promotion, leading to wiki pages that are mostly research-synthesized with no team commitment behind them. Mitigation: the stub requires frontmatter with `owners` — someone has to put their name on it. The ownership commitment is the real forcing function; a stub with nobody's name is a PR-review fail at creation time.

**Low-confidence research hardening into certainty.** Research with `confidence: low` gets promoted, the synthesis produces a wiki page, over time people forget the caveats and cite the wiki page as authoritative. Mitigation: confidence propagates from research to wiki, and wiki pages that derive from low-confidence research have a visible "derived from low-confidence research; caveats apply" marker in their rendered view. The lint pass periodically resurfaces low-confidence pages for human re-evaluation.

## 10. Open questions

- **How aggressive should the agent be about proactive research?** Every agent session could generate research records for things it looked up externally. Too aggressive and `raw/research/` becomes a landfill; too conservative and the capture value is lost. Probably needs calibration per-team.
- **Should research records be allowed to cite other research records?** Clearly yes for supersession, but for "this research builds on that research" it's less clear. Citation chains of research records risk transitive drift.
- **What's the right default for external-source archival?** Every URL snapshotted is expensive (storage, time). Snapshot only what seems load-bearing? The agent would have to judge, and judgment is fallible.
- **How does research interact with the acquisition scenario?** If Company A acquires Company B and their research records merge, how do conflicting external research findings from overlapping time periods reconcile? Same question as elsewhere in the main spec, but research is a specific case of it.
- **Sensitive research topics.** Beyond privacy concerns, some research topics are sensitive for other reasons — investigating a potential security vulnerability, for example. Should these have separate handling? Probably yes but not specified here.

## 11. Relationship to the main spec

This spec is a strict extension of the main team-context spec. It adds:

- A new raw subdirectory (`raw/research/`) with its own schema.
- One modification to the Tier 1 pipeline (purely external research is quarantined).
- A new mechanism (stub-and-promote) for bringing external research into the wiki.
- New lint-pass responsibilities.
- New MCP tools.
- New frontmatter fields.

Nothing in the main spec's core mechanisms changes. The atomic PR model, the adversarial check pipeline, the authorship model, the federation mechanics all apply to research records the same as to any other raw content. Research is treated as a specific kind of raw with one additional rule (the grounding gate), not as a parallel system.

---

*Like the main spec, this document is a design, not an implementation. The grounding gate (external research quarantined until promoted with internal anchor) is the central design commitment; most other decisions could be tuned based on experience.*
