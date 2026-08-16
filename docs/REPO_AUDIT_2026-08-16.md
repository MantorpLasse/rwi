# RWI full repository audit / architecture review

**Date:** 2026-08-16  
**Method:** read-only inspection of repository code, checked-in static export, and `data/runway_safe.db`. No application code, schema, data, tests, configuration, importer, or deployment file was changed. This report is the sole file created.

## Audit legend

- **FACT** — directly confirmed from current code or the live local SQLite database.
- **OBSERVATION** — an inspected consequence, inconsistency, or limitation.
- **RISK** — a potential harmful consequence supported by the facts.
- **RECOMMENDATION** — a future decision/action, not implemented here.

## Executive conclusion

**FACT:** RWI is presently a deliberately compact Python/FastAPI/SQLAlchemy/SQLite research application whose production-facing artifact is a static Jinja export. Its operational core is `Airport`, `Runway`, `Installation`, `Incident`, `Signal`, and `Source`. The live database holds 86 airports, 59 runways, 149 installations, 26 incidents, 68 signals, and 68 sources.

**FACT:** The inventory includes real, source-linked data: 69 installations originate from a manually obtained FAA Tableau CSV export; 61 from FAA fact sheets; 19 from public/news/manual research sources. USAspending has created 25 grant signals. This is not demo-only data.

**FACT:** The current static site has no Leaflet dependency, map initialization, or map rendering code. The request’s statement that it already uses Leaflet is not supported by this repository or the checked-in static site.

**RISK:** The live schema has serious source-FK drift: `installations.source_id`, `incidents.source_id`, and `signals.source_id` reference a dropped SQLite table, `sources_old`, instead of `sources`. Foreign-key enforcement is disabled and `PRAGMA foreign_key_check` reports 243 violations. Integer IDs happen still to resolve in application joins, but provenance is not protected by the database.

**Verdict:** The simplified architecture is a good foundation for curated EMAS intelligence, but its operational database governance, installation granularity, and FAA refresh pipeline must be understood before new feature work.

## 1. Current system, compared with documentation

### Actual architecture

**FACT:** `app/main.py` is a FastAPI/Jinja local/dynamic app with dashboard, airport, signal, `/api/signals`, and `/health` routes. `app/database.py` creates a SQLAlchemy engine against the configured SQLite DB. `scripts/export_static_site.py` invokes `app.static_export.build.build_site`; this is the practical public-site build path.

**FACT:** The exporter loads ORM data, builds restricted view objects, renders `index`, `about`, glossary, airport list/detail, and signal list/detail HTML, copies `style.css` and `watch.js`, and creates `data.json`. The static site is self-contained after export and does not require SQLite at deployment/runtime.

**FACT:** There is no Docker configuration, GitHub Actions workflow, Netlify/Vercel config, container build, or deployment automation. README describes static hosting as a manual possibility.

**OBSERVATION:** `ARCHITECTURE.md` and `CURRENT_STATE.md` are historical. They describe RunwayEnd, EmasBed, Project, Document, Observation/Fact/Intelligence, Alembic, HTMX, and Bootstrap. Those are not current models/dependencies/runtime components. `PLAN_FORENKLING.md` and current code are materially closer to reality, though PLAN also describes an intended Tableau process that is not operational today.

### Current data flow

```text
Public source / manually captured file / researched document
    -> importer or one-off curated script
    -> SQLite rows (core model + Source)
    -> static-export view allow-list
    -> HTML pages + data.json + CSS/JS
    -> static host
```

**FACT:** Source acquisition, extraction, normalisation, and enrichment are often coupled inside individual scripts. There is no central repository layer, ingestion queue, review queue, or production import scheduler.

## 2. Current data model

### Tables actually in the live DB

| Table | Rows | Purpose |
|---|---:|---|
| `airports` | 86 | Airport identity/location/container. |
| `runways` | 59 | Optional physical-runway records. |
| `installations` | 149 | Existing EMAS/product installation records. |
| `incidents` | 26 | EMAS activation/runway-excursion events. |
| `signals` | 68 | Future opportunity/project/replacement research records. |
| `sources` | 68 | Public source/document/link metadata. |
| `publishing_sources` | 0 | Acquisition subsystem publisher metadata. |
| `acquisition_sources` | 0 | Acquisition feed configuration. |
| `acquisition_runs` | 0 | Intended acquisition audit trail. |
| `snapshots` | 0 | Intended raw payload archive. |

