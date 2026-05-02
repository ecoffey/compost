# § Implementation Detail: Gitea PR Integration

Derived from `_plans/2026-04-28-0230-gitea-pr-integration-high-level.md`.

## Verified API shapes

- `POST /api/v1/repos/{owner}/{repo}/pulls` body: `{"head", "base", "title", "body"}` → PR object with `number`, `html_url`, `state`, `head.ref`
- `GET /api/v1/repos/{owner}/{repo}/pulls?state=open&limit=50` → array; no `head` query filter — filter client-side by `pr["head"]["ref"]`
- `POST /api/v1/repos/{owner}/{repo}/pulls/{index}/merge` body: `{"do": "merge"}` (`do` required, enum: merge/rebase/rebase-merge/squash/fast-forward-only/manually-merged) → 200 empty body
- `GET /api/v1/user` → `{"login": "...", ...}`
- `POST /api/v1/user/repos` body: `{"name": "..."}` → 201 on success
- `GET /api/v1/repos/{owner}/{repo}` → 200 if exists, 404 if not
- `httpx` 0.28.1 already installed; `httpx.MockTransport(handler=fn)` confirmed working

---

## § Files to create / modify / delete

| File | Action |
|---|---|
| `tools/compost/gitea/__init__.py` | New (empty) |
| `tools/compost/gitea/client.py` | New |
| `tools/compost/ingest/pr.py` | Rewrite — Gitea only, delete PRLog + _pr-log |
| `tools/compost/ingest/git.py` | Add `push_branch`, `fetch_and_ff`, `set_remote_url`, `get_remote_url` |
| `tools/compost/cli.py` | Add `gitea` group + `setup`; rewrite `raw_add`, `pr open`, `pr merge`; add doctor checks; add `_load_gitea_client` helper |
| `tools/compost/pyproject.toml` | Add `compost.gitea` package (httpx already installed) |
| `tools/compost/tests/conftest.py` | Add `bare_repo` and `compost_git_repo_with_remote` fixtures |
| `tools/compost/tests/test_gitea_client.py` | New |
| `tools/compost/tests/test_pr.py` | Rewrite |
| `tools/compost/tests/test_git.py` | Add tests for new git.py functions |
| `README.md` | Update PR section and steel thread |

---

## § Step-by-step execution (TDD)

All test runs from `tools/compost/`:
```bash
python -m pytest tests/ -x -q
```

---

### Step 1 — `compost/gitea/__init__.py`

Create empty file: `tools/compost/gitea/__init__.py`

---

### Step 2 — `compost/gitea/client.py` (red → green)

**Write tests first** in `tools/compost/tests/test_gitea_client.py`:

