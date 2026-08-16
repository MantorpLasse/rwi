# RWI repository archaeology / current-state inventory

**Date:** 2026-08-16  
**Method:** read-only inspection of repository files and `data/runway_safe.db`. No write-capable script was executed, no database writes were performed, and this report is the only file created.

## A. Executive summary

RWI is a simplified, source-oriented EMAS research site. Its live core is `Airport`, `Runway`, `Installation`, `Incident`, `Signal`, and `Source`, published by a static export. It contains valuable domain knowledge and real imported/curated data that should be preserved; the repository is not a blank foundation.

The most reusable assets are: the static exporter’s explicit public view boundary, the FAA CSV/import pipeline, NASR APT_ARS conservative runway/end enrichment, USAspending external-ID import, FAA PDF parsers, individual researched source records, and the compact core model. The significant foundation concern is live schema drift: source foreign keys still target the dropped table `sources_old`.

The recent architecture audits agree with the current repository on all independently rechecked material points: counts, inactive Tableau automation, missing Leaflet/map code, schema drift, and the public/private Signal boundary. No contradiction was found. One nuance: `PLAN_FORENKLING.md` is a valuable intent/history document, but parts describe desired/then-planned Tableau automation rather than a currently functioning production path.

## B. Current authoritative architecture

### Verified current runtime

- `app/main.py`: FastAPI/Jinja development application for dashboard, airports, signals, `/api/signals`, and health.
- `app/database.py` and `app/config.py`: SQLAlchemy against SQLite by default (`data/runway_safe.db`).
- `app/models/`: Airport/Runway, Installation, Incident, Signal, Source, plus acquisition-support models.
- `app/static_export/build.py`: the public publication path. It reads SQLite through the ORM and creates static HTML, CSS/JS copies, and `data.json`.
- `scripts/export_static_site.py`: export command wrapper.
- `scripts/*.py`: imports, targeted research entries/corrections, maintenance, and a small number of read-only diagnostics.

### Actual publication flow

```text
public source / manually captured file / curated research
  -> importer or one-off script
  -> SQLite core rows + Source
  -> static-export allow-list/view transformation
  -> generated HTML + data.json + CSS/JS
  -> static host
```

The current static site has no runtime SQLite dependency. No deployment automation/configuration, container build, CI workflow, or hosting configuration was found.

### Live data inventory

| Table | Current rows |
|---|---:|
| airports | 86 |
| runways | 59 |
| installations | 149 |
| incidents | 26 |
| signals | 68 |
| sources | 68 |
| publishing_sources / acquisition_sources / acquisition_runs / snapshots | 0 / 0 / 0 / 0 |

## C. Historical/obsolete architecture and documentation archaeology

### A — current or closest to authoritative

| Document | Content and current match | Preserved knowledge/decision |
|---|---|---|
| `README.md` | Current simplified entities, FastAPI/static export, active USAspending and dormant AIP distinction. Mostly matches code. | Operational entry points and intended use. |
| `PLAN_FORENKLING.md` | Simplification rationale, source research, FAA/NASR/USAspending/IIJA/construction investigations. Matches current direction; some prospective steps are now incomplete/outdated. | The decision not to restore a heavyweight workflow; source-specific research findings. |
| `DESIGN_BRIEF.md` | Static-site interaction/presentation specification. Matches exporter styling/labels at high level. | UX intent, confidence/category display rules. |
| `docs/acquisition/faa-emas-import-contract.md` | Intended controlled FAA acquisition constraints. Partially matches capture code; not live data path. | Safety assumptions and acquisition contract. |
| `docs/utredning_*.md` recent investigations | Source-specific factual research and decisions used by one-off scripts. Mixed current/historical status. | Keep as research evidence/context, not architecture authority. |
| `docs/CURRENT_ARCHITECTURE_AUDIT.md`, `docs/REPO_AUDIT_2026-08-16.md` | Current code/database audits. Independently corroborated for their main claims. | Current-state decision support. |

### B — historical but useful

