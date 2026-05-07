"""GitHub webhook shim: FastAPI receiver on localhost:8422.

Accepts simulated PR events with the compost/synth label and enqueues synthesis jobs.
repo_full_name mirrors the real GitHub webhook payload shape (owner/repo-name).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

from compost.ingest.raw import write_raw
from compost.worker.queue import enqueue

_SYNTH_LABEL = "compost/synth"
_TRIGGER_ACTIONS = {"opened", "labeled"}


class GitHubPRPayload(BaseModel):
    action: str
    pr_number: int
    branch: str
    labels: list[str]
    repo_full_name: str   # e.g. "acme/payments" — identifies which source repo this PR came from


def make_app(compost_repo: Path) -> FastAPI:
    """Factory: returns a FastAPI app bound to a specific compost repo."""
    app = FastAPI()

    @app.post("/webhook")
    def webhook(payload: GitHubPRPayload):
        if payload.action not in _TRIGGER_ACTIONS:
            return {"status": "ignored", "reason": "action"}
        if _SYNTH_LABEL not in payload.labels:
            return {"status": "ignored", "reason": "label"}

        body = (
            f"GitHub PR #{payload.pr_number} on `{payload.branch}`\n"
            f"Repo: {payload.repo_full_name}\n"
            f"Action: {payload.action}\n"
            f"Labels: {', '.join(payload.labels)}\n"
        )
        raw_file = write_raw(
            compost_repo,
            source="commits",
            title=f"pr-{payload.repo_full_name.replace('/', '-')}-{payload.pr_number}",
            body=body,
            captured_by="gh_webhook_shim",
            origin=f"github://{payload.repo_full_name}/pull/{payload.pr_number}",
        )
        rel = str(raw_file.rel_path)
        job = enqueue(
            compost_repo, rel,
            branch=payload.branch,
            source="gh_webhook_shim",
        )
        return {"status": "enqueued", "job_id": job.id}

    return app


# Module-level app for uvicorn. Requires COMPOST_REPO env var.
import os as _os
if _os.environ.get("COMPOST_REPO"):
    app = make_app(compost_repo=Path(_os.environ["COMPOST_REPO"]))