```python
import httpx
import pytest

from compost.gitea.client import GiteaClient, GiteaError, GiteaPR


def _make_client(handler) -> GiteaClient:
    """Build GiteaClient backed by httpx.MockTransport. Owner='owner', repo='repo'."""
    transport = httpx.MockTransport(handler=handler)
    return GiteaClient(
        "http://gitea-test", "owner", "repo", "fake-token",
        _transport=transport,
    )


# ── get_authenticated_user ────────────────────────────────────────────────────

def test_get_authenticated_user_returns_login():
    client = _make_client(lambda req: httpx.Response(200, json={"login": "alice"}))
    assert client.get_authenticated_user() == "alice"


def test_get_authenticated_user_raises_on_401():
    client = _make_client(lambda req: httpx.Response(401, json={"message": "Unauthorized"}))
    with pytest.raises(GiteaError, match="401"):
        client.get_authenticated_user()


# ── create_repo ───────────────────────────────────────────────────────────────

def test_create_repo_creates_when_not_exists():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, str(req.url)))
        if req.method == "GET":
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(201, json={"name": "my-wiki"})

    client = _make_client(handler)
    client.create_repo("my-wiki")
    assert any("POST" == m for m, _ in calls)


def test_create_repo_noop_when_exists():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.method)
        return httpx.Response(200, json={"name": "my-wiki"})

    client = _make_client(handler)
    client.create_repo("my-wiki")
    assert "POST" not in calls


# ── open_pr ───────────────────────────────────────────────────────────────────

def test_open_pr_returns_pr():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={
            "number": 3, "html_url": "http://gitea-test/owner/repo/pulls/3", "state": "open",
        })

    client = _make_client(handler)
    pr = client.open_pr("raw/2026-04-28-foo", "main", "raw: foo", "body text")
    assert pr.number == 3
    assert "pulls/3" in pr.url
    assert pr.state == "open"


def test_open_pr_raises_on_error():
    client = _make_client(lambda req: httpx.Response(422, json={"message": "head branch not found"}))
    with pytest.raises(GiteaError, match="422"):
        client.open_pr("bad-branch", "main", "title", "body")


# ── find_pr ───────────────────────────────────────────────────────────────────

def test_find_pr_returns_matching_pr():
    prs = [
        {"number": 7, "html_url": "http://gitea-test/pulls/7", "state": "open",
         "head": {"ref": "raw/2026-04-28-target"}},
        {"number": 8, "html_url": "http://gitea-test/pulls/8", "state": "open",
         "head": {"ref": "raw/2026-04-28-other"}},
    ]
    client = _make_client(lambda req: httpx.Response(200, json=prs))
    pr = client.find_pr("raw/2026-04-28-target")
    assert pr is not None
    assert pr.number == 7


def test_find_pr_returns_none_when_not_found():
    client = _make_client(lambda req: httpx.Response(200, json=[]))
    assert client.find_pr("raw/2026-04-28-no-match") is None


def test_find_pr_returns_none_when_branch_not_in_list():
    prs = [{"number": 1, "html_url": "http://gitea-test/pulls/1", "state": "open",
             "head": {"ref": "raw/2026-04-28-other"}}]
    client = _make_client(lambda req: httpx.Response(200, json=prs))
    assert client.find_pr("raw/2026-04-28-mine") is None


# ── merge_pr ──────────────────────────────────────────────────────────────────

def test_merge_pr_succeeds():
    client = _make_client(lambda req: httpx.Response(200))
    client.merge_pr(42)   # no exception = success


def test_merge_pr_raises_on_4xx():
    client = _make_client(lambda req: httpx.Response(405, json={"message": "already merged"}))
    with pytest.raises(GiteaError, match="405"):
        client.merge_pr(42)
```

**Run tests:** all fail (module not found). Now implement `tools/compost/gitea/client.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class GiteaError(Exception):
    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class GiteaPR:
    number: int
    url: str
    state: str   # "open" | "closed" | "merged"


class GiteaClient:
    def __init__(
        self,
        url: str,
        owner: str,
        repo: str,
        token: str,
        *,
        _transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not token:
            raise GiteaError("GITEA_TOKEN is not set")
        self._base = url.rstrip("/")
        self._owner = owner
        self._repo = repo
        self._http = httpx.Client(
            headers={
                "Authorization": f"token {token}",
                "Content-Type": "application/json",
            },
            transport=_transport,
        )

    def __repr__(self) -> str:
        return f"GiteaClient(url={self._base!r}, owner={self._owner!r}, repo={self._repo!r})"

    # ── internal ──────────────────────────────────────────────────────────────

    def _request(
        self, method: str, path: str, body: dict | None = None, **params: Any
    ) -> dict | list | None:
        url = f"{self._base}/api/v1{path}"
        r = self._http.request(method, url, json=body, params=params or None)
        if r.status_code >= 400:
            try:
                msg = r.json().get("message", r.text)
            except Exception:
                msg = r.text
            raise GiteaError(f"{r.status_code} {method} {path}: {msg}", status=r.status_code)
        return r.json() if r.content else None

    # ── public ────────────────────────────────────────────────────────────────

    def get_authenticated_user(self) -> str:
        data = self._request("GET", "/user")
        return data["login"]

    def create_repo(self, name: str, private: bool = False) -> None:
        """Create repo under owner. No-op if already exists."""
        try:
            self._request("GET", f"/repos/{self._owner}/{name}")
            return
        except GiteaError as e:
            if e.status != 404:
                raise
        self._request("POST", "/user/repos", {"name": name, "private": private})

    def open_pr(self, branch: str, base: str, title: str, body: str) -> GiteaPR:
        data = self._request(
            "POST",
            f"/repos/{self._owner}/{self._repo}/pulls",
            {"head": branch, "base": base, "title": title, "body": body},
        )
        return GiteaPR(number=data["number"], url=data["html_url"], state=data["state"])

    def find_pr(self, branch: str) -> GiteaPR | None:
        prs = self._request(
            "GET",
            f"/repos/{self._owner}/{self._repo}/pulls",
            state="open",
            limit=50,
        )
        for pr in (prs or []):
            if pr["head"]["ref"] == branch:
                return GiteaPR(number=pr["number"], url=pr["html_url"], state=pr["state"])
        return None

    def merge_pr(self, pr_number: int) -> None:
        self._request(
            "POST",
            f"/repos/{self._owner}/{self._repo}/pulls/{pr_number}/merge",
            {"do": "merge"},
        )
```