| Document | Why historical | Useful retained knowledge |
|---|---|---|
| `AI_CONTEXT.md`, `VISION.md`, `GLOSSARY.md`, `CONSTITUTION.md` | Predate later simplification/details. | Product intent, source-first principles, terminology. |
| `docs/architecture/*.md` and `docs/decisions/0001-0003` | Describe the former Observation/Fact/Acquisition design. | Reasons and tradeoffs behind discarded complexity; acquisition immutability concepts remain useful. |
| `docs/session-summaries/*` | Point-in-time implementation records. | Chronology for imports/corrections. |
| `docs/data-sources/faa-emas.md` and FAA investigation docs | May describe earlier capture attempts. | FAA source behavior, limitations, manual extraction history. |

### C — obsolete/superseded as design authority

- `ARCHITECTURE.md`: claims RunwayEnd, EMAS Bed, Project, Document, Observation/Fact/Intelligence, Alembic, HTMX, and Bootstrap. These are absent from the current runtime/model/dependency set.
- `CURRENT_STATE.md`: dated 2026-07-17 and describes the old Sprint 1 architecture, Alembic, baseline migrations, and 24 tests; it does not describe current data/model state.
- `ROADMAP.md`: empty.
- Old observation/fact/acquisition design documents are not current implementation instructions.

### D — unknown / needs review

- `LESSONS_LEARNED.md` is a one-sentence claim about FAA approval/manufacturing; it has no stated source or current applicability.
- `grouping_mockup.html` and `design_mockup.html` are design artifacts, not runtime inputs.
- Swedish `utredning*` documents may contain important source research, but each needs source/date review before being treated as current factual authority.

## D. Script inventory

### Operational/import scripts worth retaining

| Script | Role, dependency, current status / rerun characteristics |
|---|---|
| `scripts/import_faa_csv.py` | Imports checked-in FAA Tableau CSVs into airports/installations/incidents; creates incident signals. Still compatible. Rerunnable by airport+type and airport+date, but that installation key collapses same-type systems. |
| `scripts/import_faa_runway_ends.py` | Live NASR APT_ARS fetch, EMAS filtering, runway/end linking. Still compatible; conservative ambiguity handling; needs network/write flags. No ingestion provenance row is created. |
| `scripts/import_usaspending_grants.py` | API grants -> Source + high Signal; stable `usaspending:<id>` dedup. Active/valuable. Can create approximate airport rows where no code is present. |
| `scripts/import_faa_iija_grants.py` | IIJA PDF parse -> source + keyword Signal; external-ID dedup. Still compatible/active source path. |
| `scripts/import_faa_aip_grants.py` | AIP PDF parse -> source + low keyword Signal. Explicitly dormant fallback since USAspending; lacks equivalent external-ID/idempotency protection. |
| `scripts/import_faa_construction_report.py` | Latest FAA PDF -> source and creates/heuristically updates a Signal. Still compatible; source replay protection exists, project match may be ambiguous. |
| `scripts/import_faa_fact_sheets_2011_2016.py`, `scripts/import_faa_fact_sheets_resten.py` | Curated FAA fact-sheet installation-year/data enrichment. Useful historical/domain source mappings; one-off rather than generic parsers. |
| `scripts/import_faa_tableau_gaps.py` | Curated gap enrichment for FAA-only airports using other sources. Useful evidence/mappings; historical one-off, no general Tableau parse. |
| `scripts/export_static_site.py` | Writes/replaces selected static output; current production build wrapper. |
| `scripts/init_db.py` | `create_all` initializer. Compatible with a new blank DB only; not a migration/drift repair tool. |
| `scripts/graduate_signal_to_installation.py` | Manual Signal -> Installation graduation. Works with current fields; not general idempotence (refuses completed signal). It copies private notes into public Installation notes. |
| `scripts/merge_duplicate_runways.py` | Merges normalized runway designations and repoints children. One-off remediation tool; only safe after reviewing candidates. |

### FAA/Tableau acquisition and diagnostics