**FACT:** No Alembic migration configuration/files were found. The live DB `user_version` is 0. Schema evolution uses `create_all`, ad-hoc `ALTER TABLE`, and one-off table rebuild scripts.

### Core model details

| Model | Important fields / relationships | Authority, input and export |
|---|---|---|
| Airport | PK; optional IATA/ICAO/FAA codes; required name/country; city, state, coordinates, URL, notes. Has runways, installations, incidents, signals. | Imported/manually created. Airport notes are not exported by static views. No identifier is unique. |
| Runway | PK; required `airport_id`; designation, dimensions, surface, notes. Has installations, incidents, signals. | Created mainly by NASR enrichment. Runway object/export shows designation/dimensions only. |
| Installation | PK; required airport; optional runway/source; `runway_end`, type/product/manufacturer, install/replacement years, status, dimensions, FAA acceptance, notes, confirmed vendor. | Existing-installation assertion. Imported/curated. Static export exposes type, year, runway end, status, vendor, notes and source metadata. |
| Incident | Required airport/date/type/EMAS flag/replacement flag; optional runway/source, aircraft details, summary and URLs. | Imported FAA map incident assertion. It is exported in airport timeline/card form, but static incident views omit the source link. |
| Signal | Required airport/title/category/confidence; optional runway/source/installation; lifecycle dates, value/score/supplier fields, public `source_notes`, private `notes` and `manual_year_estimate`. | A source-backed opportunity/project/replacement assessment. Static export intentionally omits `notes` and `manual_year_estimate`, but exposes source notes and supplier reasoning. |
| Source | Required title/type/reliability; optional URL, publisher, dates, document/page ref, summary, external ID. | Provenance record. Source URL/title/type are normally public through Installation/Signal views. |

**FACT:** `Source.external_id` has a unique index. Core models otherwise rely on application-level lookup logic; there are no unique airport code, runway-per-airport, installation identity, category/status enum, or score-range constraints.

### Explicit model questions

**A. Can one Airport have multiple Runways?**  
**FACT:** Yes: `Airport.runways` is one-to-many. The data model permits unlimited runways. The current DB has runways at 59 airports; 27 airports have none.

**B. Can one Airport have multiple Installations?**  
**FACT:** Yes: `Airport.installations` is one-to-many. 66 airports currently have more than one installation.

**C. Can one Runway have multiple EMAS installations/runway-end installations?**  
**FACT:** Yes in the ORM schema: multiple Installation rows may reference one `runway_id`; no unique constraint prohibits it. **OBSERVATION:** no linked runway currently has more than one Installation, so this capability has not been exercised in the live data.

**D. Can the model represent two EMAS beds at JFK without ambiguity?**  
**OBSERVATION:** It can represent two rows connected to the same airport/runway and differentiated by `runway_end`, product/year/status/notes. It cannot robustly distinguish them if they share those values, because it has no bed/system identifier, source locator, installation identity key, or uniqueness rule. Current JFK has two Installation records, both unlinked to a runway/end, so the present data is ambiguous at runway-end level.

**E. How does Installation connect to runway/end?**  
**FACT:** nullable `runway_id -> runways.id` plus free-text nullable `runway_end`. NASR APT_ARS enrichment creates/fetches a normalized Runway, sets `runway_id`, and sets `runway_end` only for a unique end match. When both ends on one runway match, it links the runway but deliberately leaves `runway_end` blank.

**F. What happens when designations change?**  
**FACT:** No rename/alias/history model exists. A one-off runway merge/rename script exists, with normalized leading-zero matching. Changes require a controlled manual script/update; historical designation continuity is not stored.

**G. Can Installation exist without a confirmed runway?**  
**FACT:** Yes. `runway_id` is nullable; 97 of 149 installations lack it. That represents unconfirmed/unrepresented linkage, not “known to have no runway.”

**H. How are historical installations represented?**  
**OBSERVATION:** There is no dedicated historical entity or retirement state. Separate Installation rows, `replacement_year`, source notes, and fact-sheet year rows are the available mechanisms. The schema has a free-text status but no enforced historical lifecycle.

