# RWI current architecture and data audit

**Audit date:** 2026-08-15  
**Scope:** repository and the live `data/runway_safe.db`, inspected read-only. No application, database, test, configuration, deployment, or data files were changed. This report is the sole output created.

## Executive summary

RWI is currently a small Python/FastAPI + SQLAlchemy + SQLite application whose practical public product is a database-driven static HTML export. Its active domain is `Airport`, `Runway`, `Installation`, `Incident`, `Signal`, and `Source`, with an unused acquisition/snapshot subsystem retained from the earlier design.

The live database contains real, source-linked research: 86 airports, 149 installations, 26 incidents, 68 signals, and 68 sources. The dominant installation inventory came from a manually captured FAA Tableau CSV export, then was enriched with FAA fact sheets and selected public/manual sources. USAspending has supplied 25 grant signals. There is no evidence that the current FAA Tableau *automated* acquisition/parser pipeline has populated production: the acquisition tables contain zero rows and the parser deliberately fails closed because it cannot parse an observed VizQL data payload.

The simplified architecture is fundamentally sound for a curated research site, but is not yet safe as a maintainable database system. The most serious finding is live-schema drift: three source foreign keys point to a dropped table named `sources_old`; SQLite foreign-key enforcement is off, and `PRAGMA foreign_key_check` reports violations for the affected records. Thus source links happen to resolve by matching integer IDs, but are not enforced by the database.

**Five discussions to have next:** (1) repair and govern schema migration/integrity, (2) define installation identity and historical-versus-current record rules, (3) decide a supported FAA inventory acquisition approach, (4) set a public source/provenance contract, and (5) define the editorial lifecycle for signals.

## 1. Current implementation and data flow

### Runtime entry points

- `app/main.py`: FastAPI development application. It provides Jinja server routes for dashboard, airports, signals, `/api/signals`, and `/health`.
- `scripts/init_db.py`: calls `Base.metadata.create_all`; it is not a migration system.
- `scripts/export_static_site.py`: calls `app.static_export.build.build_site`, normally writing/replacing `site/`.
- `app/seed.py` and many `scripts/*.py`: data-writing seed, import, enrichment, correction, and one-off migration commands. They were not run for this audit.
- `app/scripts/capture_faa_emas.py`: opt-in live Tableau capture command; requires both explicit network and database-write flags.

### Layers actually present

| Area | Current implementation |
|---|---|
| Database | SQLite at `data/runway_safe.db` by default; SQLAlchemy 2 declarative ORM. |
| Core domain | Airport, Runway, Installation, Incident, Signal, Source. |
| Acquisition | `PublishingSource`, `AcquisitionSource`, `AcquisitionRun`, `Snapshot`; generic service and FAA providers remain. |
| Importing | FAA CSV/manual Tableau export, NASR APT_ARS runway-end enrichment, USAspending, FAA AIP/IIJA PDFs, construction reports, fact sheets, plus one-off curated scripts. |
| Presentation | FastAPI/Jinja local app plus static Jinja export with handwritten CSS/JavaScript. |
| Configuration | `pydantic-settings`, `.env` support, default SQLite URL and FAA URLs. |
| Deployment | No Docker, CI workflow, hosting config, or deployment automation was found. README describes GitHub Pages/Netlify as possible static hosts only. |

The actual flow is: public source or manually researched input -> an importer/one-off script creates or updates rows -> SQLite -> static exporter queries ORM rows, turns them into deliberately limited view objects, writes HTML plus `data.json` -> static host. The deployed static site has no runtime database dependency. The FastAPI app is a separate local/dynamic view of the same DB.

`ARCHITECTURE.md` and `CURRENT_STATE.md` are historical, not current design authority: they claim entities and technologies such as RunwayEnd, EmasBed, Project, Document, Alembic, HTMX, and Bootstrap that are not present in the current runtime/repository.

## 2. Live database model

### Tables and counts

| Table | Rows | Purpose/status |
|---|---:|---|
| `airports` | 86 | Current core |
| `runways` | 59 | Current core |
| `installations` | 149 | Current core |
| `incidents` | 26 | Current core |
| `signals` | 68 | Current core |
| `sources` | 68 | Current core provenance rows |
| `publishing_sources` | 0 | Legacy/acquisition subsystem |
| `acquisition_sources` | 0 | Legacy/acquisition subsystem |
| `acquisition_runs` | 0 | Legacy/acquisition subsystem |
| `snapshots` | 0 | Legacy/acquisition subsystem |

