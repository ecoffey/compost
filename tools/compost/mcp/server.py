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
    # Prime the qmd embedding model before the first real query.
    # qmd lazy-loads the model on first use; without this the first tool call
    # in a session can return empty results while the model initialises.
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
