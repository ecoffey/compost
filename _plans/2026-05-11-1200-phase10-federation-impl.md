# Phase 10: Federation — Implementation Detail

## § Source plan

[2026-05-08-1744-phase10-11-federation-derive-high-level.md](2026-05-08-1744-phase10-11-federation-derive-high-level.md)
(Phase 11 will get its own impl plan.)

---

## § Approach

TDD: write failing test, minimal code to pass, refactor. Tests are integration-style (real qmd processes where needed, no mock libraries). See cross-check section at the bottom for consistency notes against the high-level plan.

---

## § Step-by-step instructions

### Step 1 — Add derivation fields to Frontmatter TypedDict

**File:** `tools/compost/model/frontmatter.py`

Add four optional fields to the `Frontmatter` TypedDict (after `superseded_by`):

```python
    # derivation fields (Phase 10+)
    depends_on: list[str]
    depended_on_by: list[str]
    technology: list[str]
    org_registered: bool
```

No changes to `REQUIRED_FIELDS` or `validate_frontmatter` — these are purely optional.

**Test:** `tools/compost/tests/test_frontmatter.py` — add at the end:

```python
def test_validate_accepts_derivation_fields() -> None:
    fm = {
        "type": "service", "name": "payments", "owners": ["alice"],
        "status": "active", "updated": "2026-01-01", "confidence": "high",
        "sources": ["raw/decisions/0001.md"],
        "depends_on": ["auth", "db"],
        "depended_on_by": ["checkout"],
        "technology": ["python", "postgres"],
        "org_registered": True,
    }
    errors = validate_frontmatter(fm, Path("wiki/services/payments.md"))
    assert errors == []


def test_validate_derivation_fields_absent_is_valid() -> None:
    fm = {
        "type": "service", "name": "payments", "owners": ["alice"],
        "status": "active", "updated": "2026-01-01", "confidence": "high",
        "sources": ["raw/decisions/0001.md"],
    }
    errors = validate_frontmatter(fm, Path("wiki/services/payments.md"))
    assert errors == []
```

---

### Step 2 — Create `tools/compost/mcp/federation.py`

This module owns all federation config loading and parallel query fan-out. Nothing outside this module reads `~/.compost/federation.yaml` directly.

```python
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
        if set(c.strip("-:") for c in cells) == {""}:
            # separator row
            continue
        if len(cells) >= len(headers):
            rows.append(dict(zip(headers, cells)))
    return rows
```

**Tests** (Step 5 below covers `test_federation.py` — write the test first, then come back and verify this implementation passes).

---

### Step 3 — Extend `tools/compost/mcp/tools.py`

Add two functions after `query_wiki`. These do MCP-specific formatting on top of the federation layer.

```python
def query_across_org(query: str, repos: list, limit: int = 10) -> str:
    """Fan out to all configured repos, format merged results."""
    from compost.mcp.federation import fan_out_query
    hits = fan_out_query(query, repos, limit_per_repo=max(3, limit // max(len(repos), 1)))
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
    from compost.mcp.federation import find_owner_in_services_md, fan_out_query
    row = find_owner_in_services_md(service, repos)
    if row:
        return (
            f"# Owner info for: {service}\n\n"
            f"**Team:** {row.get('owner_team', 'unknown')}\n"
            f"**Repo:** {row.get('repo', 'unknown')}\n"
            f"**Status:** {row.get('status', 'unknown')}\n"
        )

    # Fall back to searching team repos.
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
```

Also add the `repos` import at the top of `tools.py` (only used in the new functions so the import is inside the function body — no top-level change needed).

---

### Step 4 — Extend `tools/compost/mcp/server.py`

Two changes:
1. Load federation config once at server startup; register two extra tools if repos are configured.
2. Warmup all federated indexes at startup.

Full revised file:

```python
from __future__ import annotations

import subprocess
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from compost.mcp.federation import FederatedRepo, load_federation_config
from compost.mcp.tools import load_service_context, query_wiki
from compost.repo import load_repo_config

server = Server("compost")

_active_repo: Path = Path.cwd()
_federated_repos: list[FederatedRepo] = []


def _get_index_name(repo: Path) -> str:
    config = load_repo_config(repo)
    return config["qmd_index"]


@server.list_tools()
async def list_tools() -> list[Tool]:
    tools = [
        Tool(
            name="load_service_context",
            description=(
                "Load wiki context for a named service. Returns the service page, "
                "related decisions, and recent incidents."
            ),
            inputSchema={
                "type": "object",
                "properties": {"service": {"type": "string"}},
                "required": ["service"],
            },
        ),
        Tool(
            name="query_wiki",
            description=(
                "Hybrid search over the team wiki. "
                "scope: 'team' (default) searches wiki pages; "
                "'raw' searches raw source records; "
                "'decisions' searches ADRs; 'incidents' searches postmortems."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "scope": {
                        "type": "string",
                        "enum": ["team", "raw", "decisions", "incidents"],
                        "default": "team",
                    },
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        ),
    ]

    if _federated_repos:
        tools.append(Tool(
            name="query_across_org",
            description=(
                "Fan out a query to all configured repos in the federation. "
                "Merges and deduplicates results across teams."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
        ))
        tools.append(Tool(
            name="find_service_owner",
            description=(
                "Look up who owns a service. Checks the engineering services index "
                "first; falls back to searching team wikis."
            ),
            inputSchema={
                "type": "object",
                "properties": {"service": {"type": "string"}},
                "required": ["service"],
            },
        ))

    return tools


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    repo = _active_repo
    index = _get_index_name(repo)

    if name == "load_service_context":
        result = load_service_context(arguments["service"], index, repo)
    elif name == "query_wiki":
        result = query_wiki(
            arguments["query"],
            index,
            scope=arguments.get("scope", "team"),
            limit=arguments.get("limit", 5),
        )
    elif name == "query_across_org":
        from compost.mcp.tools import query_across_org
        result = query_across_org(
            arguments["query"],
            _federated_repos,
            limit=arguments.get("limit", 10),
        )
    elif name == "find_service_owner":
        from compost.mcp.tools import find_service_owner
        result = find_service_owner(arguments["service"], _federated_repos)
    else:
        result = f"Unknown tool: {name}"

    return [TextContent(type="text", text=result)]


def _warmup(index: str) -> None:
    subprocess.run(
        ["qmd", "--index", index, "query", "warmup",
         "--collection", "wiki", "-n", "1", "--no-rerank", "--json"],
        capture_output=True,
    )


async def run_server(repo: Path) -> None:
    global _active_repo, _federated_repos
    _active_repo = repo
    _federated_repos = load_federation_config()

    _warmup(_get_index_name(repo))
    for fed_repo in _federated_repos:
        _warmup(fed_repo.qmd_index)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream,
            server.create_initialization_options(),
        )
```

---

### Step 5 — Create `tools/compost/tests/test_federation.py`

Write this file **before** implementing `federation.py` (TDD red phase). The tests cover:

1. `load_federation_config` returns `[]` when file absent
2. `load_federation_config` parses a real YAML file
3. `fan_out_query` merges results from two real qmd indexes (integration test, marked `pytest.mark.integration`)
4. `find_owner_in_services_md` parses a services.md table
5. `find_owner_in_services_md` returns `None` for unknown service
6. `_parse_services_table` handles edge cases