**I. Can it distinguish existing/planned/under-construction/completed/replacement/retired?**  
**FACT:** Existing installations are `Installation` rows, usually status `active`. Planned/under-construction/replacement are normally Signals using free-text category/status. A confirmed built Signal can be manually graduated into an Installation and marked `completed`. **OBSERVATION:** retired/removed installations have no specific semantics; repairs/replacements may be a Signal, a new Installation, `replacement_year`, or notes. This is flexible but semantically non-uniform.

## 3. Data origin, importers, and repeatability

### Current source types

**FACT:** Live Source counts: `usaspending_grant` 25; `news` 15; `CIP` 4; `faa_fact_sheet` 3; `iija_grant` 3; `faa_construction_report` 2; `master_plan` 2; `shareholder_newsletter` 2; and one each of `ALP`, `Airport`, `Authority`, `Environmental`, `FAA`, `Master Plan`, `Procurement`, `Watchlist`, `aip_grant`, `environmental_assessment`, `faa_tableau`, and `state_aviation_system_plan`.

**OBSERVATION:** source-type taxonomy is mixed case/mixed style (`master_plan` and `Master Plan`, `FAA`, `Airport`, etc.). Public renderer only has friendly mappings for part of it.

### Import and curated script inventory

| Path/source | What it does | Source/provenance and rerun behavior |
|---|---|---|
| `import_faa_csv.py` / FAA Tableau CSV | Reads checked-in `emas_airports_usa.csv` and `emas_incidents_usa.csv`; creates/updates airports, one Installation per airport+type, and incidents. | Creates source 12. Repeatable by airport/type and airport/date, but its dedup key collapses same-type multiple systems at an airport. |
| `import_faa_runway_ends.py` / NASR APT_ARS | Downloads NASR package, filters EMAS arresting-system rows; creates/links runways/end data. | No Source/Snapshot row for this enrichment. Conservative ambiguity handling; rerunnable but external retrieval history is not recorded. |
| `import_usaspending_grants.py` | Paginated USAspending API data -> Source + high-confidence Signal; resolves airport by LOC ID or city/state. | Source URLs/dates/external IDs preserved. Idempotent per upstream award external ID, but not deduped across other sources. Can create approximate-name airports. |
| `import_faa_aip_grants.py` | Discovers/parses FAA AIP PDFs -> Source and keyword-triggered low Signal at existing FAA airports. | Documented dormant fallback. No external ID, so repeated/cross-source duplicates are possible. |
| `import_faa_iija_grants.py` | Fetches predictable IIJA PDFs -> Source and keyword Signal. | Uses external ID per year/announcement/LOC ID; only existing FAA-code airports match. |
| `import_faa_construction_report.py` | Discovers/parses latest FAA construction report -> source + creates/heuristically updates high Signal. | Source external ID avoids same report duplicate; candidate matching is heuristic and can create a new signal when ambiguous. |
| `import_faa_fact_sheets_2011_2016.py`, `..._resten.py` | Curated mappings from FAA fact sheets -> add/update installation years/details. | One-off guarded enrichment, not generic extraction. |
| `import_faa_tableau_gaps.py` | Curated Tableau inventory gap work. | Not a current live Tableau parser; manual/one-off path. |
| Gadelius/Brazil/shareholder/airport update scripts | Hand-curated sources, airports, installations, signals, vendor details/corrections. | Most use local URL/title/field guards; heterogeneous and manually maintained. |

**FACT:** Airport planning/procurement documents and news sources are implemented as manual/one-off source creation, not a general crawler. NTSB import is not implemented. FAA APT_RWY is not imported; the implemented NASR path is APT_ARS, specifically for arresting-system runway/end enrichment.

**RISK:** Many importers update existing records or append notes without a uniform field-level provenance model. A later importer can overwrite a value that originated in manual research if its lookup matches; safety depends on each script’s local conditional logic.

## 4. FAA EMAS data assessment

**FACT:** The live installation base layer was acquired by manual/browser export and committed as `emas_airports_usa.csv`, then loaded by `import_faa_csv.py`. The code calls source 12 “FAA EMAS Incidents and Installations map (verified CSV export).” The CSV importer extracts FAA airport ID, airport name, city/state, latitude/longitude, type and map region. The incident CSV extracts airport ID, month/year incidence dates, incident count and persons-saved aggregate.