The schema has `user_version=0`; no Alembic migration files/configuration were found. Production alterations are implemented by individual scripts using `ALTER TABLE` or table rebuilds.

### Core tables

- `airports`: integer PK; optional indexed IATA (3), ICAO (4), FAA (5), name (required/indexed), country (required/indexed), city/state/coordinates/website/notes; created/updated timestamps required. No uniqueness constraint exists on any identifier.
- `runways`: integer PK; required indexed `airport_id` FK; required indexed designation; optional length, width, surface, notes. No per-airport designation uniqueness constraint.
- `installations`: integer PK; required indexed `airport_id`; nullable indexed `runway_id` and `source_id`; optional runway end, type, manufacturer, product name, installation/replacement year, status, dimensions, FAA acceptance, notes, confirmed vendor, timestamps. No uniqueness constraint or explicit historical/current rule.
- `incidents`: integer PK; required airport and incident date/type/EMAS-engaged/implies-replacement; optional runway/source, aircraft/operator/injury/damage/summary/URLs/timestamps. `after_insert` automatically creates a high-confidence replacement Signal when `implies_replacement` is true.
- `signals`: integer PK; required airport, title, category, confidence; nullable runway/source/installation links; planning/procurement/target years, construction/completion dates, money fields, scores, supplier fields, public `source_notes`, private `notes` and `manual_year_estimate`, status and timestamps. There are no database enums/checks for categories, statuses, confidence, or score range.
- `sources`: integer PK; required title/type/reliability; optional publisher, URL, dates, document/page reference, summary, external ID and timestamps. Only `external_id` has a unique index (SQLite allows multiple NULLs).

Indexes are mostly single-column lookup indexes. Acquisition tables have their own keys, FKs, immutable snapshot/event hooks, a unique acquisition-source key, and a unique `(acquisition_source_id, sha256, byte_size)` snapshot identity.

### Critical schema mismatch

ORM metadata declares `Installation.source_id`, `Incident.source_id`, and `Signal.source_id` as FKs to `sources.id`. In the live SQLite DDL they instead reference `sources_old.id`, a table that no longer exists. The cause is visible in `scripts/add_rw_shareholder_letter_signals.py`: it renames `sources` to `sources_old`, recreates `sources`, copies data, then drops `sources_old`; SQLite rewrites dependent FKs during the rename.

Foreign keys are disabled in the database connection (`PRAGMA foreign_keys=0`). `PRAGMA foreign_key_check` reports source-key violations, including all installation rows. Application joins still resolve because copied `sources` retained the same integer IDs; that is an accidental compatibility condition, not referential integrity.

## 3. Data provenance and observed counts

No row has an explicit `seed/demo/imported_by` field, so record-level origin cannot always be proven. The following is observable from source type, source IDs, scripts, and content—not a claim that every item is fully verified.

### Airports

- Total: **86**.
- FAA Tableau/CSV-derived inventory: at least **~70 US airport rows** were created/updated by `import_faa_csv.py`; exact creation history cannot be reconstructed from the live DB.
- USAspending-created or matched: some of the remainder; the importer may create FAA-code or approximate-name airports, but origin is not stamped.
- International/manual additions: at least **16** are evidenced by the Brazil, Runway Safe letter, Gadelius, and other curated scripts.
- Uncertain: exact categories overlap and cannot be deterministically counted without an import ledger. One airport has no IATA, ICAO, or FAA code.

### Installations

- Total: **149**: 137 `EMASMAX`, 10 `greenEMAS`, 2 generic `EMAS`.
- FAA Tableau source (source 12): **69** installations. This represents the manually captured CSV import, not current live Tableau automation.
- FAA fact sheets (sources 56/57/68): **61** installations.
- News/manual research sources: **19** installations (Gadelius, shareholder material, airport/media sources, and Brazilian research).
- All 149 have a numeric `source_id` that matches a current `sources` ID, but the database does not enforce it because of the broken FK.
- 69 have no installation year; 97 have no runway link. These mean unknown/not linked, not known absence.

### Signals, incidents, and sources

- Signals: 68. 26 are automatic `replacement_after_incident` signals sourced to FAA Tableau; 25 are USAspending grants; 3 CIP; 2 shareholder letter; remaining 12 are individual researched/FAA/planning sources.
- Incidents: 26, all source 12 / FAA Tableau map, each with a generated replacement signal.
- Sources: 68. By type: USAspending 25; news 15; CIP 4; FAA fact sheet 3; IIJA 3; FAA construction report 2; master plan 2; shareholder newsletter 2; one each of AIP, ALP, environmental assessment, state plan, FAA Tableau, and several older free-text types (`Airport`, `Authority`, `Environmental`, `FAA`, `Master Plan`, `Procurement`, `Watchlist`).

