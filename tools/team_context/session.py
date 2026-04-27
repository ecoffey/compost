from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator


def _project_dir(repo: Path) -> Path:
    slug = str(repo).replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug


def _ts_from_file(path: Path) -> str | None:
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                ts = obj.get("timestamp")
                if ts:
                    return ts
    except Exception:
        pass
    return None


def list_sessions(repo: Path) -> list[dict]:
    """Return sessions for repo, newest first."""
    d = _project_dir(repo)
    if not d.exists():
        return []
    sessions = []
    for p in d.glob("*.jsonl"):
        sessions.append({"id": p.stem, "path": p, "ts": _ts_from_file(p)})
    return sorted(sessions, key=lambda s: s["ts"] or "", reverse=True)


def find_session(session_id: str) -> Path | None:
    """Find a session .jsonl by full or prefix ID, searching all project dirs."""
    projects = Path.home() / ".claude" / "projects"
    if not projects.exists():
        return None
    matches = [p for p in projects.rglob("*.jsonl")
               if p.stem == session_id or p.stem.startswith(session_id)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # Prefer exact match
        exact = [p for p in matches if p.stem == session_id]
        return exact[0] if exact else matches[0]
    return None


def _iter_entries(path: Path) -> Iterator[dict]:
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                pass


def format_session(
    path: Path,
    tools_only: bool = False,
    mcp_only: bool = False,
    snippet_len: int = 400,
) -> str:
    """Format a session transcript as readable text.

    tools_only: show only tool calls and their results.
    mcp_only: show only MCP tool calls (mcp__*) and their results.
    snippet_len: max characters shown for tool results.
    """
    # tool_use_id -> name, used to decide whether to show the paired result
    pending: dict[str, str] = {}
    blocks: list[str] = []

    for entry in _iter_entries(path):
        role = entry.get("type")
        content = entry.get("message", {}).get("content", [])

        if role == "assistant":
            for item in content:
                if not isinstance(item, dict):
                    continue
                itype = item.get("type")
                if itype == "text":
                    if not tools_only and not mcp_only:
                        text = item["text"].strip()
                        if text:
                            blocks.append(f"ASST  {text}")
                elif itype == "tool_use":
                    name = item.get("name", "")
                    uid = item.get("id", "")
                    if mcp_only and not name.startswith("mcp__"):
                        continue
                    pending[uid] = name
                    inp = json.dumps(item.get("input", {}), indent=2)
                    inp_indented = _indent(inp, "      ")
                    blocks.append(f"TOOL  {name}\n{inp_indented}")

        elif role == "user":
            for item in content:
                if not isinstance(item, dict):
                    continue
                itype = item.get("type")
                if itype == "text":
                    if not tools_only and not mcp_only:
                        text = item["text"].strip()
                        if text and not text.startswith("<"):
                            blocks.append(f"USER  {text}")
                elif itype == "tool_result":
                    uid = item.get("tool_use_id", "")
                    if uid not in pending:
                        continue
                    del pending[uid]
                    result_text = ""
                    for c in item.get("content", []):
                        if isinstance(c, dict) and c.get("type") == "text":
                            result_text = c["text"]
                            break
                    if result_text:
                        truncated = result_text[:snippet_len]
                        if len(result_text) > snippet_len:
                            truncated += f"\n  ... ({len(result_text) - snippet_len} more chars)"
                        blocks.append(f"RESU  {_indent(truncated.strip(), '      ')}")

    return "\n\n".join(blocks)


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())
