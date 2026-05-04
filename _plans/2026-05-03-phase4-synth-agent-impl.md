# § Implementation Detail: Phase 4 — Tier 2 Synthesis Agent

Reference plan: `_plans/2026-05-03-phase4-synth-agent-high-level.md`

---

## § Deferred from high-level plan

Nothing deferred. All items in the high-level plan are implemented here.

---

## § Files to create / modify

| Action | Path |
|---|---|
| CREATE | `tools/compost/compost/qmd.py` |
| MODIFY | `tools/compost/compost/mcp/tools.py` |
| MODIFY | `tools/compost/compost/ingest/git.py` |
| MODIFY | `tools/compost/compost/ingest/pr.py` |
| CREATE | `tools/compost/compost/synth/__init__.py` |
| CREATE | `tools/compost/compost/synth/log.py` |
| CREATE | `tools/compost/compost/synth/prompt.py` |
| CREATE | `tools/compost/compost/synth/agent.py` |
| CREATE | `tools/compost/compost/synth/diff_writer.py` |
| CREATE | `tools/compost/compost/synth/prompts/find_affected.md` |
| CREATE | `tools/compost/compost/synth/prompts/propose_edit.md` |
| MODIFY | `tools/compost/compost/cli.py` |
| MODIFY | `tools/compost/pyproject.toml` |
| MODIFY | `tools/compost/tests/test_pr.py` |
| CREATE | `tools/compost/tests/test_synth.py` |
| CREATE | `tools/compost/README.md` |

All paths are relative to `/Users/eoin/workspace/compost`.

---

## § Step-by-step execution (TDD)

### Step 1 — `pyproject.toml` update

In `tools/compost/pyproject.toml`:

**Add `anthropic>=0.40` to `dependencies`:**
```toml
dependencies = [
    "anthropic>=0.40",
    "click>=8.1",
    "httpx>=0.27",
    "mcp>=1.9",
    "pyyaml>=6.0",
    "python-frontmatter>=1.1",
    "rich>=13.0",
]
```

**Add `compost.synth` to packages and package-data:**
```toml
[tool.setuptools]
package-dir = {"" = ".."}
packages = [
    "compost",
    "compost.gitea",
    "compost.model",
    "compost.mcp",
    "compost.ingest",
    "compost.synth",
]

[tool.setuptools.package-data]
"compost.ingest" = ["classifier_rules.yaml"]
"compost.synth" = ["prompts/*.md"]
```

**Then install:**
```bash
cd tools/compost && .venv/bin/pip install -e ".[dev]"
```

---

### Step 2 — Create `compost/qmd.py` (red: add import in test, green: create module)

**Create `tools/compost/compost/qmd.py`:**

```python
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
```

---

### Step 3 — Update `mcp/tools.py` to import from `compost.qmd`

Replace the local `_qmd_query` and `_qmd_get` definitions with imports. The function signatures and bodies are identical — this is purely a refactor.

**In `tools/compost/compost/mcp/tools.py`:**

Replace the top section:
```python
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _qmd_query(query: str, index: str, collection: str, limit: int = 5) -> list[dict]:
    ...


def _qmd_get(file_uri: str, index: str) -> str:
    ...
```

With:
```python
from __future__ import annotations

from pathlib import Path

from compost.qmd import qmd_query as _qmd_query, qmd_get as _qmd_get
```

All other code in `mcp/tools.py` stays unchanged. The aliases `_qmd_query` / `_qmd_get` keep internal call sites working without further edits.

**Verify:** Run `cd tools/compost && .venv/bin/pytest tests/test_doctor.py -q` — the MCP-adjacent doctor tests should still pass.

---

### Step 4 — Add `stage_and_commit` to `ingest/git.py`

`create_branch_and_commit` creates a new branch then commits. For wiki diffs we are already on the right branch and just need to stage + commit. Add after the existing `create_branch_and_commit` definition:

```python
def stage_and_commit(repo: Path, files: list[Path], message: str) -> None:
    """Stage files and commit on the current branch. Does not create a new branch."""
    for f in files:
        subprocess.run(["git", "add", str(f)], cwd=repo, check=True)
    result = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise click.UsageError(f"Commit failed: {result.stderr.strip()}")
```

**Test to add in `tests/test_git.py`** — append:
```python
def test_stage_and_commit_commits_on_current_branch(compost_git_repo):
    from compost.ingest.git import create_branch_and_commit, stage_and_commit, current_branch

    # Set up: create branch and initial commit
    f1 = compost_git_repo / "raw" / "notes" / "first.md"
    f1.parent.mkdir(parents=True, exist_ok=True)
    f1.write_text("first")
    create_branch_and_commit(compost_git_repo, "raw/2026-05-03-test", [f1], "raw: first")
    branch_before = current_branch(compost_git_repo)

    # Add a second file on the same branch
    f2 = compost_git_repo / "wiki" / "services" / "payments.md"
    f2.parent.mkdir(parents=True, exist_ok=True)
    f2.write_text("wiki content")
    stage_and_commit(compost_git_repo, [f2], "synth: wiki edits (abc123)")

    assert current_branch(compost_git_repo) == branch_before

    log = subprocess.run(
        ["git", "log", "--oneline", "-2"],
        cwd=compost_git_repo, capture_output=True, text=True,
    ).stdout
    assert "synth: wiki edits" in log
    assert "raw: first" in log

    subprocess.run(["git", "checkout", "main"], cwd=compost_git_repo, check=True, capture_output=True)
```

