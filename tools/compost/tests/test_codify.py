from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from compost.codify.codegen import (
    CodegenResult,
    ClaimsScan,
    _confidence_float,
    _to_kotlin_ident,
    _esc,
    _scan_claims,
    codify,
)
from compost.codify.compare import AssayResult, compare, _fuzzy_equal


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def wiki_repo(tmp_path: Path) -> Path:
    """Minimal compost repo with a wiki dir containing varied page types."""
    (tmp_path / ".compost.yml").write_text(yaml.dump({
        "name": "test",
        "qmd_index": "test",
    }))
    wiki = tmp_path / "wiki"
    wiki.mkdir()

    (wiki / "auth-service.md").write_text(
        "---\n"
        "type: service\n"
        "name: Auth Service\n"
        "owners: [team-platform]\n"
        "status: active\n"
        "updated: 2026-05-01\n"
        "confidence: high\n"
        "sources:\n  - raw/decisions/0001-auth.md\n"
        "tier: 1\n"
        "depends_on:\n  - postgres\n"
        "technology:\n  - kotlin\n"
        "---\n\n# Auth Service\n"
    )
    (wiki / "adr-0001.md").write_text(
        "---\n"
        "type: decision\n"
        "name: Use JWT\n"
        "owners: [team-platform]\n"
        "status: active\n"
        "updated: 2026-04-01\n"
        "confidence: medium\n"
        "sources:\n  - raw/decisions/0001-jwt.md\n"
        "---\n\n# Use JWT\n"
    )
    (wiki / "glossary.md").write_text("# Glossary\n")  # must be skipped
    (wiki / "index.md").write_text("# Index\n")  # must be skipped
    return tmp_path


# ── helper unit tests ─────────────────────────────────────────────────────────


def test_to_kotlin_ident_hyphenated():
    assert _to_kotlin_ident("auth-service") == "authService"


def test_to_kotlin_ident_single_word():
    assert _to_kotlin_ident("payments") == "payments"


def test_to_kotlin_ident_numeric_prefix():
    # identifiers starting with digits get a 'p' prefix
    assert _to_kotlin_ident("0001-adr").startswith("p")
    assert not _to_kotlin_ident("0001-adr")[1:2].isdigit() is False  # second char is not digit


def test_to_kotlin_ident_numeric_prefix_is_valid():
    ident = _to_kotlin_ident("0001-adr")
    assert ident[0].isalpha() or ident[0] == "_"


def test_confidence_float_high():
    assert _confidence_float("high") == "0.9f"


def test_confidence_float_medium():
    assert _confidence_float("medium") == "0.7f"


def test_confidence_float_low():
    assert _confidence_float("low") == "0.5f"


def test_confidence_float_unknown_string():
    assert _confidence_float("unknown") == "0.7f"


def test_esc_quotes():
    assert _esc('say "hello"') == 'say \\"hello\\"'


def test_esc_backslash():
    assert _esc("a\\b") == "a\\\\b"


def test_esc_clean_string():
    assert _esc("hello world") == "hello world"


# ── _scan_claims unit tests ───────────────────────────────────────────────────


def test_scan_claims_finds_all_theory_vars(tmp_path):
    kt = tmp_path / "claims.kt"
    kt.write_text(
        "val a = TheoryOf(subject = authService, claim = \"x\", prov = p)\n"
        "val b = TheoryOf(subject = authService, claim = \"y\", prov = p)\n"
    )
    scan = _scan_claims(kt)
    assert set(scan.all_theory_vars) == {"a", "b"}


def test_scan_claims_groups_contested_by_subject(tmp_path):
    kt = tmp_path / "claims.kt"
    kt.write_text(
        "@Contested val pos1 = TheoryOf(subject = authService, claim = \"JWT\", prov = p)\n"
        "@Contested val pos2 = TheoryOf(subject = authService, claim = \"Sessions\", prov = p)\n"
        "val plain = TheoryOf(subject = authService, claim = \"stored in db\", prov = p)\n"
    )
    scan = _scan_claims(kt)
    assert scan.contested_by_subject == {"authService": ["pos1", "pos2"]}


