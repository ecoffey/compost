from __future__ import annotations

import json
import subprocess


def qmd_query(query: str, index: str, collection: str, limit: int = 5) -> list[dict]:
    """BM25+vec search; returns hit dicts with keys: file, snippet, score, title."""
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


def qmd_get(file_uri: str, index: str) -> str:
    """Return full document text for a qmd:// URI or relative path."""
    result = subprocess.run(
        ["qmd", "--index", index, "get", file_uri, "--full"],
        capture_output=True, text=True,
    )
    return result.stdout if result.returncode == 0 else ""