| Script | Role / status |
|---|---|
| `app/scripts/capture_faa_emas.py` | Controlled Tableau bootstrap acquisition; requires explicit live-network/database-write flags. Acquisition model is intact, but live DB has no acquisition data and parser cannot interpret installation data. Retain for investigation, not current refresh operations. |
| `app/scripts/sanitize_tableau_har.py` | Sanitizes a manually captured HAR/diagnostic artifact. Useful for forensic/manual Tableau work; it is not an importer. |
| `scripts/compare_db_snapshots.py` | Read-only SQLite snapshot comparison. Useful diagnostic. |
| `scripts/explore_usaspending.py` | Read-only live API research tool; preserves no data. Useful source exploration. |
| `scripts/find_duplicate_airport_candidates.py` | Report-only candidate finder. Useful before manual identity changes. |

### Historical migrations, data repairs, and curated additions

The following are write-capable, narrow-scope historical scripts. They contain useful source/domain context but should not be casually rerun against current data; retain them as provenance/change history pending an explicit archive policy.

- **Schema/data-field changes:** `add_created_updated_timestamps.py`, `add_signal_source_notes_column.py`, `annotate_signal.py`, and the column helpers embedded in other scripts. These demonstrate the ad-hoc migration history.
- **Curated source/install additions:** `add_brazil_expansion.py`, `add_gadelius_greenemas_installations.py`, `add_rw_shareholder_letter_signals.py`, `add_iija_fy2026_known_grants.py`, `add_faa_national_emas_summary_source.py`, `add_zqn_wlg_official_source_confirmation.py`, `add_mdw_emas_bed_repairs_signal.py`.
- **Source links/follow-up:** `attach_elm_fy2011_grant_source.py`, `attach_source_to_signal.py`, `apply_svaga_poster_followup.py`, `confirm_phl_emas_completion.py`.
- **Corrections/renames/backfills:** `backfill_airport_codes.py`, `backfill_replacement_signal_titles.py`, `rename_mdw_runway_13c31c_to_13l31r.py`, `rename_sandiford_to_standiford.py`, `rename_usaspending_signal_titles.py`.
- **Public/private data cleanup:** `split_signal_notes_into_source_notes.py`, `remove_docs_and_field_leaks_from_notes.py`, `remove_source_id_leak_from_iija_notes.py`.
- **Targeted research enrichments:** `update_ase_runway_relocation_note.py`, `update_bos_emas_phase_details.py`, `update_cgh_emas_details.py`, `update_fty_emas_details.py`, `update_lex_emas_details.py`.

**OBSERVATION:** Most one-off scripts guard on a local URL/title/field condition and are often described as safe to re-run. That is not the same as a global idempotency contract. They can also encode assumptions about specific current IDs, titles, or text. Their main value is retained research provenance and business knowledge, not recurring operations.

## E. Data provenance map: 149 Installation rows

```text
FAA manually captured Tableau CSV
  emas_airports_usa.csv -> import_faa_csv.py -> 69 installations (source=faa_tableau)
  + incidents CSV -> 26 incidents -> 26 automatic replacement Signals

FAA 2011/2016 fact sheets
  curated mappings -> two fact-sheet import scripts -> 61 installations (sources=faa_fact_sheet)

Public/manual sources
  Brazil, Gadelius, Runway Safe shareholder letters, airport/news research,
  and targeted enrichments -> 19 installations (sources=news / related source)

NASR APT_ARS
  import_faa_runway_ends.py -> enriches Runway/runway_end association only;
  does not create a Source/Snapshot provenance record
```

**FACT:** Installation source-type totals are 69 `faa_tableau`, 61 `faa_fact_sheet`, and 19 `news`. The FAA Tableau CSV is still the primary imported base layer by direct source count, but it is not a complete physical-system representation.

**FACT:** Installation years primarily came from the FAA fact-sheet mappings and selected public/manual research. Of 149 installation rows, 80 have a year and 69 do not. Confirmed vendor exists on 14 rows, principally from Gadelius/Runway Safe/other explicit vendor sources. International records are created by Brazil expansion, Gadelius greenEMAS, Runway Safe shareholder-material, and related official-source follow-up scripts.

**FACT:** Manual correction/enrichment knowledge is distributed across the targeted scripts named above; it is not centralized in a provenance ledger. The database retains a source link and notes but not an `imported_by` or per-field derivation history.

## F. FAA/Tableau history

### What manual extraction achieved

