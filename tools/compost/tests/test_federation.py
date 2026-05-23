from __future__ import annotations

import subprocess
import textwrap
import uuid
from pathlib import Path

import pytest

from compost.mcp.federation import (
    FederatedRepo,
    _parse_services_table,
    fan_out_query,
    find_owner_in_services_md,
    load_federation_config,
)


# ---------------------------------------------------------------------------
# load_federation_config
# ---------------------------------------------------------------------------

def test_load_federation_config_absent_returns_empty(tmp_path: Path) -> None:
    result = load_federation_config(tmp_path / "nonexistent.yaml")
    assert result == []


def test_load_federation_config_parses_yaml(tmp_path: Path) -> None:
    cfg = tmp_path / "federation.yaml"
    cfg.write_text(textwrap.dedent("""\
        repos:
          - name: my-team
            path: /tmp/my-team
            qmd_index: my-team-idx
            role: team
          - name: eng
            path: /tmp/eng
            qmd_index: eng-idx
            role: engineering
    """))
    repos = load_federation_config(cfg)
    assert len(repos) == 2
    assert repos[0].name == "my-team"
    assert repos[0].role == "team"
    assert repos[1].role == "engineering"
    assert repos[1].qmd_index == "eng-idx"


def test_load_federation_config_empty_file_returns_empty(tmp_path: Path) -> None:
    cfg = tmp_path / "empty.yaml"
    cfg.write_text("")
    assert load_federation_config(cfg) == []


# ---------------------------------------------------------------------------
# _parse_services_table
# ---------------------------------------------------------------------------

def test_parse_services_table_basic() -> None:
    text = textwrap.dedent("""\
        # Services

        | service | owner_team | repo | status |
        |---------|------------|------|--------|
        | payments | team-alpha | team-context-test | active |
        | auth | team-beta | team-context-test-2 | active |
    """)
    rows = _parse_services_table(text)
    assert len(rows) == 2
    assert rows[0]["service"] == "payments"
    assert rows[0]["owner_team"] == "team-alpha"
    assert rows[1]["service"] == "auth"


def test_parse_services_table_empty_returns_empty() -> None:
    assert _parse_services_table("# No table here") == []


# ---------------------------------------------------------------------------
# find_owner_in_services_md
# ---------------------------------------------------------------------------

def test_find_owner_returns_row(tmp_path: Path) -> None:
    eng_path = tmp_path / "eng"
    (eng_path / "wiki").mkdir(parents=True)
    (eng_path / "wiki" / "services.md").write_text(textwrap.dedent("""\
        | service | owner_team | repo | status |
        |---------|------------|------|--------|
        | payments | team-alpha | team-context-test | active |
    """))
    repos = [FederatedRepo(
        name="eng", path=eng_path, qmd_index="eng-idx", role="engineering"
    )]
    result = find_owner_in_services_md("payments", repos)
    assert result is not None
    assert result["owner_team"] == "team-alpha"


def test_find_owner_case_insensitive(tmp_path: Path) -> None:
    eng_path = tmp_path / "eng"
    (eng_path / "wiki").mkdir(parents=True)
    (eng_path / "wiki" / "services.md").write_text(textwrap.dedent("""\
        | service | owner_team | repo | status |
        |---------|------------|------|--------|
        | Payments | team-alpha | tc-test | active |
    """))
    repos = [FederatedRepo(
        name="eng", path=eng_path, qmd_index="eng-idx", role="engineering"
    )]
    assert find_owner_in_services_md("payments", repos) is not None
    assert find_owner_in_services_md("PAYMENTS", repos) is not None


def test_find_owner_unknown_service_returns_none(tmp_path: Path) -> None:
    eng_path = tmp_path / "eng"
    (eng_path / "wiki").mkdir(parents=True)
    (eng_path / "wiki" / "services.md").write_text(textwrap.dedent("""\
        | service | owner_team | repo | status |
        |---------|------------|------|--------|
        | payments | team-alpha | tc-test | active |
    """))
    repos = [FederatedRepo(
        name="eng", path=eng_path, qmd_index="eng-idx", role="engineering"
    )]
    assert find_owner_in_services_md("unknown-service", repos) is None


def test_find_owner_no_eng_repo_returns_none(tmp_path: Path) -> None:
    repos = [FederatedRepo(
        name="team-a", path=Path("/tmp"), qmd_index="team-a", role="team"
    )]
    assert find_owner_in_services_md("payments", repos) is None


def test_find_owner_missing_services_md_returns_none(tmp_path: Path) -> None:
    eng_path = tmp_path / "eng"
    (eng_path / "wiki").mkdir(parents=True)
    repos = [FederatedRepo(
        name="eng", path=eng_path, qmd_index="eng-idx", role="engineering"
    )]
    assert find_owner_in_services_md("payments", repos) is None