```python
from __future__ import annotations

import subprocess
import textwrap
import uuid
from pathlib import Path

import pytest
import yaml

from compost.mcp.federation import (
    FederatedRepo,
    _parse_services_table,
    fan_out_query,
    find_owner_in_services_md,
    load_federation_config,
)


# ---------------------------------------------------------------------------
# load_federation_config
# ---------------------------------------------------------------------------

def test_load_federation_config_absent_returns_empty(tmp_path: Path) -> None:
    result = load_federation_config(tmp_path / "nonexistent.yaml")
    assert result == []


def test_load_federation_config_parses_yaml(tmp_path: Path) -> None:
    cfg = tmp_path / "federation.yaml"
    cfg.write_text(textwrap.dedent("""\
        repos:
          - name: my-team
            path: /tmp/my-team
            qmd_index: my-team-idx
            role: team
          - name: eng
            path: /tmp/eng
            qmd_index: eng-idx
            role: engineering
    """))
    repos = load_federation_config(cfg)
    assert len(repos) == 2
    assert repos[0].name == "my-team"
    assert repos[0].role == "team"
    assert repos[1].role == "engineering"
    assert repos[1].qmd_index == "eng-idx"


def test_load_federation_config_empty_file_returns_empty(tmp_path: Path) -> None:
    cfg = tmp_path / "empty.yaml"
    cfg.write_text("")
    assert load_federation_config(cfg) == []


# ---------------------------------------------------------------------------
# _parse_services_table
# ---------------------------------------------------------------------------

def test_parse_services_table_basic() -> None:
    text = textwrap.dedent("""\
        # Services

        | service | owner_team | repo | status |
        |---------|------------|------|--------|
        | payments | team-alpha | team-context-test | active |
        | auth | team-beta | team-context-test-2 | active |
    """)
    rows = _parse_services_table(text)
    assert len(rows) == 2
    assert rows[0]["service"] == "payments"
    assert rows[0]["owner_team"] == "team-alpha"
    assert rows[1]["service"] == "auth"


def test_parse_services_table_empty_returns_empty() -> None:
    assert _parse_services_table("# No table here") == []


# ---------------------------------------------------------------------------
# find_owner_in_services_md
# ---------------------------------------------------------------------------

def test_find_owner_returns_row(tmp_path: Path) -> None:
    eng_path = tmp_path / "eng"
    (eng_path / "wiki").mkdir(parents=True)
    (eng_path / "wiki" / "services.md").write_text(textwrap.dedent("""\
        | service | owner_team | repo | status |
        |---------|------------|------|--------|
        | payments | team-alpha | team-context-test | active |
    """))
    repos = [FederatedRepo(
        name="eng", path=eng_path, qmd_index="eng-idx", role="engineering"
    )]
    result = find_owner_in_services_md("payments", repos)
    assert result is not None
    assert result["owner_team"] == "team-alpha"


def test_find_owner_case_insensitive(tmp_path: Path) -> None:
    eng_path = tmp_path / "eng"
    (eng_path / "wiki").mkdir(parents=True)
    (eng_path / "wiki" / "services.md").write_text(textwrap.dedent("""\
        | service | owner_team | repo | status |
        |---------|------------|------|--------|
        | Payments | team-alpha | tc-test | active |
    """))
    repos = [FederatedRepo(
        name="eng", path=eng_path, qmd_index="eng-idx", role="engineering"
    )]
    assert find_owner_in_services_md("payments", repos) is not None
    assert find_owner_in_services_md("PAYMENTS", repos) is not None


def test_find_owner_unknown_service_returns_none(tmp_path: Path) -> None:
    eng_path = tmp_path / "eng"
    (eng_path / "wiki").mkdir(parents=True)
    (eng_path / "wiki" / "services.md").write_text(textwrap.dedent("""\
        | service | owner_team | repo | status |
        |---------|------------|------|--------|
        | payments | team-alpha | tc-test | active |
    """))
    repos = [FederatedRepo(
        name="eng", path=eng_path, qmd_index="eng-idx", role="engineering"
    )]
    assert find_owner_in_services_md("unknown-service", repos) is None


def test_find_owner_no_eng_repo_returns_none(tmp_path: Path) -> None:
    repos = [FederatedRepo(
        name="team-a", path=tmp_path, qmd_index="team-a", role="team"
    )]
    assert find_owner_in_services_md("payments", repos) is None


def test_find_owner_missing_services_md_returns_none(tmp_path: Path) -> None:
    eng_path = tmp_path / "eng"
    (eng_path / "wiki").mkdir(parents=True)
    # No services.md created
    repos = [FederatedRepo(
        name="eng", path=eng_path, qmd_index="eng-idx", role="engineering"
    )]
    assert find_owner_in_services_md("payments", repos) is None


# ---------------------------------------------------------------------------
# fan_out_query — qmd per-index isolation (integration, requires qmd binary)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fan_out_query_index_isolation(tmp_path: Path) -> None:
    """Two qmd indexes with the same collection name must not share content."""
    uid = uuid.uuid4().hex[:8]
    idx_a = f"test-iso-a-{uid}"
    idx_b = f"test-iso-b-{uid}"

    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    for d in (repo_a / "wiki", repo_b / "wiki"):
        d.mkdir(parents=True)

    (repo_a / "wiki" / "alpha.md").write_text(
        "---\ntype: concept\nname: alpha-concept\nowners: [alice]\n"
        "status: active\nupdated: 2026-01-01\nconfidence: high\n"
        "sources: [raw/x.md]\n---\n# Alpha Concept\nUnique alpha xyzzy content.\n"
    )
    (repo_b / "wiki" / "beta.md").write_text(
        "---\ntype: concept\nname: beta-concept\nowners: [bob]\n"
        "status: active\nupdated: 2026-01-01\nconfidence: high\n"
        "sources: [raw/y.md]\n---\n# Beta Concept\nUnique beta qwerty content.\n"
    )

    # Register collections
    subprocess.run(
        ["qmd", "--index", idx_a, "collection", "add", "wiki", "wiki", "**/*.md"],
        cwd=str(repo_a), capture_output=True, check=True,
    )
    subprocess.run(
        ["qmd", "--index", idx_b, "collection", "add", "wiki", "wiki", "**/*.md"],
        cwd=str(repo_b), capture_output=True, check=True,
    )

    # Index both
    subprocess.run(
        ["qmd", "--index", idx_a, "update"],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["qmd", "--index", idx_b, "update"],
        capture_output=True, check=True,
    )

    fed_a = FederatedRepo(name="repo-a", path=repo_a, qmd_index=idx_a, role="team")
    fed_b = FederatedRepo(name="repo-b", path=repo_b, qmd_index=idx_b, role="team")

    hits_a = fan_out_query("alpha xyzzy", [fed_a], limit_per_repo=5)
    hits_b = fan_out_query("beta qwerty", [fed_b], limit_per_repo=5)

    # Index A must not surface content from B, and vice versa
    a_files = [h["file"] for h in hits_a]
    b_files = [h["file"] for h in hits_b]
    assert not any("beta" in f for f in a_files), f"Index A leaked B content: {a_files}"
    assert not any("alpha" in f for f in b_files), f"Index B leaked A content: {b_files}"

    # fan_out_query across both must include results from both repos
    both = fan_out_query("concept", [fed_a, fed_b], limit_per_repo=5)
    repo_names = {h["_repo_name"] for h in both}
    assert "repo-a" in repo_names
    assert "repo-b" in repo_names


@pytest.mark.integration
def test_fan_out_query_deduplicates_by_title(tmp_path: Path) -> None:
    """When both repos have a page with the same title, only the higher-score hit appears."""
    uid = uuid.uuid4().hex[:8]
    idx_a = f"test-dedup-a-{uid}"
    idx_b = f"test-dedup-b-{uid}"

    for repo_dir, idx in [(tmp_path / "da", idx_a), (tmp_path / "db", idx_b)]:
        (repo_dir / "wiki").mkdir(parents=True)
        (repo_dir / "wiki" / "shared.md").write_text(
            "---\ntype: concept\nname: shared-concept\nowners: [alice]\n"
            "status: active\nupdated: 2026-01-01\nconfidence: high\n"
            "sources: [raw/x.md]\n---\n# Shared Concept\nDuplicate content.\n"
        )
        subprocess.run(
            ["qmd", "--index", idx, "collection", "add", "wiki", "wiki", "**/*.md"],
            cwd=str(repo_dir), capture_output=True, check=True,
        )
        subprocess.run(["qmd", "--index", idx, "update"], capture_output=True, check=True)

    fed_a = FederatedRepo(name="da", path=tmp_path / "da", qmd_index=idx_a, role="team")
    fed_b = FederatedRepo(name="db", path=tmp_path / "db", qmd_index=idx_b, role="team")

    hits = fan_out_query("shared concept", [fed_a, fed_b], limit_per_repo=3)
    titles = [h.get("title", h["file"]) for h in hits]
    # No duplicate titles
    assert len(titles) == len(set(titles)), f"Duplicate titles found: {titles}"


@pytest.mark.integration
def test_fan_out_query_role_filter(tmp_path: Path) -> None:
    """roles filter restricts fan-out to matching repo roles."""
    uid = uuid.uuid4().hex[:8]
    idx_team = f"test-role-team-{uid}"
    idx_eng = f"test-role-eng-{uid}"

    for repo_dir, idx in [(tmp_path / "team", idx_team), (tmp_path / "eng", idx_eng)]:
        (repo_dir / "wiki").mkdir(parents=True)
        (repo_dir / "wiki" / "page.md").write_text(
            "---\ntype: concept\nname: thing\nowners: [alice]\n"
            "status: active\nupdated: 2026-01-01\nconfidence: high\n"
            "sources: [raw/x.md]\n---\n# Thing\nContent here.\n"
        )
        subprocess.run(
            ["qmd", "--index", idx, "collection", "add", "wiki", "wiki", "**/*.md"],
            cwd=str(repo_dir), capture_output=True, check=True,
        )
        subprocess.run(["qmd", "--index", idx, "update"], capture_output=True, check=True)

    fed_team = FederatedRepo(name="team", path=tmp_path / "team", qmd_index=idx_team, role="team")
    fed_eng = FederatedRepo(name="eng", path=tmp_path / "eng", qmd_index=idx_eng, role="engineering")

    hits = fan_out_query("thing", [fed_team, fed_eng], roles=["team"], limit_per_repo=3)
    repo_names = {h["_repo_name"] for h in hits}
    assert "team" in repo_names
    assert "eng" not in repo_names
```