---

### Step 5 — Update `ingest/pr.py`

Two changes: (a) `create_pr` gains `result` param; (b) remove the wiki-edit guard from `merge_pr`.

**5a — `create_pr` with synthesis result:**

Update the `TYPE_CHECKING` block and the function signature:

```python
if TYPE_CHECKING:
    from compost.ingest.classifier import ClassifyDecision
    from compost.synth.agent import SynthesisResult
```

Updated `create_pr`:
```python
def create_pr(
    repo: Path,
    branch: str,
    client: GiteaClient,
    decision: "ClassifyDecision | None" = None,
    result: "SynthesisResult | None" = None,
) -> GiteaPR:
    """Build PR body from changed files, optional Tier 1 decision, and optional Tier 2 result."""
    base = default_branch(repo)
    files = changed_files(repo, branch, base)
    files_list = "\n".join(f"- {f}" for f in files) if files else "_(none)_"

    tier1 = ""
    if decision is not None and decision.fired:
        trigger_str = ", ".join(f"{t.kind}:{t.pattern}" for t in decision.triggers)
        tier1 = f"**Tier 1:** FIRE — {trigger_str}\n\n"

    tier2 = ""
    if result is not None:
        tier2 = result.pr_description + "\n\n"

    body = (
        f"{tier1}"
        f"{tier2}"
        f"**Files changed:**\n{files_list}\n\n"
        f"---\n"
        f"*Review and merge with `compost pr merge`.*"
    )
    return client.open_pr(branch, base, f"raw: {branch}", body)
```

**5b — Remove wiki-edit guard from `merge_pr`:**

Delete these lines from `merge_pr`:
```python
    wiki_edits = [f for f in files if f.startswith("wiki/")]
    if wiki_edits:
        raise click.UsageError(
            "Branch contains wiki/ edits which must not be auto-merged:\n"
            + "\n".join(f"  {f}" for f in wiki_edits)
        )
```

Also remove the now-unused `files` variable assignment that was only there for the wiki guard. If `files` is no longer needed for anything else in `merge_pr`, remove it. The full updated `merge_pr` after the guard is removed:

```python
def merge_pr(repo: Path, client: GiteaClient) -> GiteaPR:
    """Validate guards, merge via Gitea API, sync local repo. Returns merged PR."""
    branch = current_branch(repo)
    if not branch.startswith("raw/"):
        raise click.UsageError(
            f"Not on a raw/* branch (current: '{branch}'). "
            "Checkout a raw/* branch before merging."
        )

    pr = client.find_pr(branch)
    if pr is None:
        raise click.UsageError(
            f"No open PR found for '{branch}'. "
            "Was it already merged or not yet pushed?"
        )

    client.merge_pr(pr.number)
    base = default_branch(repo)
    fetch_and_ff(repo, "origin", base)

    return GiteaPR(number=pr.number, url=pr.url, state="merged")
```

Remove the unused `changed_files` import from the `merge_pr` scope if it's only used there. Keep the import at the top if `create_pr` still uses it.

**5c — Test updates in `tests/test_pr.py`:**

Delete the entire `test_merge_pr_blocked_on_wiki_edit` test — that guard no longer exists.

Add two new tests:
```python
def test_create_pr_body_includes_tier2_on_result(compost_git_repo):
    from compost.synth.agent import SynthesisResult, FileDiff, ContradictionNote
    from pathlib import Path

    repo = compost_git_repo
    branch = "raw/2026-05-03-with-synth"
    _add_file_on_branch(repo, "raw/decisions/2026-05-03-with-synth.md", branch)

    result = SynthesisResult(
        run_id="abc123",
        wiki_diffs=[
            FileDiff(rel_path=Path("wiki/services/payments.md"), content="...", is_new=False),
        ],
        pr_description=(
            "**Tier 2 synthesis:**\n"
            "- wiki/services/payments.md (updated)\n\n"
            "**Contradictions declared:** none"
        ),
        cited_sources=["raw/decisions/2026-05-03-with-synth.md"],
        declared_contradictions=[],
    )

    received_bodies = []

    @dataclass
    class CapturingClient:
        pr: GiteaPR = GiteaPR(1, "http://gitea/pulls/1", "open")
        def open_pr(self, branch, base, title, body):
            received_bodies.append(body)
            return self.pr
        def find_pr(self, branch): return self.pr
        def merge_pr(self, n): pass

    create_pr(repo, branch, CapturingClient(), result=result)
    body = received_bodies[0]
    assert "Tier 2 synthesis" in body
    assert "wiki/services/payments.md" in body
    assert "Contradictions declared: none" in body

    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_create_pr_body_omits_tier2_when_no_result(compost_git_repo):
    repo = compost_git_repo
    branch = "raw/2026-05-03-no-result"
    _add_file_on_branch(repo, "raw/notes/2026-05-03-no-result.md", branch)

    received_bodies = []

    @dataclass
    class CapturingClient:
        pr: GiteaPR = GiteaPR(1, "http://gitea/pulls/1", "open")
        def open_pr(self, branch, base, title, body):
            received_bodies.append(body)
            return self.pr
        def find_pr(self, branch): return self.pr
        def merge_pr(self, n): pass

    create_pr(repo, branch, CapturingClient(), result=None)
    body = received_bodies[0]
    assert "Tier 2" not in body

    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)
```