# ---------------------------------------------------------------------------
# fan_out_query — qmd per-index isolation (integration, requires qmd binary)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fan_out_query_index_isolation(tmp_path: Path) -> None:
    """Two qmd indexes with the same collection name must not share content."""
    uid = uuid.uuid4().hex[:8]
    idx_a = f"test-iso-a-{uid}"
    idx_b = f"test-iso-b-{uid}"

    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    for d in (repo_a / "wiki", repo_b / "wiki"):
        d.mkdir(parents=True)

    (repo_a / "wiki" / "alpha.md").write_text(
        "---\ntype: concept\nname: alpha-concept\nowners: [alice]\n"
        "status: active\nupdated: 2026-01-01\nconfidence: high\n"
        "sources: [raw/x.md]\n---\n# Alpha Concept\nUnique alpha xyzzy content.\n"
    )
    (repo_b / "wiki" / "beta.md").write_text(
        "---\ntype: concept\nname: beta-concept\nowners: [bob]\n"
        "status: active\nupdated: 2026-01-01\nconfidence: high\n"
        "sources: [raw/y.md]\n---\n# Beta Concept\nUnique beta qwerty content.\n"
    )

    subprocess.run(
        ["qmd", "--index", idx_a, "collection", "add", "wiki", "wiki", "**/*.md"],
        cwd=str(repo_a), capture_output=True, check=True,
    )
    subprocess.run(
        ["qmd", "--index", idx_b, "collection", "add", "wiki", "wiki", "**/*.md"],
        cwd=str(repo_b), capture_output=True, check=True,
    )
    subprocess.run(["qmd", "--index", idx_a, "update"], capture_output=True, check=True)
    subprocess.run(["qmd", "--index", idx_b, "update"], capture_output=True, check=True)

    fed_a = FederatedRepo(name="repo-a", path=repo_a, qmd_index=idx_a, role="team")
    fed_b = FederatedRepo(name="repo-b", path=repo_b, qmd_index=idx_b, role="team")

    hits_a = fan_out_query("alpha xyzzy", [fed_a], limit_per_repo=5)
    hits_b = fan_out_query("beta qwerty", [fed_b], limit_per_repo=5)

    a_files = [h["file"] for h in hits_a]
    b_files = [h["file"] for h in hits_b]
    assert not any("beta" in f for f in a_files), f"Index A leaked B content: {a_files}"
    assert not any("alpha" in f for f in b_files), f"Index B leaked A content: {b_files}"

    both = fan_out_query("concept", [fed_a, fed_b], limit_per_repo=5)
    repo_names = {h["_repo_name"] for h in both}
    assert "repo-a" in repo_names
    assert "repo-b" in repo_names


@pytest.mark.integration
def test_fan_out_query_deduplicates_by_title(tmp_path: Path) -> None:
    """When both repos have a page with the same title, only one hit appears."""
    uid = uuid.uuid4().hex[:8]
    idx_a = f"test-dedup-a-{uid}"
    idx_b = f"test-dedup-b-{uid}"

    for repo_dir, idx in [(tmp_path / "da", idx_a), (tmp_path / "db", idx_b)]:
        (repo_dir / "wiki").mkdir(parents=True)
        (repo_dir / "wiki" / "shared.md").write_text(
            "---\ntype: concept\nname: shared-concept\nowners: [alice]\n"
            "status: active\nupdated: 2026-01-01\nconfidence: high\n"
            "sources: [raw/x.md]\n---\n# Shared Concept\nDuplicate content.\n"
        )
        subprocess.run(
            ["qmd", "--index", idx, "collection", "add", "wiki", "wiki", "**/*.md"],
            cwd=str(repo_dir), capture_output=True, check=True,
        )
        subprocess.run(["qmd", "--index", idx, "update"], capture_output=True, check=True)

    fed_a = FederatedRepo(name="da", path=tmp_path / "da", qmd_index=idx_a, role="team")
    fed_b = FederatedRepo(name="db", path=tmp_path / "db", qmd_index=idx_b, role="team")

    hits = fan_out_query("shared concept", [fed_a, fed_b], limit_per_repo=3)
    titles = [h.get("title", h["file"]) for h in hits]
    assert len(titles) == len(set(titles)), f"Duplicate titles found: {titles}"


@pytest.mark.integration
def test_fan_out_query_role_filter(tmp_path: Path) -> None:
    """roles filter restricts fan-out to matching repo roles."""
    uid = uuid.uuid4().hex[:8]
    idx_team = f"test-role-team-{uid}"
    idx_eng = f"test-role-eng-{uid}"

    for repo_dir, idx in [(tmp_path / "team", idx_team), (tmp_path / "eng", idx_eng)]:
        (repo_dir / "wiki").mkdir(parents=True)
        (repo_dir / "wiki" / "page.md").write_text(
            "---\ntype: concept\nname: thing\nowners: [alice]\n"
            "status: active\nupdated: 2026-01-01\nconfidence: high\n"
            "sources: [raw/x.md]\n---\n# Thing\nContent here.\n"
        )
        subprocess.run(
            ["qmd", "--index", idx, "collection", "add", "wiki", "wiki", "**/*.md"],
            cwd=str(repo_dir), capture_output=True, check=True,
        )
        subprocess.run(["qmd", "--index", idx, "update"], capture_output=True, check=True)

    fed_team = FederatedRepo(name="team", path=tmp_path / "team", qmd_index=idx_team, role="team")
    fed_eng = FederatedRepo(name="eng", path=tmp_path / "eng", qmd_index=idx_eng, role="engineering")

    hits = fan_out_query("thing", [fed_team, fed_eng], roles=["team"], limit_per_repo=3)
    repo_names = {h["_repo_name"] for h in hits}
    assert "team" in repo_names
    assert "eng" not in repo_names