USAspending and IIJA source IDs are explicit external IDs and can be rerun safely for the same upstream ID. FAA Tableau, fact-sheet, and manual data provenance is source-level, while the particular transformation/run history is generally not retained.

## 4. FAA Tableau and other import paths

### FAA Tableau

There are two materially different paths.

1. **Inventory actually in production:** `scripts/import_faa_csv.py` reads checked-in `emas_airports_usa.csv` and `emas_incidents_usa.csv`. It creates/fills airports by `ARPT_ID`, deduplicates an installation by `(airport_id, type)`, and incidents by `(airport_id, incident_date)`. It creates source 12, `faa_tableau`, labelled “verified CSV export.” Incident insert events generate Signals. Extracted airport fields include FAA ID, name, city, state and coordinates; installation input is only product/type and map region, not per-bed year/vendor/runway.
2. **Unpopulated intended acquisition path:** `FAATableauAcquisitionProvider` requests the FAA article/view, attempts to discover a Tableau session and POST a bootstrap payload. It stores an opaque snapshot through `AcquisitionService`, hashes it for deduplication, and records outcome/error metadata. It requires explicit user flags in its command. If client-side PreBootstrap JavaScript is required, it errors and documents a manual browser-DevTools bootstrap capture fallback. Diagnostic HTML may be written under `data/diagnostics/faa_tableau` only when requested.

The live DB has zero acquisition sources, runs, publishers, and snapshots. `FAAEmasSnapshotParser` explicitly says it has no observed VizQL record grammar and currently fails closed for HTML/JSON rather than extracting candidates. Therefore it extracts no installation rows, resolves no airports/runways, records no installation provenance, and has not replaced the CSV path. Its failure handling is explicit and tested, but it is not an operational importer.

FAA NASR `APT_ARS.csv` is separately downloaded by `scripts/import_faa_runway_ends.py`. It matches airport FAA/IATA/ICAO codes, normalizes leading zero runway designations, creates Runways as needed, and safely leaves a runway end blank when several ends are on one confirmed runway. Multiple different runways remain unlinked and are noted rather than guessed. The results are not source rows or acquisition snapshots; the provenance is appended into Installation notes.

### Other important imports

| Source | Mechanism and destination | Repeatability / caveat |
|---|---|---|
| USAspending | API JSON -> Source + high-confidence Signal; airport by LOC ID, then city/state, may create approximate airport. | Idempotent per `usaspending:<id>`; cross-source duplicates not prevented. |
| FAA AIP grants | Discover/PDF parse -> Source, keyword rule may create low Signal. | Documented dormant fallback; no external ID, so reruns/cross-source duplicates are possible. |
| FAA IIJA | Predictable annual PDFs -> Source + keyword Signal at existing FAA-code airport. | Idempotent by year/announcement/LOC ID; only matches tracked airports. |
| FAA construction report | Current PDF -> Source + new/high Signal or heuristic update to a candidate Signal. | Source external ID prevents source replay; matching is heuristic and can create duplicate signals on a tie. |
| FAA fact sheets | Hard-coded curated mappings -> update/create Installation years/details. | One-off, script-level guards; not a generic repeatable extraction pipeline. |
| Gadelius, Runway Safe letters, Brazil, airport/media | One-off curated scripts -> airports/installations/signals/sources. | Usually guard by URL/title or local key; manually maintained and heterogeneous. |

No general crawler/harvester for airport documents, NTSB/news/media, or Runway Safe shareholder material exists. The repository contains no imported `nasr`, `aip`, `iija`, or raw source payload archive beyond local CSV/PDF assets and optional diagnostics.

## 5. Source/provenance and public/private handling

Most public Installation and Signal views expose source title, publisher, type, URL and, for installations, published date. Public pages can therefore usually link an entity to a source. The link is weak where a Source URL is a publisher homepage, a source has no URL (two internal shareholder letters), notes carry extra citations not normalized as Sources, or a source type is one of the ungoverned legacy strings that renders as a raw badge.

The static-export allow-list is a good boundary. `_signal_view` intentionally excludes `Signal.notes` and `Signal.manual_year_estimate` from both HTML and `data.json`. Inspection of the emitted `site/data.json` confirms neither key exists in signal records. It does expose `source_notes`, `supplier_reason`, estimated values, likely supplier, and confirmed vendor. `Installation.notes` are intentionally public and appear in static HTML/JSON.

