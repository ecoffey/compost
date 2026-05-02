from pathlib import Path
from unittest.mock import patch

from compost.cli import _run_doctor_checks


def test_doctor_passes_on_clean_repo(wiki_repo: Path) -> None:
    with (
        patch("compost.cli.subprocess.run") as mock_run,
        patch("compost.cli._qmd_collections") as mock_cols,
    ):
        mock_run.return_value.returncode = 0
        mock_cols.return_value = {"wiki", "raw", "decisions", "incidents"}

        checks = _run_doctor_checks(wiki_repo)

    # Gitea checks only apply after `compost gitea setup` has been run; a clean
    # repo without gitea config is expected to have no gitea checks at all.
    failed = [(name, msg) for name, ok, msg in checks if not ok and "gitea" not in name]
    assert failed == [], f"Expected non-gitea checks to pass, got failures: {failed}"

    # Confirm gitea config check is present and shows as failed (not configured yet)
    gitea_check = next((ok for name, ok, _ in checks if name == "gitea config"), None)
    assert gitea_check is False


def test_doctor_fails_missing_codeowners(wiki_repo: Path) -> None:
    (wiki_repo / ".github" / "CODEOWNERS").unlink()

    with (
        patch("compost.cli.subprocess.run") as mock_run,
        patch("compost.cli._qmd_collections") as mock_cols,
    ):
        mock_run.return_value.returncode = 0
        mock_cols.return_value = {"wiki", "raw", "decisions", "incidents"}

        checks = _run_doctor_checks(wiki_repo)

    codeowners_check = next(
        (ok for name, ok, _ in checks if "CODEOWNERS" in name), None
    )
    assert codeowners_check is False


def test_doctor_fails_missing_qmd_collection(wiki_repo: Path) -> None:
    with (
        patch("compost.cli.subprocess.run") as mock_run,
        patch("compost.cli._qmd_collections") as mock_cols,
    ):
        mock_run.return_value.returncode = 0
        mock_cols.return_value = {"wiki"}  # missing raw, decisions, incidents

        checks = _run_doctor_checks(wiki_repo)

    coll_check = next(
        (ok for name, ok, _ in checks if "collection" in name), None
    )
    assert coll_check is False


def test_doctor_fails_wiki_page_missing_sources(wiki_repo: Path) -> None:
    bad_page = wiki_repo / "wiki" / "services" / "broken.md"
    bad_page.write_text(
        "---\ntype: service\nname: broken\nowners: [alice]\n"
        "status: active\nupdated: 2026-01-01\nconfidence: high\n"
        "sources: []\n---\n\n# Broken\n"
    )
    with (
        patch("compost.cli.subprocess.run") as mock_run,
        patch("compost.cli._qmd_collections") as mock_cols,
    ):
        mock_run.return_value.returncode = 0
        mock_cols.return_value = {"wiki", "raw", "decisions", "incidents"}

        checks = _run_doctor_checks(wiki_repo)

    fm_check = next(
        (ok for name, ok, _ in checks if "frontmatter" in name), None
    )
    assert fm_check is False
