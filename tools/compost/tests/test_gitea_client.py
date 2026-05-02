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
