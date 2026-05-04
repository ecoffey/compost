from __future__ import annotations

from pathlib import Path


def _load_prompt(name: str) -> str:
    """Load a prompt template from synth/prompts/{name}.md."""
    prompts_dir = Path(__file__).parent / "prompts"
    return (prompts_dir / f"{name}.md").read_text(encoding="utf-8")


def build_find_affected_messages(
    raw_content: str,
    candidates: list[dict],
) -> tuple[list[dict], str]:
    """Return (messages, system_prompt) for the find_affected Phase A call."""
    system = _load_prompt("find_affected")

    candidate_text = "\n\n".join(
        f"### {h.get('title', h['file'])} ({h['file']})\n{h.get('snippet', '').strip()}"
        for h in candidates
    )

    user = (
        f"## Raw file content\n\n{raw_content}\n\n"
        f"## Candidate wiki pages\n\n{candidate_text}"
    )

    return [{"role": "user", "content": user}], system


def build_propose_edit_messages(
    raw_content: str,
    page_content: str,
    page_path: str,
) -> tuple[list[dict], str]:
    """Return (messages, system_prompt) for the propose_edit Phase B call."""
    system = _load_prompt("propose_edit")

    user = (
        f"## Raw file content\n\n{raw_content}\n\n"
        f"## Existing wiki page: {page_path}\n\n"
        f"{page_content if page_content else '(page does not exist yet — create it)'}"
    )

    return [{"role": "user", "content": user}], system