---

### Step 6 — Create `compost/synth/__init__.py`

Empty file:
```python
```

---

### Step 7 — Create `compost/synth/log.py`

```python
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def open_run(repo: Path, run_id: str) -> Path:
    """Create .compost/synth-log/{date}T{HHmmss}-{run_id}.jsonl. Return path."""
    log_dir = repo / ".compost" / "synth-log"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
    path = log_dir / f"{ts}-{run_id}.jsonl"
    path.touch()
    return path


def log_event(log_path: Path, event_type: str, **kwargs) -> None:
    """Append one JSONL line: {"event": event_type, "ts": ..., **kwargs}."""
    entry = {
        "event": event_type,
        "ts": datetime.now(timezone.utc).isoformat(),
        **kwargs,
    }
    with log_path.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def read_runs(repo: Path, last: int = 10) -> list[dict]:
    """Read run_complete events from the last N log files, newest first."""
    log_dir = repo / ".compost" / "synth-log"
    if not log_dir.exists():
        return []
    files = sorted(log_dir.glob("*.jsonl"), reverse=True)[:last]
    summaries = []
    for path in files:
        for line in path.read_text().splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "run_complete":
                summaries.append(event)
                break
    return summaries
```

---

### Step 8 — Create `compost/synth/prompt.py`

```python
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
```

---

### Step 9 — Create `compost/synth/prompts/find_affected.md`

```markdown
You are a wiki synthesis agent. Your job is to identify which wiki pages need to be updated based on a new raw source document.

Given:
- A raw source document (decision, incident, Slack thread, meeting notes, etc.)
- A list of candidate wiki pages with titles and snippets

Return a JSON array of objects identifying pages that need updating. Each object must have:
- `page`: the wiki page path (exactly as shown in the candidates list)
- `rationale`: one sentence explaining why this page needs updating
- `is_new`: true only if a brand-new wiki page should be created (the page path does not exist yet)

Rules:
- Only include pages from the candidate list (never invent new paths not shown)
- Set `is_new: true` only when the raw document introduces a completely new concept, service, or entity not covered by any existing candidate
- If no pages need updating, return an empty array: []
- Do not include pages that are only tangentially related

Return ONLY the JSON array with no other text.
```

---

### Step 10 — Create `compost/synth/prompts/propose_edit.md`

```markdown
You are a wiki synthesis agent. Your job is to update a wiki page based on new information from a raw source document.

Given:
- A raw source document
- An existing wiki page (may be empty if creating a new page)

Return a JSON object with exactly these keys:
- `content`: the complete replacement content for the wiki page, including the YAML frontmatter block
- `cited_sources`: list of source paths referenced (typically the raw file path from its `source` frontmatter)
- `contradictions`: list of objects, each with `wiki_page` (page path) and `summary` (one sentence describing the conflict) for any information that contradicts existing wiki content

Frontmatter rules — MUST follow exactly:
- `sources`: append the raw file path to the existing list; do not remove existing sources
- `updated`: set to today's date in YYYY-MM-DD format
- `confidence`: may adjust based on new evidence; if changed, note the reason in the PR description field
- `supersedes`: add the superseded page path only when the raw document explicitly supersedes a prior decision; otherwise leave unchanged
- `superseded_by`: leave unchanged
- `owners`: leave unchanged

If no meaningful update is warranted, return the original page content unchanged with empty `cited_sources` and `contradictions`.

Return ONLY the JSON object with no other text.
```

---

### Step 11 — Create `compost/synth/agent.py`

```python
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

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

    When dry_run=True: no log file is written and no wiki files are touched.
    Returns a populated SynthesisResult regardless of dry_run.
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

    _log(log_path, "run_start",
         run_id=run_id, raw=raw_rel, model=config.model, provider=config.provider)

    query_text = raw_content.replace("\n", " ")[:300]
    candidates = qmd_query(query_text, index, "wiki", limit=config.max_candidates)

    _log(log_path, "qmd_candidates",
         run_id=run_id, count=len(candidates),
         pages=[h["file"] for h in candidates])

    if not candidates:
        duration = time.monotonic() - start
        _log(log_path, "run_complete", run_id=run_id,
             total_cost_usd=0.0, wiki_edits=0, contradictions=0,
             duration_s=round(duration, 2))
        return SynthesisResult(
            run_id=run_id, wiki_diffs=[],
            pr_description="**Tier 2 synthesis:**\n- no candidate wiki pages found\n\n**Contradictions declared:** none",
            cited_sources=[], declared_contradictions=[],
        )

    total_cost = 0.0
    call_n = 0

    # Phase A: find which pages need updating
    messages, system = build_find_affected_messages(raw_content, candidates)
    call_n += 1
    _log(log_path, "llm_call", run_id=run_id, call_n=call_n, purpose="find_affected")

    text, usage = _call_provider(config, system, messages, cache_system=False)
    cost = _compute_cost(config.model, usage)
    total_cost += cost
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
         total_cost_usd=round(total_cost, 6),
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
```

