"""Public presentation strings and governed status/category views.

Domain values remain language-neutral. Swedish is the active locale; English is
a prepared presentation mapping only, not a public language switch.
"""
from __future__ import annotations

LOCALES = {
    "sv": {
        "nav_overview": "Översikt", "nav_signals": "Signaler", "nav_airports": "Flygplatser",
        "nav_glossary": "Ordlista", "project_type": "Projekttyp", "status": "Status",
        "confidence": "Confidence", "year": "År", "score": "Analytisk score",
        "source": "Källa", "completed": "Färdigställd",
        "runways": "Banor",
        "current_emas": "EMAS idag",
        "projects": "Projekt och bevakning", "historical_installations": "Historiska installationsuppgifter",
        "research_watch": "Bevakas – forskningskandidat", "under_review": "Projektuppgift under granskning",
        "hero_statement": "Global intelligens om EMAS-installationer, projekt och bansäkerhet.",
        "view_all_signals": "Se alla signaler",
        "view_all_updates": "Visa alla uppdateringar",
        "current_status_unverified": "Aktuell EMAS-status ej verifierad",
        "current_status_unverified_detail": "Historiska källor visar tidigare EMAS-installation. Aktuell fysisk status granskas.",
        # ("RWI - Sacheon Evidence Surfacing - View-Model Slice" mission)
        "evidence": "Underlag",
        "evidence_eyebrow": "Granskade sakuppgifter med originalutdrag, kopplade till källan nedan",
        "original_excerpt": "Originalutdrag",
        "score_tooltip": "Bedömning av underlagets styrka och tillförlitlighet – inte sannolikheten att en viss leverantör vinner projektet.",
    },
    "en": {
        "nav_overview": "Overview", "nav_signals": "Signals", "nav_airports": "Airports",
        "nav_glossary": "Glossary", "project_type": "Project type", "status": "Status",
        "confidence": "Confidence", "year": "Year", "score": "Analytical score",
        "source": "Source", "completed": "Completed",
        "runways": "Runways",
        "current_emas": "EMAS today",
        "projects": "Projects and watch items", "historical_installations": "Historical installation records",
        "research_watch": "Watch item – research candidate", "under_review": "Project record under review",
        "hero_statement": "Global intelligence on EMAS installations, projects and runway safety.",
        "view_all_signals": "View all signals",
        "view_all_updates": "View all updates",
        "current_status_unverified": "Current EMAS status not verified",
        "current_status_unverified_detail": "Historical sources show an earlier EMAS installation. Current physical status is under review.",
        "evidence": "Evidence",
        "evidence_eyebrow": "Governed findings with original excerpts, linked to the source below",
        "original_excerpt": "Original excerpt",
        "score_tooltip": "An assessment of how strong and reliable the evidence is - not the probability that a specific supplier wins the project.",
    },
}

STATUS_PRESENTATION = {
    "completed": {"role": "completed", "sv": "Färdigställd", "en": "Completed"},
    "identified": {"role": "identified", "sv": "Identifierad", "en": "Identified"},
    "funded": {"role": "funded", "sv": "Finansierad", "en": "Funded"},
    "design": {"role": "design", "sv": "Projektering", "en": "Design"},
    "procurement": {"role": "procurement", "sv": "Upphandling", "en": "Procurement"},
    "under construction": {"role": "construction", "sv": "Under byggnation", "en": "Under construction"},
    "environmental_review": {"role": "review", "sv": "Miljöprövning", "en": "Environmental review"},
    "master_plan": {"role": "planning", "sv": "Master Plan", "en": "Master plan"},
    "alp": {"role": "planning", "sv": "ALP", "en": "ALP"},
    "cip": {"role": "planning", "sv": "CIP", "en": "CIP"},
}

