from __future__ import annotations

import subprocess
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from team_context.mcp.tools import load_service_context, query_wiki
from team_context.repo import load_repo_config

server = Server("team-context")

_active_repo: Path = Path.cwd()


def _get_index_name(repo: Path) -> str:
    config = load_repo_config(repo)
    return config["qmd_index"]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
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
    global _active_repo
    _active_repo = repo
    _warmup(_get_index_name(repo))
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream,
            server.create_initialization_options(),
        )
