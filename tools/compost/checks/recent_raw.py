from __future__ import annotations

import subprocess
import time
from pathlib import Path

from compost.checks._llm import call_llm_anthropic, compute_cost, parse_json_field
from compost.checks.runner import CheckResult, ChecksConfig, Finding
from compost.model.frontmatter import parse_frontmatter

_SYSTEM = (
    "You are an adversarial knowledge-base auditor. "
    "Identify whether recent raw source files contain claims that contradict the new wiki content. "
    "Return JSON only — no prose, no markdown fences."
)

_SCHEMA = """{
  "contradictions": [
    {
      "page": "<path of the changed wiki page>",
      "claim_text": "<claim from the wiki page>",
      "conflicting_page": "<path of the raw file>",
      "conflicting_claim_text": "<contradicting claim from the raw file>"
    }
  ]
}"""


def check_recent_raw_scan(
    repo: Path,
    changed_wiki_files: list[Path],
    branch: str,
    raw_path: Path,
    config: ChecksConfig,
) -> CheckResult:
    """Check 5: new wiki claims don't contradict raw files from the last N days."""
    start = time.monotonic()
    findings: list[Finding] = []
    total_cost = 0.0

    recent_raws = _recent_raw_files(repo, config.recent_raw_days, exclude=raw_path)[:10]
    if not recent_raws:
        return CheckResult(
            name="recent_raw_scan", status="pass", findings=[],
            duration_s=time.monotonic() - start,
        )

    raw_texts = []
    for rp in recent_raws:
        _, body = parse_frontmatter(rp)
        raw_texts.append(f"=== {rp.relative_to(repo)} ===\n{body[:1500]}")

    for abs_path in changed_wiki_files:
        rel = str(abs_path.relative_to(repo))
        new_content = _git_show(repo, branch, rel)
        if not new_content:
            continue

        user_msg = (
            f"Changed wiki page: {rel}\n\n"
            f"{new_content[:3000]}\n\n"
            f"Recent raw files (last {config.recent_raw_days} days):\n\n"
            + "\n\n".join(raw_texts)
            + f"\n\nReturn contradictions in this JSON schema:\n{_SCHEMA}"
        )

        text, usage = call_llm_anthropic(
            config.model, _SYSTEM, [{"role": "user", "content": user_msg}]
        )
        total_cost += compute_cost(config.model, usage)

        for c in parse_json_field(text, "contradictions"):
            findings.append(Finding(
                check="recent_raw_scan", severity="fail",
                message="recent raw contradicts wiki claim",
                page=c.get("page", rel),
                conflicting_page=c.get("conflicting_page"),
                claim_text=c.get("claim_text"),
                conflicting_claim_text=c.get("conflicting_claim_text"),
            ))

    status = "fail" if findings else "pass"
    return CheckResult(
        name="recent_raw_scan", status=status, findings=findings,
        cost_usd=total_cost, duration_s=time.monotonic() - start,
    )


def _recent_raw_files(repo: Path, days: int, exclude: Path | None = None) -> list[Path]:
    """Return paths of raw/ files added in the last N days on the default branch."""
    from compost.ingest.git import default_branch
    base = default_branch(repo)
    result = subprocess.run(
        ["git", "log", "--diff-filter=A", f"--since={days} days ago",
         "--name-only", "--pretty=format:", base, "--", "raw/"],
        cwd=repo, capture_output=True, text=True,
    )
    paths = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("raw/"):
            continue
        p = repo / line
        if p.exists() and p != exclude:
            paths.append(p)
    return paths


def _git_show(repo: Path, branch: str, rel_path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{branch}:{rel_path}"],
        cwd=repo, capture_output=True, text=True,
    )
    return result.stdout if result.returncode == 0 else ""


