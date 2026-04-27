from __future__ import annotations

from pathlib import Path
from typing import Literal, TypedDict

import frontmatter as fm_lib

FrontmatterType = Literal[
    "service", "module", "decision", "runbook", "concept",
    "customer", "person", "project", "research",
]

REQUIRED_FIELDS = ("type", "name", "owners", "status", "updated",
                   "confidence", "sources")


class Frontmatter(TypedDict, total=False):
    type: FrontmatterType
    name: str
    owners: list[str]
    status: Literal["active", "deprecated", "archived"]
    updated: str
    confidence: Literal["high", "medium", "low"]
    sources: list[str]
    related: list[str]
    supersedes: list[str]
    superseded_by: str | None


def parse_frontmatter(path: Path) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_text). Never raises; returns empty dict on parse failure."""
    try:
        post = fm_lib.load(str(path))
        return dict(post.metadata), post.content
    except Exception:
        return {}, path.read_text()


def validate_frontmatter(fm: dict, rel_path: Path) -> list[str]:
    """Return a list of human-readable error strings. Empty list means valid."""
    errors: list[str] = []
    prefix = str(rel_path)

    for field in REQUIRED_FIELDS:
        if field not in fm:
            errors.append(f"{prefix}: missing required field '{field}'")

    if "sources" in fm:
        if not fm["sources"]:
            errors.append(f"{prefix}: 'sources' must be non-empty")

    if "owners" in fm:
        if not fm["owners"]:
            errors.append(f"{prefix}: 'owners' must be non-empty")

    if "type" in fm:
        valid_types = {
            "service", "module", "decision", "runbook", "concept",
            "customer", "person", "project", "research",
        }
        if fm["type"] not in valid_types:
            errors.append(f"{prefix}: unknown type '{fm['type']}'")

    if "confidence" in fm:
        if fm["confidence"] not in ("high", "medium", "low"):
            errors.append(f"{prefix}: confidence must be high|medium|low")

    if "status" in fm:
        if fm["status"] not in ("active", "deprecated", "archived"):
            errors.append(f"{prefix}: status must be active|deprecated|archived")

    return errors