---

### Step 6 — Create `tools/compost/cli/federation.py`

```python
from __future__ import annotations

import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table

console = Console()

_DEFAULT_CONFIG = Path.home() / ".compost" / "federation.yaml"


@click.group("federation")
def federation_group() -> None:
    """Manage the multi-repo federation config (~/.compost/federation.yaml)."""


@federation_group.command("list")
def federation_list() -> None:
    """Show all repos registered in the federation config."""
    from compost.mcp.federation import load_federation_config
    repos = load_federation_config()
    if not repos:
        console.print("[dim]No repos registered. Use 'compost federation add' to register one.[/dim]")
        return
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Name")
    table.add_column("Role")
    table.add_column("qmd index")
    table.add_column("Path")
    for r in repos:
        table.add_row(r.name, r.role, r.qmd_index, str(r.path))
    console.print(table)


@federation_group.command("add")
@click.option("--name", required=True, help="Repo identifier (unique).")
@click.option("--path", "repo_path", required=True, type=click.Path(), help="Absolute path to the repo.")
@click.option("--role", required=True,
              type=click.Choice(["team", "org", "engineering"]),
              help="Role this repo plays in the federation.")
@click.option("--qmd-index", required=True, help="qmd index name for this repo.")
def federation_add(name: str, repo_path: str, role: str, qmd_index: str) -> None:
    """Register a repo in ~/.compost/federation.yaml."""
    resolved = Path(repo_path).expanduser().resolve()
    if not resolved.exists():
        console.print(f"[red]Path does not exist: {resolved}[/red]")
        sys.exit(1)

    _DEFAULT_CONFIG.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if _DEFAULT_CONFIG.exists():
        existing = yaml.safe_load(_DEFAULT_CONFIG.read_text()) or {}

    repos = existing.get("repos", [])
    if any(r["name"] == name for r in repos):
        console.print(f"[yellow]A repo named '{name}' is already registered. Remove it first.[/yellow]")
        sys.exit(1)

    repos.append({
        "name": name,
        "path": str(resolved),
        "qmd_index": qmd_index,
        "role": role,
    })
    existing["repos"] = repos
    _DEFAULT_CONFIG.write_text(yaml.dump(existing, default_flow_style=False))
    console.print(f"[green]Registered '{name}' ({role}) in {_DEFAULT_CONFIG}[/green]")


@federation_group.command("doctor")
def federation_doctor() -> None:
    """Verify each federated repo: path exists, .compost.yml present, qmd collections indexed."""
    from compost.cli._helpers import _qmd_collections
    from compost.mcp.federation import load_federation_config

    repos = load_federation_config()
    if not repos:
        console.print("[dim]No repos in federation config.[/dim]")
        return

    all_ok = True
    for repo in repos:
        console.print(f"\n[bold]{repo.name}[/bold] ({repo.role})")

        path_ok = repo.path.exists()
        _check("path exists", path_ok, str(repo.path))
        if not path_ok:
            all_ok = False
            continue

        compost_yml = (repo.path / ".compost.yml").exists()
        _check(".compost.yml", compost_yml, "")
        all_ok = all_ok and compost_yml

        registered = _qmd_collections(repo.qmd_index)
        wiki_ok = "wiki" in registered
        _check("qmd wiki collection", wiki_ok,
               f"run: qmd --index {repo.qmd_index} collection add wiki wiki '**/*.md' (from repo root)")
        all_ok = all_ok and wiki_ok

    if not all_ok:
        sys.exit(1)


def _check(label: str, ok: bool, msg: str) -> None:
    icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
    detail = f"  [dim]{msg}[/dim]" if msg and not ok else ""
    console.print(f"  {icon} {label}{detail}")
```

