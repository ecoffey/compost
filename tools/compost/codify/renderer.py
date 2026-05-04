from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def render_wiki(repo: Path) -> list[dict]:
    """Execute wiki.jar main() and return the rendered page list as dicts.

    Requires java on PATH and wiki.jar to exist (run compile_kt first)."""
    if not shutil.which("java"):
        raise RuntimeError(
            "java not found on PATH. Install via SDKMAN: sdk install java"
        )

    jar_path = repo / ".compost" / "codify" / "generated" / "wiki.jar"
    if not jar_path.exists():
        raise RuntimeError(
            f"wiki.jar not found: {jar_path}. Run 'compost codify --compile' first."
        )

    result = subprocess.run(
        ["java", "-jar", str(jar_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"wiki.jar render failed:\n{result.stderr.strip()}")

    parsed = json.loads(result.stdout)
    # render_main.kt emits {"pages": [...], "claims": N}
    return parsed["pages"]