**Run tests:** all pass.

---

### Step 3 — `git.py` additions (red → green)

**Add to `test_git.py`** (append after existing tests):

```python
# ── push_branch ──────────────────────────────────────────────────────────────

@pytest.fixture
def bare_repo(tmp_path: Path) -> Path:
    """A bare git repo usable as a local remote. tmp_path-scoped."""
    bare = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(bare)],
        check=True, capture_output=True,
    )
    return bare


def test_push_branch(git_repo, bare_repo):
    from compost.ingest.git import push_branch, set_remote_url

    set_remote_url(git_repo, "origin", f"file://{bare_repo}")
    subprocess.run(
        ["git", "push", "--set-upstream", "origin", "main"],
        cwd=git_repo, check=True, capture_output=True,
    )

    new_file = git_repo / "raw" / "notes" / "push-test.md"
    new_file.parent.mkdir(parents=True, exist_ok=True)
    new_file.write_text("hello")
    subprocess.run(
        ["git", "checkout", "-b", "raw/2026-04-28-push"],
        cwd=git_repo, check=True, capture_output=True,
    )
    subprocess.run(["git", "add", str(new_file)], cwd=git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add push-test"],
        cwd=git_repo, check=True, capture_output=True,
    )

    push_branch(git_repo, "origin", "raw/2026-04-28-push")

    result = subprocess.run(
        ["git", "ls-remote", "--heads", f"file://{bare_repo}", "raw/2026-04-28-push"],
        capture_output=True, text=True,
    )
    assert "raw/2026-04-28-push" in result.stdout

    subprocess.run(["git", "checkout", "main"], cwd=git_repo, check=True, capture_output=True)


def test_push_branch_fails_on_missing_remote(git_repo):
    from compost.ingest.git import push_branch
    with pytest.raises(click.UsageError, match="Push failed"):
        push_branch(git_repo, "no-such-remote", "main")


# ── fetch_and_ff ─────────────────────────────────────────────────────────────

def test_fetch_and_ff(git_repo, bare_repo):
    from compost.ingest.git import fetch_and_ff, set_remote_url

    set_remote_url(git_repo, "origin", f"file://{bare_repo}")
    subprocess.run(
        ["git", "push", "--set-upstream", "origin", "main"],
        cwd=git_repo, check=True, capture_output=True,
    )
    # Already in sync — fetch_and_ff should succeed (no-op)
    fetch_and_ff(git_repo, "origin", "main")
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=git_repo, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "main"


# ── set_remote_url ────────────────────────────────────────────────────────────

def test_set_remote_url_adds_new_remote(git_repo, bare_repo):
    from compost.ingest.git import set_remote_url
    set_remote_url(git_repo, "origin", f"file://{bare_repo}")
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=git_repo, capture_output=True, text=True,
    )
    assert str(bare_repo) in result.stdout


def test_set_remote_url_updates_existing(git_repo, bare_repo):
    from compost.ingest.git import set_remote_url
    set_remote_url(git_repo, "origin", "http://old.example.com/repo.git")
    set_remote_url(git_repo, "origin", f"file://{bare_repo}")
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=git_repo, capture_output=True, text=True,
    )
    assert str(bare_repo) in result.stdout


# ── get_remote_url ────────────────────────────────────────────────────────────

def test_get_remote_url_returns_url(git_repo, bare_repo):
    from compost.ingest.git import get_remote_url, set_remote_url
    set_remote_url(git_repo, "origin", f"file://{bare_repo}")
    url = get_remote_url(git_repo, "origin")
    assert str(bare_repo) in url


def test_get_remote_url_returns_none_when_missing(git_repo):
    from compost.ingest.git import get_remote_url
    assert get_remote_url(git_repo, "origin") is None
```

**Run tests:** new tests fail. Now add to `tools/compost/ingest/git.py` (append after `fast_forward_merge`):

