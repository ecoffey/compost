import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from click.testing import CliRunner

from compost.cli import main
from compost.gitea.client import GiteaError


def _invoke_setup(repo: Path, extra_args: list[str] | None = None, env: dict | None = None):
    runner = CliRunner()
    args = ["--repo", str(repo), "gitea", "setup", "--owner", "testowner"]
    if extra_args:
        args.extend(extra_args)
    return runner.invoke(main, args, env=env or {})


def _patched_setup(repo: Path, auth_return="testuser", create_repo_side_effect=None,
                   extra_args: list[str] | None = None):
    """Invoke gitea setup with GiteaClient mocked and push/doctor isolated."""
    with (
        patch("compost.gitea.client.GiteaClient") as mock_cls,
        patch("compost.ingest.git.push_branch"),
        patch("compost.cli._run_doctor_checks", return_value=[]),
    ):
        instance = MagicMock()
        instance.get_authenticated_user.return_value = auth_return
        if create_repo_side_effect:
            instance.create_repo.side_effect = create_repo_side_effect
        mock_cls.return_value = instance
        result = _invoke_setup(repo, extra_args=extra_args,
                               env={"GITEA_TOKEN": "fake-token"})
    return result


# ── happy path ────────────────────────────────────────────────────────────────

def test_gitea_setup_writes_config_to_compost_yml(compost_git_repo):
    result = _patched_setup(compost_git_repo)

    assert result.exit_code == 0, result.output
    config = yaml.safe_load((compost_git_repo / ".compost.yml").read_text())
    assert config["gitea"]["owner"] == "testowner"
    assert config["gitea"]["url"] == "http://localhost:3000"
    assert config["gitea"]["repo"] == "test-wiki"


def test_gitea_setup_commits_compost_yml(compost_git_repo):
    _patched_setup(compost_git_repo)

    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=compost_git_repo, capture_output=True, text=True,
    )
    assert "gitea" in log.stdout.lower()


def test_gitea_setup_uses_custom_repo_name(compost_git_repo):
    result = _patched_setup(compost_git_repo, extra_args=["--repo", "custom-wiki"])

    assert result.exit_code == 0, result.output
    config = yaml.safe_load((compost_git_repo / ".compost.yml").read_text())
    assert config["gitea"]["repo"] == "custom-wiki"


def test_gitea_setup_prints_authenticated_user(compost_git_repo):
    result = _patched_setup(compost_git_repo, auth_return="alice")

    assert "alice" in result.output


# ── failure cases ─────────────────────────────────────────────────────────────

def test_gitea_setup_fails_without_token(compost_git_repo):
    result = _invoke_setup(compost_git_repo, env={"GITEA_TOKEN": ""})

    assert result.exit_code != 0
    assert "GITEA_TOKEN" in result.output


def test_gitea_setup_fails_on_auth_error(compost_git_repo):
    with (
        patch("compost.gitea.client.GiteaClient") as mock_cls,
    ):
        instance = MagicMock()
        instance.get_authenticated_user.side_effect = GiteaError("401 Unauthorized", 401)
        mock_cls.return_value = instance
        result = _invoke_setup(compost_git_repo, env={"GITEA_TOKEN": "bad-token"})

    assert result.exit_code != 0
    assert "Auth failed" in result.output


def test_gitea_setup_fails_on_repo_creation_error(compost_git_repo):
    result = _patched_setup(
        compost_git_repo,
        create_repo_side_effect=GiteaError("500 internal", 500),
    )

    assert result.exit_code != 0
    assert "Repo creation failed" in result.output
