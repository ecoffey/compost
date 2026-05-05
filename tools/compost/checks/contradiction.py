from __future__ import annotations

import subprocess
import time
from pathlib import Path

from compost.checks._llm import call_llm_anthropic, compute_cost, parse_json_field
from compost.checks.runner import CheckResult, ChecksConfig, Finding
from compost.qmd import qmd_query
from compost.repo import load_repo_config

_SYSTEM = (
    "You are an adversarial knowledge-base auditor. "
    "Identify claims in a new wiki page that directly contradict claims in existing pages. "
    "Return JSON only — no prose, no markdown fences."
)

_SCHEMA = """{
  "contradictions": [
    {
      "page": "<path of the new/changed page>",
      "claim_text": "<exact or paraphrased claim from the new page>",
      "conflicting_page": "<path of the existing page it contradicts>",
      "conflicting_claim_text": "<claim from the existing page>"
    }
  ]
}"""


def check_contradiction_scan(
    repo: Path,
    changed_wiki_files: list[Path],
    branch: str,
    config: ChecksConfig,
) -> CheckResult:
    """Check 4: new claims don't contradict the rest of the wiki."""
    start = time.monotonic()
    findings: list[Finding] = []
    total_cost = 0.0

    repo_config = load_repo_config(repo)
    index = repo_config["qmd_index"]

    for abs_path in changed_wiki_files:
        rel = str(abs_path.relative_to(repo))

        new_content = _git_show(repo, branch, rel)
        if not new_content:
            continue

        candidates = [
            h for h in qmd_query(new_content[:300].replace("\n", " "), index, "wiki", limit=5)
            if not h.get("file", "").endswith(rel.lstrip("/"))
        ]
        if not candidates:
            continue

        candidate_texts = []
        for h in candidates:
            cfile = h.get("file", "")
            if cfile.startswith("qmd://"):
                cfile = cfile[len("qmd://"):]
            cpath = repo / cfile
            if cpath.exists():
                candidate_texts.append(f"=== {cfile} ===\n{cpath.read_text()[:2000]}")

        if not candidate_texts:
            continue

        user_msg = (
            f"New/changed page: {rel}\n\n"
            f"{new_content[:3000]}\n\n"
            f"Existing pages to check against:\n\n"
            + "\n\n".join(candidate_texts)
            + f"\n\nReturn contradictions in this JSON schema:\n{_SCHEMA}"
        )

        text, usage = call_llm_anthropic(
            config.model, _SYSTEM, [{"role": "user", "content": user_msg}]
        )
        total_cost += compute_cost(config.model, usage)

        for c in parse_json_field(text, "contradictions"):
            findings.append(Finding(
                check="contradiction_scan", severity="fail",
                message="contradiction detected",
                page=c.get("page", rel),
                conflicting_page=c.get("conflicting_page"),
                claim_text=c.get("claim_text"),
                conflicting_claim_text=c.get("conflicting_claim_text"),
            ))

    status = "fail" if findings else "pass"
    return CheckResult(
        name="contradiction_scan", status=status, findings=findings,
        cost_usd=total_cost, duration_s=time.monotonic() - start,
    )


def _git_show(repo: Path, branch: str, rel_path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{branch}:{rel_path}"],
        cwd=repo, capture_output=True, text=True,
    )
    return result.stdout if result.returncode == 0 else ""