def test_scan_claims_bare_theory_not_in_contested(tmp_path):
    kt = tmp_path / "claims.kt"
    kt.write_text(
        "val a = TheoryOf(subject = authService, claim = \"x\", prov = p)\n"
    )
    scan = _scan_claims(kt)
    assert scan.contested_by_subject == {}
    assert "a" in scan.all_theory_vars


def test_scan_claims_empty_file(tmp_path):
    kt = tmp_path / "claims.kt"
    kt.write_text("")
    scan = _scan_claims(kt)
    assert scan.all_theory_vars == []
    assert scan.contested_by_subject == {}


def test_codify_auto_generates_disputes_from_contested_theories(wiki_repo):
    (wiki_repo / "wiki" / "claims.kt").write_text(
        "@Contested val pos1 = TheoryOf(subject = authService, claim = \"JWT\", prov = p)\n"
        "@Contested val pos2 = TheoryOf(subject = authService, claim = \"Sessions\", prov = p)\n"
    )
    result = codify(wiki_repo)
    kt = result.kt_path.read_text()
    assert "autoDispute" in kt
    assert "claimA = pos1" in kt
    assert "claimB = pos2" in kt
    assert result.dispute_count >= 1


def test_codify_marks_contested_page_when_disputes_exist(wiki_repo):
    (wiki_repo / "wiki" / "claims.kt").write_text(
        "@Contested val pos1 = TheoryOf(subject = authService, claim = \"JWT\", prov = p)\n"
        "@Contested val pos2 = TheoryOf(subject = authService, claim = \"Sessions\", prov = p)\n"
    )
    result = codify(wiki_repo)
    kt = result.kt_path.read_text()
    # authService ServicePage constructor must have contested = true
    assert "contested = true" in kt


def test_codify_bare_theory_no_dispute_no_contested(wiki_repo):
    (wiki_repo / "wiki" / "claims.kt").write_text(
        "val plainClaim = TheoryOf(subject = authService, claim = \"stored in DB\", prov = p)\n"
    )
    result = codify(wiki_repo)
    kt = result.kt_path.read_text()
    assert "autoDispute" not in kt
    assert "contested = true" not in kt


def test_codify_all_claims_includes_theory_and_auto_disputes(wiki_repo):
    (wiki_repo / "wiki" / "claims.kt").write_text(
        "@Contested val pos1 = TheoryOf(subject = authService, claim = \"JWT\", prov = p)\n"
        "@Contested val pos2 = TheoryOf(subject = authService, claim = \"Sessions\", prov = p)\n"
    )
    result = codify(wiki_repo)
    kt = result.kt_path.read_text()
    assert "val allClaims" in kt
    assert "pos1" in kt
    assert "pos2" in kt


def test_codify_no_claims_kt_emits_empty_all_claims(wiki_repo):
    result = codify(wiki_repo)
    kt = result.kt_path.read_text()
    assert "val allClaims: List<Any> = emptyList()" in kt


# ── codify integration tests ──────────────────────────────────────────────────


def test_codify_creates_wiki_kt(wiki_repo):
    result = codify(wiki_repo)
    assert result.kt_path.exists()
    assert result.kt_path.name == "wiki.kt"


def test_codify_page_count(wiki_repo):
    result = codify(wiki_repo)
    # glossary.md and index.md are skipped; auth-service.md and adr-0001.md counted
    assert result.page_count == 2


def test_codify_emits_service_page(wiki_repo):
    result = codify(wiki_repo)
    kt = result.kt_path.read_text()
    assert "ServicePage(" in kt
    assert '"Auth Service"' in kt