---

### Step 12 — Create `compost/synth/diff_writer.py`

```python
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
```

---

### Step 13 — Update `cli.py`

Three changes: (a) add `--no-synth` to `raw_add` and synthesis invocation, (b) add `synth` group.

**13a — Add `--no-synth` and synthesis call to `raw_add`:**

Add the option to the `raw_add` command decorator:
```python
@click.option("--no-synth", is_flag=True, default=False,
              help="Skip Tier 2 synthesis even if Tier 1 fires.")
```

Add `no_synth: bool` to the function signature.

Replace the section after classify (starting at `try: push_branch`) with:

```python
    # Tier 2 synthesis (runs before push so both commits land in one push)
    synth_result = None
    if decision and decision.fired and not no_synth:
        console.print("[dim][Tier 2] synthesizing...[/dim]")
        try:
            from compost.synth.agent import synthesize
            from compost.synth.diff_writer import apply_diffs, commit_diffs
            synth_result = synthesize(raw_file.path, repo)
            if synth_result.wiki_diffs:
                written = apply_diffs(repo, synth_result)
                commit_diffs(repo, written, synth_result.run_id)
                names = ", ".join(str(d.rel_path) for d in synth_result.wiki_diffs)
                console.print(f"[green][Tier 2][/green] {len(synth_result.wiki_diffs)} wiki page(s) updated: {names}")
            else:
                console.print("[dim][Tier 2] no wiki edits proposed[/dim]")
        except Exception as exc:
            console.print(f"[dim]⚠ synthesis failed: {exc}[/dim]")
            synth_result = None

    try:
        push_branch(repo, "origin", branch)
        client = _load_gitea_client(repo)
        pr = create_pr(repo, branch, client, decision, synth_result)
        console.print(f"PR #{pr.number}: {pr.url}")
    except (click.UsageError, GiteaError) as e:
        console.print(f"[yellow]⚠ push/PR failed: {e}[/yellow]")
        console.print("Push manually: [bold]git push origin " + branch + "[/bold]")
```

Note: the `create_pr` call now passes `synth_result` as the fifth argument.

**13b — Add `synth` group:**

After the existing `classify` group, add:

```python
@main.group("synth")
def synth_group() -> None:
    """Tier 2 synthesis: propose wiki edits from raw files."""


@synth_group.command("run")
@click.option("--raw", "raw_rel", required=True,
              help="Raw file path relative to repo root.")
@click.option("--provider", default=None,
              help="Override .compost.yml synth.provider.")
@click.option("--model", default=None,
              help="Override .compost.yml synth.model.")
@click.option("--dry-run", is_flag=True, default=False,
              help="Propose edits but do not write to disk or git.")
@click.pass_context
def synth_run(ctx: click.Context, raw_rel: str, provider: str | None,
              model: str | None, dry_run: bool) -> None:
    """Run synthesis on a raw file and optionally commit wiki edits to the current branch."""
    from dataclasses import replace
    from compost.synth.agent import synthesize, load_synth_config
    from compost.synth.diff_writer import apply_diffs, commit_diffs

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    raw_path = repo / raw_rel
    if not raw_path.exists():
        console.print(f"[red]Raw file not found: {raw_path}[/red]")
        sys.exit(1)

    cfg = load_synth_config(repo)
    if provider:
        cfg = replace(cfg, provider=provider)
    if model:
        cfg = replace(cfg, model=model)

    result = synthesize(raw_path, repo, config=cfg, dry_run=dry_run)

    if not result.wiki_diffs:
        console.print("[dim][Tier 2] no wiki edits proposed[/dim]")
        return

    if dry_run:
        console.print(f"[dim][Tier 2] dry-run: {len(result.wiki_diffs)} wiki page(s) would be updated[/dim]")
        for d in result.wiki_diffs:
            tag = "new" if d.is_new else "update"
            console.print(f"  {d.rel_path} ({tag})")
        return

    written = apply_diffs(repo, result)
    commit_diffs(repo, written, result.run_id)
    console.print(f"[green][Tier 2][/green] {len(result.wiki_diffs)} wiki page(s) updated")
    for d in result.wiki_diffs:
        tag = "new" if d.is_new else "updated"
        console.print(f"  {d.rel_path} ({tag})")


@synth_group.command("log")
@click.option("--last", default=10, show_default=True,
              help="Number of recent runs to show.")
@click.pass_context
def synth_log(ctx: click.Context, last: int) -> None:
    """Show recent synthesis run history."""
    from compost.synth.log import read_runs

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    runs = read_runs(repo, last=last)
    if not runs:
        console.print("[dim]No synthesis runs found.[/dim]")
        return

    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Run ID", style="cyan")
    table.add_column("Timestamp")
    table.add_column("Raw file")
    table.add_column("Edits", justify="right")
    table.add_column("Contradictions", justify="right")
    table.add_column("Cost (USD)", justify="right")
    table.add_column("Duration (s)", justify="right")

    for r in runs:
        table.add_row(
            (r.get("run_id") or "?")[:8],
            (r.get("ts") or "")[:19].replace("T", " "),
            r.get("raw", "?"),
            str(r.get("wiki_edits", 0)),
            str(r.get("contradictions", 0)),
            f"{r.get('total_cost_usd', 0):.4f}",
            f"{r.get('duration_s', 0):.1f}",
        )

    console.print(table)
```

