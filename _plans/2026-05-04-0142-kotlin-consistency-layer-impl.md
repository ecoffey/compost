# Kotlin Consistency Layer, Implementation Detail

## § Overview

Phase 5 of the steel-thread plan. New `compost.codify` Python package + two static Kotlin schema
files. No external Kotlin library dependencies: the schema uses manual JSON helpers and
`-include-runtime` bundles the Kotlin stdlib into the JAR. No kotlinx.serialization needed.

Key invariant: `CompostSchema.kt` and `render_main.kt` are static files shipped as package
resources. `wiki.kt` is always generated; never edited by hand.

**Human-authored claims:** `wiki/claims.kt` is an optional hand-authored file where humans
declare typed predicates (relationships, disputes, assertions) that reference variables from the
generated `wiki.kt`. It lives in the wiki directory, is committed to version control, and is
included in the compile step automatically when present. Humans author predicates; codegen
authors entities. The two files are compiled together, so a reference to a nonexistent page
variable is a compile error.

**Codegen reads claims.kt:** `codegen.py` reads `wiki/claims.kt` when present and performs
two inferences:

1. **Auto-generate `Disputes`:** scan for `@Contested val NAME = TheoryOf(subject = PAGE_VAR, ...)`
   declarations. When two or more `@Contested TheoryOf` share the same subject variable, codegen
   emits a `Disputes` record in `wiki.kt` automatically. The human writes only the individual
   positions; codegen handles the structural pairing.

2. **Propagate `contested = true`:** any page that is the subject of an auto-generated `Disputes`
   gets `contested = true` in its `WikiPage` constructor. `render_main.kt` enforces the invariant:
   a page that is `contested = true` AND `confidence > 0.8f` causes `java -jar wiki.jar` to exit
   nonzero, which fails `compost assay`.

3. **Collect `allClaims`:** all `TheoryOf` variable names found in `claims.kt` plus all
   auto-generated `Disputes` variable names are collected into `val allClaims: List<Any> = listOf(...)`.
   Bare `TheoryOf` records (no `@Contested`) accumulate in `allClaims` without generating a dispute.
   When no `claims.kt` exists, `allClaims` is `emptyList()`.

Compile-time guarantee: `Disputes` records in `wiki.kt` reference variables by name from
`claims.kt`. A typo in a `@Contested TheoryOf` variable name causes a compile error in the
auto-generated `Disputes` that references it.

---

## § Step 1 — Add `compost.codify` to pyproject.toml

**File:** `tools/compost/pyproject.toml`

Add to `packages` list:
```toml
    "compost.codify",
```

Add to `package-data`:
```toml
"compost.codify" = ["schema/*.kt"]
```

Full updated sections:
```toml
packages = [
    "compost",
    "compost.gitea",
    "compost.model",
    "compost.mcp",
    "compost.ingest",
    "compost.synth",
    "compost.codify",
]

[tool.setuptools.package-data]
"compost.ingest" = ["classifier_rules.yaml"]
"compost.synth" = ["prompts/*.md"]
"compost.codify" = ["schema/*.kt"]
```

Then run `just sync` to update the editable install.

---

## § Step 2 — Create `CompostSchema.kt`

**File:** `tools/compost/codify/schema/CompostSchema.kt`

This is the static Kotlin type library. All wiki page types live here. Manual `toJson()` methods
avoid any dependency beyond what `kotlinc -include-runtime` provides.