```python
def push_branch(repo: Path, remote: str, branch: str) -> None:
    """Push branch to remote. Raises click.UsageError on failure."""
    result = subprocess.run(
        ["git", "push", remote, branch],
        cwd=repo, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise click.UsageError(
            f"Push failed: {result.stderr.strip()}\n"
            f"Retry manually: git push {remote} {branch}"
        )


def fetch_and_ff(repo: Path, remote: str, branch: str) -> None:
    """Fetch from remote, checkout branch, fast-forward to remote tracking ref."""
    subprocess.run(["git", "fetch", remote], cwd=repo, check=True, capture_output=True)

    result = subprocess.run(
        ["git", "checkout", branch],
        cwd=repo, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise click.UsageError(f"Cannot checkout '{branch}': {result.stderr.strip()}")

    result = subprocess.run(
        ["git", "merge", "--ff-only", f"{remote}/{branch}"],
        cwd=repo, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise click.UsageError(
            f"Cannot fast-forward '{branch}' to '{remote}/{branch}': {result.stderr.strip()}"
        )


def set_remote_url(repo: Path, remote: str, url: str) -> None:
    """Add remote if missing, or update its URL if it already exists."""
    result = subprocess.run(
        ["git", "remote", "get-url", remote],
        cwd=repo, capture_output=True,
    )
    if result.returncode == 0:
        subprocess.run(
            ["git", "remote", "set-url", remote, url],
            cwd=repo, check=True, capture_output=True,
        )
    else:
        subprocess.run(
            ["git", "remote", "add", remote, url],
            cwd=repo, check=True, capture_output=True,
        )


def get_remote_url(repo: Path, remote: str) -> str | None:
    """Return the URL for a remote, or None if the remote doesn't exist."""
    result = subprocess.run(
        ["git", "remote", "get-url", remote],
        cwd=repo, capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()
```

**Run tests:** all pass.

---

### Step 4 — `ingest/pr.py` rewrite (red → green)

**Add `bare_repo` and `compost_git_repo_with_remote` to `conftest.py`** (append after `compost_git_repo`):

```python
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
```

**Note:** `bare_repo` fixture is defined in both `conftest.py` and `test_git.py`. Since `test_git.py` defines it locally, conftest.py adds it as a shared fixture for `test_pr.py`. Remove the local definition from `test_git.py` once conftest.py has it, or keep them both (pytest will use the nearest scope). To avoid duplication, **remove the local `bare_repo` fixture from `test_git.py`** after adding it to `conftest.py`.

**Rewrite `tools/compost/tests/test_pr.py`:**

```python
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
```

**Run tests:** fail (no `create_pr`/`merge_pr` in pr.py). Now rewrite `tools/compost/ingest/pr.py`:

```python
from __future__ import annotations

from pathlib import Path

import click

from compost.gitea.client import GiteaClient, GiteaPR
from compost.ingest.git import (
    changed_files,
    current_branch,
    default_branch,
    fetch_and_ff,
)


def create_pr(repo: Path, branch: str, client: GiteaClient) -> GiteaPR:
    """Build PR body from changed files and open a Gitea PR. Returns the new PR."""
    base = default_branch(repo)
    files = changed_files(repo, branch, base)
    files_list = "\n".join(f"- {f}" for f in files) if files else "_(none)_"
    body = (
        f"**Files changed:**\n{files_list}\n\n"
        f"---\n"
        f"*Review and merge with `compost pr merge`.*"
    )
    return client.open_pr(branch, base, f"raw: {branch}", body)


def merge_pr(repo: Path, client: GiteaClient) -> GiteaPR:
    """Validate guards, merge via Gitea API, sync local repo. Returns merged PR."""
    branch = current_branch(repo)
    if not branch.startswith("raw/"):
        raise click.UsageError(
            f"Not on a raw/* branch (current: '{branch}'). "
            "Checkout a raw/* branch before merging."
        )

    base = default_branch(repo)
    files = changed_files(repo, branch, base)
    wiki_edits = [f for f in files if f.startswith("wiki/")]
    if wiki_edits:
        raise click.UsageError(
            "Branch contains wiki/ edits which must not be auto-merged:\n"
            + "\n".join(f"  {f}" for f in wiki_edits)
        )

    pr = client.find_pr(branch)
    if pr is None:
        raise click.UsageError(
            f"No open PR found for '{branch}'. "
            "Was it already merged or not yet pushed?"
        )

    client.merge_pr(pr.number)
    fetch_and_ff(repo, "origin", base)

    return GiteaPR(number=pr.number, url=pr.url, state="merged")
```

**Run tests:** all pass.

---

### Step 5 — `pyproject.toml` update

In `tools/compost/pyproject.toml`, add `"compost.gitea"` to the `packages` list:

```toml
packages = [
    "compost",
    "compost.gitea",
    "compost.model",
    "compost.mcp",
    "compost.ingest",
]
```