**FACT:** The current automated Tableau acquisition code (`app/acquisition/faa_tableau.py`, `app/scripts/capture_faa_emas.py`) can attempt session/bootstrap retrieval and preserve a raw Snapshot through `AcquisitionService`. It requires explicit `--allow-live-network` and `--allow-database-write` flags. The live DB has zero acquisition sources/runs/snapshots.

**FACT:** `FAAEmasSnapshotParser` intentionally fails closed: it says no observed VizQL installation-mark grammar is available and does not extract installation candidates from JSON/HTML. It extracts no rows, no years, no vendors, no product/system counts, and resolves no airport/runway identity.

**OBSERVATION:** The automated capture/parser is therefore not the mechanism that created today’s database. The production inventory refresh path is controlled manual CSV capture/import, not a working end-to-end Tableau parser.

### Multi-system correctness

**FACT:** FAA-source base import deduplicates Installation by `(airport_id, type)`. It therefore intentionally treats a Tableau row as an airport/type aggregate rather than a runway-end/system identity.

**RISK:** FAA material’s real-world “systems at runway ends” granularity cannot be faithfully preserved by that import key. If one airport has several EMASMAX systems, one base row represents them all unless separate later enrichment rows happen to exist. It must not be interpreted as one FAA system per airport.

**FACT:** The live data has 69 FAA-Tableau-source installations, of which many have no runway/end. The NASR enrichment supplies 52 known runway links and 32 known ends across the full 149-installation dataset. It is authoritative for its APT_ARS `EMAS` rows at time of retrieval, but is not recorded as a Source row/snapshot in the DB.

**FACT:** Coordinates are stored on Airport, not Installation. Installation year is absent on 69 records. Type is present on all 149. `manufacturer` is never required; only 14 rows have a `confirmed_vendor`. The FAA CSV flow does not preserve system count, vendor/manufacturer, installation year, or a source-row locator.

## 5. Live data quality

### Quantified current state

| Measure | Count |
|---|---:|
| Airports / Runways / Installations / Incidents / Signals / Sources | 86 / 59 / 149 / 26 / 68 / 68 |
| Installations with known runway | 52 |
| Installations without runway | 97 |
| Installations with known runway end | 32 |
| Installations with known install year | 80 |
| Installations with product/type | 149 |
| Installations with confirmed vendor | 14 |
| Airports with multiple installations | 66 |
| Airports with no runway row | 27 |
| Runways with >1 linked installation | 0 |
| Installations with recorded ambiguous/multiple-end note | 23 |
| Exact duplicate-looking `(airport, type, install_year)` group | 1: Cuyahoga EMASMAX 2018, rows 144/145 |
| Logical airport/runway/signal/installations orphans | 0 |
| Core entity records with null source ID | 0 |
| Signal sources with no public URL | 2 |
| Source rows with URL/retrieval date/published date/external ID | 65 / 50 / 52 / 30 |

### Top data-quality problems

1. **CRITICAL — source FK drift.** Evidence: 243 `foreign_key_check` results, source FKs pointing at removed `sources_old`.
2. **HIGH — airport/type collapse of FAA systems.** Evidence: CSV importer dedup key omits runway/end/system locator.
3. **HIGH — incomplete runway association.** 97 installations are not linked to a Runway; only 32 identify an end.
4. **HIGH — no durable installation identity/history.** Separate beds, replacement history, and corroborating sources are hard to distinguish.
5. **HIGH — no retained raw/live Tableau payload in production.** Acquisition/snapshot row counts are zero.
6. **MEDIUM — 69 unknown installation years.** Unknown is distinct from absent, but public aggregation can imply completeness.
7. **MEDIUM — ungoverned source/category/status vocabulary.** Mixed values weaken filtering and reporting consistency.
8. **MEDIUM — approximate identity creation possible in USAspending.** Name can be recipient-derived where a code is absent.
9. **MEDIUM — FAA incident dates are month/year source data stored on day 1.** This is documented in summaries but unsuitable as exact-event date.
10. **MEDIUM — duplicate-looking Cuyahoga 2018 records.** They may be two legitimate systems; current identity fields cannot demonstrate it.

## 6. Provenance and public leakage

**FACT:** Static Signal view includes source title/publisher/type/URL and public `source_notes`; Installation view includes source title/publisher/type/URL/published date and Installation notes. All 149 installations, 68 signals, and 26 incidents have a logical source link. Current source joins resolve by numerical ID despite the broken FK.