**FACT:** Checked-in `emas_airports_usa.csv` and `emas_incidents_usa.csv` are the currently imported manual Tableau extraction artifacts. Airport CSV data provides `ARPT_ID`, airport name, city/state, latitude/longitude, type, and map-region context. Incident CSV data provides airport ID, incident dates/count and aggregate persons-saved information.

**FACT:** `import_faa_csv.py` uses FAA code to match/create Airport; it stores coordinates on Airport. It sets Installation type and an airport/type-level source association. It does not create per-system source locators, preserve a Tableau system count, installation year, manufacturer/vendor, distinct runway/end, or raw Tableau row payload.

### How data is collapsed

**FACT:** CSV import selects an existing installation by `(airport_id, type)`. A second FAA EMASMAX system at the same airport cannot produce a distinct base row through this key. This is exactly the wrong granularity for the real possibility of several systems at multiple runway ends.

**FACT:** NASR APT_ARS enrichment independently supplies `ARPT_ID`, `RWY_ID`, `RWY_END_ID`, and arresting device code. It can attach a known runway/end to an existing installation. If several ends are on one runway, it links the runway and deliberately leaves end unknown; several candidate runways stay unlinked with a note. It does not solve the fact that one airport-level base row may stand for multiple FAA systems.

### Can existing material improve the importer without another FAA investigation?

**OBSERVATION:** Existing CSV and scripts provide enough knowledge to preserve the fields they already contain, link known APT_ARS runway/end evidence more carefully, and avoid collapsing rows *if input rows have system-level identity*. They do not contain a reliable Tableau system-row identifier, system count mapping, or per-installation runway/end mapping for all current records. A complete per-system importer would require either a richer manual export/recording contract or a verified Tableau extraction investigation; the repository alone cannot manufacture lost fields.

## G. Installation identity findings

**FACT:** An Installation is nominally “what is installed today,” with optional `runway_id` and `runway_end`. The database permits many installations at an airport and many on one runway.

**OBSERVATION:** In practice, rows currently mean a mixture of:

- an airport/type aggregate imported from FAA Tableau;
- a fact-sheet-derived dated installation assertion;
- a manually researched specific project/system;
- an additional vendor-confirmed/greenEMAS record alongside a generic FAA row; or
- potentially a replacement/historical record.

**FACT:** 66 airports have multiple Installation rows; 52 installations are runway-linked; 32 have a runway end; 97 have no runway link; 23 contain an explicit NASR ambiguity/multiple-end note. JFK has two installation rows, but neither is runway/end linked. Cuyahoga contains an exact duplicate-looking `(airport,type,year)` group (EMASMAX, 2018, rows 144/145), which could be two real systems but cannot be proven from its current identity fields.

**FACT:** No unique system key, source-row locator, runway-end FK/table, historical interval, or installation lifecycle enum exists. `replacement_year`, free-text status, notes, and separate rows are the only existing historical/replacement constructs.

## H. Database integrity findings

**FACT:** No Alembic configuration/migrations were found. SQLite `PRAGMA foreign_keys` returns `0`. The current schema’s three source FKs are:

```text
installations.source_id -> sources_old.id
incidents.source_id     -> sources_old.id
signals.source_id       -> sources_old.id
```

`sources_old` does not exist. `PRAGMA foreign_key_check` returns **243** violations read-only, reproducing the architecture audit result.

**FACT:** The cause is visible in `add_rw_shareholder_letter_signals.py`: it renames `sources` to `sources_old`, rebuilds/copies `sources` to make URL nullable, then drops `sources_old`. SQLite retains rewritten dependent FK targets. Current app-level logical joins work only because copied Source IDs retained numeric values.

**FACT:** No logical orphan was observed for airport/runway/installation/signal links or for source IDs when joined manually against current `sources`; the fault is the declared relational constraint, not the current integer values.

## I. Public/private boundary findings

**FACT:** Static `_signal_view` excludes `Signal.notes` and `manual_year_estimate` from public HTML and `data.json`. It exports `source_notes`, `supplier_reason`, vendor details, values, status/category, airport/runway display fields, and source metadata. Static Installation view exports `Installation.notes`, confirmed vendor, type/year/end/status, and source metadata.

**FACT:** Current emitted `data.json` has no `notes` or `manual_year_estimate` key in Signal objects. It does expose internal entity IDs (`id`, `airport_id`, `installation_id`) but no source ID/external ID/database schema.