---

### Step 14 — Create `tests/test_synth.py`

```python
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from compost.synth.agent import (
    ContradictionNote,
    FileDiff,
    SynthConfig,
    SynthesisResult,
    _parse_find_affected,
    _parse_propose_edit,
    load_synth_config,
    synthesize,
)
from compost.synth.diff_writer import apply_diffs, commit_diffs
from compost.synth.log import log_event, open_run, read_runs


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def synth_repo(tmp_path: Path) -> Path:
    """Minimal compost repo for synth tests."""
    (tmp_path / ".compost.yml").write_text(yaml.dump({
        "name": "test",
        "qmd_index": "test",
        "synth": {"provider": "anthropic", "model": "claude-sonnet-4-6", "max_candidates": 5},
    }))
    (tmp_path / "wiki" / "services").mkdir(parents=True)
    (tmp_path / "wiki" / "services" / "payments.md").write_text(
        "---\ntype: service\nname: payments\nowners: [alice]\n"
        "status: active\nupdated: 2026-01-15\nconfidence: high\n"
        "sources:\n  - raw/decisions/0001-stripe.md\n---\n\n# Payments\n"
    )
    (tmp_path / "raw" / "decisions").mkdir(parents=True)
    (tmp_path / "raw" / "decisions" / "2026-05-03-drop-postgres.md").write_text(
        "---\nsource: decision\ncaptured_at: 2026-05-03T10:00:00Z\n"
        "captured_by: alice\norigin: internal\n---\n\nWe decided to drop Postgres.\n"
    )
    return tmp_path


# ── load_synth_config ─────────────────────────────────────────────────────────

def test_load_synth_config_reads_yml(synth_repo):
    cfg = load_synth_config(synth_repo)
    assert cfg.provider == "anthropic"
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.max_candidates == 5


def test_load_synth_config_defaults_when_block_absent(tmp_path):
    (tmp_path / ".compost.yml").write_text("name: x\nqmd_index: x\n")
    cfg = load_synth_config(tmp_path)
    assert cfg.provider == "anthropic"
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.max_candidates == 10


# ── _parse_find_affected ──────────────────────────────────────────────────────

def test_parse_find_affected_bare_json():
    text = '[{"page": "wiki/services/p.md", "rationale": "r", "is_new": false}]'
    result = _parse_find_affected(text)
    assert len(result) == 1
    assert result[0]["page"] == "wiki/services/p.md"


def test_parse_find_affected_fenced_code_block():
    text = '```json\n[{"page": "wiki/services/p.md", "rationale": "r", "is_new": false}]\n```'
    result = _parse_find_affected(text)
    assert len(result) == 1


def test_parse_find_affected_empty_array():
    assert _parse_find_affected("[]") == []


def test_parse_find_affected_bad_json_returns_empty():
    assert _parse_find_affected("not json at all") == []


# ── _parse_propose_edit ───────────────────────────────────────────────────────

def test_parse_propose_edit_valid():
    data = {"content": "# page", "cited_sources": ["raw/x.md"], "contradictions": []}
    assert _parse_propose_edit(json.dumps(data))["content"] == "# page"


def test_parse_propose_edit_bad_json_returns_none():
    assert _parse_propose_edit("not json") is None


def test_parse_propose_edit_missing_content_returns_none():
    assert _parse_propose_edit('{"cited_sources": []}') is None


# ── log ───────────────────────────────────────────────────────────────────────

def test_log_open_run_creates_dir_and_file(tmp_path):
    path = open_run(tmp_path, "abc123")
    assert (tmp_path / ".compost" / "synth-log").exists()
    assert path.exists()
    assert "abc123" in path.name


def test_log_event_appends_valid_jsonl(tmp_path):
    path = open_run(tmp_path, "abc")
    log_event(path, "run_start", run_id="abc", raw="raw/x.md")
    log_event(path, "run_complete", run_id="abc", wiki_edits=1)
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    events = [json.loads(l) for l in lines]
    assert events[0]["event"] == "run_start"
    assert events[1]["event"] == "run_complete"
    assert "ts" in events[0]


def test_read_runs_returns_newest_first(tmp_path):
    import time as _time
    p1 = open_run(tmp_path, "run1")
    log_event(p1, "run_complete", run_id="run1", wiki_edits=0, total_cost_usd=0.0, duration_s=1.0)
    _time.sleep(0.02)  # ensure different filenames
    p2 = open_run(tmp_path, "run2")
    log_event(p2, "run_complete", run_id="run2", wiki_edits=1, total_cost_usd=0.005, duration_s=2.0)

    runs = read_runs(tmp_path, last=10)
    assert len(runs) == 2
    assert runs[0]["run_id"] == "run2"


def test_read_runs_empty_when_no_logs(tmp_path):
    assert read_runs(tmp_path) == []


def test_read_runs_respects_last_limit(tmp_path):
    import time as _time
    for i in range(4):
        p = open_run(tmp_path, f"run{i}")
        log_event(p, "run_complete", run_id=f"run{i}", wiki_edits=0, total_cost_usd=0.0, duration_s=0.0)
        _time.sleep(0.02)
    assert len(read_runs(tmp_path, last=2)) == 2


# ── apply_diffs / commit_diffs ────────────────────────────────────────────────

def test_apply_diffs_creates_new_page(tmp_path):
    result = SynthesisResult(
        run_id="abc",
        wiki_diffs=[FileDiff(rel_path=Path("wiki/services/new.md"), content="# New", is_new=True)],
        pr_description="",
        cited_sources=[],
        declared_contradictions=[],
    )
    written = apply_diffs(tmp_path, result)
    assert (tmp_path / "wiki" / "services" / "new.md").read_text() == "# New"
    assert len(written) == 1


def test_apply_diffs_overwrites_existing_page(tmp_path):
    page = tmp_path / "wiki" / "services" / "payments.md"
    page.parent.mkdir(parents=True)
    page.write_text("old")
    result = SynthesisResult(
        run_id="abc",
        wiki_diffs=[FileDiff(rel_path=Path("wiki/services/payments.md"), content="new", is_new=False)],
        pr_description="",
        cited_sources=[],
        declared_contradictions=[],
    )
    apply_diffs(tmp_path, result)
    assert page.read_text() == "new"


def test_apply_diffs_returns_absolute_paths(tmp_path):
    result = SynthesisResult(
        run_id="abc",
        wiki_diffs=[FileDiff(rel_path=Path("wiki/services/new.md"), content="x", is_new=True)],
        pr_description="",
        cited_sources=[],
        declared_contradictions=[],
    )
    written = apply_diffs(tmp_path, result)
    assert all(p.is_absolute() for p in written)


def test_apply_diffs_empty_result_writes_nothing(tmp_path):
    result = SynthesisResult(
        run_id="abc", wiki_diffs=[], pr_description="",
        cited_sources=[], declared_contradictions=[],
    )
    assert apply_diffs(tmp_path, result) == []


# ── synthesize (LLM mocked) ───────────────────────────────────────────────────

_CANDIDATE = [{"file": "wiki/services/payments.md", "snippet": "payments", "title": "Payments", "score": 0.9}]

_FIND_RESP = json.dumps([
    {"page": "wiki/services/payments.md", "rationale": "related", "is_new": False}
])

_PROPOSE_RESP = json.dumps({
    "content": "---\ntype: service\nname: payments\n---\n\n# Payments\n\nUpdated.",
    "cited_sources": ["raw/decisions/2026-05-03-drop-postgres.md"],
    "contradictions": [],
})

_EMPTY_USAGE = {"input_tokens": 100, "output_tokens": 50,
                "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


@patch("compost.synth.agent.qmd_query")
@patch("compost.synth.agent._call_anthropic")
def test_synthesize_populates_result(mock_llm, mock_qmd, synth_repo):
    mock_qmd.return_value = _CANDIDATE
    mock_llm.side_effect = [
        (_FIND_RESP, _EMPTY_USAGE),
        (_PROPOSE_RESP, _EMPTY_USAGE),
    ]
    raw_path = synth_repo / "raw" / "decisions" / "2026-05-03-drop-postgres.md"
    result = synthesize(raw_path, synth_repo)

    assert result.run_id
    assert len(result.wiki_diffs) == 1
    assert result.wiki_diffs[0].rel_path == Path("wiki/services/payments.md")
    assert not result.wiki_diffs[0].is_new
    assert "raw/decisions/2026-05-03-drop-postgres.md" in result.cited_sources
    assert result.declared_contradictions == []


@patch("compost.synth.agent.qmd_query")
@patch("compost.synth.agent._call_anthropic")
def test_synthesize_no_candidates_returns_empty(mock_llm, mock_qmd, synth_repo):
    mock_qmd.return_value = []
    raw_path = synth_repo / "raw" / "decisions" / "2026-05-03-drop-postgres.md"
    result = synthesize(raw_path, synth_repo)
    assert result.wiki_diffs == []
    mock_llm.assert_not_called()


@patch("compost.synth.agent.qmd_query")
@patch("compost.synth.agent._call_anthropic")
def test_synthesize_llm_finds_no_affected_returns_empty(mock_llm, mock_qmd, synth_repo):
    mock_qmd.return_value = _CANDIDATE
    mock_llm.return_value = ("[]", _EMPTY_USAGE)
    raw_path = synth_repo / "raw" / "decisions" / "2026-05-03-drop-postgres.md"
    result = synthesize(raw_path, synth_repo)
    assert result.wiki_diffs == []
    assert mock_llm.call_count == 1  # only Phase A


@patch("compost.synth.agent.qmd_query")
@patch("compost.synth.agent._call_anthropic")
def test_synthesize_two_affected_pages_two_propose_calls(mock_llm, mock_qmd, synth_repo):
    mock_qmd.return_value = [
        {"file": "wiki/services/payments.md", "snippet": "x", "title": "P", "score": 0.9},
        {"file": "wiki/concepts/idempotency.md", "snippet": "y", "title": "I", "score": 0.8},
    ]
    two_affected = json.dumps([
        {"page": "wiki/services/payments.md", "rationale": "r1", "is_new": False},
        {"page": "wiki/concepts/idempotency.md", "rationale": "r2", "is_new": False},
    ])
    (synth_repo / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
    (synth_repo / "wiki" / "concepts" / "idempotency.md").write_text("---\ntype: concept\n---\n")
    mock_llm.side_effect = [
        (two_affected, _EMPTY_USAGE),
        (_PROPOSE_RESP, _EMPTY_USAGE),
        (_PROPOSE_RESP, _EMPTY_USAGE),
    ]
    raw_path = synth_repo / "raw" / "decisions" / "2026-05-03-drop-postgres.md"
    result = synthesize(raw_path, synth_repo)
    assert mock_llm.call_count == 3
    assert len(result.wiki_diffs) == 2


@patch("compost.synth.agent.qmd_query")
@patch("compost.synth.agent._call_anthropic")
def test_synthesize_dry_run_skips_log_file(mock_llm, mock_qmd, synth_repo):
    mock_qmd.return_value = _CANDIDATE
    mock_llm.side_effect = [
        (_FIND_RESP, _EMPTY_USAGE),
        (_PROPOSE_RESP, _EMPTY_USAGE),
    ]
    raw_path = synth_repo / "raw" / "decisions" / "2026-05-03-drop-postgres.md"
    result = synthesize(raw_path, synth_repo, dry_run=True)

    assert not (synth_repo / ".compost" / "synth-log").exists()
    assert len(result.wiki_diffs) == 1  # result still populated


@patch("compost.synth.agent.qmd_query")
@patch("compost.synth.agent._call_anthropic")
def test_synthesize_bad_propose_json_gracefully_skips(mock_llm, mock_qmd, synth_repo):
    mock_qmd.return_value = _CANDIDATE
    mock_llm.side_effect = [
        (_FIND_RESP, _EMPTY_USAGE),
        ("not valid json at all", _EMPTY_USAGE),
    ]
    raw_path = synth_repo / "raw" / "decisions" / "2026-05-03-drop-postgres.md"
    result = synthesize(raw_path, synth_repo)
    assert result.wiki_diffs == []


# ── qmd refactor: mcp tools still work ───────────────────────────────────────

def test_qmd_module_has_expected_functions():
    from compost import qmd
    assert callable(qmd.qmd_query)
    assert callable(qmd.qmd_get)


@patch("compost.qmd.qmd_query")
def test_mcp_query_wiki_uses_qmd_module(mock_qmd_query):
    mock_qmd_query.return_value = [
        {"file": "wiki/services/p.md", "snippet": "s", "title": "P", "score": 0.9}
    ]
    from compost.mcp.tools import query_wiki
    result = query_wiki("payments", "test-index", scope="team", limit=3)
    mock_qmd_query.assert_called_once()
    assert "payments" in result or "Results" in result or "p.md" in result
```