**FACT:** Static `data.json` exposes entity IDs, airport IDs, installation IDs, source metadata, source URLs, source types, vendor/supplier values, scores, and public research text. It does not expose raw database schema, source ID, external ID, private `Signal.notes`, or `manual_year_estimate`.

**FACT:** `source_notes` exists on all 68 current signals; private `notes` is currently null on all signals; two signals have a manual-year estimate; 11 signals expose supplier reasoning. This means the static export currently has no Signal.notes leak, but it intentionally publishes `source_notes` and `supplier_reason`.

**RISK:** `source_notes` and `supplier_reason` are free-text public fields without structural proof/citation validation. Private implementation detail can reappear if someone places it there. This is a remaining leakage risk even after prior note-cleanup scripts.

**RISK:** `graduate_signal_to_installation.py` copies private Signal `notes` into public Installation `notes`. It is a direct private-to-public path unless the operator reviews the data. The current static export also has no public incident-source link, limiting traceability for incidents.

**OBSERVATION:** FastAPI dev templates read ORM Signal fields directly and documentation indicates they display private annotations; therefore that app must not be treated as a public replacement for the static export without re-evaluation.

## 7. Signal model and incident automation

### Category versus lifecycle

**FACT:** Signal `category` represents the kind of opportunity: observed live values include `new_installation`, `replacement`, `replacement_after_incident`, `maintenance`, `study`, and `replacement_watch`. `status` represents lifecycle-ish state: observed values include `identified`, `alp`, `cip`, `design`, `funded`, `under construction`, `environmental_review`, `procurement`, `master_plan`, and `completed`. They are free text, not enums.

**OBSERVATION:** “Ny installation” + “completed” is semantically valid: it means a signal classified as a prospective new installation has become confirmed/completed, typically with an Installation link. The static UI displays both category and status, which may look contradictory to a visitor because it lacks an explicit “historical signal” explanation.

**RECOMMENDATION:** Treat category as “what kind of project” and status as “where it is in the lifecycle” in future product language; decide whether completed Signals remain a public opportunity list or a visible historical handoff. Do not infer a model change from this observation alone.

### Incident -> Signal event

**FACT:** SQLAlchemy’s `Incident.after_insert` listener runs whenever an Incident row is inserted and `implies_replacement` is true. It directly inserts a Signal with the same airport/runway/source, category `replacement_after_incident`, confidence `high`, status `identified`, score 8.0, and the incident summary as private notes.

**FACT:** `import_faa_csv.py` first checks `(airport_id, incident_date)` and skips an existing incident; normal re-import therefore does not fire the insert event again. A manual/direct insertion of the same incident can create another Incident and automatic Signal because there is no DB unique constraint and no listener-level duplicate check.

**FACT:** Generated Signal has no `incident_id`, title includes airport/runway/date, and shared source/summary are the only trace to the incident. It is exported immediately in static builds; no human review gate exists.

**RISK:** The blanket “activation implies replacement” rule is deliberate and practical, but its generated signal cannot be mechanically linked back to one incident or deduplicated under all import paths.

## 8. Static-site architecture, scalability, and map readiness

**FACT:** The output includes full entity-derived data in `data.json` and generated individual HTML pages. Client JS provides localStorage “watch” stars, client-side table search/filtering/group expansion, and no server calls. `watch.js` has no map behavior. No `leaflet`, `L.map`, Mapbox, Google Maps, or other map reference was found in app or site code.

**FACT:** Private Signal notes/manual-year estimates are explicitly excluded by `_signal_view`; Installation notes are explicitly included. Export deletes/recreates its selected output directory, so a build must not target an unintended directory.

**OBSERVATION:** Hundreds to low thousands of records are plausible for the current static strategy: output is simple files and filtering occurs client-side. At much larger volume, repeated full page generation, a single large JSON payload, duplicate embedding of airport/signal data, and DOM table filtering will become limiting before SQLite itself does.