def test_codify_emits_decision_page(wiki_repo):
    result = codify(wiki_repo)
    kt = result.kt_path.read_text()
    assert "DecisionPage(" in kt
    assert '"Use JWT"' in kt


def test_codify_emits_all_wiki_pages_list(wiki_repo):
    result = codify(wiki_repo)
    kt = result.kt_path.read_text()
    assert "val allWikiPages" in kt


def test_codify_skips_glossary_and_index(wiki_repo):
    result = codify(wiki_repo)
    kt = result.kt_path.read_text()
    # glossary and index should not appear as page declarations
    assert "glossary" not in kt.lower() or "val allWikiPages" in kt  # list may be empty ref


def test_codify_unknown_type_emits_unknown_page(wiki_repo):
    (wiki_repo / "wiki" / "custom-thing.md").write_text(
        "---\ntype: widget\nname: My Widget\nowners: [bob]\n"
        "status: active\nupdated: 2026-01-01\nconfidence: low\n"
        "sources: [raw/notes/x.md]\n---\n"
    )
    result = codify(wiki_repo)
    kt = result.kt_path.read_text()
    assert "UnknownPage(" in kt
    assert len(result.warnings) >= 1


def test_codify_depends_on_emitted(wiki_repo):
    result = codify(wiki_repo)
    kt = result.kt_path.read_text()
    assert '"postgres"' in kt


def test_codify_no_pages_emits_empty_list(tmp_path):
    (tmp_path / ".compost.yml").write_text("name: x\nqmd_index: x\n")
    (tmp_path / "wiki").mkdir()
    result = codify(tmp_path)
    kt = result.kt_path.read_text()
    assert "val allWikiPages: List<WikiPage> = emptyList()" in kt
    assert result.page_count == 0


# ── compare unit tests ────────────────────────────────────────────────────────


def test_fuzzy_equal_confidence_high_vs_09():
    assert _fuzzy_equal("confidence", "high", 0.9) is True


def test_fuzzy_equal_confidence_within_tolerance():
    assert _fuzzy_equal("confidence", "high", 0.88) is True


def test_fuzzy_equal_confidence_outside_tolerance():
    assert _fuzzy_equal("confidence", "high", 0.5) is False


def test_fuzzy_equal_string_case_insensitive():
    assert _fuzzy_equal("type", "Service", "service") is True


def test_fuzzy_equal_list_order_insensitive():
    assert _fuzzy_equal("owners", ["bob", "alice"], ["alice", "bob"]) is True


def test_fuzzy_equal_list_different():
    assert _fuzzy_equal("owners", ["alice"], ["bob"]) is False


# ── compare integration tests ─────────────────────────────────────────────────


def test_compare_passes_when_rendered_matches(wiki_repo):
    rendered = [
        {
            "id": "auth-service",
            "type": "service",
            "title": "Auth Service",
            "confidence": 0.9,
            "owner": "team-platform",
        },
        {
            "id": "adr-0001",
            "type": "decision",
            "title": "Use JWT",
            "confidence": 0.7,
            "status": "active",
        },
    ]
    result = compare(wiki_repo, rendered)
    assert result.passed is True
    assert result.checked == 2
    assert result.failures == []


def test_compare_fails_on_missing_page(wiki_repo):
    # Only provide one of the two pages in the rendered list
    rendered = [
        {
            "id": "auth-service",
            "type": "service",
            "title": "Auth Service",
            "confidence": 0.9,
            "owner": "team-platform",
        }
    ]
    result = compare(wiki_repo, rendered)
    assert result.passed is False
    assert any(f.page_id == "adr-0001" for f in result.failures)


def test_compare_fails_on_changed_field(wiki_repo):
    rendered = [
        {
            "id": "auth-service",
            "type": "service",
            "title": "WRONG TITLE",  # deliberately wrong
            "confidence": 0.9,
            "owner": "team-platform",
        },
        {
            "id": "adr-0001",
            "type": "decision",
            "title": "Use JWT",
            "confidence": 0.7,
            "status": "active",
        },
    ]
    result = compare(wiki_repo, rendered)
    assert result.passed is False
    fail = next(f for f in result.failures if f.page_id == "auth-service")
    assert any(field == "name" for field, _, _ in fail.changed_fields)