---

### Step 7 — Register `federation_group` in `tools/compost/cli/__init__.py`

Add the import after the existing imports:

```python
from compost.cli.federation import federation_group
```

Add before (or after) the last `main.add_command(lint_group)` line:

```python
main.add_command(federation_group)
```

---

### Step 8 — Bootstrap `~/local-test/engineering-context/`

Run these commands after the CLI code is working:

```bash
compost init ~/local-test/engineering-context --name engineering-context
```

Then create `~/local-test/engineering-context/wiki/services.md`:

```markdown
# Services

| service | owner_team | repo | status |
|---------|------------|------|--------|
| payments | team-alpha | team-context-test | active |
| auth | team-alpha | team-context-test | active |
```

Create `~/local-test/engineering-context/ENGINEERING.md`:

```markdown
# Engineering Context

Organization-wide engineering reference.

## Service Index

See [wiki/services.md](wiki/services.md) for the master service list.
```

Add a `.compost.yml` with `role` for reference (not used by code, just documentation):

```yaml
name: engineering-context
qmd_index: eng-context
```

Note: `compost init` writes this without `role`; that's fine since role lives in `federation.yaml`, not `.compost.yml`.

Register the engineering repo's wiki collection (qmd):

```bash
cd ~/local-test/engineering-context
qmd --index eng-context collection add wiki wiki "**/*.md"
qmd --index eng-context update
```

