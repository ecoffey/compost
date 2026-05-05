from __future__ import annotations

import shutil
import time
from pathlib import Path

from compost.checks.runner import CheckResult, Finding
from compost.codify.codegen import codify
from compost.codify.compiler import compile_kt
from compost.codify.renderer import render_wiki


def check_kotlin_assay(repo: Path) -> CheckResult:
    """Check 0: Kotlin consistency layer. Skipped gracefully when kotlinc absent."""
    start = time.monotonic()

    if not shutil.which("kotlinc"):
        return CheckResult(
            name="kotlin_assay", status="skipped", findings=[],
            skip_reason="kotlinc not found",
        )

    findings: list[Finding] = []

    try:
        codegen_result = codify(repo)
        for w in codegen_result.warnings:
            findings.append(Finding(check="kotlin_assay", severity="warn", message=w))
    except Exception as e:
        return CheckResult(
            name="kotlin_assay", status="fail",
            findings=[Finding(check="kotlin_assay", severity="fail",
                              message=f"codegen error: {e}")],
            duration_s=time.monotonic() - start,
        )

    try:
        compile_result = compile_kt(repo)
    except RuntimeError as e:
        return CheckResult(
            name="kotlin_assay", status="skipped", findings=[],
            skip_reason=str(e), duration_s=time.monotonic() - start,
        )

    if not compile_result.success:
        for err in compile_result.errors:
            findings.append(Finding(
                check="kotlin_assay", severity="fail",
                message=f"compile error: {err.message}",
                page=str(err.source_md) if err.source_md else None,
            ))
        return CheckResult(
            name="kotlin_assay", status="fail", findings=findings,
            duration_s=time.monotonic() - start,
        )

    try:
        render_wiki(repo)
    except RuntimeError as e:
        findings.append(Finding(
            check="kotlin_assay", severity="fail",
            message=f"invariant violation: {e}",
        ))
        return CheckResult(
            name="kotlin_assay", status="fail", findings=findings,
            duration_s=time.monotonic() - start,
        )

    status = "fail" if any(f.severity == "fail" for f in findings) else (
        "warn" if findings else "pass"
    )
    return CheckResult(
        name="kotlin_assay", status=status, findings=findings,
        duration_s=time.monotonic() - start,
    )
