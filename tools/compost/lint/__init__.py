from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class LintFinding:
    linter: str
    severity: Literal["error", "warn"]
    message: str
    path: str | None = None   # repo-relative path; None for repo-wide findings


@dataclass
class LintResult:
    linter: str
    status: Literal["pass", "warn", "error", "skipped"]
    findings: list[LintFinding] = field(default_factory=list)
    duration_s: float = 0.0
    skip_reason: str | None = None