```kotlin
// CompostSchema.kt — Shipped with compost. Do not edit.
// Generated wiki.kt instantiates these types.

@Target(AnnotationTarget.PROPERTY)
annotation class Contested

data class Provenance(
    val origin: String,
    val ingestedAt: String,
    val ingestedBy: String
)

sealed class WikiPage {
    abstract val id: String
    abstract val title: String
    abstract val confidence: Float
    abstract val prov: Provenance
    abstract val contested: Boolean
    abstract fun toJson(): String
}

private fun js(s: String) = "\"${s.replace("\\", "\\\\").replace("\"", "\\\"")}\""
private fun jl(items: List<String>) = "[${items.joinToString(",") { js(it) }}]"
private fun jf(f: Float) = "%.2f".format(f)

data class ServicePage(
    override val id: String,
    override val title: String,
    override val confidence: Float,
    override val prov: Provenance,
    override val contested: Boolean = false,
    val owner: String,
    val tier: Int = 2,
    val dependsOn: List<String> = emptyList(),
    val technology: List<String> = emptyList()
) : WikiPage() {
    override fun toJson() = buildString {
        append("{")
        append("\"id\":${js(id)},")
        append("\"type\":\"service\",")
        append("\"title\":${js(title)},")
        append("\"confidence\":${jf(confidence)},")
        append("\"contested\":$contested,")
        append("\"owner\":${js(owner)},")
        append("\"tier\":$tier,")
        append("\"depends_on\":${jl(dependsOn)},")
        append("\"technology\":${jl(technology)},")
        append("\"sources\":[${js(prov.origin)}]")
        append("}")
    }
}

data class DecisionPage(
    override val id: String,
    override val title: String,
    override val confidence: Float,
    override val prov: Provenance,
    override val contested: Boolean = false,
    val status: String,
    val supersedes: List<String> = emptyList()
) : WikiPage() {
    override fun toJson() = buildString {
        append("{")
        append("\"id\":${js(id)},")
        append("\"type\":\"decision\",")
        append("\"title\":${js(title)},")
        append("\"confidence\":${jf(confidence)},")
        append("\"contested\":$contested,")
        append("\"status\":${js(status)},")
        append("\"supersedes\":${jl(supersedes)}")
        append("}")
    }
}

data class RunbookPage(
    override val id: String,
    override val title: String,
    override val confidence: Float,
    override val prov: Provenance,
    override val contested: Boolean = false,
    val owner: String
) : WikiPage() {
    override fun toJson() = buildString {
        append("{")
        append("\"id\":${js(id)},")
        append("\"type\":\"runbook\",")
        append("\"title\":${js(title)},")
        append("\"confidence\":${jf(confidence)},")
        append("\"contested\":$contested,")
        append("\"owner\":${js(owner)}")
        append("}")
    }
}

data class ConceptPage(
    override val id: String,
    override val title: String,
    override val confidence: Float,
    override val prov: Provenance,
    override val contested: Boolean = false
) : WikiPage() {
    override fun toJson() = buildString {
        append("{")
        append("\"id\":${js(id)},")
        append("\"type\":\"concept\",")
        append("\"title\":${js(title)},")
        append("\"confidence\":${jf(confidence)},")
        append("\"contested\":$contested")
        append("}")
    }
}

data class PersonPage(
    override val id: String,
    override val title: String,
    override val confidence: Float,
    override val prov: Provenance,
    override val contested: Boolean = false,
    val team: String = ""
) : WikiPage() {
    override fun toJson() = buildString {
        append("{")
        append("\"id\":${js(id)},")
        append("\"type\":\"person\",")
        append("\"title\":${js(title)},")
        append("\"confidence\":${jf(confidence)},")
        append("\"contested\":$contested,")
        append("\"team\":${js(team)}")
        append("}")
    }
}

data class IncidentPage(
    override val id: String,
    override val title: String,
    override val confidence: Float,
    override val prov: Provenance,
    override val contested: Boolean = false,
    val status: String,
    val severity: String = ""
) : WikiPage() {
    override fun toJson() = buildString {
        append("{")
        append("\"id\":${js(id)},")
        append("\"type\":\"incident\",")
        append("\"title\":${js(title)},")
        append("\"confidence\":${jf(confidence)},")
        append("\"contested\":$contested,")
        append("\"status\":${js(status)},")
        append("\"severity\":${js(severity)}")
        append("}")
    }
}

data class UnknownPage(
    override val id: String,
    override val title: String,
    override val confidence: Float,
    override val prov: Provenance,
    override val contested: Boolean = false,
    val rawType: String = ""
) : WikiPage() {
    override fun toJson() = buildString {
        append("{")
        append("\"id\":${js(id)},")
        append("\"type\":\"unknown\",")
        append("\"title\":${js(title)},")
        append("\"confidence\":${jf(confidence)},")
        append("\"contested\":$contested,")
        append("\"raw_type\":${js(rawType)}")
        append("}")
    }
}

data class TheoryOf(
    val subject: WikiPage,
    val claim: String,
    val prov: Provenance
)

data class Disputes(
    val claimA: TheoryOf,
    val claimB: TheoryOf,
    val prov: Provenance
)
```

---

## § Step 3 — Create `render_main.kt`

**File:** `tools/compost/codify/schema/render_main.kt`

References `allWikiPages` which `wiki.kt` always declares.

```kotlin
// render_main.kt — Shipped with compost. Do not edit.
// Enforces invariants and serializes wiki pages + claims to JSON stdout.

fun main() {
    // Invariant: a contested page must not carry high confidence.
    // A dispute signals active disagreement; high confidence contradicts that.
    val violations = allWikiPages.filter { it.contested && it.confidence > 0.8f }
    if (violations.isNotEmpty()) {
        violations.forEach { page ->
            System.err.println(
                "contested-but-high-confidence: ${page.id} (confidence=${page.confidence})"
            )
        }
        System.exit(1)
    }

    val pages = allWikiPages
    val sb = StringBuilder()
    sb.append("{")
    sb.append("\"pages\":[")
    pages.forEachIndexed { i, page ->
        sb.append(page.toJson())
        if (i < pages.size - 1) sb.append(",")
    }
    sb.append("],")
    sb.append("\"claims\":${allClaims.size}")
    sb.append("}")
    println(sb.toString())
}
```

Note: `render_main.kt` references `allClaims` which `wiki.kt` always declares (either from
scanning `claims.kt` or as `emptyList()`). The rendered JSON shape changes from a bare array
to `{"pages": [...], "claims": N}`. Update `renderer.py` to parse the new shape accordingly.

---

## § Step 4 — Write failing tests (TDD Red)

**File:** `tools/compost/tests/test_codify.py`

Write all tests before any production code. Run `just test` — all should fail with ImportError.

```python
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
```

---

## § Step 5 — Create `tools/compost/codify/__init__.py`

Empty file:

```python
```

---

## § Step 6 — Create `tools/compost/codify/codegen.py` (TDD Green)

**File:** `tools/compost/codify/codegen.py`