Run `pip install -e .` from `tools/compost/` to register the new package.

---

### Step 6 — `cli.py` updates

Replace the entire `cli.py` with the following (full file shown; changes are: `_load_gitea_client` helper, `gitea` group + `gitea setup`, rewritten `raw_add`, rewritten `pr_open` and `pr_merge`, new doctor checks):

```python
import os
import subprocess
import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table

from compost.model.frontmatter import validate_frontmatter, parse_frontmatter
from compost.repo import find_repo_root, load_repo_config
from compost.session import list_sessions, find_session, format_session

console = Console()


@click.group()
@click.option("--repo", envvar="COMPOST_REPO", default=None,
              help="Path to wiki repo. Falls back to COMPOST_REPO env var then cwd walk.")
@click.pass_context
def main(ctx: click.Context, repo: str | None) -> None:
    ctx.ensure_object(dict)
    ctx.obj["repo"] = Path(repo).resolve() if repo else None


@main.command()
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Verify repo health: qmd collections, frontmatter, CODEOWNERS, Gitea."""
    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found in cwd or any parent.[/red]")
        sys.exit(1)

    checks = _run_doctor_checks(repo)
    _render_checks(checks)
    if any(not ok for _, ok, _ in checks):
        sys.exit(1)


@main.command("mcp")
@click.pass_context
def mcp_serve(ctx: click.Context) -> None:
    """Start the MCP server (stdio transport)."""
    import asyncio
    from compost.mcp.server import run_server

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found. Run compost init first.[/red]")
        sys.exit(1)
    asyncio.run(run_server(repo))


@main.command()
@click.argument("path", default=".", type=click.Path())
@click.option("--name", required=True, help="Team name (used as qmd index name).")
def init(path: str, name: str) -> None:
    """Bootstrap a new compost wiki repo at PATH."""
    from compost.bootstrap import bootstrap_repo
    bootstrap_repo(Path(path).resolve(), name)


@main.group("session")
def session_group() -> None:
    """Inspect Claude Code session transcripts."""


@session_group.command("list")
@click.pass_context
def session_list(ctx: click.Context) -> None:
    """List sessions for the current repo."""
    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    sessions = list_sessions(repo)
    if not sessions:
        console.print("[dim]No sessions found.[/dim]")
        return

    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("ID", style="cyan")
    table.add_column("Timestamp")
    for s in sessions:
        ts = (s["ts"] or "")[:19].replace("T", " ")
        table.add_row(s["id"], ts)
    console.print(table)


@session_group.command("show")
@click.argument("session_id")
@click.option("--tools", "tools_only", is_flag=True, help="Show tool calls and results only.")
@click.option("--mcp", "mcp_only", is_flag=True, help="Show MCP tool calls and results only.")
@click.option("--snippet", default=400, show_default=True,
              help="Max chars shown per tool result.")
def session_show(session_id: str, tools_only: bool, mcp_only: bool, snippet: int) -> None:
    """Show a session transcript by ID (prefix match supported)."""
    path = find_session(session_id)
    if path is None:
        console.print(f"[red]Session not found: {session_id}[/red]")
        sys.exit(1)

    console.print(f"[dim]{path}[/dim]\n")
    output = format_session(path, tools_only=tools_only, mcp_only=mcp_only,
                            snippet_len=snippet)
    console.print(output)


# ── raw commands ─────────────────────────────────────────────────────────────

SOURCE_CHOICES = ["slack", "incident", "decision", "note", "meeting", "support"]


@main.group("raw")
def raw_group() -> None:
    """Manage raw source ingestion."""


@raw_group.command("add")
@click.option("--source", required=True, type=click.Choice(SOURCE_CHOICES),
              help="Source type.")
@click.option("--title", required=True, help="Human-readable title (slug + frontmatter).")
@click.option("--captured-by", default=None,
              help="Author. Defaults to git config user.name.")
@click.option("--origin", default="",
              help="Source reference (e.g. PagerDuty ID, Slack URL).")
@click.option("--channel", default="",
              help="Slack channel name. Required when --source=slack.")
@click.pass_context
def raw_add(ctx: click.Context, source: str, title: str, captured_by: str | None,
            origin: str, channel: str) -> None:
    """Add a raw source file from stdin, commit on a new branch, and open a Gitea PR."""
    from datetime import datetime, timezone

    from compost.ingest.git import assert_git_repo, create_branch_and_commit, get_git_user_name, push_branch
    from compost.ingest.pr import create_pr
    from compost.ingest.raw import slugify, write_raw
    from compost.gitea.client import GiteaError

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    assert_git_repo(repo)

    body = click.get_text_stream("stdin").read()

    if captured_by is None:
        captured_by = get_git_user_name(repo)

    try:
        raw_file = write_raw(
            repo, source, title, body,
            captured_by=captured_by,
            origin=origin,
            channel=channel,
        )
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    ts = datetime.now(timezone.utc)
    branch = f"raw/{ts:%Y-%m-%d}-{slugify(title)}"

    create_branch_and_commit(
        repo, branch, [raw_file.path],
        message=f"raw: {title}",
    )

    console.print(f"[green]✓[/green] {raw_file.rel_path}")
    console.print(f"branch: {branch}")

    try:
        push_branch(repo, "origin", branch)
        client = _load_gitea_client(repo)
        pr = create_pr(repo, branch, client)
        console.print(f"PR #{pr.number}: {pr.url}")
    except (click.UsageError, GiteaError) as e:
        console.print(f"[yellow]⚠ push/PR failed: {e}[/yellow]")
        console.print("Push manually: [bold]git push origin " + branch + "[/bold]")


# ── pr commands ──────────────────────────────────────────────────────────────


@main.group("pr")
def pr_group() -> None:
    """Manage Gitea PRs for raw/* branches."""


@pr_group.command("open")
@click.pass_context
def pr_open(ctx: click.Context) -> None:
    """Print the Gitea PR URL for the current branch."""
    from compost.ingest.git import current_branch
    from compost.gitea.client import GiteaError

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    client = _load_gitea_client(repo)
    branch = current_branch(repo)

    try:
        pr = client.find_pr(branch)
    except GiteaError as e:
        console.print(f"[red]Gitea error: {e}[/red]")
        sys.exit(1)

    if pr is None:
        console.print(f"[red]No open PR for '{branch}'. Did `compost raw add` succeed?[/red]")
        sys.exit(1)

    console.print(f"PR #{pr.number}: {pr.url}  ({pr.state})")


@pr_group.command("merge")
@click.pass_context
def pr_merge(ctx: click.Context) -> None:
    """Merge the current raw/* branch via Gitea PR, then sync local repo."""
    from compost.ingest.pr import merge_pr
    from compost.gitea.client import GiteaError

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    client = _load_gitea_client(repo)

    try:
        pr = merge_pr(repo, client)
        console.print(f"[green]merged[/green] (PR #{pr.number})")
    except GiteaError as e:
        console.print(f"[red]Gitea error: {e}[/red]")
        sys.exit(1)


# ── gitea commands ────────────────────────────────────────────────────────────


@main.group("gitea")
def gitea_group() -> None:
    """Configure and manage the Gitea integration."""


@gitea_group.command("setup")
@click.option("--url", default="http://localhost:3000", show_default=True,
              help="Gitea base URL.")
@click.option("--owner", required=True, help="Gitea user or org login.")
@click.option("--repo", "repo_name", default=None,
              help="Gitea repo name. Defaults to .compost.yml 'name'.")
@click.option("--token", default=None, envvar="GITEA_TOKEN", help="API token.")
@click.option("--remote", default="origin", show_default=True, help="Git remote name.")
@click.pass_context
def gitea_setup(ctx: click.Context, url: str, owner: str, repo_name: str | None,
                token: str | None, remote: str) -> None:
    """Bootstrap Gitea integration: create repo, set remote, push main, update config."""
    from compost.gitea.client import GiteaClient, GiteaError
    from compost.ingest.git import push_branch, set_remote_url

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    if not token:
        console.print("[red]GITEA_TOKEN not set. Export it or pass --token.[/red]")
        sys.exit(1)

    config = load_repo_config(repo)
    gitea_repo_name = repo_name or config["name"]

    client = GiteaClient(url=url, owner=owner, repo=gitea_repo_name, token=token)

    try:
        username = client.get_authenticated_user()
        console.print(f"[green]✓[/green] authenticated as {username}")
    except GiteaError as e:
        console.print(f"[red]Auth failed: {e}[/red]")
        sys.exit(1)

    try:
        client.create_repo(gitea_repo_name)
        console.print(f"[green]✓[/green] repo {owner}/{gitea_repo_name}")
    except GiteaError as e:
        console.print(f"[red]Repo creation failed: {e}[/red]")
        sys.exit(1)

    clone_url = f"{url.rstrip('/')}/{owner}/{gitea_repo_name}.git"
    set_remote_url(repo, remote, clone_url)
    console.print(f"[green]✓[/green] remote '{remote}' → {clone_url}")

    try:
        push_branch(repo, remote, "main")
        console.print(f"[green]✓[/green] pushed main")
    except click.UsageError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    config["gitea"] = {"url": url, "owner": owner, "repo": gitea_repo_name}
    config_path = repo / ".compost.yml"
    with config_path.open("w") as f:
        yaml.dump(config, f, default_flow_style=False)
    console.print("[green]✓[/green] .compost.yml updated with gitea config")


# ── helpers ───────────────────────────────────────────────────────────────────


def _load_gitea_client(repo: Path) -> "GiteaClient":
    """Build GiteaClient from .compost.yml gitea config + GITEA_TOKEN env var."""
    from compost.gitea.client import GiteaClient
    config = load_repo_config(repo)
    gitea_cfg = config.get("gitea")
    if not gitea_cfg:
        console.print("[red].compost.yml missing 'gitea:' config. Run: compost gitea setup[/red]")
        sys.exit(1)
    token = os.environ.get("GITEA_TOKEN", "")
    if not token:
        console.print("[red]GITEA_TOKEN env var is not set.[/red]")
        sys.exit(1)
    return GiteaClient(
        url=gitea_cfg["url"],
        owner=gitea_cfg["owner"],
        repo=gitea_cfg["repo"],
        token=token,
    )


# ─────────────────────────────────────────────────────────────────────────────

def _run_doctor_checks(repo: Path) -> list[tuple[str, bool, str]]:
    config = load_repo_config(repo)
    index_name = config["qmd_index"]
    checks: list[tuple[str, bool, str]] = []

    checks.append((".compost.yml present", True, ""))

    result = subprocess.run(["qmd", "--version"], capture_output=True)
    checks.append(("qmd binary", result.returncode == 0,
                   "" if result.returncode == 0 else "qmd not found on PATH"))

    expected = {"wiki", "raw", "decisions", "incidents"}
    registered = _qmd_collections(index_name)
    missing = expected - registered
    checks.append(("qmd collections", not missing,
                   f"missing: {', '.join(sorted(missing))}" if missing else ""))

    codeowners = repo / ".github" / "CODEOWNERS"
    checks.append(("CODEOWNERS", codeowners.exists() and codeowners.stat().st_size > 0,
                   "missing or empty .github/CODEOWNERS"))

    wiki_dir = repo / "wiki"
    errors: list[str] = []
    for md in wiki_dir.rglob("*.md"):
        if md.name in ("glossary.md", "index.md", "log.md"):
            continue
        fm, body = parse_frontmatter(md)
        errs = validate_frontmatter(fm, md.relative_to(repo))
        errors.extend(errs)
    checks.append(("wiki frontmatter", not errors,
                   "; ".join(errors[:3]) + ("..." if len(errors) > 3 else "")))

    # ── Gitea checks ──
    gitea_cfg = config.get("gitea", {})
    gitea_ok = all(k in gitea_cfg for k in ("url", "owner", "repo"))
    checks.append(("gitea config", gitea_ok,
                   "missing gitea.url/owner/repo in .compost.yml" if not gitea_ok else ""))

    if gitea_ok:
        token = os.environ.get("GITEA_TOKEN", "")
        if not token:
            checks.append(("gitea connectivity", False, "GITEA_TOKEN not set"))
        else:
            from compost.gitea.client import GiteaClient, GiteaError
            try:
                client = GiteaClient(
                    url=gitea_cfg["url"], owner=gitea_cfg["owner"],
                    repo=gitea_cfg["repo"], token=token,
                )
                user = client.get_authenticated_user()
                checks.append(("gitea connectivity", True, f"authenticated as {user}"))
            except GiteaError as e:
                checks.append(("gitea connectivity", False, str(e)))

        from compost.ingest.git import get_remote_url
        remote_url = get_remote_url(repo, "origin")
        expected_url_prefix = gitea_cfg["url"].rstrip("/")
        remote_ok = remote_url is not None and expected_url_prefix in remote_url
        checks.append(("gitea remote", remote_ok,
                       f"origin URL '{remote_url}' doesn't match {expected_url_prefix}"
                       if not remote_ok else ""))

    return checks


def _qmd_collections(index_name: str) -> set[str]:
    result = subprocess.run(
        ["qmd", "--index", index_name, "collection", "list"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return set()
    names: set[str] = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if line and not line.startswith("Collections") and "(" in line:
            name = line.split("(")[0].strip()
            if name:
                names.add(name)
    return names


def _render_checks(checks: list[tuple[str, bool, str]]) -> None:
    table = Table(show_header=False, box=None, padding=(0, 1))
    for name, ok, msg in checks:
        icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
        detail = f"  [dim]{msg}[/dim]" if (msg and not ok) else ""
        table.add_row(icon, name + detail)
    console.print(table)
```

