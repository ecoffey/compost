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


## Verify CLI examples against --help before closing a plan

CLI option names in documentation and README examples must match the actual `@click.option`
declarations. After implementation, spot-check every documented flag with `compost <cmd> --help`
before marking a plan complete. Flags whose Python param name differs from the CLI name (e.g.
`@click.option("--ts", "thread_ts", ...)`) are the most common source of mismatch.

---

## Config fields that are plumbed but not yet applied

When a config field is wired through a dataclass and `.compost.yml` loading but not yet applied
in the underlying logic, mark it with a `# TODO: not yet applied` comment at the use site. Do
not leave it silently passing through to a function that ignores it — that misleads users who set
the config and see no effect. If a field must be deferred, list it explicitly in the
"Dropped / deferred" section of the impl plan.

---

## Worker and shim test isolation

Worker and shim tests run against a real on-disk queue and real git repos; there are no mocks of
the file system or queue state. Use the `compost_git_repo_with_queue` fixture (defined in
`tests/conftest.py`) for any test that exercises `enqueue`, `claim_next`, `complete`,
`dead_letter`, or `requeue_stuck`. This fixture creates a bootstrapped repo with queue directories
already present.

Shim FastAPI tests use `fastapi.testclient.TestClient` against `make_app(repo, ...)` directly.
They never spin up a real uvicorn process. The module-level `app` variable (used by uvicorn in
production) is guarded by `os.environ.get("COMPOST_REPO")` to prevent it from being instantiated
at import time during tests.

The `[shims]` optional extra (`fastapi`, `uvicorn`) is declared in `pyproject.toml`. `fastapi` is
also listed under `[dev]` so that `just test` (which installs dev extras) always has it available
without requiring `pip install "compost[shims]"`.

---

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
