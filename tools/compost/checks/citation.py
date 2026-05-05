from __future__ import annotations

import subprocess
import time
from pathlib import Path

from compost.checks._llm import call_llm_anthropic, compute_cost, parse_json_field
from compost.checks.runner import CheckResult, ChecksConfig, Finding
from compost.model.frontmatter import parse_frontmatter

_SYSTEM = (
    "You are an adversarial knowledge-base auditor. "
    "Check whether the claims in a wiki page are supported by its cited sources. "
    "Flag claims that are not supported or that contradict the source text. "
    "Return JSON only — no prose, no markdown fences."
)

_SCHEMA = """{
  "unfaithful_claims": [
    {
      "claim_text": "<claim from the wiki page>",
      "reason": "<why it is not supported or contradicted by sources>"
    }
  ]
}"""


def check_citation_faithfulness(
    repo: Path,
    changed_wiki_files: list[Path],
    branch: str,
    config: ChecksConfig,
) -> CheckResult:
    """Check 6: cited sources actually support the claims they back."""
    start = time.monotonic()
    findings: list[Finding] = []
    total_cost = 0.0

    for abs_path in changed_wiki_files:
        rel = str(abs_path.relative_to(repo))
        new_content = _git_show(repo, branch, rel)
        if not new_content:
            continue

        fm, _ = parse_frontmatter(abs_path)
        sources = fm.get("sources") or []
        if not sources:
            continue

        source_texts = []
        for src in sources:
            src_path = repo / src
            if src_path.exists():
                _, body = parse_frontmatter(src_path)
                source_texts.append(f"=== {src} ===\n{body[:2000]}")

        if not source_texts:
            continue

        user_msg = (
            f"Wiki page: {rel}\n\n"
            f"{new_content[:3000]}\n\n"
            f"Cited sources:\n\n"
            + "\n\n".join(source_texts)
            + f"\n\nReturn unfaithful claims in this JSON schema:\n{_SCHEMA}"
        )

        text, usage = call_llm_anthropic(
            config.model, _SYSTEM, [{"role": "user", "content": user_msg}]
        )
        total_cost += compute_cost(config.model, usage)

        for c in parse_json_field(text, "unfaithful_claims"):
            findings.append(Finding(
                check="citation_faithfulness", severity="fail",
                message=c.get("reason", "claim not supported by sources"),
                page=rel,
                claim_text=c.get("claim_text"),
            ))

    status = "fail" if findings else "pass"
    return CheckResult(
        name="citation_faithfulness", status=status, findings=findings,
        cost_usd=total_cost, duration_s=time.monotonic() - start,
    )


def _git_show(repo: Path, branch: str, rel_path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{branch}:{rel_path}"],
        cwd=repo, capture_output=True, text=True,
    )
    return result.stdout if result.returncode == 0 else ""


