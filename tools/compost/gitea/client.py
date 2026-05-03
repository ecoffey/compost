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

    def get_clone_url(self, name: str, ssh: bool = False) -> str:
        """Return the clone URL for the repo as reported by Gitea (HTTP or SSH)."""
        data = self._request("GET", f"/repos/{self._owner}/{name}")
        return data["ssh_url"] if ssh else data["clone_url"]

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
