import subprocess
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner

from compost.checks.runner import CheckResult, ChecksConfig, Finding
from compost.cli import main
from compost.gitea.client import GiteaPR
from compost.ingest.pr import create_pr, merge_pr


@dataclass
class FakeGiteaClient:
    """Minimal stub: returns a fixed PR for find/open, records merge calls."""
    pr: GiteaPR = GiteaPR(42, "http://gitea-test/pulls/42", "open")
    merged_prs: list = None

    def __post_init__(self):
        if self.merged_prs is None:
            self.merged_prs = []

    def open_pr(self, branch, base, title, body):
        return self.pr

    def find_pr(self, branch):
        return self.pr

    def merge_pr(self, pr_number):
        self.merged_prs.append(pr_number)


def _add_file_on_branch(repo: Path, rel_path: str, branch: str, content: str = "body") -> None:
    """Create a file and commit it on a new branch. Leaves HEAD on that branch."""
    f = repo / rel_path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)
    subprocess.run(["git", "checkout", "-b", branch], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", str(f)], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", f"add {rel_path}"],
                   cwd=repo, check=True, capture_output=True)


# ── create_pr ────────────────────────────────────────────────────────────────

def test_create_pr_returns_pr(compost_git_repo):
    repo = compost_git_repo
    branch = "raw/2026-04-28-decision"
    _add_file_on_branch(repo, "raw/decisions/2026-04-28-decision.md", branch)

    client = FakeGiteaClient()
    pr = create_pr(repo, branch, client)

    assert pr.number == 42
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_create_pr_body_includes_changed_files(compost_git_repo):
    repo = compost_git_repo
    branch = "raw/2026-04-28-note"
    _add_file_on_branch(repo, "raw/notes/2026-04-28-note.md", branch)

    received_bodies = []

    @dataclass
    class CapturingClient:
        pr: GiteaPR = GiteaPR(1, "http://gitea/pulls/1", "open")
        def open_pr(self, branch, base, title, body):
            received_bodies.append(body)
            return self.pr
        def find_pr(self, branch): return self.pr
        def merge_pr(self, n): pass

    create_pr(repo, branch, CapturingClient())
    assert len(received_bodies) == 1
    assert "raw/notes/2026-04-28-note.md" in received_bodies[0]

    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


# ── merge_pr ─────────────────────────────────────────────────────────────────

def test_merge_pr_calls_gitea_and_syncs_local(compost_git_repo_with_remote):
    repo = compost_git_repo_with_remote
    branch = "raw/2026-04-28-merge-me"
    _add_file_on_branch(repo, "raw/notes/2026-04-28-merge-me.md", branch)
    subprocess.run(["git", "push", "origin", branch], cwd=repo, check=True, capture_output=True)

    client = FakeGiteaClient()
    result = merge_pr(repo, client)

    assert client.merged_prs == [42]
    assert result.number == 42
    head = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo, capture_output=True, text=True,
    ).stdout.strip()
    assert head == "main"


def test_merge_pr_blocked_on_non_raw_branch(compost_git_repo_with_remote):
    repo = compost_git_repo_with_remote
    subprocess.run(
        ["git", "checkout", "-b", "feature/something"],
        cwd=repo, check=True, capture_output=True,
    )
    with pytest.raises(click.UsageError, match="Not on a raw/"):
        merge_pr(repo, FakeGiteaClient())
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)



def test_merge_pr_raises_when_no_open_pr(compost_git_repo_with_remote):
    repo = compost_git_repo_with_remote
    branch = "raw/2026-04-28-no-pr"
    _add_file_on_branch(repo, "raw/notes/2026-04-28-no-pr.md", branch)

    @dataclass
    class NoPRClient:
        def find_pr(self, branch): return None
        def merge_pr(self, n): pass
        def open_pr(self, *a, **kw): pass

    with pytest.raises(click.UsageError, match="No open PR"):
        merge_pr(repo, NoPRClient())
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