---

### Step 9 — Bootstrap `~/local-test/team-context-test-2/`

```bash
compost init ~/local-test/team-context-test-2 --name team-context-test-2
cd ~/local-test/team-context-test-2
qmd --index team-context-test-2 update
```

Add a seed wiki page so federation queries return results:

`~/local-test/team-context-test-2/wiki/services/auth.md`:

```markdown
---
type: service
name: auth
owners: [bob]
status: active
updated: 2026-05-11
confidence: high
sources:
  - raw/notes/seed.md
related: []
supersedes: []
superseded_by: null
depends_on: []
depended_on_by: [payments]
technology: [python, jwt]
org_registered: true
---

# Auth Service

Handles authentication and session management.

## Summary
JWT-based auth service used by all team services.
```

Create the source note it references:
`~/local-test/team-context-test-2/raw/notes/seed.md`:

```markdown
---
source: note
captured_at: 2026-05-11T12:00:00Z
captured_by: bob
origin: internal
---
Seed note for auth service.
```

Re-index after writing:

```bash
cd ~/local-test/team-context-test-2
qmd --index team-context-test-2 update
```

---

### Step 10 — Create `~/.compost/federation.yaml`

After the CLI is working, run:

```bash
compost federation add \
  --name team-context-test \
  --path ~/local-test/team-context-test \
  --role team \
  --qmd-index tc-test

compost federation add \
  --name team-context-test-2 \
  --path ~/local-test/team-context-test-2 \
  --role team \
  --qmd-index team-context-test-2

compost federation add \
  --name engineering-context \
  --path ~/local-test/engineering-context \
  --role engineering \
  --qmd-index eng-context
```

