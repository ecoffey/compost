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


def query_across_org(query: str, repos: list, limit: int = 10) -> str:
    """Fan out to all configured federated repos; format merged results with repo attribution."""
    from compost.mcp.federation import fan_out_query
    per_repo = max(3, limit // max(len(repos), 1))
    hits = fan_out_query(query, repos, limit_per_repo=per_repo)
    if not hits:
        return f"No results for query '{query}' across {len(repos)} repo(s)."

    lines = [f"# Cross-org results for: {query}\n"]
    for i, h in enumerate(hits[:limit], 1):
        score = h.get("score", "")
        snippet = h.get("snippet", "").strip()
        title = h.get("title", h["file"])
        repo_name = h.get("_repo_name", "")
        lines.append(f"{i}. **{title}** ({repo_name}: {h['file']}) score: {score}\n   {snippet}\n")
    return "\n".join(lines)


def find_service_owner(service: str, repos: list) -> str:
    """Return owner info for a service. Checks engineering services.md first, then team wikis."""
    from compost.mcp.federation import fan_out_query, find_owner_in_services_md
    row = find_owner_in_services_md(service, repos)
    if row:
        return (
            f"# Owner info for: {service}\n\n"
            f"**Team:** {row.get('owner_team', 'unknown')}\n"
            f"**Repo:** {row.get('repo', 'unknown')}\n"
            f"**Status:** {row.get('status', 'unknown')}\n"
        )

    team_repos = [r for r in repos if r.role == "team"]
    hits = fan_out_query(f"service {service}", team_repos, limit_per_repo=3)
    if not hits:
        return f"No owner information found for service '{service}'."
    top = hits[0]
    return (
        f"# Owner info for: {service} (via wiki search)\n\n"
        f"**Source:** {top.get('_repo_name', '')}: {top['file']}\n"
        f"{top.get('snippet', '').strip()}\n"
    )


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
