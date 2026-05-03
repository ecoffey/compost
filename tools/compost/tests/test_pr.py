import subprocess
from dataclasses import dataclass
from pathlib import Path

import click
import pytest

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


def test_merge_pr_blocked_on_wiki_edit(compost_git_repo_with_remote):
    repo = compost_git_repo_with_remote
    branch = "raw/2026-04-28-has-wiki-edit"
    raw_f = repo / "raw" / "notes" / "2026-04-28-has-wiki-edit.md"
    raw_f.parent.mkdir(parents=True, exist_ok=True)
    raw_f.write_text("raw content")
    wiki_f = repo / "wiki" / "services" / "payments.md"
    wiki_f.parent.mkdir(parents=True, exist_ok=True)
    wiki_f.write_text("wiki content")
    subprocess.run(["git", "checkout", "-b", branch], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", str(raw_f), str(wiki_f)], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "raw + wiki edit"],
                   cwd=repo, check=True, capture_output=True)
    with pytest.raises(click.UsageError, match="wiki/"):
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


# ── create_pr: Tier 1 integration ────────────────────────────────────────────


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
