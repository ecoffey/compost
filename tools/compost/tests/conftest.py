from pathlib import Path
import pytest
import yaml


@pytest.fixture
def wiki_repo(tmp_path: Path) -> Path:
    """A minimal, valid wiki repo in a temp directory."""
    repo = tmp_path / "my-team"
    _make_dirs(repo)
    _write_config(repo, "my-team")
    _write_codeowners(repo)
    _write_seed_pages(repo)
    return repo


def _make_dirs(repo: Path) -> None:
    for d in [
        "raw/decisions", "raw/incidents",
        "wiki/services", "wiki/decisions", "wiki/concepts", "wiki/runbooks",
        ".github",
    ]:
        (repo / d).mkdir(parents=True)


def _write_config(repo: Path, name: str) -> None:
    (repo / ".compost.yml").write_text(yaml.dump({"name": name, "qmd_index": name}))


def _write_codeowners(repo: Path) -> None:
    (repo / ".github" / "CODEOWNERS").write_text(
        "wiki/ @my-team-wiki-maintainer\n"
    )


def _write_seed_pages(repo: Path) -> None:
    (repo / "raw" / "decisions" / "0001-stripe.md").write_text(
        "---\nsource: decision\ncaptured_at: 2026-01-10T10:00:00Z\n"
        "captured_by: alice\norigin: internal\n---\n"
        "We chose Stripe for payment processing.\n"
    )
    (repo / "wiki" / "services" / "payments.md").write_text(
        "---\n"
        "type: service\nname: payments\nowners: [alice]\n"
        "status: active\nupdated: 2026-01-15\nconfidence: high\n"
        "sources:\n  - raw/decisions/0001-stripe.md\n"
        "related: []\nsupersedes: []\nsuperseded_by: null\n"
        "---\n\n# Payments\n\n## Summary\nHandles all payment processing.\n"
        "## Key decisions\n- [Stripe as payment processor](../decisions/0001-stripe.md)\n"
        "## Architecture\nStripe integration via webhooks.\n"
        "## Gotchas\nNone known.\n"
        "## Runbooks\nNone yet.\n"
        "## Open questions\nNone.\n"
    )
    (repo / "wiki" / "concepts" / "idempotency.md").write_text(
        "---\n"
        "type: concept\nname: idempotency\nowners: [alice]\n"
        "status: active\nupdated: 2026-01-15\nconfidence: high\n"
        "sources:\n  - raw/decisions/0001-stripe.md\n"
        "related: []\nsupersedes: []\nsuperseded_by: null\n"
        "---\n\n# Idempotency\n\n"
        "Stripe requires idempotency keys on payment intent creation.\n"
    )