Risks:

- `source_notes` is public free text with no structural requirement for a source URL/page/citation; editorial material can be published if incorrectly placed there.
- `supplier_reason` is public despite being an analytical judgment field; it is not classified private in code.
- The FastAPI development routes read ORM signals directly: signal list searching includes private `notes`, and the signal detail template is documented as showing private notes/manual estimates. This is safe only while that dynamic application is not publicly deployed.
- Static export omits incident source links entirely: incidents expose only ID/date/type/EMAS flag, despite an incident source relationship in the DB.

## 6. Signal, installation, airport and runway semantics

A Signal means “something that could become a future EMAS order.” Categories observed include new installation, replacement, maintenance, study, replacement watch, and automatic post-incident replacement. Confidence and status are free text; the UI buckets confidence into three levels. Scores default to high=8, medium=6, low=3 for automatic rules, but are not constrained. `target_year`, `planning_year`, `procurement_year`, construction and completion fields coexist; public timeline uses target year then planning year, deliberately excluding private manual estimate.

Graduation is a manually invoked one-signal command. It creates an active Installation, copies airport/runway/source/confirmed vendor and **private Signal.notes** into public `Installation.notes`, then marks the signal completed and links it. This is a direct private-to-public leakage route unless the operator reviews the notes. Two completed signals are linked to installations in current data.

Installation represents an installed product record, but it does not encode whether records are separate beds, a system aggregate, replacement history, or duplicate corroboration. The base FAA CSV deduplicates by airport+type, while later fact-sheet and manual imports can add additional rows at the same airport/type/year. This supports multiple installations but does not provide a universal identity/dedup rule. `runway_id=NULL` means not confirmed/represented; `runway_end=NULL` with a runway link means the runway is known but end unknown. The model correctly permits both; neither must mean absent.

Airport matching is pragmatic: FAA CSV uses FAA ID; NASR checks FAA/IATA/ICAO; USAspending uses embedded code then city/state; one-off scripts generally use IATA. Codes are nullable/nonunique. International airports can exist with IATA/ICAO only. Renames have no aliases/history model. Runway normalization is present only in NASR enrichment/merge scripts, not a database constraint. The live DB has no duplicate IATA/ICAO or exact duplicate runway designation by airport, but the constraints do not prevent future duplicates.

## 7. Static public site

`build_site` destructively recreates its selected output directory, copies `style.css`/`watch.js`, renders index/about/glossary, airport list + 86 airport pages, signal list + 68 signal pages, and `data.json`. The current checked-in export contains 86 airports and 68 signals. JavaScript performs client-side signal filtering/grouping; no map library or map rendering code was found. The public information architecture is dashboard, airports, airport detail/timeline, signals, signal detail, glossary, and about.

Static output is self-contained after build. It does not query SQLite after deployment. Its freshness is entirely dependent on a manual export/deploy process; no deployment automation was found.

## 8. Data quality observations

- No exact duplicate IATA/ICAO codes or exact `(airport, designation)` runway duplicates were found in the live DB.
- 97/149 installations lack runway links and 69/149 lack installation year; these are materially important unknowns.
- Product labels are inconsistent by design/data history: `EMASMAX`, `greenEMAS`, and generic `EMAS` coexist, without an enum or normalization table.
- 26 incident dates came from month-year map data and are stored as day 1, documented in summaries; they must not be interpreted as exact dates.
- Source taxonomy is inconsistent (snake_case and free-text capitalization). Some legacy source URLs point only to publisher homepages.
- Airport codes lack uniqueness; one airport has no identifier, and USAspending can create approximate recipient-derived airport names.
- Multiple installation records at an airport may be genuine separate beds, historical replacements, or duplicate corroboration; current model/import contracts cannot distinguish them reliably.
- No relational or database-level orphan was found for airport/runway/installation links or signal-installation links, but source FKs are invalid as described above.

## 9. Tests and legacy architecture

There are 49 `test_*.py` files containing 296 `test_*` functions by static count. Strongest coverage is model contracts, importer parsers/guards, Tableau acquisition error paths, static-export views, signal filters/rules, and one-off data scripts. Tests use isolated DBs/mocks and cover implementation mechanics well.

Weak coverage: a production-schema migration/foreign-key integrity test; end-to-end real import -> production DB -> export validation; actual Tableau bootstrap parsing (no real parse grammar exists); data-quality reconciliation; public deployment; and provenance completeness. Many tests necessarily verify one-off scripts and exact field text, which is useful regression protection but implementation-specific rather than broad behavioral assurance.