---

### Step 15 — Create `README.md` in `tools/compost/`

Create `tools/compost/README.md` as a new file. The document describes the compost CLI and is progressive: each section maps to a phase.

```markdown
# compost

A CLI for maintaining a team knowledge wiki. Raw source documents are ingested, classified, synthesized into wiki pages, and served via an MCP server for use in Claude Code sessions.

## Installation

```bash
cd tools/compost
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Quick start

```bash
# Bootstrap a new wiki repo
compost init --name my-team /path/to/my-team-wiki

# Add a raw source document
echo "We decided to use Stripe." | COMPOST_REPO=/path/to/my-team-wiki compost raw add \
  --source decision --title "Use Stripe for payments"

# Verify repo health
COMPOST_REPO=/path/to/my-team-wiki compost doctor

# Start the MCP server (point Claude Code at this)
COMPOST_REPO=/path/to/my-team-wiki compost mcp
```

## Commands

### `compost init`

Bootstrap a new wiki repo with `.compost.yml`, seed directories, and qmd configuration.

### `compost raw add`

Ingest a raw source document from stdin. Commits the file on a new branch, runs the Tier 1 classifier, and (if Tier 1 fires) synthesizes wiki edits via the Tier 2 agent.

```bash
echo "Postmortem: payments service was down for 2 hours." | \
  COMPOST_REPO=. compost raw add --source incident --title "Payments outage 2026-05-03"
```