# ── create_pr: Tier 1 + Tier 2 integration ───────────────────────────────────


def test_create_pr_body_includes_tier1_on_fire(compost_git_repo):
    from compost.ingest.classifier import ClassifyDecision, Trigger

    repo = compost_git_repo
    branch = "raw/2026-04-28-tier1-fire"
    _add_file_on_branch(repo, "raw/decisions/2026-04-28-tier1-fire.md", branch)

    decision = ClassifyDecision(
        fired=True,
        triggers=[
            Trigger(kind="source", pattern="decision"),
            Trigger(kind="semantic", pattern="decision"),
        ],
        rationale="2 matched triggers: source:decision, semantic:decision",
    )

    received_bodies = []

    @dataclass
    class CapturingClient:
        pr: GiteaPR = GiteaPR(1, "http://gitea/pulls/1", "open")
        def open_pr(self, branch, base, title, body):
            received_bodies.append(body)
            return self.pr
        def find_pr(self, branch): return self.pr
        def merge_pr(self, n): pass

    create_pr(repo, branch, CapturingClient(), decision=decision)
    assert len(received_bodies) == 1
    body = received_bodies[0]
    assert "**Tier 1:** FIRE" in body
    assert "source:decision" in body
    assert "semantic:decision" in body

    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_create_pr_body_omits_tier1_when_no_decision(compost_git_repo):
    repo = compost_git_repo
    branch = "raw/2026-04-28-no-decision"
    _add_file_on_branch(repo, "raw/notes/2026-04-28-no-decision.md", branch)

    received_bodies = []

    @dataclass
    class CapturingClient:
        pr: GiteaPR = GiteaPR(1, "http://gitea/pulls/1", "open")
        def open_pr(self, branch, base, title, body):
            received_bodies.append(body)
            return self.pr
        def find_pr(self, branch): return self.pr
        def merge_pr(self, n): pass

    create_pr(repo, branch, CapturingClient(), decision=None)
    body = received_bodies[0]
    assert "Tier 1" not in body
    assert "**Files changed:**" in body

    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_create_pr_body_includes_tier2_on_result(compost_git_repo):
    from compost.synth.agent import SynthesisResult, FileDiff, ContradictionNote

    repo = compost_git_repo
    branch = "raw/2026-05-03-with-synth"
    _add_file_on_branch(repo, "raw/decisions/2026-05-03-with-synth.md", branch)

    result = SynthesisResult(
        run_id="abc123",
        wiki_diffs=[
            FileDiff(rel_path=Path("wiki/services/payments.md"), content="...", is_new=False),
        ],
        pr_description=(
            "**Tier 2 synthesis:**\n"
            "- wiki/services/payments.md (updated)\n\n"
            "**Contradictions declared:** none"
        ),
        cited_sources=["raw/decisions/2026-05-03-with-synth.md"],
        declared_contradictions=[],
    )

    received_bodies = []

    @dataclass
    class CapturingClient:
        pr: GiteaPR = GiteaPR(1, "http://gitea/pulls/1", "open")
        def open_pr(self, branch, base, title, body):
            received_bodies.append(body)
            return self.pr
        def find_pr(self, branch): return self.pr
        def merge_pr(self, n): pass

    create_pr(repo, branch, CapturingClient(), result=result)
    body = received_bodies[0]
    assert "Tier 2 synthesis" in body
    assert "wiki/services/payments.md" in body
    assert "Contradictions declared:** none" in body

    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


def test_create_pr_body_omits_tier2_when_no_result(compost_git_repo):
    repo = compost_git_repo
    branch = "raw/2026-05-03-no-result"
    _add_file_on_branch(repo, "raw/notes/2026-05-03-no-result.md", branch)

    received_bodies = []

    @dataclass
    class CapturingClient:
        pr: GiteaPR = GiteaPR(1, "http://gitea/pulls/1", "open")
        def open_pr(self, branch, base, title, body):
            received_bodies.append(body)
            return self.pr
        def find_pr(self, branch): return self.pr
        def merge_pr(self, n): pass

    create_pr(repo, branch, CapturingClient(), result=None)
    body = received_bodies[0]
    assert "Tier 2" not in body

    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


