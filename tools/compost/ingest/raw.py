from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml

SOURCE_TYPES = Literal["slack", "incident", "decision", "note", "meeting", "support"]


@dataclass(frozen=True)
class RawFile:
    path: Path       # absolute path written
    rel_path: Path   # relative to repo root (used in git commit, PR log)
    source: str


def slugify(title: str) -> str:
    """Convert a title to a filesystem-safe lowercase slug."""
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug


def write_raw(
    repo: Path,
    source: SOURCE_TYPES,
    title: str,
    body: str,
    *,
    captured_by: str = "",
    origin: str = "",
    channel: str = "",
    ts: datetime | None = None,
) -> RawFile:
    """Resolve date-based path, write frontmatter + body, return descriptor."""
    if source == "slack" and not channel:
        raise ValueError("--channel is required when --source is slack")

    if ts is None:
        ts = datetime.now(timezone.utc)

    slug = slugify(title)
    rel_path = _resolve_path(source, slug, channel=channel, ts=ts)
    abs_path = repo / rel_path

    abs_path.parent.mkdir(parents=True, exist_ok=True)

    frontmatter = {
        "source": source,
        "captured_at": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "captured_by": captured_by,
        "intent": title,
        "origin": origin,
    }

    content = f"---\n{yaml.dump(frontmatter, default_flow_style=False)}---\n\n{body}"
    abs_path.write_text(content)

    return RawFile(path=abs_path, rel_path=rel_path, source=source)


def _resolve_path(source: str, slug: str, *, channel: str, ts: datetime) -> Path:
    yyyy = ts.strftime("%Y")
    mm = ts.strftime("%m")
    dd = ts.strftime("%d")
    date = f"{yyyy}-{mm}-{dd}"

    if source == "slack":
        return Path(f"raw/slack/{yyyy}/{mm}/{date}-{channel}-{slug}.md")
    if source == "incident":
        return Path(f"raw/incidents/{yyyy}/INC-{date}-{slug}.md")
    if source == "decision":
        return Path(f"raw/decisions/{date}-{slug}.md")
    if source == "note":
        return Path(f"raw/notes/{date}-{slug}.md")
    if source == "meeting":
        return Path(f"raw/meetings/{date}-{slug}.md")
    if source == "support":
        return Path(f"raw/support/{date}-{slug}.md")
    raise ValueError(f"Unknown source type: {source}")
