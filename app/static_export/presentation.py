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
        "projects": "Projekt och bevakning", "historical_emas_context": "Historisk EMAS-kontext",
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
        # ("RWI - Juicy Design Mission #2" mission)
        "hero_headline_1": "Global intelligens.",
        "hero_headline_2": "Tidigare insikt.",
        "hero_headline_3": "Bansäkerhet börjar här.",
        "market_pulse": "Marknadsläge",
        "stage_distribution": "Projekt per stadium",
        "signals_snapshot": "Signalöversikt",
        # ("RWI - Juicy Design Mission #2 - V2.3" mission)
        "global_intelligence": "Global intelligens",
        "global_intelligence_eyebrow": "Var RWI ser verklig aktivitet just nu – landbaserad, inte en exakt karta",
        # ("RWI - Juicy Design Mission #2 - V2.4" mission) Shortened from
        # "Viktiga utvecklingar" - the longer heading wrapped onto two
        # lines at desktop width next to the new map card. Same
        # deterministic selection/real data underneath, heading text only.
        "important_developments": "Utveckling",
        "important_developments_eyebrow": "Projekt i mest avancerat skede just nu – byggnation, upphandling eller finansiering",
        "important_developments_empty": "Inga signaler befinner sig i byggnation, upphandling eller finansiering just nu.",
        # ("RWI - Juicy Design Mission #3" mission)
        "project_intelligence": "Projektintelligens",
        "project_intelligence_eyebrow": "Var i processen projektet befinner sig, baserat på källans egen statusuppgift",
        "location_intelligence": "Plats",
        "location_no_coordinates": "Inga verifierade koordinater registrerade för denna flygplats ännu.",
        "why_watching": "Varför RWI bevakar detta",
        # ("RWI - Juicy Design Mission #4" mission)
        "intelligence_history": "Intelligenshistorik",
        "intelligence_history_eyebrow": "Verkliga, källbelagda projekt- och finansieringshändelser i kronologisk ordning – aldrig ett antaget orsakssamband mellan poster",
        "intelligence_history_empty": "Inga daterade eller odaterade projekt- eller finansieringshändelser registrerade.",
        "date_missing_heading": "Utan säkert händelsedatum",
        "date_reason_no_event_date": "Källan är daterad, men själva händelsens tidpunkt är inte fastställd.",
        "date_reason_no_date": "Varken händelsens tidpunkt eller källans datum är kända.",
        "event_type_project": "Projekt",
        "event_type_funding": "Finansiering",
        "event_type_incident": "Incident",
        "current_physical_state": "Aktuellt tillstånd",
        "current_physical_state_no_year": "Inget installationsår fastställt – beskriver dagens fysiska läge, inte en daterad historisk händelse.",
        "evidence_source_only": "Källbelagd, men ingen granskad sakuppgift har transkriberats ännu – se länken till originalkällan.",
        # ("RWI - Juicy Design Mission #4 - Visual Polish Checkpoint" mission)
        "sources_one": "källa", "sources_many": "källor",
        "claims_one": "granskad sakuppgift", "claims_many": "granskade sakuppgifter",
        "evidence_finding": "Sakuppgift",
        "evidence_provenance_note": "Källbelagd, men ingen granskad sakuppgift har transkriberats ännu för källorna nedan – se respektive länk till originalkällan.",
    },
    "en": {
        "nav_overview": "Overview", "nav_signals": "Signals", "nav_airports": "Airports",
        "nav_glossary": "Glossary", "project_type": "Project type", "status": "Status",
        "confidence": "Confidence", "year": "Year", "score": "Analytical score",
        "source": "Source", "completed": "Completed",
        "runways": "Runways",
        "current_emas": "EMAS today",
        "projects": "Projects and watch items", "historical_emas_context": "Historical EMAS context",
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
        "hero_headline_1": "Global intelligence.",
        "hero_headline_2": "Earlier insight.",
        "hero_headline_3": "Runway safety starts here.",
        "market_pulse": "Market pulse",
        "stage_distribution": "Projects by stage",
        "signals_snapshot": "Signals snapshot",
        "global_intelligence": "Global intelligence",
        "global_intelligence_eyebrow": "Where RWI sees real activity right now - country-based, not a precise map",
        "important_developments": "Developments",
        "important_developments_eyebrow": "Projects in the most advanced stage right now - construction, procurement or funding",
        "important_developments_empty": "No signals are currently in construction, procurement or funding.",
        "project_intelligence": "Project intelligence",
        "project_intelligence_eyebrow": "Where the project stands in the process, based on the source's own status",
        "location_intelligence": "Location",
        "location_no_coordinates": "No verified coordinates are recorded for this airport yet.",
        "why_watching": "Why RWI is watching this",
        "intelligence_history": "Intelligence history",
        "intelligence_history_eyebrow": "Real, source-backed project and funding events in chronological order - never an assumed causal link between entries",
        "intelligence_history_empty": "No dated or undated project or funding events recorded.",
        "date_missing_heading": "Without a certain event date",
        "date_reason_no_event_date": "The source itself is dated, but the event's own timing is not established.",
        "date_reason_no_date": "Neither the event's timing nor the source's date is known.",
        "event_type_project": "Project",
        "event_type_funding": "Funding",
        "event_type_incident": "Incident",
        "current_physical_state": "Current physical state",
        "current_physical_state_no_year": "No installation year established - describes today's physical state, not a dated historical event.",
        "evidence_source_only": "Source-backed, but no reviewed claim has been transcribed yet - see the link to the original source.",
        "sources_one": "source", "sources_many": "sources",
        "claims_one": "reviewed claim", "claims_many": "reviewed claims",
        "evidence_finding": "Finding",
        "evidence_provenance_note": "Source-backed, but no reviewed claim has been transcribed yet for the sources below - see each one's own link to the original source.",
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