**Map/geospatial assessment:** Airport latitude/longitude exist and enable airport-level points. The model can distinguish planned Signals from existing Installations. It can attach installations to runways/ends where known, but 97 missing runway links and 117 missing end values mean an accurate per-installation/per-end map is not currently supportable for most records. It also lacks runway geometries, end coordinates, and a stable installation/system identity. Incidents are airport/runway-associated but not reliably end-associated. A future map is feasible from this foundation, but current data only supports an airport-level map with qualified installation detail.

## 9. Future discovery, AI, and n8n foundation

**FACT:** Reusable elements are Source records, source external IDs, URL/date metadata, `Signal` as a compact candidate/project entity, keyword flagging, USAspending and FAA PDF parsers, static export, and the acquisition/snapshot schema/service (currently unused).

**OBSERVATION:** The model can support a small background discovery layer if it creates a Source first and then an explicitly sourced Signal or proposes a human-reviewed update. It does not currently provide a first-class candidate/finding review state, source-to-many-claim relationship, raw artifact archive in use, or per-field provenance.

**RECOMMENDATION:** Keep automated discovery separate from automatic publication. Automation can collect, retrieve, deduplicate by upstream ID, extract candidate airport/runway/project evidence, and create low-confidence candidates/signals only where a deterministic rule supports that. Human review should remain required for airport resolution ambiguity, runway/end mapping ambiguity, vendor/manufacturer claims, project completion, duplicate/merge decisions, and conclusions drawn from narrative PDFs/news/AI extraction.

**RISK:** AI should not autonomously establish an installation as existing, select a runway end from ambiguous text, infer vendor/product/year, merge records, or mark a signal completed. Those actions need source evidence and human judgment because an error contaminates public intelligence.

**FACT:** An n8n/agent layer would currently need to write a Source, resolve Airport/Runway, create/update Signal/Installation, preserve external ID/retrieval details, and trigger export separately. There is no safe public ingestion API, queue, review endpoint, or idempotent universal write contract today.

## 10. Multilingual readiness

**FACT:** Swedish presentation text is hard-coded throughout static templates, `build.py` label dictionaries (`_CATEGORY`, confidence/status/source-type labels), CSS-adjacent markup, public JS aria labels, and many database titles/notes/source notes. English is also hard-coded in model/docstrings, source titles, database content, some UI labels (“Confidence”, “Score”), and public source material. The current result is mixed language.

**OBSERVATION:** Presentation labels are partly centralized in exporter mappings, which is helpful. Category/status stored values are mixed technical English/free text and then selectively translated; values and prose in the DB are not translation-ready. Static export does not prevent i18n but would need a language-aware render context/content policy; it does not create a special technical obstacle beyond build-time generation of language variants.

**RECOMMENDATION:** Before implementing i18n, decide which database fields are source-language evidence versus editorial/public copy, and centralize public labels. Do not translate source evidence as if it were canonical data.

## 11. Fifteen principal architectural/data risks

| Rank | Risk, evidence, and consequence |
|---|---|
| CRITICAL | **Broken source FKs.** Live DDL targets `sources_old`, foreign keys off, 243 integrity-check errors. Provenance can silently corrupt and FK-enabled future connections can fail. |
| CRITICAL | **Private-to-public graduation path.** Graduation copies Signal.notes to public Installation.notes. Internal assessment can publish accidentally. |
| HIGH | **FAA base importer collapses systems by airport/type.** Multi-end systems may be underrepresented or ambiguous. |
| HIGH | **No operational Tableau refresh/parser.** Live acquisition/snapshot tables are empty and parser fails closed; inventory freshness depends on manual work. |
| HIGH | **No durable installation identity/history.** Cannot safely distinguish source corroboration, a bed, a system, a replacement, and a retired record. |
| HIGH | **Ad-hoc schema changes.** Table rebuild caused the FK defect; `create_all` cannot repair drift. |
| HIGH | **Runway/end coverage incomplete.** 97 unlinked and 117 end-unknown installations limit physical accuracy and map reliability. |
| MEDIUM | **Free-text taxonomy/lifecycle.** Source/category/status inconsistency makes filters, analytics, and automation brittle. |
| MEDIUM | **Incident signal lacks incident link/dedup guard.** Duplicate direct inserts produce duplicate public Signals. |
| MEDIUM | **Public free-text provenance.** `source_notes`/supplier reasoning can contain unsupported or private claims. |
| MEDIUM | **Importer field overwrite behavior varies.** There is no uniform authoritative-field/provenance policy. |
| MEDIUM | **Static deployment freshness unmanaged.** No automated validation, export, or deploy; public data can become stale. |
| MEDIUM | **Approximate airport identity.** USAspending fallback names may create unclear entity matching. |
| LOW | **Historical documents misdescribe current architecture.** New work can follow obsolete layers/tools. |
| LOW | **No deployment/backup automation.** Many point-in-time DB backups exist, but recovery and release process are manual. |

