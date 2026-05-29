from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from compost.model.frontmatter import parse_frontmatter
from compost.qmd import qmd_get, qmd_query
from compost.repo import load_repo_config
from compost.synth.log import log_event, open_run
from compost.synth.prompt import build_find_affected_messages, build_propose_edit_messages


@dataclass(frozen=True)
class SynthConfig:
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    max_candidates: int = 10


@dataclass(frozen=True)
class FileDiff:
    rel_path: Path
    content: str
    is_new: bool


@dataclass(frozen=True)
class ContradictionNote:
    wiki_page: str
    summary: str


@dataclass(frozen=True)
class SynthesisResult:
    run_id: str
    wiki_diffs: list[FileDiff]
    pr_description: str
    cited_sources: list[str]
    declared_contradictions: list[ContradictionNote]


def load_synth_config(repo: Path) -> SynthConfig:
    """Read synth: block from .compost.yml; fall back to SynthConfig defaults."""
    config = load_repo_config(repo)
    synth = config.get("synth", {})
    return SynthConfig(
        provider=synth.get("provider", "anthropic"),
        model=synth.get("model", "claude-sonnet-4-6"),
        max_candidates=int(synth.get("max_candidates", 10)),
    )


def synthesize(
    raw_path: Path,
    repo: Path,
    *,
    config: SynthConfig | None = None,
    dry_run: bool = False,
) -> SynthesisResult:
    """Query qmd for candidates, call LLM to find affected pages, propose edits.

    When dry_run=True: no log file is written. Returns a populated SynthesisResult regardless.
    """
    if config is None:
        config = load_synth_config(repo)

    run_id = uuid.uuid4().hex[:8]
    log_path = open_run(repo, run_id) if not dry_run else None
    start = time.monotonic()

    repo_config = load_repo_config(repo)
    index = repo_config["qmd_index"]
    raw_rel = str(raw_path.relative_to(repo))
    raw_content = raw_path.read_text()
    _, raw_body = parse_frontmatter(raw_path)

    _log(log_path, "run_start",
         run_id=run_id, raw=raw_rel, model=config.model, provider=config.provider)

    query_text = (raw_body or raw_content).replace("\n", " ")[:300]
    candidates = qmd_query(query_text, index, "wiki", limit=config.max_candidates)

    _log(log_path, "qmd_candidates",
         run_id=run_id, count=len(candidates),
         pages=[h["file"] for h in candidates])

    if not candidates:
        duration = time.monotonic() - start
        _log(log_path, "run_complete", run_id=run_id,
             raw=raw_rel, total_cost_usd=0.0, wiki_edits=0, contradictions=0,
             duration_s=round(duration, 2))
        return SynthesisResult(
            run_id=run_id, wiki_diffs=[],
            pr_description="**Tier 2 synthesis:**\n- no candidate wiki pages found\n\n**Contradictions declared:** none",
            cited_sources=[], declared_contradictions=[],
        )

    total_cost = 0.0
    total_input_tokens = 0
    total_output_tokens = 0
    call_n = 0

    # Phase A: find which pages need updating
    messages, system = build_find_affected_messages(raw_content, candidates)
    call_n += 1
    _log(log_path, "llm_call", run_id=run_id, call_n=call_n, purpose="find_affected")

    text, usage = _call_provider(config, system, messages, cache_system=False)
    cost = _compute_cost(config.model, usage)
    total_cost += cost
    total_input_tokens += usage["input_tokens"]
    total_output_tokens += usage["output_tokens"]
    affected = _parse_find_affected(text)

    _log(log_path, "llm_response", run_id=run_id, call_n=call_n,
         input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
         cost_usd=round(cost, 6), affected_count=len(affected))

    # Phase B: propose edits for each affected page
    wiki_diffs: list[FileDiff] = []
    cited: list[str] = []
    contradictions: list[ContradictionNote] = []

    for item in affected:
        page_path = item.get("page", "")
        if not page_path:
            continue
        # Strip qmd:// URI prefix that the LLM may echo back from candidate file keys.
        # The LLM sometimes returns the full heading format "Title (qmd://path)" —
        # extract just the URI in that case.
        import re as _re
        qmd_match = _re.search(r'qmd://([^\s)]+)', page_path)
        if qmd_match:
            page_path = qmd_match.group(1)
        elif page_path.startswith("qmd://"):
            page_path = page_path[len("qmd://"):]
        is_new_hint = item.get("is_new", False)
        abs_page = repo / page_path
        existing = abs_page.read_text() if abs_page.exists() else ""

        msgs, sys_p = build_propose_edit_messages(raw_content, existing, page_path)
        call_n += 1
        _log(log_path, "llm_call", run_id=run_id, call_n=call_n,
             purpose="propose_edit", page=page_path)

        edit_text, edit_usage = _call_provider(config, sys_p, msgs, cache_system=True)
        edit_cost = _compute_cost(config.model, edit_usage)
        total_cost += edit_cost
        total_input_tokens += edit_usage["input_tokens"]
        total_output_tokens += edit_usage["output_tokens"]

        _log(log_path, "llm_response", run_id=run_id, call_n=call_n,
             input_tokens=edit_usage["input_tokens"],
             output_tokens=edit_usage["output_tokens"],
             cost_usd=round(edit_cost, 6))

        edit_result = _parse_propose_edit(edit_text)
        if edit_result is None:
            continue

        diff = FileDiff(
            rel_path=Path(page_path),
            content=edit_result["content"],
            is_new=is_new_hint or not abs_page.exists(),
        )
        wiki_diffs.append(diff)
        cited.extend(edit_result.get("cited_sources", []))
        for c in edit_result.get("contradictions", []):
            contradictions.append(ContradictionNote(
                wiki_page=c.get("wiki_page", page_path),
                summary=c.get("summary", ""),
            ))

        _log(log_path, "diff_written", run_id=run_id,
             page=page_path, is_new=diff.is_new)

    duration = time.monotonic() - start
    _log(log_path, "run_complete", run_id=run_id,
         raw=raw_rel, total_cost_usd=round(total_cost, 6),
         total_input_tokens=total_input_tokens,
         total_output_tokens=total_output_tokens,
         wiki_edits=len(wiki_diffs),
         contradictions=len(contradictions),
         duration_s=round(duration, 2))

    return SynthesisResult(
        run_id=run_id,
        wiki_diffs=wiki_diffs,
        pr_description=_build_pr_description(wiki_diffs, contradictions),
        cited_sources=sorted(set(cited)),
        declared_contradictions=contradictions,
    )


