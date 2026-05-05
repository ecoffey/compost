# AGENTS.md

Project-specific conventions for AI agents working in this repo.

---

## Tooling: uv and just

This project uses `uv` for Python dependency management and `just` for common task automation. Both are expected to be available on PATH.

- `just` — run default target (tests): `just`
- `just test` — run the full test suite
- `just test-file tests/test_synth.py` — run a specific test file
- `just sync` — install/sync all dependencies including dev extras
- `just run <args>` — run the `compost` CLI

When installing dependencies or running Python tools in `tools/compost`, always use `uv run --project tools/compost` or `uv sync --project tools/compost --extra dev`. Do not use `pip` or bare `python` invocations.

---

## YAML serialization of timestamps

`yaml.dump` quotes ISO 8601 strings (e.g. `captured_at: '2026-04-27T10:00:00Z'`), not the bare form shown in documentation. When writing test assertions against YAML frontmatter, assert on the value alone (`"2026-04-27T10:00:00Z" in text`), not on the full key-value pair including YAML quoting. If the unquoted form is required, use a custom YAML representer or write the frontmatter section manually.

---

## External service integrations must be provider-pluggable

Any integration that calls an external service (LLM provider, git forge, etc.) must carry a `provider` field in its config and dispatch through a provider-keyed path rather than hardcoding the vendor. Phase 4 example: `SynthConfig.provider = "anthropic"` with dispatch in `agent.py`; adding a new provider requires only a new branch there plus a call helper, nothing else changes. The config is always parsed into a typed dataclass — callers never receive raw strings from `.compost.yml`.

---

## Hardcoded pricing tables for external APIs

When integrating with an external API that returns usage metrics (token counts, request counts, etc.) but not dollar costs, hardcode a pricing table in the module that computes costs. The table comment must explain why it is hardcoded (the API does not return dollar amounts) and link to the published rate page. The observability log or output must also record the raw metric counts alongside the derived cost so users can recompute if rates change.

Example (Anthropic):
```python
# The Anthropic messages API returns token counts only — not dollar amounts.
# Cost is derived by multiplying counts by published per-token rates.
# Keep in sync with: https://www.anthropic.com/pricing
_PRICING: dict[str, tuple[float, float]] = {
    # (input $/MTok, output $/MTok)
    "claude-sonnet-4-6": (3.0, 15.0),
}
```

---

## Raw file naming

Use date-based filenames (`YYYY-MM-DD-{slug}`) rather than sequence numbers for raw source files. Sequence scanning is fragile and unnecessary — timestamps give ordering without coordination. The `INC-` prefix on incident files is kept for visual correlation with external incident systems, but carries no numeric sequence.

---

## Tooling: Kotlin Consistency Layer

`compost codify --compile` and `compost assay` require `kotlinc` and `java` on PATH.

Install via SDKMAN (manages JVM toolchains per-user without touching system Java):

```bash
curl -s "https://get.sdkman.io" | bash
source "$HOME/.sdkman/bin/sdkman-init.sh"
sdk install kotlin    # installs kotlinc + JVM
kotlinc -version      # verify
```

**Schema file:** `tools/compost/codify/schema/CompostSchema.kt` is a versioned package resource.
Do not edit it directly. Schema changes (adding a new wiki page type) require updating both
`CompostSchema.kt` and `_TYPE_MAP` in `codegen.py` together.

**CI promotion:** when moving to GitHub Actions (Phase 9), swap SDKMAN for:
```yaml
- uses: actions/setup-java@v4
  with: { java-version: '21', distribution: 'temurin' }
- uses: fwilhe2/setup-kotlin@v1
```
No Python or compost code changes required.


## Adversarial Checks

`compost pr merge` now runs Tier 2.5 adversarial checks before merging any branch. The check
gate is automatic; use `--no-checks` only for raw-only branches with no wiki edits.

`ANTHROPIC_API_KEY` must be set for LLM-based checks (checks 4-6: contradiction scan, recent
raw scan, citation faithfulness). The same key used for synthesis works here.

To bypass failing checks with an audit trail:
```bash
compost pr merge --override --reason "contradiction already declared in PR; safe to merge"
```
The override reason is appended to `wiki/log.md` and committed to main.
