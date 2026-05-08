import subprocess
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def wiki_repo(tmp_path: Path) -> Path:
    """A minimal, valid wiki repo in a temp directory."""
    repo = tmp_path / "my-team"
    _make_dirs(repo)
    _write_config(repo, "my-team")
    _write_codeowners(repo)
    _write_gitignore(repo)
    _write_seed_pages(repo)
    return repo


def _make_dirs(repo: Path) -> None:
    for d in [
        "raw/decisions", "raw/incidents",
        "wiki/services", "wiki/decisions", "wiki/concepts", "wiki/runbooks",
        ".github",
    ]:
        (repo / d).mkdir(parents=True)


def _write_config(repo: Path, name: str) -> None:
    (repo / ".compost.yml").write_text(yaml.dump({"name": name, "qmd_index": name}))


def _write_codeowners(repo: Path) -> None:
    (repo / ".github" / "CODEOWNERS").write_text(
        "wiki/ @my-team-wiki-maintainer\n"
    )


def _write_gitignore(repo: Path) -> None:
    (repo / ".gitignore").write_text(
        ".compost/codify/\n"
        ".compost/synth-log/\n"
        ".compost/assay-report.md\n"
        ".compost/checks/\n"
        ".compost/queue/\n"
        ".compost/shims/\n"
        ".compost/lint-log/\n"
    )


def _write_seed_pages(repo: Path) -> None:
    (repo / "raw" / "decisions" / "0001-stripe.md").write_text(
        "---\nsource: decision\ncaptured_at: 2026-01-10T10:00:00Z\n"
        "captured_by: alice\norigin: internal\n---\n"
        "We chose Stripe for payment processing.\n"
    )
    (repo / "wiki" / "services" / "payments.md").write_text(
        "---\n"
        "type: service\nname: payments\nowners: [alice]\n"
        "status: active\nupdated: 2026-01-15\nconfidence: high\n"
        "sources:\n  - raw/decisions/0001-stripe.md\n"
        "related: []\nsupersedes: []\nsuperseded_by: null\n"
        "---\n\n# Payments\n\n## Summary\nHandles all payment processing.\n"
        "## Key decisions\n- [Stripe as payment processor](../decisions/0001-stripe.md)\n"
        "## Architecture\nStripe integration via webhooks.\n"
        "## Gotchas\nNone known.\n"
        "## Runbooks\nNone yet.\n"
        "## Open questions\nNone.\n"
    )
    (repo / "wiki" / "concepts" / "idempotency.md").write_text(
        "---\n"
        "type: concept\nname: idempotency\nowners: [alice]\n"
        "status: active\nupdated: 2026-01-15\nconfidence: high\n"
        "sources:\n  - raw/decisions/0001-stripe.md\n"
        "related: []\nsupersedes: []\nsuperseded_by: null\n"
        "---\n\n# Idempotency\n\n"
        "Stripe requires idempotency keys on payment intent creation.\n"
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A minimal git repo with an initial commit on main."""
    repo = tmp_path / "git-wiki"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"],
                   cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"],
                   cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "initial"],
                   cwd=repo, check=True, capture_output=True)
    # Normalize to main
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    if result.stdout.strip() != "main":
        subprocess.run(["git", "branch", "-m", result.stdout.strip(), "main"],
                       cwd=repo, check=True, capture_output=True)
    return repo


@pytest.fixture
def compost_git_repo(git_repo: Path) -> Path:
    """A git repo that is also a valid compost wiki instance."""
    (git_repo / ".compost.yml").write_text(
        yaml.dump({"name": "test-wiki", "qmd_index": "test-wiki"})
    )
    subprocess.run(["git", "add", ".compost.yml"], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "add compost config"],
                   cwd=git_repo, check=True, capture_output=True)
    return git_repo


@pytest.fixture
def compost_git_repo_with_queue(compost_git_repo: Path) -> Path:
    """compost_git_repo with queue directories initialized. Leaves HEAD on main."""
    for d in ("inbox", "processing", "done", "dead"):
        (compost_git_repo / ".compost" / "queue" / d).mkdir(parents=True, exist_ok=True)
    (compost_git_repo / ".compost" / "shims").mkdir(parents=True, exist_ok=True)
    (compost_git_repo / "_plans" / "dead-letter").mkdir(parents=True, exist_ok=True)
    return compost_git_repo


@pytest.fixture
def bare_repo(tmp_path: Path) -> Path:
    """A bare git repo, usable as a local 'origin' remote. Leaves no HEAD."""
    bare = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(bare)],
        check=True, capture_output=True,
    )
    return bare


@pytest.fixture
def compost_git_repo_with_remote(compost_git_repo: Path, bare_repo: Path) -> Path:
    """compost_git_repo with a bare local repo set as 'origin'. HEAD on main."""
    subprocess.run(
        ["git", "remote", "add", "origin", f"file://{bare_repo}"],
        cwd=compost_git_repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "push", "--set-upstream", "origin", "main"],
        cwd=compost_git_repo, check=True, capture_output=True,
    )
    return compost_git_repo
