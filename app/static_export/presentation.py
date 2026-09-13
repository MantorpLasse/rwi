"""Public presentation strings and governed status/category views.

Domain values remain language-neutral. RWI HQ "Bilingual Static Site - Slice 2:
English Build + Language Switcher" mission: English is now a real, publicly
generated language (app.static_export.build's own per-locale tree generation),
not merely a prepared mapping - Swedish remains the default/unprefixed site.
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

        # ("RWI HQ Bilingual Static Site - Slice 2" mission) Remaining
        # template UI/chrome prose, migrated from hardcoded template text
        # into this same central catalog - never database/evidence text.
        "footer_generated": "Genererad",
        "footer_static_note": "statisk export, ingen server behövs för att läsa den.",
        "footer_about_link": "Om sidan & ansvarsfriskrivning →",
        "kpi_airports_monitored": "Övervakade flygplatser",
        "kpi_active_opportunities": "Aktiva möjligheter",
        "kpi_projects_in_motion": "Projekt i rörelse",
        "kpi_emas_installations": "EMAS-installationer",
        "col_airport": "Flygplats",
        "market_no_data": "Ingen marknadsdata ännu.",
        "of_activity": "av aktiviteten",
        "signal_count_word": "signaler",
        "no_signal_data": "Ingen signaldata ännu.",
        "no_activity_data": "Ingen aktivitetsdata ännu.",
        "airport_singular": "flygplats",
        "airport_plural": "flygplatser",
        "with_documented_emas": "med dokumenterad EMAS",
        "installed_label": "Installerat",
        "installed_word": "Installerad",
        "runway_prefix": "Bana",
        "confirmed_vendor_label": "Bekräftad leverantör",
        "confirmed_vendor_tooltip": (
            "Vi har hittat en källa som namnger exakt vilket företag som levererat eller "
            "ska leverera systemet."
        ),
        "source_type_prefix": "Källtyp",
        "watch_signal_label": "Bevaka signal",
        "emas_activity_over_time": "EMAS-aktivitet över tid",
        "trend_legend_installations": "Installationer (bekräftat)",
        "trend_legend_signals": "Signaler (prognos, planerat år)",
        "trend_chart_aria": "Nya installationer och signaler per år",
        "trend_cumulative_aria": "Kumulativt antal installationer per år",
        "trend_tooltip_installations": "Installationer",
        "trend_tooltip_signals_forecast": "Signaler (prognos)",
        "trend_tooltip_total_installed": "Totalt installerat",
        "trend_caption": (
            "{installations} installationer med känt installationsår (av {installation_total} totalt) "
            "och {signals} signaler med bedömt år (av {signal_total} totalt), {start_year}–{end_year}. "
            "Signaler är planerade/möjliga projekt, inte bekräftade installationer."
        ),
        "no_dated_data": "Ingen daterad installations- eller signaldata ännu.",
        "recently_updated": "Senast uppdaterat",
        "recent_updates_intro": (
            "Aktuella projekt- och källuppdateringar. Historiska incidenter och rena "
            "bevakningsuppgifter visas inte här."
        ),
        "col_type_upper": "TYP",
        "col_airport_upper": "FLYGPLATS",
        "col_description_upper": "BESKRIVNING",
        "col_date_upper": "DATUM",
        "changelog_empty": "Ändringsloggen startade {date}. Ändringar före dess finns inte registrerade.",
        "market_subtitle": (
            "Vad som händer i EMAS-marknaden just nu, byggt uteslutande på RWI:s publicerade "
            "signaler och deras redan granskade underlag."
        ),
        "active_opportunities_heading": "Aktuella möjligheter",
        "developing_watch_heading": "Under bevakning",
        "needs_research_heading": "Behöver research",
        "watch_column_label": "Bevaka",
        "watch_tooltip": (
            "Bevakning sparas bara i din webbläsare (localStorage) - fungerar inte mellan "
            "enheter och försvinner om du rensar webbläsardata."
        ),
        "lifecycle_column_label": "Läge",
        "no_active_opportunities": "Inga aktuella möjligheter är publicerade just nu.",
        "no_developing_watch_signals": "Inga signaler under bevakning just nu.",
        "stale_unresolved_explain": (
            "Källbelagda signaler där RWI ännu saknar färsk uppföljning - inte bedömda som "
            "avslutade eller irrelevanta, bara obekräftade sedan sist."
        ),
        "no_stale_signals": "Inga signaler behöver förnyad research just nu.",
        "by_project_type_heading": "Efter projekttyp",
        "active_plus_watch_eyebrow": "Aktuella möjligheter + Under bevakning",
        "no_categorizable_projects": "Inga aktuella eller bevakade projekt att kategorisera just nu.",
        "why_now_prefix": "Varför nu?",
        "airports_watched_subtitle": "{count} flygplatser bevakas.",
        "search_airports_placeholder": "Sök namn, kod eller land...",
        "col_code": "Kod",
        "col_name": "Namn",
        "col_country": "Land",
        "col_signals": "Signaler",
        "no_airports_matched": "Inga flygplatser matchade sökningen.",
        "signals_count_subtitle": "{count} signaler.",
        "search_signals_placeholder": "Sök signal eller flygplats...",
        "lifecycle_filter_title": "Visa signaler efter var i sin livscykel de befinner sig - se rubrikerna ovan.",
        "filter_current": "Aktuellt (möjligheter + under bevakning)",
        "filter_historical": "Historik",
        "filter_stale": "Behöver research",
        "filter_all": "Alla",
        "filter_all_statuses": "Alla statusar",
        "filter_all_countries": "Alla länder",
        "filter_watched_only": "Visa bara bevakade",
        "no_signals_registered": "Inga signaler registrerade.",
        "no_signals_matched": "Inga signaler matchade sökningen eller filtret.",
        "back_to_signals": "← Signaler",
        "details_heading": "Detaljer",
        "lifecycle_label_word": "Läge",
        "planning_year_label": "Planeringsår",
        "procurement_year_label": "Upphandlingsår",
        "runway_label_word": "Bana",
        "likely_supplier_label": "Trolig leverantör",
        "reasoning_label": "Motivering",
        "not_assessed": "Inte bedömd",
        "last_verified_source": "Senast verifierad källa",
        "open_source_link": "Öppna källa →",
        "no_public_link": "Ingen offentlig länk (t.ex. internt dokument).",
        "no_source_linked": "Ingen källa kopplad ännu.",
        "source_details_heading": "Detaljer från källan",
        "source_details_eyebrow": "Fritext (kostnad, datum m.m.) utan egna fält i databasen",
        "economy_heading": "Ekonomi",
        "grant_amount_label": "Bidragsbelopp",
        "total_project_budget_label": "Total projektbudget",
        "estimated_emas_share_label": "Bedömd EMAS-del",
        "see_installation_link": "→ Se installation",
        "stage_other_label": "Övrigt",
        "back_to_airports": "← Flygplatser",
        "evidence_strength_label": "Styrka i underlaget",
        "whats_happening_now": "Vad händer just nu",
        "more_below_under": "+{n} till nedan under \"{label}\".",
        "no_runway_data": "Ingen banuppgift registrerad.",
        "documented_emas_heading": "Dokumenterad EMAS",
        "installation_singular": "installation",
        "installation_plural": "installationer",
        "no_separate_later_verification": "Ingen separat senare verifiering registrerad.",
        "no_separate_verification_full": (
            "RWI har ingen separat verifiering av installationens senare förekomst. Den "
            "dokumenterade installationen visas nedan under \"{label}\"."
        ),
        "no_emas_or_verification": "Ingen EMAS-installation eller separat verifiering registrerad.",
        "unknown_type": "Okänd typ",
        "no_confirmed_runway_link": "Ingen bekräftad bankoppling",
        "no_confirmed_runway_tooltip": (
            "Källan pekar ut flera olika banor för denna installation - ingen enskild bana "
            "kan bekräftas, se detaljer nedan."
        ),
        "no_installations_registered": "Inga installationer registrerade.",
        "funding_and_grants_heading": "Finansiering och bidrag",
        "no_public_projects": "Inga offentliga projekt- eller bevakningsuppgifter registrerade.",
        "incidents_heading": "Incidenter",
        "col_date": "Datum",
        "col_type": "Typ",
        "yes_word": "Ja",
        "no_word": "Nej",
        "airport_website_link": "Flygplatsens webbplats →",
        "grant_prefix": "Bidrag",
        "and_conjunction": "och",
        "emas_engaged": "EMAS aktiverat",
        "emas_not_engaged": "EMAS inte aktiverat",
        "funding_caveat_text": (
            "Ett bidragsbelopp anger inte automatiskt ett EMAS-kontraktsvärde, en "
            "leverantörsintäkt, en total projektkostnad eller en genomförd upphandling - se "
            "\"Bedömd EMAS-del\" på signalens egen sida för vad som faktiskt är fastställt."
        ),
        "current_emas_provenance": "Fysisk placering enligt {basis}: bana {end}.",
        "current_emas_nasr_cycle_note": (
            " NASR-cykel {cycle}. Uppgiften beskriver förekomst vid banände, inte "
            "projektstatus eller fysisk historik."
        ),
        # om.html
        "about_page_title": "Om sidan & ansvarsfriskrivning",
        "about_this_site_heading": "OM DEN HÄR SIDAN",
        "about_this_site_body": (
            "Det här är ett privat, personligt projekt. Sidan är inte kopplad till, godkänd "
            "av, eller på annat sätt associerad med Runway Safe Group, FAA, eller någon av de "
            "flygplatser som nämns."
        ),
        "about_data_source_heading": "Varifrån datan kommer",
        "about_data_source_body": (
            "Informationen på den här sidan är sammanställd från offentligt tillgängliga "
            "källor: FAA:s officiella data och kartor, amerikanska myndigheters "
            "bidragsregister (t.ex. USAspending.gov), flygplatsers egna hemsidor och "
            "pressmeddelanden, samt nyhetsartiklar. Källan till varje enskild uppgift visas "
            "alltid tillsammans med informationen."
        ),
        "about_no_guarantee_heading": "Ingen garanti för korrekthet",
        "about_no_guarantee_body": (
            "Informationen kan innehålla fel, vara inaktuell, eller bygga på ofullständiga "
            "källor. \"Confidence\"-nivån (Hög/Medel/Låg) är en subjektiv bedömning av hur "
            "pålitlig en källa är, inte en garanti för att något faktiskt kommer att "
            "inträffa. Framtida projekt kan försenas, ändras, eller aldrig bli av."
        ),
        "about_not_advice_heading": "Inte investeringsrådgivning",
        "about_not_advice_body": (
            "Ingenting på den här sidan ska tolkas som en rekommendation att köpa, sälja, "
            "eller behålla värdepapper i något bolag. Gör alltid din egen bedömning och "
            "research innan du fattar ekonomiska beslut."
        ),
        "about_trademarks_heading": "Varumärken",
        "about_trademarks_body": (
            "EMAS, EMASMAX, greenEMAS och Runway Safe är varumärken som tillhör sina "
            "respektive ägare. De nämns här enbart i informationssyfte."
        ),
        # ordlista.html
        "glossary_page_title": "Ordlista",
        "glossary_subtitle": "Förklaringar till signaler och källor.",
        "glossary_emas_title": "EMAS (Engineered Material Arresting System)",
        "glossary_emas_body": (
            "Ett säkerhetssystem i slutet av en landningsbana, byggt av krossbart material. "
            "Om ett plan kör för långt vid landning eller start bromsas det säkert in i "
            "materialet istället för att fortsätta ut i terrängen."
        ),
        "glossary_emasmax_title": "EMASMAX",
        "glossary_emasmax_body": "Ett specifikt varumärke/produktlinje av EMAS, tillverkat av Runway Safe.",
        "glossary_greenemas_title": "greenEMAS",
        "glossary_greenemas_body": "En miljövänligare variant av EMAS-tekniken, även den från Runway Safe.",
        "glossary_rsa_title": "RSA (Runway Safety Area)",
        "glossary_rsa_body": (
            "Det säkerhetsområde som enligt lag ska finnas runt en landningsbana. Om platsen "
            "inte räcker till för det normala kravet kan EMAS användas som lösning istället."
        ),
        "glossary_resa_title": "RESA (Runway End Safety Area)",
        "glossary_resa_body": "Samma sak som RSA, men den beteckning som används internationellt (utanför USA).",
        "glossary_part139_title": "Part 139",
        "glossary_part139_body": (
            "Det amerikanska regelverket som styr säkerhetskrav på kommersiella "
            "flygplatser, inklusive kravet på RSA/EMAS."
        ),
        "glossary_notam_title": "NOTAM (Notice to Airmen)",
        "glossary_notam_body": (
            "Ett officiellt meddelande till piloter om tillfälliga förändringar på en "
            "flygplats, t.ex. en stängd bana under ett byggprojekt."
        ),
        "glossary_master_plan_title": "Master Plan",
        "glossary_master_plan_body": (
            "En flygplats långsiktiga utvecklingsplan, ofta 10-20 år framåt. Om EMAS nämns "
            "här är det oftast flera år innan något faktiskt byggs."
        ),
        "glossary_aip_title": "AIP (Airport Improvement Program)",
        "glossary_aip_body": (
            "Ett amerikanskt statligt bidragsprogram. Varje år delar staten ut pengar till "
            "flygplatser för säkerhets- och underhållsprojekt. Ett beviljat AIP-bidrag "
            "betyder att pengarna finns, men inte alltid att bygget redan startat."
        ),
        "glossary_iija_title": "IIJA-bidrag",
        "glossary_iija_body": (
            "En separat, större statlig bidragspott (del av en stor amerikansk "
            "infrastruktursatsning från 2021), fungerar ungefär som AIP men är en egen "
            "pengapåse."
        ),
        "glossary_cip_title": "CIP (Capital Improvement Plan)",
        "glossary_cip_body": (
            "En flygplats egen, mer kortsiktiga investeringslista (vanligtvis 3-5 år). Mer "
            "konkret än en Master Plan, men fortfarande en plan, inte ett färdigt bygge."
        ),
        "glossary_alp_title": "ALP (Airport Layout Plan)",
        "glossary_alp_body": (
            "En teknisk ritning över hur flygplatsen ser ut och ska se ut. Om EMAS finns "
            "med på en ny ALP-ritning är det ett tidigt tecken på att något är på gång."
        ),
        "glossary_usaspending_title": "USAspending-bidrag",
        "glossary_usaspending_body": (
            "Ett verkligt, redan beviljat federalt bidrag, hämtat direkt från den "
            "amerikanska statens egna offentliga utbetalningsregister. Högre konfidens än "
            "en plan, eftersom pengarna redan är tilldelade."
        ),
        "glossary_faa_data_title": "FAA:s kartdata / FAA:s faktablad",
        "glossary_faa_data_body": (
            "Officiell information direkt från den amerikanska luftfartsmyndigheten (FAA) "
            "om vad som redan är byggt."
        ),
        "glossary_confirmed_vendor_title": "Bekräftad leverantör",
        "glossary_confirmed_vendor_body": (
            "Vi har hittat en källa som namnger exakt vilket företag som levererat eller "
            "ska leverera systemet."
        ),
        "glossary_confidence_title": "Confidence (Hög/Medel/Låg)",
        "glossary_confidence_body": (
            "Hur säkra vi är på att informationen stämmer, baserat på hur bra källan är – "
            "inte hur säkert det är att projektet blir av."
        ),
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

        "footer_generated": "Generated",
        "footer_static_note": "static export, no server needed to read it.",
        "footer_about_link": "About & disclaimer →",
        "kpi_airports_monitored": "Airports monitored",
        "kpi_active_opportunities": "Active opportunities",
        "kpi_projects_in_motion": "Projects in motion",
        "kpi_emas_installations": "EMAS installations",
        "col_airport": "Airport",
        "market_no_data": "No market data yet.",
        "of_activity": "of activity",
        "signal_count_word": "signals",
        "no_signal_data": "No signal data yet.",
        "no_activity_data": "No activity data yet.",
        "airport_singular": "airport",
        "airport_plural": "airports",
        "with_documented_emas": "with documented EMAS",
        "installed_label": "Installed",
        "installed_word": "Installed",
        "runway_prefix": "Runway",
        "confirmed_vendor_label": "Confirmed vendor",
        "confirmed_vendor_tooltip": (
            "We have found a source that names exactly which company has supplied or will "
            "supply the system."
        ),
        "source_type_prefix": "Source type",
        "watch_signal_label": "Watch signal",
        "emas_activity_over_time": "EMAS activity over time",
        "trend_legend_installations": "Installations (confirmed)",
        "trend_legend_signals": "Signals (forecast, planned year)",
        "trend_chart_aria": "New installations and signals per year",
        "trend_cumulative_aria": "Cumulative number of installations per year",
        "trend_tooltip_installations": "Installations",
        "trend_tooltip_signals_forecast": "Signals (forecast)",
        "trend_tooltip_total_installed": "Total installed",
        "trend_caption": (
            "{installations} installations with a known installation year (of {installation_total} total) "
            "and {signals} signals with an assessed year (of {signal_total} total), {start_year}–{end_year}. "
            "Signals are planned/possible projects, not confirmed installations."
        ),
        "no_dated_data": "No dated installation or signal data yet.",
        "recently_updated": "Recently updated",
        "recent_updates_intro": (
            "Current project and source updates. Historical incidents and pure watch items "
            "are not shown here."
        ),
        "col_type_upper": "TYPE",
        "col_airport_upper": "AIRPORT",
        "col_description_upper": "DESCRIPTION",
        "col_date_upper": "DATE",
        "changelog_empty": "The changelog started on {date}. Changes before that are not recorded.",
        "market_subtitle": (
            "What's happening in the EMAS market right now, built exclusively from RWI's "
            "published signals and their already-reviewed evidence."
        ),
        "active_opportunities_heading": "Active opportunities",
        "developing_watch_heading": "Developing watch",
        "needs_research_heading": "Needs research",
        "watch_column_label": "Watch",
        "watch_tooltip": (
            "Watching is saved only in your browser (localStorage) - it does not work across "
            "devices and disappears if you clear your browser data."
        ),
        "lifecycle_column_label": "Lifecycle",
        "no_active_opportunities": "No active opportunities are published right now.",
        "no_developing_watch_signals": "No signals are under watch right now.",
        "stale_unresolved_explain": (
            "Source-backed signals for which RWI still lacks fresh follow-up - not judged as "
            "concluded or irrelevant, just unconfirmed since last checked."
        ),
        "no_stale_signals": "No signals currently need renewed research.",
        "by_project_type_heading": "By project type",
        "active_plus_watch_eyebrow": "Active opportunities + Developing watch",
        "no_categorizable_projects": "No active or watched projects to categorize right now.",
        "why_now_prefix": "Why now?",
        "airports_watched_subtitle": "{count} airports monitored.",
        "search_airports_placeholder": "Search name, code or country...",
        "col_code": "Code",
        "col_name": "Name",
        "col_country": "Country",
        "col_signals": "Signals",
        "no_airports_matched": "No airports matched the search.",
        "signals_count_subtitle": "{count} signals.",
        "search_signals_placeholder": "Search signal or airport...",
        "lifecycle_filter_title": "Show signals by where they are in their lifecycle - see the headings above.",
        "filter_current": "Current (opportunities + developing watch)",
        "filter_historical": "Historical",
        "filter_stale": "Needs research",
        "filter_all": "All",
        "filter_all_statuses": "All statuses",
        "filter_all_countries": "All countries",
        "filter_watched_only": "Show watched only",
        "no_signals_registered": "No signals registered.",
        "no_signals_matched": "No signals matched the search or filter.",
        "back_to_signals": "← Signals",
        "details_heading": "Details",
        "lifecycle_label_word": "Lifecycle",
        "planning_year_label": "Planning year",
        "procurement_year_label": "Procurement year",
        "runway_label_word": "Runway",
        "likely_supplier_label": "Likely supplier",
        "reasoning_label": "Reasoning",
        "not_assessed": "Not assessed",
        "last_verified_source": "Last verified source",
        "open_source_link": "Open source →",
        "no_public_link": "No public link (e.g. an internal document).",
        "no_source_linked": "No source linked yet.",
        "source_details_heading": "Details from the source",
        "source_details_eyebrow": "Free text (cost, date, etc.) without its own database fields",
        "economy_heading": "Economics",
        "grant_amount_label": "Grant amount",
        "total_project_budget_label": "Total project budget",
        "estimated_emas_share_label": "Estimated EMAS share",
        "see_installation_link": "→ See installation",
        "stage_other_label": "Other",
        "back_to_airports": "← Airports",
        "evidence_strength_label": "Strength of evidence",
        "whats_happening_now": "What's happening now",
        "more_below_under": "+{n} more below under \"{label}\".",
        "no_runway_data": "No runway data registered.",
        "documented_emas_heading": "Documented EMAS",
        "installation_singular": "installation",
        "installation_plural": "installations",
        "no_separate_later_verification": "No separate later verification registered.",
        "no_separate_verification_full": (
            "RWI has no separate verification of the installation's later presence. The "
            "documented installation is shown below under \"{label}\"."
        ),
        "no_emas_or_verification": "No EMAS installation or separate verification registered.",
        "unknown_type": "Unknown type",
        "no_confirmed_runway_link": "No confirmed runway link",
        "no_confirmed_runway_tooltip": (
            "The source points to several different runways for this installation - no "
            "single runway can be confirmed, see details below."
        ),
        "no_installations_registered": "No installations registered.",
        "funding_and_grants_heading": "Funding and grants",
        "no_public_projects": "No public project or watch items registered.",
        "incidents_heading": "Incidents",
        "col_date": "Date",
        "col_type": "Type",
        "yes_word": "Yes",
        "no_word": "No",
        "airport_website_link": "Airport website →",
        "grant_prefix": "Grant",
        "and_conjunction": "and",
        "emas_engaged": "EMAS engaged",
        "emas_not_engaged": "EMAS not engaged",
        "funding_caveat_text": (
            "A grant amount does not automatically indicate an EMAS contract value, a "
            "vendor's revenue, a total project cost, or a completed procurement - see "
            "\"Estimated EMAS share\" on the signal's own page for what is actually established."
        ),
        "current_emas_provenance": "Physical location per {basis}: runway {end}.",
        "current_emas_nasr_cycle_note": (
            " NASR cycle {cycle}. This record describes runway-end presence, not project "
            "status or physical history."
        ),
        # om.html
        "about_page_title": "About & disclaimer",
        "about_this_site_heading": "ABOUT THIS SITE",
        "about_this_site_body": (
            "This is a private, personal project. The site is not affiliated with, "
            "endorsed by, or otherwise associated with Runway Safe Group, the FAA, or any "
            "of the airports mentioned."
        ),
        "about_data_source_heading": "Where the data comes from",
        "about_data_source_body": (
            "The information on this site is compiled from publicly available sources: the "
            "FAA's official data and maps, US federal grant registers (e.g. USAspending.gov), "
            "airports' own websites and press releases, and news articles. The source of "
            "each individual piece of information is always shown alongside it."
        ),
        "about_no_guarantee_heading": "No guarantee of accuracy",
        "about_no_guarantee_body": (
            "The information may contain errors, be outdated, or rely on incomplete "
            "sources. The \"Confidence\" level (High/Medium/Low) is a subjective assessment "
            "of how reliable a source is, not a guarantee that something will actually "
            "happen. Future projects may be delayed, changed, or never happen at all."
        ),
        "about_not_advice_heading": "Not investment advice",
        "about_not_advice_body": (
            "Nothing on this site should be interpreted as a recommendation to buy, sell, "
            "or hold securities in any company. Always do your own assessment and research "
            "before making financial decisions."
        ),
        "about_trademarks_heading": "Trademarks",
        "about_trademarks_body": (
            "EMAS, EMASMAX, greenEMAS and Runway Safe are trademarks belonging to their "
            "respective owners. They are mentioned here for informational purposes only."
        ),
        # ordlista.html
        "glossary_page_title": "Glossary",
        "glossary_subtitle": "Explanations of signals and sources.",
        "glossary_emas_title": "EMAS (Engineered Material Arresting System)",
        "glossary_emas_body": (
            "A safety system at the end of a runway, built of crushable material. If an "
            "aircraft overruns during landing or takeoff, it is safely decelerated into the "
            "material instead of continuing into the terrain beyond."
        ),
        "glossary_emasmax_title": "EMASMAX",
        "glossary_emasmax_body": "A specific brand/product line of EMAS, manufactured by Runway Safe.",
        "glossary_greenemas_title": "greenEMAS",
        "glossary_greenemas_body": "A more environmentally friendly variant of EMAS technology, also from Runway Safe.",
        "glossary_rsa_title": "RSA (Runway Safety Area)",
        "glossary_rsa_body": (
            "The safety area that is legally required around a runway. If the site isn't "
            "large enough for the normal requirement, EMAS can be used as a solution instead."
        ),
        "glossary_resa_title": "RESA (Runway End Safety Area)",
        "glossary_resa_body": "The same thing as RSA, but the designation used internationally (outside the US).",
        "glossary_part139_title": "Part 139",
        "glossary_part139_body": (
            "The US regulatory framework governing safety requirements at commercial "
            "airports, including the RSA/EMAS requirement."
        ),
        "glossary_notam_title": "NOTAM (Notice to Airmen)",
        "glossary_notam_body": (
            "An official notice to pilots about temporary changes at an airport, e.g. a "
            "closed runway during a construction project."
        ),
        "glossary_master_plan_title": "Master Plan",
        "glossary_master_plan_body": (
            "An airport's long-term development plan, often 10-20 years ahead. If EMAS is "
            "mentioned here, actual construction is usually still several years away."
        ),
        "glossary_aip_title": "AIP (Airport Improvement Program)",
        "glossary_aip_body": (
            "A US federal grant program. Each year the government distributes money to "
            "airports for safety and maintenance projects. An awarded AIP grant means the "
            "money exists, but not always that construction has already started."
        ),
        "glossary_iija_title": "IIJA grant",
        "glossary_iija_body": (
            "A separate, larger federal grant pool (part of a large 2021 US infrastructure "
            "investment), functioning similarly to AIP but its own funding pot."
        ),
        "glossary_cip_title": "CIP (Capital Improvement Plan)",
        "glossary_cip_body": (
            "An airport's own, more short-term investment list (typically 3-5 years). More "
            "concrete than a Master Plan, but still a plan, not a completed build."
        ),
        "glossary_alp_title": "ALP (Airport Layout Plan)",
        "glossary_alp_body": (
            "A technical drawing of how the airport looks and is planned to look. If EMAS "
            "appears on a new ALP drawing, that's an early sign that something is underway."
        ),
        "glossary_usaspending_title": "USAspending grant",
        "glossary_usaspending_body": (
            "A real, already-awarded federal grant, taken directly from the US "
            "government's own public payment register. Higher confidence than a plan, since "
            "the money is already allocated."
        ),
        "glossary_faa_data_title": "FAA map data / FAA fact sheet",
        "glossary_faa_data_body": (
            "Official information directly from the US aviation authority (FAA) about what "
            "has already been built."
        ),
        "glossary_confirmed_vendor_title": "Confirmed vendor",
        "glossary_confirmed_vendor_body": (
            "We have found a source that names exactly which company has supplied or will "
            "supply the system."
        ),
        "glossary_confidence_title": "Confidence (High/Medium/Low)",
        "glossary_confidence_body": (
            "How confident we are that the information is correct, based on how good the "
            "source is - not how certain it is that the project will happen."
        ),
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
STATUS_ROLE_PRESENTATION = {
    "completed": {"sv": "Färdigställd", "en": "Completed"},
    "funded": {"sv": "Finansierad", "en": "Funded"},
    "design": {"sv": "Projektering", "en": "Design"},
    "procurement": {"sv": "Upphandling", "en": "Procurement"},
    "construction": {"sv": "Under byggnation", "en": "Under construction"},
    "review": {"sv": "Miljöprövning", "en": "Environmental review"},
    "planning": {"sv": "Planering", "en": "Planning"},
    "unknown": {"sv": "Ej klassificerad", "en": "Unclassified"},
}

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


def status_role_label(role: str, locale: str = "sv") -> str:
    entry = STATUS_ROLE_PRESENTATION.get(role)
    return entry[locale] if entry else role


# RWI HQ "Bilingual Static Site - Slice 2" mission: the current-EMAS
# protected-direction presentation family Slice 1 explicitly left
# Swedish-only (docs/product/public-emas-protected-direction-presentation.md).
# Same bilingual-dict-plus-accessor shape as everything else in this module;
# semantics unchanged from the original Swedish-only build.py constants.
CURRENT_EMAS_BASIS_PRESENTATION = {
    "reviewed": {"sv": "Granskad identitet", "en": "Reviewed identity"},
    "nasr": {"sv": "FAA NASR aktuell förekomst", "en": "FAA NASR current presence"},
}
# Separate from CURRENT_EMAS_BASIS_PRESENTATION (the badge text) because "FAA
# NASR" reads correctly capitalized in a badge but awkward mid-sentence if
# naively lowercased for prose - own phrasing per basis keeps both correct
# instead of deriving one from the other (unchanged reasoning from the
# original build.py constant).
CURRENT_EMAS_PROVENANCE_PHRASE_PRESENTATION = {
    "reviewed": {"sv": "granskad identitet", "en": "reviewed identity"},
    "nasr": {"sv": "FAA NASR", "en": "FAA NASR"},
}


def current_emas_basis_label(evidence_basis: str, locale: str = "sv") -> str:
    return CURRENT_EMAS_BASIS_PRESENTATION[evidence_basis][locale]


def current_emas_provenance_phrase(evidence_basis: str, locale: str = "sv") -> str:
    return CURRENT_EMAS_PROVENANCE_PHRASE_PRESENTATION[evidence_basis][locale]


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
