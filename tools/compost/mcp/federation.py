"""Federation config loader and multi-repo query fan-out.

Single point of ownership for ~/.compost/federation.yaml format.
"""
from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml


@dataclass(frozen=True)
class FederatedRepo:
    name: str
    path: Path
    qmd_index: str
    role: Literal["team", "org", "engineering"]


_DEFAULT_CONFIG = Path.home() / ".compost" / "federation.yaml"


def load_federation_config(config_path: Path | None = None) -> list[FederatedRepo]:
    """Read ~/.compost/federation.yaml; returns [] if the file is absent."""
    path = config_path or _DEFAULT_CONFIG
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text()) or {}
    repos = []
    for entry in raw.get("repos", []):
        repos.append(FederatedRepo(
            name=entry["name"],
            path=Path(entry["path"]).expanduser(),
            qmd_index=entry["qmd_index"],
            role=entry["role"],
        ))
    return repos


def fan_out_query(
    query: str,
    repos: list[FederatedRepo],
    roles: list[str] | None = None,
    limit_per_repo: int = 5,
) -> list[dict]:
    """Query each repo's qmd index in parallel. Deduplicate by title (highest score wins)."""
    targets = [r for r in repos if roles is None or r.role in roles]
    if not targets:
        return []

    def _query_one(repo: FederatedRepo) -> list[dict]:
        result = subprocess.run(
            [
                "qmd", "--index", repo.qmd_index,
                "query", query,
                "--collection", "wiki",
                "-n", str(limit_per_repo),
                "--json", "--no-rerank",
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return []
        try:
            hits = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []
        for h in hits:
            h["_repo_name"] = repo.name
        return hits

    all_hits: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        futures = {pool.submit(_query_one, r): r for r in targets}
        for future in as_completed(futures):
            all_hits.extend(future.result())

    # Deduplicate by title: keep highest-score hit per title.
    seen: dict[str, dict] = {}
    for hit in all_hits:
        title = hit.get("title") or hit.get("file", "")
        existing = seen.get(title)
        if existing is None or hit.get("score", 0) > existing.get("score", 0):
            seen[title] = hit

    return sorted(seen.values(), key=lambda h: h.get("score", 0), reverse=True)


def find_owner_in_services_md(
    service: str,
    repos: list[FederatedRepo],
) -> dict | None:
    """
    Deterministic lookup in engineering repo's wiki/services.md.
    Returns a dict with keys: service, owner_team, repo, status.
    Returns None if not found.
    """
    eng_repos = [r for r in repos if r.role == "engineering"]
    for repo in eng_repos:
        services_md = repo.path / "wiki" / "services.md"
        if not services_md.exists():
            continue
        for row in _parse_services_table(services_md.read_text()):
            if row.get("service", "").lower() == service.lower():
                return row
    return None


def _parse_services_table(text: str) -> list[dict]:
    """Parse a markdown pipe table. Returns list of row dicts keyed by header."""
    rows: list[dict] = []
    headers: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not headers:
            headers = cells
            continue
        if set(c.strip("-: ") for c in cells) == {""}:
            # separator row
            continue
        if len(cells) >= len(headers):
            rows.append(dict(zip(headers, cells)))
    return rows
