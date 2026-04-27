from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _qmd_query(query: str, index: str, collection: str, limit: int = 5) -> list[dict]:
    """Return hit dicts with keys: file, snippet, score, title."""
    result = subprocess.run(
        [
            "qmd", "--index", index,
            "query", query,
            "--collection", collection,
            "-n", str(limit),
            "--json",
            "--no-rerank",
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return []


def _qmd_get(file_uri: str, index: str) -> str:
    """Return full document text for the given qmd:// URI."""
    result = subprocess.run(
        ["qmd", "--index", index, "get", file_uri, "--full"],
        capture_output=True, text=True,
    )
    return result.stdout if result.returncode == 0 else ""


_SCOPE_TO_COLLECTION = {
    "team": "wiki",
    "raw": "raw",
    "decisions": "decisions",
    "incidents": "incidents",
}


def load_service_context(service: str, index: str, repo: Path) -> str:
    """Return formatted context for the named service."""
    parts: list[str] = [f"# Context for service: {service}\n"]

    # Try the canonical path first to avoid semantic search returning wrong service.
    canonical_uri = f"wiki/services/{service}.md"
    full = _qmd_get(canonical_uri, index)
    if full:
        parts.append(f"## Service page ({canonical_uri})\n\n{full}\n")
    else:
        hits = _qmd_query(f"service {service}", index, "wiki", limit=3)
        if not hits:
            return f"No wiki content found for service '{service}'."
        top = hits[0]
        full = _qmd_get(top["file"], index)
        parts.append(f"## Service page ({top['file']})\n\n{full}\n")

    dec_hits = _qmd_query(service, index, "decisions", limit=3)
    if dec_hits:
        parts.append("## Related decisions\n")
        for h in dec_hits:
            parts.append(f"- {h['file']}: {h.get('snippet', '')}\n")

    inc_hits = _qmd_query(service, index, "incidents", limit=3)
    if inc_hits:
        parts.append("\n## Related incidents\n")
        for h in inc_hits:
            parts.append(f"- {h['file']}: {h.get('snippet', '')}\n")

    return "".join(parts)


def query_wiki(query: str, index: str, scope: str = "team", limit: int = 5) -> str:
    """Return formatted search results."""
    collection = _SCOPE_TO_COLLECTION.get(scope, "wiki")
    hits = _qmd_query(query, index, collection, limit=limit)
    if not hits:
        return f"No results for query '{query}' in scope '{scope}'."

    lines = [f"# Results for: {query}\n"]
    for i, h in enumerate(hits, 1):
        score = h.get("score", "")
        snippet = h.get("snippet", "").strip()
        title = h.get("title", h["file"])
        lines.append(f"{i}. **{title}** ({h['file']}) score: {score}\n   {snippet}\n")
    return "\n".join(lines)
