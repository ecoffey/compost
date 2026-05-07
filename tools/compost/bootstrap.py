from __future__ import annotations

import subprocess
from pathlib import Path

import yaml
from rich.console import Console

console = Console()

_DIRECTORIES = [
    "raw/checkpoints",
    "raw/commits",
    "raw/slack",
    "raw/incidents",
    "raw/decisions",
    "raw/meetings",
    "raw/support",
    "raw/notes",
    "wiki/services",
    "wiki/modules",
    "wiki/decisions",
    "wiki/runbooks",
    "wiki/concepts",
    "wiki/customers",
    "wiki/people",
    "wiki/projects",
    ".github",
]

_COLLECTIONS = {
    "wiki": ("wiki", "Synthesized, maintained knowledge. Every claim cites a raw source."),
    "raw": ("raw", "Immutable source records: commits, Slack threads, incidents, meetings."),
    "decisions": ("raw/decisions", "Authoritative ADRs and RFCs."),
    "incidents": ("raw/incidents", "Postmortems."),
}


def bootstrap_repo(path: Path, name: str) -> None:
    path.mkdir(parents=True, exist_ok=True)

    for d in _DIRECTORIES:
        (path / d).mkdir(parents=True, exist_ok=True)
        (path / d / ".gitkeep").touch()

    config = {"name": name, "qmd_index": name}
    (path / ".compost.yml").write_text(yaml.dump(config))

    (path / ".gitignore").write_text(
        "# compost generated artifacts — always re-derivable, never commit\n"
        ".compost/codify/\n"
        ".compost/synth-log/\n"
        ".compost/assay-report.md\n"
        ".compost/checks/\n"
        ".compost/queue/\n"
        ".compost/shims/\n"
    )

    _create_queue_dirs(path)

    codeowners = (
        "# CODEOWNERS for wiki content.\n"
        "# Format: <path_pattern> <owner> [auto-merge: true|false]\n"
        "wiki/ @" + name + "-wiki-maintainer\n"
    )
    (path / ".github" / "CODEOWNERS").write_text(codeowners)

    _init_wiki_index(path)
    _init_wiki_log(path)
    _init_wiki_glossary(path)

    for collection_name, (subdir, _context) in _COLLECTIONS.items():
        subdir_path = Path(subdir)
        result = subprocess.run(
            ["qmd", "--index", name, "collection", "add",
             collection_name, subdir_path.name, "**/*.md"],
            capture_output=True, text=True,
            cwd=str(path / subdir_path.parent),
        )
        if result.returncode != 0:
            console.print(
                f"[yellow]Warning: could not register qmd collection "
                f"'{collection_name}': {result.stderr.strip()}[/yellow]"
            )
        else:
            console.print(f"[green]  qmd collection registered: {collection_name}[/green]")

    console.print(f"\n[green]Initialized wiki repo at {path}[/green]")
    console.print(f"  name:      {name}")
    console.print(f"  qmd index: {name}")
    console.print(f"\nNext: add seed wiki pages, then run [bold]tc doctor[/bold].")


def _create_queue_dirs(repo: Path) -> None:
    for d in ("inbox", "processing", "done", "dead"):
        (repo / ".compost" / "queue" / d).mkdir(parents=True, exist_ok=True)
    (repo / ".compost" / "shims").mkdir(parents=True, exist_ok=True)


def _init_wiki_index(path: Path) -> None:
    (path / "wiki" / "index.md").write_text(
        "# Wiki Index\n\nAll wiki pages.\n\n"
        "| Page | Type | Status |\n"
        "|------|------|--------|\n"
    )


def _init_wiki_log(path: Path) -> None:
    (path / "wiki" / "log.md").write_text(
        "# Synthesis Log\n\nAppend-only log of synthesis events.\n\n"
    )


def _init_wiki_glossary(path: Path) -> None:
    (path / "wiki" / "glossary.md").write_text(
        "# Glossary\n\nTeam-specific terms and abbreviations.\n\n"
    )
