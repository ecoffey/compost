from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from compost.ingest.git import stage_and_commit

if TYPE_CHECKING:
    from compost.synth.agent import SynthesisResult


def apply_diffs(repo: Path, result: "SynthesisResult") -> list[Path]:
    """Write FileDiff.content to working tree. Returns absolute paths written."""
    written: list[Path] = []
    for diff in result.wiki_diffs:
        abs_path = repo / diff.rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(diff.content, encoding="utf-8")
        written.append(abs_path)
    return written


def commit_diffs(repo: Path, written: list[Path], run_id: str) -> None:
    """Stage written paths and commit on the current branch."""
    if not written:
        return
    stage_and_commit(repo, written, message=f"synth: wiki edits ({run_id})")
