from datetime import datetime, timezone
from pathlib import Path

import pytest

from compost.ingest.raw import slugify, write_raw


def _ts(year=2026, month=4, day=27) -> datetime:
    return datetime(year, month, day, 10, 0, 0, tzinfo=timezone.utc)


# ── slugify ───────────────────────────────────────────────────────────────────

def test_slugify_basic():
    assert slugify("Payments standup") == "payments-standup"


def test_slugify_special_chars():
    assert slugify("Hello, World!") == "hello-world"


def test_slugify_multiple_hyphens():
    assert slugify("foo  bar--baz") == "foo-bar-baz"


def test_slugify_leading_trailing():
    assert slugify("  hello  ") == "hello"


# ── write_raw paths ───────────────────────────────────────────────────────────

def test_write_raw_note_path(tmp_path):
    f = write_raw(tmp_path, "note", "retry ownership", "body text", ts=_ts())
    assert f.rel_path == Path("raw/notes/2026-04-27-retry-ownership.md")
    assert f.path == tmp_path / f.rel_path
    assert f.source == "note"


def test_write_raw_slack_path(tmp_path):
    f = write_raw(tmp_path, "slack", "Payments standup", "body", channel="eng", ts=_ts())
    assert f.rel_path == Path("raw/slack/2026/04/2026-04-27-eng-payments-standup.md")


def test_write_raw_incident_path(tmp_path):
    f = write_raw(tmp_path, "incident", "payment timeout", "body", ts=_ts())
    assert f.rel_path == Path("raw/incidents/2026/INC-2026-04-27-payment-timeout.md")


def test_write_raw_decision_path(tmp_path):
    f = write_raw(tmp_path, "decision", "use stripe", "body", ts=_ts())
    assert f.rel_path == Path("raw/decisions/2026-04-27-use-stripe.md")


def test_write_raw_meeting_path(tmp_path):
    f = write_raw(tmp_path, "meeting", "weekly sync", "body", ts=_ts())
    assert f.rel_path == Path("raw/meetings/2026-04-27-weekly-sync.md")


def test_write_raw_support_path(tmp_path):
    f = write_raw(tmp_path, "support", "billing issue", "body", ts=_ts())
    assert f.rel_path == Path("raw/support/2026-04-27-billing-issue.md")


# ── write_raw file content ────────────────────────────────────────────────────

def test_write_raw_creates_file(tmp_path):
    f = write_raw(tmp_path, "note", "test note", "This is the body.", ts=_ts())
    assert f.path.exists()


def test_write_raw_frontmatter_fields(tmp_path):
    f = write_raw(
        tmp_path, "note", "test note", "body",
        captured_by="alice", origin="slack://foo", ts=_ts(),
    )
    text = f.path.read_text()
    assert "source: note" in text
    assert "2026-04-27T10:00:00Z" in text
    assert "captured_by: alice" in text
    assert "intent: test note" in text
    assert "origin: slack://foo" in text


def test_write_raw_body_follows_frontmatter(tmp_path):
    f = write_raw(tmp_path, "note", "test note", "the body text", ts=_ts())
    text = f.path.read_text()
    parts = text.split("---\n")
    assert len(parts) >= 3
    assert "the body text" in parts[-1]


def test_write_raw_creates_parent_dirs(tmp_path):
    f = write_raw(tmp_path, "slack", "sync", "body", channel="eng", ts=_ts())
    assert f.path.parent.exists()


def test_write_raw_slack_without_channel_raises(tmp_path):
    with pytest.raises(ValueError, match="--channel"):
        write_raw(tmp_path, "slack", "sync", "body", ts=_ts())