Legacy remains as code only: `PublishingSource`, `AcquisitionSource`, `AcquisitionRun`, and `Snapshot` are present, referenced by models/services/capture tests, but unused by live data/runtime refresh. The old Observation/Verification/Fact/Intelligence/FindingType/Document/Project/RunwayEnd/EmasBed models are absent from `app.models` and no corresponding tables exist. Their design documents and old architecture/status documents remain. The acquisition subsystem is the only meaningful vestige connected to current code, not current data.

The current project should **not** rebuild the old multi-stage observation/verification/fact/intelligence workflow, persistent document framework, or broad acquisition abstraction merely because it existed in documents. The simple core model, explicit source row, and static publication boundary are appropriate for a small curated intelligence site.

## 10. Risks ranked

### CRITICAL

1. **Broken source foreign keys in the live schema.** All three main entity tables reference dropped `sources_old`; enforcement is off and integrity checks fail. This makes source provenance silently vulnerable to corruption and makes any future FK-enabled connection fail.
2. **Private notes can be made public by signal graduation.** `graduate_signal_to_installation.py` copies private `Signal.notes` into public Installation notes without an explicit review/sanitization boundary.

### HIGH

1. **FAA inventory update is not operationally automated.** Production data is a manually captured CSV import; the preserved Tableau acquisition/parser path has no live snapshots and cannot parse the intended payload.
2. **Installation identity is under-specified.** No universal uniqueness/dedup contract distinguishes beds, systems, replacements, and corroborating source records.
3. **Schema change process is unsafe.** Ad-hoc `ALTER TABLE`/rebuild scripts instead of a migration ledger created the current broken FKs; `create_all` cannot reconcile drift.

### MEDIUM

1. Public provenance is incomplete for some sources, all incident cards, and any claims placed solely in free-text notes.
2. Signals mix fact, inference, opportunity, source flag, and completed work in one extensible row; free-text categories/statuses make lifecycle reporting ambiguous.
3. Static publishing has no automated freshness, validation, or deploy pipeline.
4. Source taxonomy and airport identity matching are inconsistent; cross-source duplicate signals are possible.

### LOW

1. Historical documentation is materially stale and can mislead future work.
2. No backup automation is visible, though numerous point-in-time SQLite backups exist in `data/backups`.

## 11. What is good and gap analysis

Worth preserving: the compact six-entity public domain; source IDs on all current installations/signals/incidents; conservative NASR runway ambiguity handling; fail-closed Tableau parser; explicit network/write gates on live import commands; hash-based snapshot design (although unused); USAspending external-ID idempotency; and the static export’s allow-list exclusion of private Signal fields.

Against the stated goal—reliable installation intelligence plus future-project identification—the biggest gaps are: a safe database evolution/integrity path; a repeatable supported FAA installation refresh with transformation provenance; explicit installation identity/history rules; consistent public source evidence at claim level; a deliberate editorial/review lifecycle for signals; and operational static-site validation/deployment. These are gaps, not implementation instructions.

## 12. Recommended next investigation

### A. Must understand before changing anything

1. Reproduce and scope the live `sources_old` FK defect against a copy/backup, including whether the source rebuild affected every historical DB.
2. Establish the intended meaning and identity key of an Installation (bed, system, airport aggregate, replacement, or source assertion).
3. Decide whether FAA inventory is to remain controlled-manual CSV input or gain a supported acquisition/parser path.
4. Decide whether the FastAPI app is ever public; its private-note behavior must be assessed before that.

### B. Safe improvements likely worth doing

1. Replace stale documentation with a generated/maintained current-model reference after schema governance is decided.
2. Add read-only/CI validation for schema integrity, source completeness, duplicate candidates, and export allow-list behavior.
3. Formalize source types and public citation requirements.

### C. Things that can wait

1. Restoring the historic multi-stage intelligence architecture.
2. General-purpose document persistence/crawling infrastructure.
3. Sophisticated map or analytics layers before inventory/provenance reliability is settled.

### D. Questions requiring product/design decision

1. Is a Signal a lead, a source-backed project fact, or both—and which statuses are public?
2. Are Runway Safe shareholder letters permitted public evidence when the document itself has no public URL?
3. What is the public policy for supplier reasoning, estimates, and manually curated source notes?
4. What installation granularity and historical replacement representation should visitors see?
5. What refresh cadence and accountability are required for a public static site?
