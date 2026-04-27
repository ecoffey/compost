import json
from pathlib import Path

from team_context.session import format_session, find_session


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    with path.open("w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _make_session(tmp_path: Path) -> Path:
    p = tmp_path / "abc123.jsonl"
    _write_jsonl(p, [
        {
            "type": "user",
            "message": {"content": [{"type": "text", "text": "what is payments?"}]},
        },
        {
            "type": "assistant",
            "message": {"content": [
                {"type": "text", "text": "Let me look that up."},
                {"type": "tool_use", "id": "id1", "name": "mcp__team-context__load_service_context",
                 "input": {"service": "payments"}},
            ]},
        },
        {
            "type": "user",
            "message": {"content": [
                {"type": "tool_result", "tool_use_id": "id1",
                 "content": [{"type": "text", "text": "# Payments\nHandles payment processing."}]},
            ]},
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Payments handles processing."}]},
        },
    ])
    return p


def test_format_session_full(tmp_path):
    path = _make_session(tmp_path)
    output = format_session(path)
    assert "USER  what is payments?" in output
    assert "ASST  Let me look that up." in output
    assert "mcp__team-context__load_service_context" in output
    assert "RESU" in output
    assert "ASST  Payments handles processing." in output


def test_format_session_mcp_only(tmp_path):
    path = _make_session(tmp_path)
    output = format_session(path, mcp_only=True)
    assert "USER" not in output
    assert "ASST" not in output
    assert "mcp__team-context__load_service_context" in output
    assert "RESU" in output


def test_format_session_tools_only(tmp_path):
    path = _make_session(tmp_path)
    output = format_session(path, tools_only=True)
    assert "USER" not in output
    assert "ASST" not in output
    assert "mcp__team-context__load_service_context" in output


def test_format_session_snippet_truncation(tmp_path):
    path = tmp_path / "trunc.jsonl"
    long_text = "x" * 1000
    _write_jsonl(path, [
        {
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "id": "id1", "name": "mcp__tc__query_wiki",
                 "input": {"query": "test"}},
            ]},
        },
        {
            "type": "user",
            "message": {"content": [
                {"type": "tool_result", "tool_use_id": "id1",
                 "content": [{"type": "text", "text": long_text}]},
            ]},
        },
    ])
    output = format_session(path, snippet_len=100)
    assert "900 more chars" in output


def test_find_session(tmp_path, monkeypatch):
    monkeypatch.setattr("team_context.session.Path.home", lambda: tmp_path)
    proj = tmp_path / ".claude" / "projects" / "-test"
    proj.mkdir(parents=True)
    (proj / "abcdef12-0000-0000-0000-000000000000.jsonl").write_text("")

    result = find_session("abcdef12")
    assert result is not None
    assert result.stem == "abcdef12-0000-0000-0000-000000000000"


def test_find_session_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr("team_context.session.Path.home", lambda: tmp_path)
    (tmp_path / ".claude" / "projects").mkdir(parents=True)
    assert find_session("doesnotexist") is None
