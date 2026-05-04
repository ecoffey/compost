from __future__ import annotations

from pathlib import Path

from compost.qmd import qmd_get as _qmd_get, qmd_query as _qmd_query


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