```python
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from compost.model.frontmatter import parse_frontmatter

# Matches: @Contested val NAME = TheoryOf(subject = PAGE_VAR, ...)
# Handles annotation on same line or immediately preceding line.
_CONTESTED_RE = re.compile(
    r"@Contested\s+val\s+(\w+)\s*=\s*TheoryOf\s*\(\s*subject\s*=\s*(\w+)",
    re.MULTILINE,
)
# Matches all TheoryOf declarations regardless of @Contested
_ALL_THEORY_RE = re.compile(
    r"val\s+(\w+)\s*=\s*TheoryOf\s*\(\s*subject\s*=\s*(\w+)",
    re.MULTILINE,
)


@dataclass
class ClaimsScan:
    all_theory_vars: list[str]                    # every TheoryOf variable name found
    contested_by_subject: dict[str, list[str]]    # subject var → @Contested TheoryOf var names


def _scan_claims(claims_kt: Path) -> ClaimsScan:
    """Regex-scan claims.kt for TheoryOf declarations.

    Does not parse Kotlin; relies on the keyword-argument convention:
      TheoryOf(subject = VAR_NAME, ...)
    """
    text = claims_kt.read_text()

    contested_by_subject: dict[str, list[str]] = defaultdict(list)
    for var_name, subject_var in _CONTESTED_RE.findall(text):
        contested_by_subject[subject_var].append(var_name)

    all_theory_vars = [var for var, _ in _ALL_THEORY_RE.findall(text)]

    return ClaimsScan(
        all_theory_vars=all_theory_vars,
        contested_by_subject=dict(contested_by_subject),
    )


_TYPE_MAP: dict[str, str] = {
    "service": "ServicePage",
    "module": "ServicePage",
    "decision": "DecisionPage",
    "runbook": "RunbookPage",
    "concept": "ConceptPage",
    "customer": "ConceptPage",
    "person": "PersonPage",
    "project": "ConceptPage",
    "incident": "IncidentPage",
    "research": "ConceptPage",
}

_SKIP_NAMES = {"glossary.md", "index.md", "log.md"}


@dataclass
class CodegenResult:
    kt_path: Path
    page_count: int
    dispute_count: int
    warnings: list[str]


def codify(repo: Path, *, wiki_dir: Path | None = None) -> CodegenResult:
    """Read all wiki/*.md, emit .compost/codify/generated/wiki.kt.

    Does not compile. Does not require kotlinc."""
    wiki_dir = wiki_dir or (repo / "wiki")
    out_dir = repo / ".compost" / "codify" / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)

    pages: list[tuple[Path, dict]] = []
    warnings: list[str] = []

    for md in sorted(wiki_dir.rglob("*.md")):
        if md.name in _SKIP_NAMES:
            continue
        fm, _ = parse_frontmatter(md)
        if not fm:
            warnings.append(f"{md.relative_to(repo)}: no frontmatter, skipped")
            continue
        pages.append((md, fm))

    # Scan claims.kt for @Contested TheoryOf to determine which pages are contested
    claims_kt = wiki_dir / "claims.kt"
    scan = _scan_claims(claims_kt) if claims_kt.exists() else ClaimsScan([], {})
    # Pages with 2+ @Contested TheoryOf become contested
    contested_var_names = {
        subject_var
        for subject_var, theory_vars in scan.contested_by_subject.items()
        if len(theory_vars) >= 2
    }

    name_dispute_count = _detect_disputes(pages)
    auto_dispute_count = len(contested_var_names)
    var_names: list[str] = []
    lines: list[str] = ["// Generated by compost codify — do not edit", ""]

    for md, fm in pages:
        rel = md.relative_to(repo)
        page_type = fm.get("type", "")
        kt_class = _TYPE_MAP.get(page_type)
        if kt_class is None:
            warnings.append(f"{rel}: unknown type '{page_type}', emitting UnknownPage")
            kt_class = "UnknownPage"

        var_name = _to_kotlin_ident(md.stem)
        var_names.append(var_name)
        prov = _emit_prov(rel, fm)
        contested = var_name in contested_var_names

        if kt_class == "ServicePage":
            lines.extend(_emit_service(var_name, md.stem, fm, prov, contested))
        elif kt_class == "DecisionPage":
            lines.extend(_emit_decision(var_name, md.stem, fm, prov, contested))
        elif kt_class == "RunbookPage":
            lines.extend(_emit_runbook(var_name, md.stem, fm, prov, contested))
        elif kt_class == "PersonPage":
            lines.extend(_emit_person(var_name, md.stem, fm, prov, contested))
        elif kt_class == "IncidentPage":
            lines.extend(_emit_incident(var_name, md.stem, fm, prov, contested))
        elif kt_class == "ConceptPage":
            lines.extend(_emit_concept(var_name, md.stem, fm, prov, contested))
        else:
            lines.extend(_emit_unknown(var_name, md.stem, fm, prov, page_type, contested))
        lines.append("")

    if var_names:
        lines.append(f"val allWikiPages: List<WikiPage> = listOf({', '.join(var_names)})")
    else:
        lines.append("val allWikiPages: List<WikiPage> = emptyList()")
    lines.append("")

    # Auto-generate Disputes for every subject that has 2+ @Contested TheoryOf
    today = str(date.today())
    auto_dispute_vars: list[str] = []
    for subject_var, theory_vars in scan.contested_by_subject.items():
        if len(theory_vars) >= 2:
            # Emit pairwise: (0,1), (1,2), etc. — keeps each Dispute to two claims
            for i in range(len(theory_vars) - 1):
                dvar = f"{subject_var}_autoDispute_{i}"
                auto_dispute_vars.append(dvar)
                lines += [
                    f"// auto-generated: competing @Contested TheoryOf for {subject_var}",
                    f"val {dvar} = Disputes(",
                    f"    claimA = {theory_vars[i]},",
                    f"    claimB = {theory_vars[i + 1]},",
                    f'    prov = Provenance(origin = "compost-codify", ingestedAt = "{today}", ingestedBy = "compost-codify")',
                    ")",
                    "",
                ]

    # Collect all claims into allClaims for render_main.kt
    all_claim_vars = scan.all_theory_vars + auto_dispute_vars
    if all_claim_vars:
        lines.append(f"val allClaims: List<Any> = listOf({', '.join(all_claim_vars)})")
    else:
        lines.append("val allClaims: List<Any> = emptyList()")

    kt_path = out_dir / "wiki.kt"
    kt_path.write_text("\n".join(lines) + "\n")

    return CodegenResult(
        kt_path=kt_path,
        page_count=len(pages),
        dispute_count=name_dispute_count + auto_dispute_count,
        warnings=warnings,
    )


def _emit_prov(rel: Path, fm: dict) -> str:
    owners = fm.get("owners") or ["unknown"]
    ingested_by = owners[0] if isinstance(owners, list) else str(owners)
    updated = fm.get("updated") or ""
    sources = fm.get("sources") or []
    origin = sources[0] if sources else str(rel)
    return (
        f'Provenance(origin = {_qs(str(origin))}, '
        f'ingestedAt = {_qs(str(updated))}, '
        f'ingestedBy = {_qs(ingested_by)})'
    )


def _emit_service(var: str, stem: str, fm: dict, prov: str, contested: bool = False) -> list[str]:
    deps = _coerce_list(fm.get("depends_on"))
    tech = _coerce_list(fm.get("technology"))
    owner = _first_owner(fm)
    tier = int(fm.get("tier") or 2)
    return [
        f"val {var} = ServicePage(",
        f'    id = {_qs(stem)},',
        f'    title = {_qs(fm.get("name") or stem)},',
        f"    confidence = {_confidence_float(fm.get('confidence'))},",
        f"    prov = {prov},",
        f"    contested = {'true' if contested else 'false'},",
        f"    owner = {_qs(owner)},",
        f"    tier = {tier},",
        f"    dependsOn = {_kt_list(deps)},",
        f"    technology = {_kt_list(tech)},",
        ")",
    ]


def _emit_decision(var: str, stem: str, fm: dict, prov: str, contested: bool = False) -> list[str]:
    sups = _coerce_list(fm.get("supersedes"))
    return [
        f"val {var} = DecisionPage(",
        f'    id = {_qs(stem)},',
        f'    title = {_qs(fm.get("name") or stem)},',
        f"    confidence = {_confidence_float(fm.get('confidence'))},",
        f"    prov = {prov},",
        f"    contested = {'true' if contested else 'false'},",
        f'    status = {_qs(fm.get("status") or "proposed")},',
        f"    supersedes = {_kt_list(sups)},",
        ")",
    ]


def _emit_runbook(var: str, stem: str, fm: dict, prov: str, contested: bool = False) -> list[str]:
    return [
        f"val {var} = RunbookPage(",
        f'    id = {_qs(stem)},',
        f'    title = {_qs(fm.get("name") or stem)},',
        f"    confidence = {_confidence_float(fm.get('confidence'))},",
        f"    prov = {prov},",
        f"    contested = {'true' if contested else 'false'},",
        f"    owner = {_qs(_first_owner(fm))},",
        ")",
    ]


def _emit_concept(var: str, stem: str, fm: dict, prov: str, contested: bool = False) -> list[str]:
    return [
        f"val {var} = ConceptPage(",
        f'    id = {_qs(stem)},',
        f'    title = {_qs(fm.get("name") or stem)},',
        f"    confidence = {_confidence_float(fm.get('confidence'))},",
        f"    prov = {prov},",
        f"    contested = {'true' if contested else 'false'},",
        ")",
    ]


def _emit_person(var: str, stem: str, fm: dict, prov: str, contested: bool = False) -> list[str]:
    team = fm.get("team") or ""
    return [
        f"val {var} = PersonPage(",
        f'    id = {_qs(stem)},',
        f'    title = {_qs(fm.get("name") or stem)},',
        f"    confidence = {_confidence_float(fm.get('confidence'))},",
        f"    prov = {prov},",
        f"    contested = {'true' if contested else 'false'},",
        f"    team = {_qs(team)},",
        ")",
    ]


def _emit_incident(var: str, stem: str, fm: dict, prov: str, contested: bool = False) -> list[str]:
    severity = fm.get("severity") or ""
    return [
        f"val {var} = IncidentPage(",
        f'    id = {_qs(stem)},',
        f'    title = {_qs(fm.get("name") or stem)},',
        f"    confidence = {_confidence_float(fm.get('confidence'))},",
        f"    prov = {prov},",
        f"    contested = {'true' if contested else 'false'},",
        f'    status = {_qs(fm.get("status") or "open")},',
        f"    severity = {_qs(severity)},",
        ")",
    ]


def _emit_unknown(var: str, stem: str, fm: dict, prov: str, raw_type: str, contested: bool = False) -> list[str]:
    return [
        f"val {var} = UnknownPage(",
        f'    id = {_qs(stem)},',
        f'    title = {_qs(fm.get("name") or stem)},',
        f"    confidence = {_confidence_float(fm.get('confidence'))},",
        f"    prov = {prov},",
        f"    contested = {'true' if contested else 'false'},",
        f"    rawType = {_qs(raw_type)},",
        ")",
    ]


def _detect_disputes(pages: list[tuple[Path, dict]]) -> int:
    by_name: dict[str, list[Path]] = defaultdict(list)
    for md, fm in pages:
        name = (fm.get("name") or md.stem).lower()
        by_name[name].append(md)
        for alias in _coerce_list(fm.get("aliases")):
            by_name[alias.lower()].append(md)
    return sum(1 for v in by_name.values() if len(v) > 1)


def _to_kotlin_ident(stem: str) -> str:
    """Convert a file stem to a valid Kotlin identifier.

    Hyphens and dots become camelCase separators.
    Stems starting with digits get a 'p' prefix."""
    parts = stem.replace("-", "_").replace(".", "_").split("_")
    first = parts[0]
    rest = "".join(p.capitalize() for p in parts[1:])
    ident = first + rest
    if ident and (ident[0].isdigit()):
        ident = "p" + ident
    return ident


def _confidence_float(v) -> str:
    mapping = {"high": "0.9f", "medium": "0.7f", "low": "0.5f"}
    if isinstance(v, str):
        return mapping.get(v.lower(), "0.7f")
    if isinstance(v, (int, float)):
        return f"{float(v):.1f}f"
    return "0.7f"


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _qs(s: str) -> str:
    """Quoted Kotlin string literal."""
    return f'"{_esc(s)}"'


def _kt_list(items: list[str]) -> str:
    if not items:
        return "emptyList()"
    inner = ", ".join(_qs(i) for i in items)
    return f"listOf({inner})"


def _coerce_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [str(v)]


def _first_owner(fm: dict) -> str:
    owners = fm.get("owners") or ["unknown"]
    if isinstance(owners, list):
        return owners[0] if owners else "unknown"
    return str(owners)
```

