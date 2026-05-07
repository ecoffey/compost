"""Slack shim: FastAPI webhook receiver on localhost:8421.

Accepts fake reaction_added payloads and writes raw/slack/... files,
then enqueues synthesis jobs.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

from compost.ingest.raw import write_raw
from compost.worker.queue import enqueue


class SlackReactionPayload(BaseModel):
    channel: str
    thread_ts: str
    emoji: str
    text: str
    user: str


def make_app(compost_repo: Path, wiki_emoji: str = "wiki") -> FastAPI:
    """Factory: returns a FastAPI app bound to a specific compost repo and emoji config.

    Using a factory (not module-level app) keeps the repo path injectable for tests.
    """
    app = FastAPI()

    @app.post("/events")
    def events(payload: SlackReactionPayload):
        if payload.emoji != wiki_emoji:
            return {"status": "ignored"}

        raw_file = write_raw(
            compost_repo,
            source="slack",
            title=f"slack-{payload.channel}-{payload.thread_ts}",
            body=payload.text,
            captured_by=payload.user,
            origin=f"slack://{payload.channel}/{payload.thread_ts}",
            channel=payload.channel,
        )
        rel = str(raw_file.rel_path)
        # Branch is synthetic — the shim does not commit to git.
        # The synthesis worker handles git operations.
        job = enqueue(
            compost_repo, rel,
            branch=f"shim/slack/{payload.thread_ts}",
            source="slack_shim",
        )
        return {"status": "enqueued", "job_id": job.id}

    return app


# Module-level app for uvicorn. Requires COMPOST_REPO env var.
import os as _os
if _os.environ.get("COMPOST_REPO"):
    app = make_app(
        compost_repo=Path(_os.environ["COMPOST_REPO"]),
        wiki_emoji=_os.environ.get("COMPOST_WIKI_EMOJI", "wiki"),
    )
