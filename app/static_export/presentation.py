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
        "current_emas": "EMAS idag", "reviewed_emas": "Granskade EMAS-installationer",
        "faa_current_presence": "FAA-rapporterad aktuell förekomst",
        "projects": "Projekt och bevakning", "historical_installations": "Historiska installationsuppgifter",
        "research_watch": "Bevakas – forskningskandidat", "under_review": "Projektuppgift under granskning",
        "hero_statement": "Global intelligens om EMAS-installationer, projekt och bansäkerhet.",
        "view_all_signals": "Se alla signaler",
        "view_all_updates": "Visa alla uppdateringar",
        "current_status_unverified": "Aktuell EMAS-status ej verifierad",
        "current_status_unverified_detail": "Historiska källor visar tidigare EMAS-installation. Aktuell fysisk status granskas.",
    },
    "en": {
        "nav_overview": "Overview", "nav_signals": "Signals", "nav_airports": "Airports",
        "nav_glossary": "Glossary", "project_type": "Project type", "status": "Status",
        "confidence": "Confidence", "year": "Year", "score": "Analytical score",
        "source": "Source", "completed": "Completed",
        "runways": "Runways",
        "current_emas": "EMAS today", "reviewed_emas": "Reviewed EMAS installations",
        "faa_current_presence": "FAA-reported current presence",
        "projects": "Projects and watch items", "historical_installations": "Historical installation records",
        "research_watch": "Watch item – research candidate", "under_review": "Project record under review",
        "hero_statement": "Global intelligence on EMAS installations, projects and runway safety.",
        "view_all_signals": "View all signals",
        "view_all_updates": "View all updates",
        "current_status_unverified": "Current EMAS status not verified",
        "current_status_unverified_detail": "Historical sources show an earlier EMAS installation. Current physical status is under review.",
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