# ── pr merge CLI (check gate options) ────────────────────────────────────────


def _setup_raw_branch(repo: Path, branch: str) -> None:
    raw = repo / "raw" / "notes" / "test.md"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("body")
    subprocess.run(["git", "checkout", "-b", branch], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", str(raw)], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add raw"], cwd=repo, check=True, capture_output=True)


def _passing_checks() -> list[CheckResult]:
    return [CheckResult(name="kotlin_assay", status="skipped",
                        findings=[], skip_reason="kotlinc absent")]


def _failing_checks() -> list[CheckResult]:
    return [CheckResult(
        name="provenance", status="fail",
        findings=[Finding(check="provenance", severity="fail",
                          message="sources list is empty", page="wiki/services/foo.md")],
    )]


def test_pr_merge_cli_no_checks_skips_run_checks(compost_git_repo):
    """--no-checks flag bypasses run_checks entirely and merges."""
    repo = compost_git_repo
    _setup_raw_branch(repo, "raw/2026-05-04-no-checks")

    runner = CliRunner()
    with (
        patch("compost.checks.runner.run_checks") as mock_checks,
        patch("compost.ingest.pr.merge_pr",
              return_value=GiteaPR(99, "http://gitea/pulls/99", "merged")),
        patch("compost.cli._load_gitea_client", return_value=FakeGiteaClient()),
    ):
        result = runner.invoke(main, ["--repo", str(repo), "pr", "merge", "--no-checks"])

    mock_checks.assert_not_called()
    assert result.exit_code == 0, result.output


def test_pr_merge_cli_failing_checks_block_merge(compost_git_repo):
    """Failing checks cause a non-zero exit and prevent merge_pr from being called."""
    repo = compost_git_repo
    _setup_raw_branch(repo, "raw/2026-05-04-check-fail")

    runner = CliRunner()
    merge_calls: list[int] = []
    with (
        patch("compost.checks.runner.run_checks", return_value=_failing_checks()),
        patch("compost.checks.runner.load_checks_config", return_value=ChecksConfig()),
        patch("compost.checks.runner.infer_raw_path", return_value=None),
        patch("compost.checks.runner.write_check_report", return_value=Path("/tmp/r.md")),
        patch("compost.ingest.pr.merge_pr",
              side_effect=lambda *a, **kw: merge_calls.append(1)),
        patch("compost.cli._load_gitea_client", return_value=FakeGiteaClient()),
    ):
        result = runner.invoke(main, ["--repo", str(repo), "pr", "merge"])

    assert result.exit_code != 0
    assert merge_calls == []


def test_pr_merge_cli_override_allows_merge_despite_failing_checks(compost_git_repo):
    """--override --reason bypasses a failing check gate and merges."""
    repo = compost_git_repo
    _setup_raw_branch(repo, "raw/2026-05-04-override")

    runner = CliRunner()
    with (
        patch("compost.checks.runner.run_checks", return_value=_failing_checks()),
        patch("compost.checks.runner.load_checks_config", return_value=ChecksConfig()),
        patch("compost.checks.runner.infer_raw_path", return_value=None),
        patch("compost.checks.runner.write_check_report", return_value=Path("/tmp/r.md")),
        patch("compost.ingest.pr.merge_pr",
              return_value=GiteaPR(99, "http://gitea/pulls/99", "merged")),
        patch("compost.cli._load_gitea_client", return_value=FakeGiteaClient()),
        patch("compost.cli._log_override"),
    ):
        result = runner.invoke(
            main,
            ["--repo", str(repo), "pr", "merge", "--override", "--reason", "known issue"],
        )

    assert result.exit_code == 0, result.output