Verify: `compost federation list` and `compost federation doctor`.

---

### Step 11 — Steel thread smoke test

From Claude Code pointed at `compost mcp --repo ~/local-test/team-context-test`:

1. Call `find_service_owner("payments")` — should return team-alpha from `engineering-context/wiki/services.md`.
2. Call `query_across_org("auth service")` — should return hits from both team repos.
3. Call `find_service_owner("unknown-xyz")` — should fall back to team wiki search and return a graceful "not found" message.

---

## § Test execution plan

TDD order per IMPLEMENTATION_STRATEGY.md:

1. Write `test_federation.py` (Step 5) — all tests should fail since the module doesn't exist.
2. Implement `federation.py` (Step 2) — run non-integration tests to go green.
3. Write `test_frontmatter.py` additions (Step 1a) — fail.
4. Add derivation fields to `frontmatter.py` (Step 1) — go green.
5. Implement `tools.py` additions (Step 3) — no isolated unit tests; covered by integration tests.
6. Implement `server.py` changes (Step 4) — covered by existing server behavior; no new tests needed (server is a thin wiring layer).
7. Implement `cli/federation.py` (Step 6) — run `compost federation --help` to smoke test.
8. Register in `cli/__init__.py` (Step 7).
9. Run integration tests: `pytest -m integration tools/compost/tests/test_federation.py`
10. Bootstrap repos (Steps 8-10).
11. Steel thread smoke test (Step 11).

Run full test suite: `pytest tools/compost/tests/` (excludes integration by default unless `-m integration` passed).

---

## § Documentation updates

No separate doc files to create (per project conventions). Module-level docstrings added inline:

- `federation.py` module docstring: "Federation config loader and multi-repo query fan-out. Single point of ownership for ~/.compost/federation.yaml format."
- `cli/federation.py`: no docstring needed; Click group help string serves as docs.

---

## § Cross-check: high-level plan vs. this impl plan

The following items from the high-level plan are **fully covered**:

- `tools/compost/mcp/federation.py` — FederatedRepo, load_federation_config, fan_out_query: ✓
- `tools/compost/mcp/tools.py` — query_across_org, find_service_owner: ✓
- `tools/compost/mcp/server.py` — federation tools registered when config present: ✓
- `tools/compost/model/frontmatter.py` — derivation fields: ✓
- `~/.compost/federation.yaml` — created via CLI: ✓
- `~/local-test/engineering-context/` — bootstrapped: ✓
- `~/local-test/team-context-test-2/` — bootstrapped: ✓
- `tools/compost/cli/federation.py` — list/add/doctor: ✓
- `tools/compost/tests/test_federation.py` — qmd per-index isolation, fan_out_query, find_service_owner, config absent: ✓
- Frontmatter test extensions: ✓
- Federation config absent → server starts without federation tools (load_federation_config returns []): ✓
- Warmup of all federated indexes at server startup: ✓

The following item from the high-level plan is **intentionally dropped**:

- `compost claims suggest` command (mentioned in backlog context section). The high-level plan notes this as a Phase 10 deliverable "since it reads from the same wiki graph that federation will surface." However it was not included in the Phase 10 CLI additions table and requires Phase 5 check output data that is not yet structured for claims extraction. **Dropped from this impl; stays in backlog.**

The following item is **deferred per the high-level plan**:

- Persistent qmd process / single long-lived qmd server. The high-level plan explicitly defers this to Phase 12+. The current fan_out_query spawns subprocess calls per query, matching Phase 10 scope.

**Ambiguity resolved:** The high-level plan shows `compost federation init` in a flowchart but the CLI additions table only lists `list`, `add`, `doctor`. This impl uses `compost init` (existing command) for repo creation and `compost federation add` for registration — matching the explicit CLI table. No `federation init` subcommand is added.