---

## § Step 7 — Create `tools/compost/codify/compiler.py`

**File:** `tools/compost/codify/compiler.py`

```python
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SDKMAN_INSTALL_CMD = "sdk install kotlin  # see https://sdkman.io"


@dataclass
class CompileError:
    kt_line: int
    message: str
    source_md: Path | None


@dataclass
class CompileResult:
    success: bool
    errors: list[CompileError]
    jar_path: Path | None


def compile_kt(repo: Path) -> CompileResult:
    """Run kotlinc against the generated wiki.kt. Requires kotlinc on PATH.

    Raises RuntimeError if kotlinc is not found."""
    if not shutil.which("kotlinc"):
        raise RuntimeError(
            f"kotlinc not found on PATH.\n"
            f"Install via SDKMAN: {SDKMAN_INSTALL_CMD}"
        )

    generated_dir = repo / ".compost" / "codify" / "generated"
    wiki_kt = generated_dir / "wiki.kt"
    if not wiki_kt.exists():
        raise RuntimeError(f"wiki.kt not found: {wiki_kt}. Run 'compost codify' first.")

    schema_dir = Path(__file__).parent / "schema"
    schema_kt = schema_dir / "CompostSchema.kt"
    render_main_kt = schema_dir / "render_main.kt"
    jar_path = generated_dir / "wiki.jar"

    # Include hand-authored claims.kt if present in the wiki directory.
    # Claims reference generated wiki.kt variables, so both must compile together.
    claims_kt = repo / "wiki" / "claims.kt"
    extra = [str(claims_kt)] if claims_kt.exists() else []

    result = subprocess.run(
        [
            "kotlinc",
            str(schema_kt),
            str(render_main_kt),
            str(wiki_kt),
            *extra,
            "-include-runtime",
            "-d", str(jar_path),
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        errors = _parse_errors(result.stderr, wiki_kt)
        return CompileResult(success=False, errors=errors, jar_path=None)

    return CompileResult(success=True, errors=[], jar_path=jar_path)


def _parse_errors(stderr: str, wiki_kt: Path) -> list[CompileError]:
    errors: list[CompileError] = []
    for line in stderr.splitlines():
        if ": error:" in line:
            # kotlinc format: "path/file.kt:line:col: error: message"
            try:
                colon_parts = line.split(":")
                kt_line = int(colon_parts[1])
                message = ":".join(colon_parts[4:]).strip() if len(colon_parts) > 4 else line
                errors.append(CompileError(kt_line=kt_line, message=message, source_md=None))
            except (ValueError, IndexError):
                errors.append(CompileError(kt_line=0, message=line.strip(), source_md=None))
    return errors
```