# ── provider dispatch ─────────────────────────────────────────────────────────

def _call_provider(
    config: SynthConfig,
    system: str,
    messages: list[dict],
    *,
    cache_system: bool,
) -> tuple[str, dict]:
    if config.provider == "anthropic":
        return _call_anthropic(config.model, system, messages, cache_system=cache_system)
    raise ValueError(f"Unknown provider: {config.provider!r}. Only 'anthropic' is supported.")


def _call_anthropic(
    model: str,
    system: str,
    messages: list[dict],
    *,
    cache_system: bool = False,
) -> tuple[str, dict]:
    try:
        import anthropic
    except ImportError:
        raise RuntimeError(
            "anthropic package not installed. Run: pip install 'anthropic>=0.40'"
        )
    client = anthropic.Anthropic()
    system_content: list[dict] = [{"type": "text", "text": system}]
    if cache_system:
        system_content[0]["cache_control"] = {"type": "ephemeral"}

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_content,
        messages=messages,
    )
    text = response.content[0].text if response.content else ""
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
    }
    return text, usage


# ── pricing ───────────────────────────────────────────────────────────────────

# The Anthropic messages API returns token counts only (input_tokens, output_tokens,
# cache_read_input_tokens, cache_creation_input_tokens) — not dollar amounts. Cost
# is derived by multiplying counts by published per-token rates. The observability
# log records raw token counts alongside cost_usd so users can recompute if rates change.
# Keep in sync with: https://www.anthropic.com/pricing
_PRICING: dict[str, tuple[float, float]] = {
    # (input $/MTok, output $/MTok)
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7": (15.0, 75.0),
    "claude-haiku-4-5-20251001": (0.8, 4.0),
}


def _compute_cost(model: str, usage: dict) -> float:
    in_rate, out_rate = _PRICING.get(model, (3.0, 15.0))
    input_cost = usage["input_tokens"] / 1_000_000 * in_rate
    output_cost = usage["output_tokens"] / 1_000_000 * out_rate
    cache_read_cost = usage.get("cache_read_input_tokens", 0) / 1_000_000 * (in_rate * 0.1)
    return input_cost + output_cost + cache_read_cost


# ── JSON parsing ──────────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict | list:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        end = -1 if lines[-1].strip() in ("```", "") else len(lines)
        text = "\n".join(lines[1:end]).strip()
    return json.loads(text)


def _parse_find_affected(text: str) -> list[dict]:
    try:
        data = _extract_json(text)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict) and "page" in item]
        if isinstance(data, dict) and "pages" in data:
            return [item for item in data["pages"] if isinstance(item, dict) and "page" in item]
        return []
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return []


def _parse_propose_edit(text: str) -> dict | None:
    try:
        data = _extract_json(text)
        if isinstance(data, dict) and "content" in data:
            return data
        return None
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _build_pr_description(diffs: list[FileDiff], contradictions: list[ContradictionNote]) -> str:
    lines = ["**Tier 2 synthesis:**"]
    if diffs:
        for d in diffs:
            tag = "new page" if d.is_new else "updated"
            lines.append(f"- {d.rel_path} ({tag})")
    else:
        lines.append("- no wiki edits proposed")
    lines.append("")
    if contradictions:
        lines.append("**Contradictions declared:**")
        for c in contradictions:
            lines.append(f"- {c.wiki_page}: {c.summary}")
    else:
        lines.append("**Contradictions declared:** none")
    return "\n".join(lines)


def _log(log_path: "Path | None", event_type: str, **kwargs) -> None:
    if log_path is not None:
        log_event(log_path, event_type, **kwargs)
