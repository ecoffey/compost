from pathlib import Path
from compost.model.frontmatter import parse_frontmatter, validate_frontmatter


def test_parse_valid_frontmatter(tmp_path: Path) -> None:
    f = tmp_path / "page.md"
    f.write_text(
        "---\ntype: service\nname: payments\nowners: [alice]\n"
        "status: active\nupdated: 2026-01-01\nconfidence: high\n"
        "sources: [raw/decisions/0001.md]\nrelated: []\n"
        "supersedes: []\nsuperseded_by: null\n---\n\n# Body\n"
    )
    fm, body = parse_frontmatter(f)
    assert fm["type"] == "service"
    assert fm["name"] == "payments"
    assert "alice" in fm["owners"]
    assert "# Body" in body


def test_validate_valid_frontmatter() -> None:
    fm = {
        "type": "service", "name": "payments", "owners": ["alice"],
        "status": "active", "updated": "2026-01-01", "confidence": "high",
        "sources": ["raw/decisions/0001.md"],
    }
    errors = validate_frontmatter(fm, Path("wiki/services/payments.md"))
    assert errors == []


def test_validate_missing_required_field() -> None:
    fm = {"type": "service", "name": "payments"}  # missing many fields
    errors = validate_frontmatter(fm, Path("wiki/services/payments.md"))
    missing = [e for e in errors if "missing required field" in e]
    assert len(missing) >= 4


def test_validate_empty_sources() -> None:
    fm = {
        "type": "service", "name": "payments", "owners": ["alice"],
        "status": "active", "updated": "2026-01-01", "confidence": "high",
        "sources": [],
    }
    errors = validate_frontmatter(fm, Path("wiki/services/payments.md"))
    assert any("sources" in e and "non-empty" in e for e in errors)


def test_validate_empty_owners() -> None:
    fm = {
        "type": "service", "name": "payments", "owners": [],
        "status": "active", "updated": "2026-01-01", "confidence": "high",
        "sources": ["raw/decisions/0001.md"],
    }
    errors = validate_frontmatter(fm, Path("wiki/services/payments.md"))
    assert any("owners" in e for e in errors)


def test_validate_unknown_type() -> None:
    fm = {
        "type": "widget", "name": "payments", "owners": ["alice"],
        "status": "active", "updated": "2026-01-01", "confidence": "high",
        "sources": ["raw/decisions/0001.md"],
    }
    errors = validate_frontmatter(fm, Path("wiki/services/payments.md"))
    assert any("type" in e and "widget" in e for e in errors)


def test_parse_returns_empty_dict_on_bad_file(tmp_path: Path) -> None:
    f = tmp_path / "bad.md"
    f.write_text("no frontmatter here")
    fm, body = parse_frontmatter(f)
    assert fm == {}
    assert "no frontmatter here" in body
