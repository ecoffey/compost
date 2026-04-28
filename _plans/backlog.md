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

## Synthesis pipeline observability dashboard

**Context:** Deferred from the Phase 4 plan. Before the synthesis pipeline is used daily, there needs to be a way to observe it: watch invocations, inspect LLM session input/output, track token cost per run.

**Minimum viable form:**
- Structured JSONL event log per synthesis run written to `.compost/synth-log/`
- `tc synth log` view command that renders recent runs (inputs, outputs, cost, pass/fail)

**Full form (eventual):** a richer dashboard surface, likely a local web UI or a rendered markdown report, covering invocation history, cost trends, and diff previews.

**When to revisit:** Phase 8 is the natural landing zone, since we will already be building the long-running worker with per-run visibility at that point.

---
