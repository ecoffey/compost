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