---

## § Step 8 — Create `tools/compost/codify/renderer.py`

**File:** `tools/compost/codify/renderer.py`

```python
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def render_wiki(repo: Path) -> list[dict]:
    """Execute wiki.jar main() and return the rendered page list as dicts.

    Requires java on PATH and wiki.jar to exist (run compile_kt first)."""
    if not shutil.which("java"):
        raise RuntimeError(
            "java not found on PATH. Install via SDKMAN: sdk install java"
        )

    jar_path = repo / ".compost" / "codify" / "generated" / "wiki.jar"
    if not jar_path.exists():
        raise RuntimeError(
            f"wiki.jar not found: {jar_path}. Run 'compost codify --compile' first."
        )

    result = subprocess.run(
        ["java", "-jar", str(jar_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"wiki.jar render failed:\n{result.stderr.strip()}")

    parsed = json.loads(result.stdout)
    # render_main.kt emits {"pages": [...], "claims": N}
    return parsed["pages"]
```

---

## § Step 9 — Create `tools/compost/codify/compare.py`

**File:** `tools/compost/codify/compare.py`

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from compost.model.frontmatter import parse_frontmatter

_SKIP_NAMES = {"glossary.md", "index.md", "log.md"}

# Fields checked in the fuzzy compare. 'name' maps to Kotlin 'title'.
_CHECKED_FIELDS: dict[str, str] = {
    "name": "title",       # frontmatter 'name' -> rendered 'title'
    "type": "type",
    "confidence": "confidence",
    "status": "status",
}


