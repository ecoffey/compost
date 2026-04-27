from pathlib import Path
import yaml


def find_repo_root(start: Path) -> Path | None:
    for parent in [start, *start.parents]:
        if (parent / ".team-context.yml").exists():
            return parent
    return None


def load_repo_config(repo: Path) -> dict:
    config_file = repo / ".team-context.yml"
    with config_file.open() as f:
        return yaml.safe_load(f)
