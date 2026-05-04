from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from compost.model.frontmatter import parse_frontmatter

_SKIP_NAMES = {"glossary.md", "index.md", "log.md"}

# Fields checked in the fuzzy compare. 'name' maps to Kotlin 'title'.
_CHECKED_FIELDS: dict[str, str] = {
    "name": "title",
    "type": "type",
    "confidence": "confidence",
    "status": "status",
}

# status is only emitted for these rendered page types
_STATUS_TYPES = {"decision", "runbook", "incident"}


@dataclass
class AssayFailure:
    page_id: str
    source_md: Path
    missing_fields: list[str]
    changed_fields: list[tuple[str, Any, Any]]


@dataclass
class AssayResult:
    passed: bool
    checked: int
    failures: list[AssayFailure]


def compare(
    repo: Path,
    rendered: list[dict],
    *,
    wiki_dir: Path | None = None,
) -> AssayResult:
    """Fuzzy compare a rendered page list against original wiki frontmatter.

    Never raises on comparison failure; only raises on infrastructure errors."""
    wiki_dir = wiki_dir or (repo / "wiki")
    by_id = {r["id"]: r for r in rendered}

    failures: list[AssayFailure] = []
    checked = 0

    for md in sorted(wiki_dir.rglob("*.md")):
        if md.name in _SKIP_NAMES:
            continue
        fm, _ = parse_frontmatter(md)
        if not fm:
            continue

        page_id = md.stem
        checked += 1

        if page_id not in by_id:
            failures.append(AssayFailure(
                page_id=page_id,
                source_md=md,
                missing_fields=list(_CHECKED_FIELDS),
                changed_fields=[],
            ))
            continue

        rend = by_id[page_id]
        missing: list[str] = []
        changed: list[tuple[str, Any, Any]] = []

        for fm_field, rend_field in _CHECKED_FIELDS.items():
            orig = fm.get(fm_field)
            if orig is None:
                continue
            # status is not emitted for all page types; skip if rendered type doesn't have it
            if fm_field == "status" and rend.get("type") not in _STATUS_TYPES:
                continue
            rend_val = rend.get(rend_field)
            if rend_val is None:
                missing.append(fm_field)
            elif not _fuzzy_equal(fm_field, orig, rend_val):
                changed.append((fm_field, orig, rend_val))

        if missing or changed:
            failures.append(AssayFailure(
                page_id=page_id,
                source_md=md,
                missing_fields=missing,
                changed_fields=changed,
            ))

    return AssayResult(
        passed=len(failures) == 0,
        checked=checked,
        failures=failures,
    )


def _fuzzy_equal(field: str, orig: Any, rend: Any) -> bool:
    if field == "confidence":
        return abs(_conf_to_float(orig) - _conf_to_float(rend)) <= 0.05
    if isinstance(orig, list) and isinstance(rend, list):
        return sorted(str(x) for x in orig) == sorted(str(x) for x in rend)
    if isinstance(orig, str) and isinstance(rend, str):
        return orig.strip().lower() == rend.strip().lower()
    return str(orig) == str(rend)


def _conf_to_float(v: Any) -> float:
    if isinstance(v, str):
        return {"high": 0.9, "medium": 0.7, "low": 0.5}.get(v.lower(), 0.7)
    return float(v)
