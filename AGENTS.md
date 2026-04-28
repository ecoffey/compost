# AGENTS.md

Project-specific conventions for AI agents working in this repo.

---

## YAML serialization of timestamps

`yaml.dump` quotes ISO 8601 strings (e.g. `captured_at: '2026-04-27T10:00:00Z'`), not the bare form shown in documentation. When writing test assertions against YAML frontmatter, assert on the value alone (`"2026-04-27T10:00:00Z" in text`), not on the full key-value pair including YAML quoting. If the unquoted form is required, use a custom YAML representer or write the frontmatter section manually.

---

## Raw file naming

Use date-based filenames (`YYYY-MM-DD-{slug}`) rather than sequence numbers for raw source files. Sequence scanning is fragile and unnecessary — timestamps give ordering without coordination. The `INC-` prefix on incident files is kept for visual correlation with external incident systems, but carries no numeric sequence.