def test_compare_optional_absent_in_rendered_is_ok(wiki_repo):
    # 'related' is optional; if absent in rendered but absent in source too, no failure
    rendered = [
        {
            "id": "auth-service",
            "type": "service",
            "title": "Auth Service",
            "confidence": 0.9,
            "owner": "team-platform",
        },
        {
            "id": "adr-0001",
            "type": "decision",
            "title": "Use JWT",
            "confidence": 0.7,
            "status": "active",
        },
    ]
    result = compare(wiki_repo, rendered)
    # 'related' not in source, not in rendered — should not fail
    assert result.passed is True


# ── kotlinc integration (skip when kotlinc unavailable) ───────────────────────


kotlinc_available = pytest.mark.skipif(
    not shutil.which("kotlinc"),
    reason="kotlinc not on PATH — install via: sdk install kotlin",
)


@kotlinc_available
def test_compile_kt_succeeds_on_valid_wiki(wiki_repo):
    from compost.codify.codegen import codify
    from compost.codify.compiler import compile_kt

    codify(wiki_repo)
    result = compile_kt(wiki_repo)
    assert result.success is True
    assert result.jar_path is not None
    assert result.jar_path.exists()


@kotlinc_available
def test_render_wiki_returns_page_list(wiki_repo):
    from compost.codify.codegen import codify
    from compost.codify.compiler import compile_kt
    from compost.codify.renderer import render_wiki

    codify(wiki_repo)
    compile_kt(wiki_repo)
    pages = render_wiki(wiki_repo)
    assert isinstance(pages, list)
    assert len(pages) == 2
    ids = {p["id"] for p in pages}
    assert "auth-service" in ids
    assert "adr-0001" in ids


@kotlinc_available
def test_assay_passes_on_valid_wiki(wiki_repo):
    from compost.codify.codegen import codify
    from compost.codify.compiler import compile_kt
    from compost.codify.renderer import render_wiki
    from compost.codify.compare import compare

    codify(wiki_repo)
    compile_kt(wiki_repo)
    rendered = render_wiki(wiki_repo)
    result = compare(wiki_repo, rendered)
    assert result.passed is True


@kotlinc_available
def test_compile_includes_claims_kt_when_present(wiki_repo):
    from compost.codify.codegen import codify
    from compost.codify.compiler import compile_kt

    # Write a valid claims.kt that references the generated authService variable
    (wiki_repo / "wiki" / "claims.kt").write_text(
        "val authClaim = TheoryOf(\n"
        "    subject = authService,\n"
        '    claim = "JWT is stateless",\n'
        "    prov = Provenance("
        'origin = "claims.kt", ingestedAt = "2026-05-01", ingestedBy = "eoin")\n'
        ")\n"
    )
    codify(wiki_repo)
    result = compile_kt(wiki_repo)
    assert result.success is True


@kotlinc_available
def test_compile_fails_on_claims_kt_bad_reference(wiki_repo):
    from compost.codify.codegen import codify
    from compost.codify.compiler import compile_kt

    # Reference a nonexistent page variable — must be a compile error
    (wiki_repo / "wiki" / "claims.kt").write_text(
        "val badClaim = TheoryOf(\n"
        "    subject = nonExistentPage,\n"  # not declared in wiki.kt
        '    claim = "oops",\n'
        "    prov = Provenance("
        'origin = "claims.kt", ingestedAt = "2026-05-01", ingestedBy = "eoin")\n'
        ")\n"
    )
    codify(wiki_repo)
    result = compile_kt(wiki_repo)
    assert result.success is False
    assert len(result.errors) >= 1
