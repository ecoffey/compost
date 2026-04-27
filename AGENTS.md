# AGENTS.md

Project-specific conventions for AI agents working in this repo.

---

## Raw file naming

Use date-based filenames (`YYYY-MM-DD-{slug}`) rather than sequence numbers for raw source files. Sequence scanning is fragile and unnecessary — timestamps give ordering without coordination. The `INC-` prefix on incident files is kept for visual correlation with external incident systems, but carries no numeric sequence.