**RISK:** `source_notes` and `supplier_reason` are public free text with no structural citation enforcement. Prior cleanup scripts show that implementation details/source IDs did leak into these kinds of public notes before; future manual/script writers can repeat that mistake.

**RISK:** `graduate_signal_to_installation.py` copies private Signal.notes into public Installation.notes. No review/sanitization step exists in that transition.

**OBSERVATION:** Static incident presentation lacks source metadata despite Incident having a Source relationship. FastAPI development templates access ORM Signal fields directly and are not equivalent to the static privacy boundary.

## J. Reusable assets worth preserving

1. The compact six-entity public domain and static site strategy.
2. The explicit `_signal_view` allow-list for public export.
3. FAA CSV artifacts and import code as provenance of the current base layer.
4. NASR APT_ARS parsing and conservative runway/end ambiguity handling.
5. USAspending API acquisition/parser and external-ID idempotency approach.
6. AIP/IIJA/construction-report PDF parsers and their research documentation.
7. Existing Source records, external IDs, and source-specific script docstrings.
8. Acquisition snapshot/run model and hashing/immutability design, as an optional future asset—not evidence of a working live pipeline.
9. Read-only diagnostics: DB comparison, duplicate-airport candidate search, USAspending exploration.
10. The targeted correction scripts as an audit trail of data decisions.

## K. Files/scripts probably obsolete or requiring archival review

**Probably obsolete as active architecture authority:** `ARCHITECTURE.md`, `CURRENT_STATE.md`, empty `ROADMAP.md`, old Observation/Fact architecture documents/decision records.

**Historical one-off scripts needing archive/review rather than routine execution:** all narrow `add_*`, `update_*`, `rename_*`, `backfill_*`, `attach_*`, `remove_*`, `split_*`, `apply_*`, and `confirm_*` scripts listed in Section D. They should remain available as historical evidence until an explicit source/change-history policy is chosen.

**Dormant but retain:** `import_faa_aip_grants.py` is explicitly a fallback. **Investigate, do not remove:** Tableau capture/parser code, because it contains acquisition knowledge even though current parsing is not operational.

## L. Unknowns requiring investigation

1. What physical/business object an Installation must represent: individual bed, airport system, end, replacement, or historical assertion.
2. Whether the manually captured FAA CSV contains unretrieved/raw fields or came from an export that can be repeated at system granularity.
3. Whether 149 installation rows represent 122 FAA systems plus history/duplicates, and which rows correspond to current versus historical records.
4. The authoritative public policy for Runway Safe shareholder letters that lack public URLs.
5. Whether dynamic FastAPI is, or may become, publicly exposed.
6. A safe production schema migration/backup/restore procedure after the source FK finding.
7. Which historical research-document claims remain current enough to retain as public evidence.

## M. Recommended order of future work

1. Establish a verified database/schema baseline and explicit Installation identity semantics before adding data or automation.
2. Define a source/provenance contract, including public/private editorial boundaries and field authority.
3. Reconcile/refresh FAA installation coverage at real system/runway-end granularity using a controlled evidence contract.
4. Build repeatable discovery only after those foundations exist; AI/n8n should create evidence-backed candidates, not autonomous facts.
5. Add map/UI and multilingual work after physical installation accuracy and lifecycle semantics are trustworthy.

## Test result, git status, and scope confirmation

- **Tests:** not run. The request allows them only if read-only; normal pytest use can create cache/temp/test database files, so it was excluded under the strict no-write scope.
- **Database writes:** none performed.
- **Write-capable scripts:** none executed.
- **Git status after audit:** `docs/REPOSITORY_ARCHAEOLOGY.md` is the only file created in this audit. The pre-existing untracked `docs/CURRENT_ARCHITECTURE_AUDIT.md` and `docs/REPO_AUDIT_2026-08-16.md` remain untouched.

## RECOMMENDED NEXT STEP

1. Decide and document the authoritative meaning/identity of one Installation row.
2. Plan a safe, separately approved investigation/remediation of the live source foreign-key drift.
3. Define the evidence contract for a system-level FAA installation refresh before implementing automation.
