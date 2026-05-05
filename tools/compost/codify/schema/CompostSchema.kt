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
    val owner: String,
    val status: String
) : WikiPage() {
    override fun toJson() = buildString {
        append("{")
        append("\"id\":${js(id)},")
        append("\"type\":\"runbook\",")
        append("\"title\":${js(title)},")
        append("\"confidence\":${jf(confidence)},")
        append("\"contested\":$contested,")
        append("\"owner\":${js(owner)},")
        append("\"status\":${js(status)}")
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