Output:
```
✓ raw/incidents/2026/INC-2026-05-03-payments-outage-2026-05-03.md
branch: raw/2026-05-03-payments-outage-2026-05-03
[Tier 1] FIRE — source:incident, semantic:postmortem
[Tier 2] synthesizing...
  wiki/services/payments.md (updated)
PR #4: http://localhost:3000/myteam/wiki/pulls/4
```

Use `--no-synth` to skip synthesis even when Tier 1 fires.

### `compost pr merge`

Merge the current `raw/*` branch via Gitea PR, then sync the local repo.

```bash
COMPOST_REPO=. compost pr merge
# merged (PR #4)
```

## Tier 1 Classifier

`compost raw add` automatically classifies new raw files using rule-based triggers. FIRE means the file is likely worth synthesizing into the wiki.

```
[Tier 1] FIRE — source:decision, semantic:decision
```

### `compost classify run`

Classify a single file without adding it:

```bash
COMPOST_REPO=. compost classify run raw/decisions/2026-04-27-use-stripe.md
# ✓ FIRE   raw/decisions/2026-04-27-use-stripe.md
#   source:decision · semantic:decision
```

### `compost classify replay`

Re-run the classifier across recent raw files to tune rules:

```bash
COMPOST_REPO=. compost classify replay --since 7d
```