**Run full test suite:** all pass.

---

### Step 7 — README.md update

**Replace** the entire "## Ingestion commands" section (and the steel thread within it) with:

```markdown
## Gitea setup (one-time)

Gitea must be running before using `compost raw add`. Configure the integration from your wiki repo:

```bash
export GITEA_TOKEN=your-token-here
cd ~/my-team-wiki
compost gitea setup --owner myuser
# → creates Gitea repo, sets origin remote, pushes main, writes gitea: to .compost.yml
```

## Ingestion commands

### `compost raw add`

Capture a raw source file from stdin, commit it on a new `raw/*` branch, push to Gitea, and open a PR.

```bash
echo "Discussed retry ownership. Alice owns it." | \
  COMPOST_REPO=. compost raw add \
    --source note \
    --title "retry ownership discussion"
```

Output:
```
✓ raw/notes/2026-04-28-retry-ownership-discussion.md
branch: raw/2026-04-28-retry-ownership-discussion
PR #3: http://localhost:3000/myuser/my-team-wiki/pulls/3
```

Options:

| Flag | Description |
|---|---|
| `--source` | `slack\|incident\|decision\|note\|meeting\|support` (required) |
| `--title` | Human-readable title, used for slug and frontmatter (required) |
| `--captured-by` | Author; defaults to `git config user.name` |
| `--origin` | Source reference (e.g. PagerDuty ID, Slack URL) |
| `--channel` | Slack channel name; required when `--source=slack` |

Body is read from stdin.

### `compost pr open`

Print the Gitea PR URL for the current `raw/*` branch.

```bash
COMPOST_REPO=. compost pr open
# → PR #3: http://localhost:3000/myuser/my-team-wiki/pulls/3  (open)
```

### `compost pr merge`

Merge the current `raw/*` branch via Gitea PR, then sync the local repo. Blocked if any `wiki/` files were modified on the branch.

```bash
COMPOST_REPO=. compost pr merge
# → merged (PR #3)
```

### Steel thread

```bash
# One-time: start Gitea, create a token, then:
export GITEA_TOKEN=your-token
cd ~/local-test/my-wiki
compost gitea setup --owner myuser

# Ingest a raw file:
echo "Discussed retry ownership. Alice owns it." | \
  COMPOST_REPO=. compost raw add --source note --title "retry ownership discussion"
# → PR opened at http://localhost:3000/myuser/my-wiki/pulls/1

# Review the PR in browser, then merge:
COMPOST_REPO=. compost pr merge
# → merged (PR #1)
```
```

**Specifically:** remove the old `### compost pr open` section that mentions `_plans/pr-log/`, the old `### compost pr merge` section, and the old steel thread. Replace with the above.

---

### Step 8 — Cleanup

Remove the `bare_repo` fixture from `test_git.py` (it is now in `conftest.py`). Verify the import still works by running the full test suite.

---

### Step 9 — Final test run

```bash
cd tools/compost && python -m pytest tests/ -q
```

All tests must pass.

---

## § Design notes

**`_pr-log/` directory is deleted from wiki repos** — any existing `_pr-log/` dirs in test repos should be manually removed or ignored. They will not be created by new code.

**`PRLog` dataclass is gone** — any code importing `PRLog` will break. The only importer was `cli.py`, now updated.

**Branch names in `find_pr`** — the Gitea API filters open PRs and we match `pr["head"]["ref"]` client-side. If a repo has more than 50 open PRs, `find_pr` may miss branches. `limit=50` is sufficient for the steel thread; pagination can be added in a future phase.

**`create_repo` uses `POST /user/repos`** — works when `owner` is the authenticated user's login. For org-owned repos, the owner check at `gitea setup` would need to route to `POST /orgs/{owner}/repos`. This is not needed for the local steel thread where owner == user.

**`raw_add` push/PR failures are non-fatal** — the local commit is always preserved. If push fails, the user can retry with `git push origin {branch}` and then `compost pr open` would find nothing (no PR yet). They can re-run the push and manually open via the Gitea UI, or we can add a `compost pr create` command in a future phase. For the steel thread this is acceptable.