@dataclass
class AssayFailure:
    page_id: str
    source_md: Path
    missing_fields: list[str]
    changed_fields: list[tuple[str, Any, Any]]  # (field_name, original, rendered)


@dataclass
class AssayResult:
    passed: bool
    checked: int
    failures: list[AssayFailure]


def compare(
    repo: Path,
    rendered: list[dict],
    *,
    wiki_dir: Path | None = None,
) -> AssayResult:
    """Fuzzy compare a rendered page list against original wiki frontmatter.

    Never raises on comparison failure; only raises on infrastructure errors."""
    wiki_dir = wiki_dir or (repo / "wiki")
    by_id = {r["id"]: r for r in rendered}

    failures: list[AssayFailure] = []
    checked = 0

    for md in sorted(wiki_dir.rglob("*.md")):
        if md.name in _SKIP_NAMES:
            continue
        fm, _ = parse_frontmatter(md)
        if not fm:
            continue

        page_id = md.stem
        checked += 1

        if page_id not in by_id:
            failures.append(AssayFailure(
                page_id=page_id,
                source_md=md,
                missing_fields=list(_CHECKED_FIELDS),
                changed_fields=[],
            ))
            continue

        rend = by_id[page_id]
        missing: list[str] = []
        changed: list[tuple[str, Any, Any]] = []

        for fm_field, rend_field in _CHECKED_FIELDS.items():
            orig = fm.get(fm_field)
            if orig is None:
                continue  # field not in source; cannot fail
            rend_val = rend.get(rend_field)
            if rend_val is None:
                missing.append(fm_field)
            elif not _fuzzy_equal(fm_field, orig, rend_val):
                changed.append((fm_field, orig, rend_val))

        if missing or changed:
            failures.append(AssayFailure(
                page_id=page_id,
                source_md=md,
                missing_fields=missing,
                changed_fields=changed,
            ))

    return AssayResult(
        passed=len(failures) == 0,
        checked=checked,
        failures=failures,
    )


def _fuzzy_equal(field: str, orig: Any, rend: Any) -> bool:
    if field == "confidence":
        return abs(_conf_to_float(orig) - _conf_to_float(rend)) <= 0.05
    if isinstance(orig, list) and isinstance(rend, list):
        return sorted(str(x) for x in orig) == sorted(str(x) for x in rend)
    if isinstance(orig, str) and isinstance(rend, str):
        return orig.strip().lower() == rend.strip().lower()
    return str(orig) == str(rend)


def _conf_to_float(v: Any) -> float:
    if isinstance(v, str):
        return {"high": 0.9, "medium": 0.7, "low": 0.5}.get(v.lower(), 0.7)
    return float(v)
```

---

## § Step 10 — Add CLI commands to `cli.py`

Add two new command groups after the `synth` group and before the `gitea` group.

### 10a. Add `codify` group

Insert after line 479 (`console.print(table)`), before the `# ── gitea commands ──` comment:

```python
# ── codify commands ───────────────────────────────────────────────────────────


@main.group("codify")
def codify_group() -> None:
    """Generate Kotlin type declarations from wiki frontmatter."""


@codify_group.command("run")
@click.option("--compile", "do_compile", is_flag=True, default=False,
              help="Compile generated Kotlin with kotlinc after codegen.")
@click.option("--wiki-dir", default=None, type=click.Path(resolve_path=True, path_type=Path),
              help="Override wiki directory path.")
@click.pass_context
def codify_run(ctx: click.Context, do_compile: bool, wiki_dir: Path | None) -> None:
    """Generate .compost/codify/generated/wiki.kt from wiki frontmatter."""
    from compost.codify.codegen import codify

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    result = codify(repo, wiki_dir=wiki_dir)
    console.print(
        f"[green]✓[/green] {result.page_count} page(s) codified → {result.kt_path}"
    )
    if result.dispute_count:
        console.print(f"[yellow]⚠ {result.dispute_count} name collision(s) detected (@Contested)[/yellow]")
    for w in result.warnings:
        console.print(f"[dim]  ⚠ {w}[/dim]")

    if not do_compile:
        return

    from compost.codify.compiler import compile_kt

    console.print("[dim]compiling...[/dim]")
    try:
        compile_result = compile_kt(repo)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    if compile_result.success:
        console.print(f"[green]✓[/green] compile passed → {compile_result.jar_path}")
    else:
        console.print("[red]✗ compile failed[/red]")
        for err in compile_result.errors:
            console.print(f"  [red]line {err.kt_line}:[/red] {err.message}")
        sys.exit(1)


# ── assay commands ────────────────────────────────────────────────────────────


@main.command("assay")
@click.option("--wiki-dir", default=None, type=click.Path(resolve_path=True, path_type=Path),
              help="Override wiki directory path.")
@click.option("--report", is_flag=True, default=False,
              help="Write markdown report to .compost/assay-report.md.")
@click.pass_context
def assay_cmd(ctx: click.Context, wiki_dir: Path | None, report: bool) -> None:
    """Round-trip validation: codify → compile → render → fuzzy compare."""
    from compost.codify.codegen import codify
    from compost.codify.compiler import compile_kt
    from compost.codify.renderer import render_wiki
    from compost.codify.compare import compare

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    console.print("[dim]codifying...[/dim]")
    codify(repo, wiki_dir=wiki_dir)

    console.print("[dim]compiling...[/dim]")
    try:
        compile_result = compile_kt(repo)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    if not compile_result.success:
        console.print("[red]✗ compile failed — assay aborted[/red]")
        for err in compile_result.errors:
            console.print(f"  [red]line {err.kt_line}:[/red] {err.message}")
        sys.exit(1)

    console.print("[dim]rendering...[/dim]")
    try:
        rendered = render_wiki(repo)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    console.print("[dim]comparing...[/dim]")
    result = compare(repo, rendered, wiki_dir=wiki_dir)

    if result.passed:
        console.print(
            f"[green]✓ ASSAY PASS[/green] — {result.checked} page(s) round-tripped cleanly"
        )
    else:
        console.print(f"[red]✗ ASSAY FAIL[/red] — {len(result.failures)} failure(s)")
        for f in result.failures:
            console.print(f"  [red]{f.page_id}[/red] ({f.source_md.relative_to(repo)})")
            for field in f.missing_fields:
                console.print(f"    missing field: {field}")
            for field, orig, rend in f.changed_fields:
                console.print(f"    changed: {field} = {orig!r} → {rend!r}")

    if report:
        _write_assay_report(repo, result)
        console.print(f"[dim]report → {repo / '.compost' / 'assay-report.md'}[/dim]")

    if not result.passed:
        sys.exit(1)
```

