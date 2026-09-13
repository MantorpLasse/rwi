"""Public presentation strings and governed status/category views.

Domain values remain language-neutral. Swedish is the active locale; English is
a prepared presentation mapping only, not a public language switch.
"""
from __future__ import annotations

LOCALES = {
    "sv": {
        "nav_overview": "Översikt", "nav_signals": "Signaler", "nav_airports": "Flygplatser",
        "nav_market": "Marknadsläge",
        "nav_glossary": "Ordlista", "project_type": "Projekttyp", "status": "Status",
        "confidence": "Confidence", "year": "År", "score": "Analytisk score",
        "source": "Källa", "completed": "Färdigställd",
        "runways": "Banor",
        # ("RWI - Mission #8H" mission) Renamed from "EMAS idag"/"Historisk
        # EMAS-kontext": Mission #8G proved neither _current_emas_views()
        # pathway (reviewed physical identity, or a one-time FAA/NASR
        # import) actually certifies live/current presence in the ordinary
        # sense of "idag" ("today") - and that "historisk" risked reading
        # as "no longer present" for a documented, still-current
        # installation like London City's. The KEY names are unchanged
        # (minimizing blast radius - both remain single-use, see build.py's
        # own call sites) even though their VALUES no longer describe what
        # their old names implied.
        "current_emas": "Verifierad förekomst",
        "projects": "Projekt och bevakning", "historical_emas_context": "EMAS-installation",
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
        # ("RWI - Mission #23C" mission) Installed Base global footprint -
        # a second, independent map mode built from governed Installation
        # data, never Signal data. See _installed_base_global_view()'s own
        # docstring in build.py for the full invariant preserved here.
        "footprint_heading": "Global närvaro",
        "footprint_mode_installed": "Installerad bas",
        "footprint_mode_current": "Aktuell intelligens",
        "footprint_installed_explain": "Var Runway Safe/EMAS finns dokumenterat installerat, enligt tillgängliga källor.",
        "footprint_current_explain": "Publicerade signals — vad som är aktuellt nu, inte en fullständig installationsöversikt.",
        "footprint_disclosure": "Dokumenterad installation enligt tillgängliga källor. Ingen separat färskhetsverifiering görs globalt.",
        "footprint_empty": "Inga dokumenterade installationer i denna vy.",
        "footprint_vendor_unconfirmed": "Leverantör ej bekräftad",
        "footprint_year_unknown": "Installationsår okänt",
        "footprint_no_marker": "ingen kartposition ännu",
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
        # ("RWI - Mission #6" mission) Canonical public Score explanation -
        # ordlista.html's own #score entry, linked from every place a raw
        # numeric Score is shown (score_tooltip above stays the short
        # inline hover/link text; this is the fuller glossary body).
        "score_glossary_title": "Score",
        "score_glossary_body": (
            "Score är samma bedömning som Confidence, uttryckt som en siffra istället för Hög/Medel/Låg – "
            "hur starkt och tillförlitligt det underlag är som ligger bakom en signal. Ett högre Score betyder "
            "starkare, mer väldokumenterat underlag – inte att projektet är mer sannolikt att bli av, och inte "
            "sannolikheten att Runway Safe eller någon annan leverantör vinner ett eventuellt kontrakt. Score "
            "ersätter varken projektfas/status (var i processen projektet befinner sig) eller Läge (hur aktuell "
            "möjligheten bedöms vara just nu) – en historisk, redan avslutad händelse kan ha ett högt Score om "
            "underlaget är starkt, och en aktuell möjlighet kan ha ett bara måttligt Score om underlaget ännu är "
            "tunt. Saknas Score för en signal visas ett streck (–) istället för en påhittad siffra."
        ),
        # ("RWI HQ Bilingual Static Site - Slice 1" mission) Mission #7J
        # "Varför nu?" dynamic sentence templates - {vendor} is substituted
        # by build.py, never re-translated per airport/signal.
        "attention_vendor_confirmed": "{vendor} bekräftad som leverantör",
        "attention_grant_current": "Aktuellt federalt finansieringsunderlag finns",
        "attention_incident_unconfirmed": "En incident har registrerats, ersättning inte bekräftad",
    },
    "en": {
        "nav_overview": "Overview", "nav_signals": "Signals", "nav_airports": "Airports",
        "nav_market": "Market",
        "nav_glossary": "Glossary", "project_type": "Project type", "status": "Status",
        "confidence": "Confidence", "year": "Year", "score": "Analytical score",
        "source": "Source", "completed": "Completed",
        "runways": "Runways",
        "current_emas": "Verified presence",
        "projects": "Projects and watch items", "historical_emas_context": "EMAS installation",
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
        "footprint_heading": "Global footprint",
        "footprint_mode_installed": "Installed base",
        "footprint_mode_current": "Current intelligence",
        "footprint_installed_explain": "Where Runway Safe/EMAS is documented as installed, according to available sources.",
        "footprint_current_explain": "Published signals - what's current now, not a complete installed-base overview.",
        "footprint_disclosure": "Documented installation per available sources. No separate freshness verification is performed globally.",
        "footprint_empty": "No documented installations in this view.",
        "footprint_vendor_unconfirmed": "Vendor not confirmed",
        "footprint_year_unknown": "Installation year unknown",
        "footprint_no_marker": "no map position yet",
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
        "score_glossary_title": "Score",
        "score_glossary_body": (
            "Score is the same assessment as Confidence, expressed as a number instead of High/Medium/Low - "
            "how strong and reliable the evidence behind a signal is. A higher Score means stronger, "
            "better-documented evidence - not that the project is more likely to happen, and not the probability "
            "that Runway Safe or any other supplier wins an eventual contract. Score replaces neither project "
            "phase/status (where the project stands in the process) nor Lifecycle (how current the opportunity is "
            "judged to be right now) - a historical, already-concluded event can have a high Score if the evidence "
            "is strong, and a current opportunity can have only a moderate Score while the evidence is still thin. "
            "When no Score is recorded for a signal, a dash (-) is shown instead of a fabricated number."
        ),
        "attention_vendor_confirmed": "{vendor} confirmed as vendor",
        "attention_grant_current": "Current federal funding evidence exists",
        "attention_incident_unconfirmed": "An incident has been recorded, replacement not confirmed",
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


# RWI HQ "Bilingual Static Site — Slice 1: Localization Plumbing" mission:
# the remaining small, closed presentation vocabularies that used to live as
# Swedish-only mappings in app/static_export/build.py, migrated here to
# follow the exact same bilingual-dict-plus-accessor-function shape already
# proven by STATUS_PRESENTATION/LIFECYCLE_PRESENTATION above - one place,
# never duplicated between files. Canonical keys and non-language metadata
# (css class, glossary anchor) are preserved unchanged from their build.py
# originals; only the label/tooltip text itself gained an "en" counterpart,
# translated conservatively/directly from the existing Swedish wording -
# never database values, source titles, airport names, or evidence text,
# none of which pass through this module at all.

CATEGORY_PRESENTATION = {
    "new_installation": {"class": "new", "sv": "Ny installation", "en": "New installation"},
    "replacement": {"class": "replace", "sv": "Ersättning", "en": "Replacement"},
    "replacement_after_incident": {"class": "incident", "sv": "Efter incident", "en": "After incident"},
    "study": {"class": "study", "sv": "Studie", "en": "Study"},
    "potential_new_construction": {"class": "new", "sv": "Möjlig ny installation", "en": "Possible new installation"},
    "maintenance": {"class": "study", "sv": "Underhåll", "en": "Maintenance"},
    "replacement_watch": {"class": "replace", "sv": "Ersättning – bevakas", "en": "Replacement – watched"},
    "unknown": {"class": "study", "sv": "Ej klassificerad", "en": "Unclassified"},
}

CONFIDENCE_LABEL_PRESENTATION = {
    "high": {"sv": "Hög", "en": "High"},
    "med": {"sv": "Medel", "en": "Medium"},
    "low": {"sv": "Låg", "en": "Low"},
}

CLAIM_CATEGORY_PRESENTATION = {
    "explicit_document_fact": {"sv": "Bekräftat sakförhållande", "en": "Confirmed factual statement"},
    "procedural_request": {"sv": "Begäran/förfarande", "en": "Request/procedure"},
    "temporal_statement": {"sv": "Tidsuppgift", "en": "Timing statement"},
    "relationship": {"sv": "Ansvarig part", "en": "Responsible party"},
}

TEMPORAL_QUALIFIER_PRESENTATION = {
    "historical_fact": {"sv": "Historiskt förhållande", "en": "Historical fact"},
    "current_state_as_of_document_date": {
        "sv": "Aktuellt läge vid källans datum", "en": "Current state as of the source's date",
    },
    "planned_future_action": {"sv": "Planerad/kommande åtgärd", "en": "Planned/upcoming action"},
    "requested_pending_approval": {"sv": "Begärd, väntar godkännande", "en": "Requested, pending approval"},
    "completed": {"sv": "Genomförd", "en": "Completed"},
    "unknown": {"sv": "Okänt tidsläge", "en": "Unknown timing"},
}

# (label, anchor, sv_tooltip, en_tooltip) per Source.source_type - anchor is
# never translated (it is a URL fragment into ordlista.html, language-
# neutral by construction). Tooltip text is a direct, conservative
# translation of the existing Swedish wording, never a paraphrase that could
# drift from it independently.
SOURCE_TYPE_PRESENTATION = {
    "master_plan": {
        "sv": "Master Plan", "en": "Master Plan", "anchor": "master-plan",
        "sv_tooltip": "En flygplats långsiktiga utvecklingsplan, ofta 10-20 år framåt.",
        "en_tooltip": "An airport's long-term development plan, typically 10-20 years ahead.",
    },
    "Master Plan": {
        "sv": "Master Plan", "en": "Master Plan", "anchor": "master-plan",
        "sv_tooltip": "En flygplats långsiktiga utvecklingsplan, ofta 10-20 år framåt.",
        "en_tooltip": "An airport's long-term development plan, typically 10-20 years ahead.",
    },
    "aip_grant": {
        "sv": "AIP-bidrag", "en": "AIP grant", "anchor": "aip",
        "sv_tooltip": (
            "Ett amerikanskt statligt bidragsprogram. Ett beviljat AIP-bidrag betyder att "
            "pengarna finns, men inte alltid att bygget redan startat."
        ),
        "en_tooltip": (
            "A US federal grant program. An awarded AIP grant means the money exists, but not "
            "always that construction has already started."
        ),
    },
    "iija_grant": {
        "sv": "IIJA-bidrag", "en": "IIJA grant", "anchor": "iija-bidrag",
        "sv_tooltip": (
            "En separat, större statlig bidragspott, fungerar ungefär som AIP men är en "
            "egen pengapåse."
        ),
        "en_tooltip": (
            "A separate, larger federal grant pool, functioning similarly to AIP but its own "
            "funding pot."
        ),
    },
    "usaspending_grant": {
        "sv": "USAspending-bidrag", "en": "USAspending grant", "anchor": "usaspending-bidrag",
        "sv_tooltip": (
            "Ett verkligt, redan beviljat federalt bidrag, hämtat direkt från den "
            "amerikanska statens egna offentliga utbetalningsregister."
        ),
        "en_tooltip": (
            "A real, already-awarded federal grant, taken directly from the US government's "
            "own public payment register."
        ),
    },
    "faa_tableau": {
        "sv": "FAA:s kartdata", "en": "FAA map data", "anchor": "faa-kartdata",
        "sv_tooltip": (
            "Officiell information direkt från den amerikanska luftfartsmyndigheten "
            "(FAA) om vad som redan är byggt."
        ),
        "en_tooltip": (
            "Official information directly from the US aviation authority (FAA) about what "
            "has already been built."
        ),
    },
    "faa_fact_sheet": {
        "sv": "FAA:s faktablad", "en": "FAA fact sheet", "anchor": "faa-kartdata",
        "sv_tooltip": (
            "Officiell information direkt från den amerikanska luftfartsmyndigheten "
            "(FAA) om vad som redan är byggt."
        ),
        "en_tooltip": (
            "Official information directly from the US aviation authority (FAA) about what "
            "has already been built."
        ),
    },
    "CIP": {
        "sv": "CIP", "en": "CIP", "anchor": "cip",
        "sv_tooltip": "En flygplats egen, mer kortsiktiga investeringslista (vanligtvis 3-5 år).",
        "en_tooltip": "An airport's own, more short-term investment list (typically 3-5 years).",
    },
    "ALP": {
        "sv": "ALP", "en": "ALP", "anchor": "alp",
        "sv_tooltip": "En teknisk ritning över hur flygplatsen ser ut och ska se ut.",
        "en_tooltip": "A technical drawing of how the airport looks and is planned to look.",
    },
    "news": {"sv": "Nyhetskälla", "en": "News source", "anchor": None, "sv_tooltip": None, "en_tooltip": None},
    "shareholder_newsletter": {
        "sv": "Aktieägarbrev", "en": "Shareholder letter", "anchor": None, "sv_tooltip": None, "en_tooltip": None,
    },
    "faa_construction_report": {
        "sv": "FAA byggrapport", "en": "FAA construction report", "anchor": None,
        "sv_tooltip": None, "en_tooltip": None,
    },
    "environmental_assessment": {
        "sv": "Miljökonsekvensbeskrivning (EA)", "en": "Environmental assessment (EA)", "anchor": None,
        "sv_tooltip": None, "en_tooltip": None,
    },
    "state_aviation_system_plan": {
        "sv": "Delstatlig flygplatsplan", "en": "State aviation system plan", "anchor": None,
        "sv_tooltip": None, "en_tooltip": None,
    },
}

# Mission #7J "Varför nu?" status wording - deliberately a separate, shorter
# "in progress" phrasing from STATUS_PRESENTATION's own plain status labels
# above (e.g. "Upphandling pågår"/"Procurement underway" here vs.
# STATUS_PRESENTATION's plain "Upphandling"/"Procurement") - not a
# duplicate, a different grammatical context, kept intentionally distinct.
ATTENTION_STATUS_PRESENTATION = {
    "procurement": {"sv": "Upphandling pågår", "en": "Procurement underway"},
    "under construction": {"sv": "Byggnation pågår", "en": "Construction underway"},
    "design": {"sv": "Projektering pågår", "en": "Design underway"},
    "master_plan": {"sv": "Master Plan-fas", "en": "Master Plan phase"},
    "environmental_review": {"sv": "Miljöprövning pågår", "en": "Environmental review underway"},
    "cip": {"sv": "CIP-planering pågår", "en": "CIP planning underway"},
    "alp": {"sv": "ALP-planering pågår", "en": "ALP planning underway"},
    "funded": {"sv": "Finansiering beviljad", "en": "Funding approved"},
}


def text(key: str, locale: str = "sv") -> str:
    return LOCALES.get(locale, LOCALES["sv"]).get(key, key)


def category_view(value: str | None, locale: str = "sv") -> tuple[str, str]:
    """(label, css_class). Mirrors status_view()'s own fallback discipline:
    an unrecognized category value renders as its own raw text (never a
    translated placeholder), matching build.py's pre-existing behavior
    exactly."""
    entry = CATEGORY_PRESENTATION.get(value or "")
    if entry:
        return entry[locale], entry["class"]
    fallback = value or ("Okänd" if locale == "sv" else "Unclassified")
    return fallback, "study"


def confidence_label(level: str, locale: str = "sv") -> str:
    entry = CONFIDENCE_LABEL_PRESENTATION.get(level)
    return entry[locale] if entry else level


def claim_category_label(value: str, locale: str = "sv") -> str:
    entry = CLAIM_CATEGORY_PRESENTATION.get(value)
    return entry[locale] if entry else value


def temporal_qualifier_label(value: str, locale: str = "sv") -> str:
    entry = TEMPORAL_QUALIFIER_PRESENTATION.get(value)
    return entry[locale] if entry else value


def source_type_view(value: str | None, locale: str = "sv") -> tuple[str | None, str | None, str | None]:
    """(label, anchor, tooltip) - anchor/tooltip are None when this
    source_type has no glossary entry, so source_badge() falls back to a
    plain, unlinked badge instead of a dead link. Matches build.py's
    pre-existing _source_type_view() fallback exactly."""
    if not value:
        return None, None, None
    entry = SOURCE_TYPE_PRESENTATION.get(value)
    if not entry:
        return ("Övrig källa" if locale == "sv" else "Other source"), None, None
    return entry[locale], entry["anchor"], entry[f"{locale}_tooltip"]


def attention_status_wording(status: str, locale: str = "sv") -> "str | None":
    entry = ATTENTION_STATUS_PRESENTATION.get(status)
    return entry[locale] if entry else None

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