## 12. What is genuinely good

1. **The simplified core domain is appropriate.** Airport/Runway/Installation/Incident/Signal/Source is understandable for the stated research purpose.
2. **Static export is a strong publication boundary.** It is inexpensive, portable, and has no deployed database dependency.
3. **Private Signal fields are deliberately excluded from static views.** This is a real and tested design boundary.
4. **NASR enrichment is conservative.** It leaves ambiguity unresolved rather than guessing an end.
5. **FAA Tableau parser fails closed.** It does not invent a parser for unknown VizQL payloads.
6. **Live-network/live-write import flags are explicit.** Sensitive capture/import commands require deliberate opt-in.
7. **USAspending external IDs give real replay safety.** The strongest importer has stable upstream provenance/deduplication.
8. **Acquisition snapshots are conceptually robust.** Hashing, immutable snapshots/runs, and status tracking are useful if activated purposefully.
9. **Incident replacement logic reflects a useful business rule.** It captures an important signal with minimal process overhead.
10. **The export UI is scoped and readable.** It provides signal grouping/search/watch state without a large frontend stack.

**OBSERVATION:** The acquisition subsystem, parser tests, import parsers, static exporter, and thorough one-off correction tests are worth retaining as assets. They should not be mistaken for a reason to restore the former Observation -> Verification -> Fact -> Intelligence application architecture.

## 13. Recommended next phase (not implementation)

### Phase 1 — data correctness and operating baseline

**RECOMMENDATION:** First resolve the schema integrity/migration situation on a safe copy and establish a production DB schema contract. Then define what one Installation row means and how historical/replacement systems are represented. These are prerequisites for all later automation.

### Phase 2 — provenance and existing FAA coverage

**RECOMMENDATION:** Define source/citation requirements, distinguish public sourced notes from private editorial notes, and ensure incidents can expose their source. Establish a controlled FAA inventory refresh process that preserves raw input, field provenance, system counts, and known runway/end detail without collapsing multi-system airports.

### Phase 3 — runway/end correctness and review

**RECOMMENDATION:** Continue conservative NASR enrichment, record its retrieval provenance, and create a manual resolution process for ambiguous associations/renamed runways rather than guessing.

### Phase 4 — discovery with human review

**RECOMMENDATION:** Expand repeatable sources (USAspending, IIJA, construction reports, planning documents) with source-first idempotent discovery. Make AI/n8n produce evidence-backed candidates, not autonomous public facts. Keep human confirmation for semantic decisions.

### Phase 5 — map/UI and multilingual publication

**RECOMMENDATION:** Once installation identity and physical linkage are reliable, build map views around airport points first, then end-level geometry/data. Centralize presentation text and decide English/Swedish editorial policy before multilingual static pages.

## Audit verdict

| Area | Score | Explanation |
|---|---:|---|
| Current architecture health | **6/10** | The compact core and static approach are sound, but schema drift and mixed operational layers materially reduce health. |
| Current data quality | **5/10** | Significant real/source-linked data exists; physical installation granularity, runway/end coverage, duplicate interpretation, and source FK integrity are weak. |
| Import robustness | **5/10** | USAspending is solid; many paths are manually curated/one-off, FAA Tableau automation is non-operational, and field authority is not uniform. |
| Source provenance | **5/10** | Most records logically link to sources and public URLs, but FKs are broken, some URLs are absent/general, incidents lack public source display, and free text is weakly governed. |
| Future AI/n8n readiness | **4/10** | There are good reusable primitives, but no safe ingestion/review contract, active raw snapshot flow, or universal idempotency/provenance model. |
| UI/UX readiness | **6/10** | The static interface is clear and appropriately lightweight for current scale; no map/i18n and lifecycle/provenance presentation need future work. |

**Overall:** retain the simplified direction. Make data integrity, source evidence, and FAA installation granularity reliable before increasing discovery automation, map sophistication, or UI scope.