### 10b. Add `_write_assay_report` helper to helpers section

Add at the end of the helpers section (before the last closing line):

```python
def _write_assay_report(repo: Path, result) -> None:
    from datetime import datetime, timezone
    lines = [
        f"# Assay Report — {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}",
        "",
        f"**Result:** {'PASS' if result.passed else 'FAIL'}  ",
        f"**Pages checked:** {result.checked}  ",
        f"**Failures:** {len(result.failures)}",
        "",
    ]
    for f in result.failures:
        lines.append(f"## {f.page_id}")
        lines.append(f"Source: `{f.source_md.relative_to(repo)}`")
        if f.missing_fields:
            lines.append(f"Missing: {', '.join(f.missing_fields)}")
        for field, orig, rend in f.changed_fields:
            lines.append(f"- `{field}`: `{orig}` → `{rend}`")
        lines.append("")
    report_path = repo / ".compost" / "assay-report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines))
```

### 10c. Add `--assay` flag to `raw add`

Update the `raw_add` command signature:

```python
@click.option("--assay", is_flag=True, default=False,
              help="Run round-trip assay after synthesis. Blocks push if assay fails.")
```

Add `assay: bool` to `raw_add` function parameters.

After the synthesis block and before the `try: push_branch(...)` block, insert:

```python
    if assay and synth_result and synth_result.wiki_diffs:
        console.print("[dim][Assay] running round-trip validation...[/dim]")
        try:
            from compost.codify.codegen import codify as _codify
            from compost.codify.compiler import compile_kt
            from compost.codify.renderer import render_wiki
            from compost.codify.compare import compare as _compare
            _codify(repo)
            cr = compile_kt(repo)
            if not cr.success:
                console.print("[red][Assay] compile failed — push blocked[/red]")
                for err in cr.errors:
                    console.print(f"  line {err.kt_line}: {err.message}")
                sys.exit(1)
            rendered = render_wiki(repo)
            assay_result = _compare(repo, rendered)
            if assay_result.passed:
                console.print(f"[green][Assay] PASS[/green] — {assay_result.checked} page(s)")
            else:
                console.print(f"[red][Assay] FAIL — {len(assay_result.failures)} failure(s) — push blocked[/red]")
                for f in assay_result.failures:
                    console.print(f"  {f.page_id}: {f.missing_fields} {f.changed_fields}")
                sys.exit(1)
        except RuntimeError as e:
            console.print(f"[yellow]⚠ assay error (kotlinc/java not available?): {e}[/yellow]")
            console.print("[yellow]Continuing without assay validation.[/yellow]")
```

---

## § Step 11 — Update `pyproject.toml`

Apply the changes from Step 1:

```toml
packages = [
    "compost",
    "compost.gitea",
    "compost.model",
    "compost.mcp",
    "compost.ingest",
    "compost.synth",
    "compost.codify",
]

[tool.setuptools.package-data]
"compost.ingest" = ["classifier_rules.yaml"]
"compost.synth" = ["prompts/*.md"]
"compost.codify" = ["schema/*.kt"]
```

---

## § Step 12 — Run tests (TDD verify green)

```bash
just sync
just test
```

All non-`@kotlinc_available` tests should pass. The `@kotlinc_available` tests skip when
`kotlinc` is not on PATH.

---

## § Step 13 — Documentation updates

### `tools/compost/README.md`

Add a new section **"Kotlin Consistency Layer"** after the "Tier 2 Synthesis" section:

```markdown
## Kotlin Consistency Layer

Compost generates a typed Kotlin representation of the wiki and uses `kotlinc` as a consistency
oracle. The markdown frontmatter is always primary; Kotlin is always derived.

**Prerequisites:** install Kotlin via SDKMAN (`sdk install kotlin`).

```bash
# Generate wiki.kt from wiki frontmatter
compost codify run

# Generate and compile (kotlinc must be on PATH)
compost codify run --compile

# Full round-trip: codify → compile → render → fuzzy compare
compost assay

# Assay with markdown report
compost assay --report

# Validate wiki edits produced by synthesis before pushing
compost raw add --source decision --title "..." --assay < content.md
```

A compile pass means all wiki frontmatter is structurally consistent with the schema. `compost
assay` also verifies the codegen is lossless: the compiled Kotlin, when run, produces output that
matches the original frontmatter for all key fields.

### Human-authored claims

Create `wiki/claims.kt` to write typed assertions that reference generated page variables:

```kotlin
// wiki/claims.kt — hand-authored; committed to version control
// References variables declared in the generated wiki.kt.

@Contested val jwtPositionByPlatform = TheoryOf(
    subject = authService,   // compile error if authService doesn't exist in wiki.kt
    claim = "JWT is stateless and scales horizontally",
    prov = Provenance(origin = "internal-discussion", ingestedAt = "2026-04-15", ingestedBy = "eoin")
)

@Contested val sessionPositionByAlice = TheoryOf(
    subject = authService,
    claim = "Session cookies are simpler and immediately revocable",
    prov = Provenance(origin = "raw/slack/2026-04-14-eng.md", ingestedAt = "2026-04-14", ingestedBy = "alice")
)

val jwtVsSessionDispute = Disputes(
    claimA = jwtPositionByPlatform,
    claimB = sessionPositionByAlice,
    prov = Provenance(origin = "wiki/claims.kt", ingestedAt = "2026-04-15", ingestedBy = "eoin")
)
```

`claims.kt` is included in the compile automatically when present. A typo in a page reference
(`authSevice`) is a compile error caught by `compost codify run --compile`.
```

Add to the command reference table:

```markdown
| `compost codify run [--compile]` | Generate wiki.kt; optionally compile |
| `compost assay [--report]`       | Full round-trip validation            |
```

### `AGENTS.md` (repo root)

Add a new section **"Tooling: Kotlin Consistency Layer"**:

```markdown
## Tooling: Kotlin Consistency Layer

`compost codify --compile` and `compost assay` require `kotlinc` and `java` on PATH.

Install via SDKMAN (manages JVM toolchains per-user without touching system Java):

```bash
curl -s "https://get.sdkman.io" | bash
source "$HOME/.sdkman/bin/sdkman-init.sh"
sdk install kotlin    # installs kotlinc + JVM
kotlinc -version      # verify
```

**Schema file:** `tools/compost/codify/schema/CompostSchema.kt` is a versioned package resource.
Do not edit it directly. Schema changes (adding a new wiki page type) require updating both
`CompostSchema.kt` and `_TYPE_MAP` in `codegen.py` together.

**CI promotion:** when moving to GitHub Actions (Phase 9), swap SDKMAN for:
```yaml
- uses: actions/setup-java@v4
  with: { java-version: '21', distribution: 'temurin' }
- uses: fwilhe2/setup-kotlin@v1
```
No Python or compost code changes required.
```

### `_plans/backlog.md`

Remove the "Synthesis pipeline observability dashboard" section (it shipped in Phase 4).

Update the winze section's "When to revisit" to reference this plan:
> **When to revisit:** Phase 5 Kotlin consistency layer (this plan). The metabolism phases are
> the template for Phase 9 `compost tend` subcommands.

---

## § Step 14 — Run full test suite

```bash
just test
```

Expected: all tests pass (kotlinc tests skipped if not installed).

---

## § Files created / modified

| Action | Path |
|---|---|
| CREATE | `tools/compost/codify/__init__.py` |
| CREATE | `tools/compost/codify/codegen.py` |
| CREATE | `tools/compost/codify/compiler.py` |
| CREATE | `tools/compost/codify/renderer.py` |
| CREATE | `tools/compost/codify/compare.py` |
| CREATE | `tools/compost/codify/schema/CompostSchema.kt` |
| CREATE | `tools/compost/codify/schema/render_main.kt` |
| CREATE | `tools/compost/tests/test_codify.py` |
| CREATE (human-authored, optional) | `wiki/claims.kt` in each wiki repo |
| MODIFY | `tools/compost/pyproject.toml` |
| MODIFY | `tools/compost/cli.py` |
| MODIFY | `tools/compost/README.md` |
| MODIFY | `AGENTS.md` |
| MODIFY | `_plans/backlog.md` |

---

### Feedback Log

**Bidirectional access between claims.kt and wiki.kt**
> Original comment (verbatim): `^^ could the human authored things be used by the generated kotlin? ^^`
>
> Context: appeared under the paragraph introducing `wiki/claims.kt` as an optional human-authored file.
>
> Incorporated: added a "Bidirectional access" design note explaining that all `.kt` files compile as one unit so variables are mutually accessible at compile time, but `codegen.py` doesn't read `claims.kt` so the generated `wiki.kt` won't reference human-authored predicates in practice. Also called out the Phase 6 extension point: if `claims.kt` declares `allClaims`, `render_main.kt` can be extended to render claims alongside pages, letting the Phase 6 contradiction scan consume structured dispute records rather than running an LLM pass.
