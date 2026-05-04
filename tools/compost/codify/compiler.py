from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SDKMAN_INSTALL_CMD = "sdk install kotlin  # see https://sdkman.io"


@dataclass
class CompileError:
    kt_line: int
    message: str
    source_md: Path | None


@dataclass
class CompileResult:
    success: bool
    errors: list[CompileError]
    jar_path: Path | None


def compile_kt(repo: Path) -> CompileResult:
    """Run kotlinc against the generated wiki.kt. Requires kotlinc on PATH."""
    if not shutil.which("kotlinc"):
        raise RuntimeError(
            f"kotlinc not found on PATH.\n"
            f"Install via SDKMAN: {SDKMAN_INSTALL_CMD}"
        )

    generated_dir = repo / ".compost" / "codify" / "generated"
    wiki_kt = generated_dir / "wiki.kt"
    if not wiki_kt.exists():
        raise RuntimeError(f"wiki.kt not found: {wiki_kt}. Run 'compost codify' first.")

    schema_dir = Path(__file__).parent / "schema"
    schema_kt = schema_dir / "CompostSchema.kt"
    render_main_kt = schema_dir / "render_main.kt"
    jar_path = generated_dir / "wiki.jar"

    # Include hand-authored claims.kt when present; it references generated wiki.kt variables.
    claims_kt = repo / "wiki" / "claims.kt"
    extra = [str(claims_kt)] if claims_kt.exists() else []

    result = subprocess.run(
        [
            "kotlinc",
            str(schema_kt),
            str(render_main_kt),
            str(wiki_kt),
            *extra,
            "-include-runtime",
            "-d", str(jar_path),
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        errors = _parse_errors(result.stderr, wiki_kt)
        return CompileResult(success=False, errors=errors, jar_path=None)

    return CompileResult(success=True, errors=[], jar_path=jar_path)


def _parse_errors(stderr: str, wiki_kt: Path) -> list[CompileError]:
    errors: list[CompileError] = []
    for line in stderr.splitlines():
        if ": error:" in line:
            try:
                colon_parts = line.split(":")
                kt_line = int(colon_parts[1])
                message = ":".join(colon_parts[4:]).strip() if len(colon_parts) > 4 else line
                errors.append(CompileError(kt_line=kt_line, message=message, source_md=None))
            except (ValueError, IndexError):
                errors.append(CompileError(kt_line=0, message=line.strip(), source_md=None))
    return errors