## Tier 2 Synthesis

When Tier 1 fires, the synthesis agent queries the wiki for candidate pages, identifies which ones need updating, and proposes new content. This runs automatically from `compost raw add`, or manually:

```bash
COMPOST_REPO=. compost synth run --raw raw/decisions/2026-05-03-drop-postgres.md
COMPOST_REPO=. compost synth run --raw raw/decisions/... --dry-run  # inspect without writing
```

### `compost synth log`

View recent synthesis runs:

```bash
COMPOST_REPO=. compost synth log --last 5
```

## Gitea integration

Configure Gitea as the PR backend:

```bash
export GITEA_TOKEN=your-token
COMPOST_REPO=. compost gitea setup --url http://localhost:3000 --owner myteam
```

## MCP server

Start the MCP server for use in Claude Code:

```bash
COMPOST_REPO=/path/to/my-team-wiki compost mcp
```

Tools available to Claude Code:
- `load_service_context(service)` — load wiki context for a service
- `query_wiki(query, scope)` — hybrid search over wiki, decisions, or incidents
```

---

### Step 16 — Run tests

```bash
cd tools/compost && .venv/bin/pytest tests/ -q
```

All tests should pass. Key checks:
- `test_synth.py` — 20+ new tests, all green
- `test_pr.py` — `test_merge_pr_blocked_on_wiki_edit` removed, two new Tier 2 tests pass
- `test_git.py` — `test_stage_and_commit_commits_on_current_branch` passes
- Existing test suite unchanged otherwise

---

## § Design notes

**`merge_pr` guard removal:** The wiki-edit guard (`wiki_edits` check) was added in Phase 2 to prevent accidental wiki merges before the synthesis pipeline existed. Phase 4 adds wiki edits intentionally. The guard is removed here; Phase 5's adversarial check gate replaces it with a proper pre-merge validation.

**`dry_run` semantics:** `synthesize()` itself never writes wiki pages (that is `apply_diffs`'s job). The only disk write inside `synthesize()` is the JSONL log file. So `dry_run=True` only suppresses the log write. The `SynthesisResult` is always populated regardless of `dry_run`, letting the CLI display what would have happened.

**Provider dispatch in `_call_provider`:** Phase 4 only implements `"anthropic"`. Adding `"openai"` requires: (1) adding a `_call_openai` function, (2) adding a branch in `_call_provider`. No other file changes.

**Prompt files as package data:** `find_affected.md` and `propose_edit.md` are loaded via `Path(__file__).parent / "prompts"`. This works at both dev time (editable install) and installed time because `pyproject.toml` includes `"compost.synth" = ["prompts/*.md"]` in `package-data`. Prompts are diff-reviewable like code.

---

### Feedback Log

**`_PRICING` table — why hardcoded, not from API response**
> Original comment (verbatim): `^^ why are we hardcoding these? do api reponses not include how much the actual action cost? ^^`
>
> Context: appeared inline after the `_PRICING` dict definition in Step 11 (`agent.py`), asking whether the Anthropic API returns dollar cost so we could avoid a hardcoded table.
>
> Resolution: The Anthropic messages API returns only token counts (`input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`) — not dollar amounts. Cost computation requires multiplying token counts by model-specific per-token rates, which are published by Anthropic but not returned by the API. The hardcoded table is therefore necessary. Updated the comment above `_PRICING` to explain this and link to the pricing page. The observability log records raw token counts alongside `cost_usd`, so users can recompute if rates change.