# SLT1 (docs/architecture/rwi-signal-temporal-relevance-opportunity-lifecycle-design.md):
# presentation for app.static_export.signal_lifecycle.SignalLifecycleState.
# Concise, investor-facing labels - never the raw enum name - plus a short
# tooltip that states plainly what each state does and does not claim
# (design doc S11/S13's own explicit ask: e.g. "Behöver research" must read
# as "unconfirmed", not "discarded" or "unimportant").
LIFECYCLE_PRESENTATION = {
    "active_opportunity": {
        "css": "active",
        "sv": "Aktuell möjlighet",
        "en": "Active opportunity",
        "sv_tooltip": "Källor pekar på pågående eller kommande ekonomisk aktivitet just nu - budget, upphandling, projektering eller byggnation.",
        "en_tooltip": "Sources point to current or upcoming economic activity right now - funding, procurement, design or construction.",
    },
    "developing_watch": {
        "css": "watch",
        "sv": "Under bevakning",
        "en": "Developing watch",
        "sv_tooltip": "Relevant EMAS-aktivitet finns, men det är ännu för osäkert eller för tidigt för att bedöma kommersiell mognad.",
        "en_tooltip": "Relevant EMAS activity exists, but commercial timing or maturity is still uncertain.",
    },
    "realized_historical": {
        "css": "historical",
        "sv": "Historik",
        "en": "Historical",
        "sv_tooltip": "Den ekonomiska aktiviteten är bekräftat genomförd eller avslutad - värdefull historik, inte en aktuell möjlighet.",
        "en_tooltip": "The economic activity is confirmed completed or concluded - valuable history, not a current opportunity.",
    },
    "stale_unresolved": {
        "css": "stale",
        "sv": "Behöver research",
        "en": "Needs research",
        "sv_tooltip": "Gammal nog att inte visas som aktuell, men RWI saknar ännu bevis för vad som hänt sedan dess. Inte bortsorterad - bara obekräftad.",
        "en_tooltip": "Old enough that treating it as current would be misleading, but RWI has no evidence yet of what happened since. Not discarded - just unconfirmed.",
    },
    "other": {
        "css": "unknown",
        "sv": "Ej klassificerad",
        "en": "Unclassified",
        "sv_tooltip": "Kunde inte klassificeras säkert utifrån tillgänglig information.",
        "en_tooltip": "Could not be safely classified from the information available.",
    },
}


def text(key: str, locale: str = "sv") -> str:
    return LOCALES.get(locale, LOCALES["sv"]).get(key, key)

def status_view(value: str | None, locale: str = "sv") -> tuple[str, str]:
    if not value:
        return ("Ej angiven", "unknown") if locale == "sv" else ("Not specified", "unknown")
    entry = STATUS_PRESENTATION.get(value)
    if entry:
        return entry[locale], entry["role"]
    # Unknown values are not assigned an invented lifecycle meaning.
    return value.replace("_", " "), "unknown"


def lifecycle_view(state: str | None, locale: str = "sv") -> tuple[str, str, str]:
    """(label, css_class, tooltip) for a SignalLifecycleState value's own
    `.value` (e.g. "active_opportunity"). Mirrors status_view()'s own
    fallback discipline: an unrecognized value renders as its own
    underscore-stripped text, never silently coerced into an existing
    label."""
    entry = LIFECYCLE_PRESENTATION.get(state or "")
    if entry:
        return entry[locale], entry["css"], entry[f"{locale}_tooltip"]
    fallback = (state or "").replace("_", " ") or ("Okänd" if locale == "sv" else "Unknown")
    return fallback, "unknown", ""


def public_signal_state(signal_id: int, status: str | None, locale: str = "sv") -> tuple[str, str | None]:
    """Public qualification without changing the stored domain status."""
    if signal_id == 6:
        return text("under_review", locale), (
            "Den här projektuppgiften granskas vidare. Ingen bygg- eller färdigställd-status kan utläsas här."
            if locale == "sv" else "This project record is under further review; it does not state construction or completion."
        )
    if status == "identified":
        return text("research_watch", locale), (
            "En källbaserad bevakningsuppgift, inte en bekräftelse på aktivt projekt eller aktuell fysisk installation."
            if locale == "sv" else "A source-backed watch item, not confirmation of an active project or current physical installation."
        )
    return status_view(status, locale)[0], None
