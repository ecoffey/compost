# Backlog

Parking lot for ideas and architectural questions that aren't ready for a phase plan yet.

---

## Architecture: single persistent qmd/compost process

**Context:** The current design starts a fresh `tc mcp` process per Claude Code session. Each process spawns `qmd` as subprocesses, which cold-loads the embedding model on first query (~1s warmup). We added a warmup call in `run_server` as a short-term fix.

**Idea:** Run one persistent qmd or compost process per machine that stays alive across sessions. This would:
- Eliminate the per-session warmup cost entirely (model stays loaded)
- Allow a single process to serve multiple wiki instances simultaneously
- Enable smarter caching (e.g. keep recently-retrieved pages warm in memory)

**Related:** `qmd` already has its own MCP server mode (`qmd mcp`, stdio transport). Investigate whether we can use `qmd mcp` directly — either by pointing Claude Code at it instead of `compost mcp`, or by having `compost mcp` talk to a running `qmd mcp` process over a local socket instead of shelling out per query. This would offload model management entirely to qmd and let `compost` focus on the compost routing and tool shaping.

**When to revisit:** Phase 8 (long-running synthesis daemon) is a natural moment — we'll already be building persistent background processes at that point.

---

## Verify: qmd --index per-index isolation

**Context:** The impl plan flagged this as an open question: are collections registered under `--index tc-test` invisible to the default index and to other named indexes?

If isolation is NOT guaranteed, collection names across different wiki instances could collide. The fallback would be to namespace collection names with the index prefix (e.g. `tc-test/wiki`, `tc-test/raw`) when registering.

**When to revisit:** Before bootstrapping a second wiki instance (Phase 10, or whenever a second local wiki is created for testing).

---

## TUI

**Context**: UX for interacting with a wiki both to browse content and manage raw / synthesis flows

---

## hybrid — install the Claude Code skill

https://github.com/justinstimatze/hybrid (cloned: `~/workspace/hybrid`)

A vocabulary and Claude Code skill for naming the design pattern compost already implements: LLM extracts (Tier 1 lens / Tier 2 reasoner) → typed records accumulate (raw files, wiki frontmatter) → deterministic code filters (classifier rules, qmd scores) → LLM proposes edits → action (commit + PR). There is no library to depend on — the repo is markdown + manifests.

The skill is worth installing: it gives future phase design conversations a shared vocabulary (lens, substrate, gate, reasoner, action, calibration, metabolism) and a 5-phase diagnostic for deciding when the full pattern applies vs. a simpler approach.

**Install:**
```bash
ln -s ~/workspace/hybrid/skills/hybrid-loops ~/.claude/skills/hybrid-loops
```

**When to revisit:** Install now. Reference the vocabulary when designing Phase 5+ adversarial check gate and any future background-metabolism features.

---

## winze — metabolism and epistemic discipline patterns

https://github.com/justinstimatze/winze (cloned: `~/workspace/winze`)

A knowledge base implemented as Go source code where `go build` is the consistency checker. Entities are typed constants, predicates are generic types (`BinaryRelation[S, O]`, `UnaryClaim[S]`), every claim carries a `Provenance` struct (origin, ingest date, ingester, exact source quote). Contested claims are first-class: a `Disputes` predicate and `//winze:contested` annotation mark active disagreements. An autonomous metabolism loop runs phases:

- **Dream** (NREM): consolidation without new ingest — bridge entities, file balance, provenance gaps
- **Trip** (REM): speculative cross-cluster connections, scored and promoted
- **Bias audit**: 9 deterministic auditors (confirmation bias, anchoring, availability heuristic, survivorship bias, etc.) run against the KB's own structure
- **Calibrate**: predictions tracked against outcomes; per-evaluator hit-rate over time
- **Evolve**: topology-driven sensor queries (arXiv, RSS, Wikipedia), quality-gated ingest

**What compost can borrow (without adopting Go-source KB):**

1. **Disputes as a first-class predicate.** Compost's `ContradictionNote` is a stub that surfaces what the LLM notices in passing. Winze shows what a real version looks like: a typed `Disputes(wiki_page, claim_a, claim_b)` record written to a dedicated log, queryable by page, trackable over time. Phase 5's adversarial check gate should produce structured contradiction records, not free-text observations.

   **Phase 5 status:** adversarial checks now produce structured `Finding` objects with `conflicting_page`, `claim_text`, and `conflicting_claim_text` fields (`checks/runner.py`). The remaining missing piece is `compost claims suggest` — a command that reads these `Finding` objects and writes draft `@Contested TheoryOf` entries to `wiki/claims.kt`.

2. **Metabolism phase structure.** The dream/bias-audit/calibrate split maps cleanly onto what a compost background worker should do: dream = re-run synthesis on recently changed raw files without new ingest; bias audit = structural health checks on the wiki graph (orphan pages, provenance concentration, stale confidence scores); calibrate = track whether pages flagged as high-confidence actually stay stable.

3. **`//winze:contested` annotation discipline.** Wiki frontmatter already has `confidence` and `supersedes`. Adding an explicit `contested_by: [list of raw file paths]` field would let compost surface which claims are actively disputed across the corpus — a deterministic query, not an LLM call.

4. **Mirror-source-commitments principle.** Winze enforces: only encode what the source explicitly states; prose is I/O not state (source docs are transient, the KB is canonical). Compost already follows this structurally (raw files → wiki), but the principle is worth naming explicitly in wiki page authoring guidelines.

**Go-source KB approach:** probably not right for compost's target audience (teams writing markdown), but worth revisiting if the wiki ever needs formal consistency checking beyond frontmatter lint.

**When to revisit:** Phase 5 Kotlin consistency layer shipped. The metabolism phases are the template for Phase 9 `compost tend` subcommands.

---

## Structured `TheoryOf` claims for auto-dispute detection

**Context:** `TheoryOf(subject, claim, prov)` currently takes a free-form `claim: String`. Codegen
can only detect conflicts when humans explicitly annotate competing positions with `@Contested`.

**Idea:** make `claim` structured — a predicate slot plus an object value — so codegen can detect
likely conflicts automatically. If two `TheoryOf` records share the same subject AND the same
predicate slot but have different object values, that is a structural conflict codegen can flag
without any human annotation.

Example shape (predicate as typed enum or sealed class):

```kotlin
TheoryOf(subject = authService, predicate = AuthMechanism, value = "JWT tokens", prov = ...)
TheoryOf(subject = authService, predicate = AuthMechanism, value = "session cookies", prov = ...)
// same subject + same predicate + different value → codegen auto-generates Disputes
```

**Requires:** designing a predicate vocabulary (what predicates exist for each `WikiPage` subtype,
how they are typed, whether predicates are open or closed). This is non-trivial and should wait
until there are enough real `claims.kt` examples to see which predicates recur naturally.

**When to revisit:** after the first real wiki instance has accumulated several weeks of
`claims.kt` entries. The recurring predicate patterns will surface the right vocabulary.

---

## Initialize from existing documentation and artifacts

**Context**: Bootstrapping this system will almost certainly occur in a scenario where there is already lots of
existing wiki content, commit events, decisions, etc. Need some way to sort through all that. Things I'm concerned
about:

- Preserving accurate timestamps from the past
- Doing this work batch / async so that it can continue loading in historical context while its being used

---

## Feedback on PRs

A user should be able to leave feedback on a PR for the compost agent to react to and update the PR. Bonus if it can also 
update the entire process for later iterations.
