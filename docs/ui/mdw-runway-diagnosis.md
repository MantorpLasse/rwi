# MDW "Banor" runway-display diagnosis (read-only)

Diagnostic only. No files, database rows, or git state were modified. All
queries below were run as read-only `SELECT`s against the real development
database (`data/runway_safe.db`) via a plain SQLAlchemy connection; no
session was committed.

## Summary

The public MDW airport page's "Banor" panel shows exactly one runway
(`13L/31R`, 1988 x 46 m) because that is the **only row that exists in the
`runways` table for MDW**. The four reviewed EMAS ends (`04R`, `13L`, `22L`,
`31R`) come from a separate table, `physical_installation_identities`, whose
rows are *by design* not linked to any `Runway` row (`runway_id` is `NULL`
on all four). The "Banor" panel and the "EMAS idag" panel read two
disconnected tables; both render exactly what's in their respective table.
There is no bug in the export or template code — the gap is that the
`runways` table was seeded as a lightweight, non-exhaustive placeholder
(documented as such in a prior commit, see below) and a later feature
(reviewed current-presence identities) was deliberately built to avoid
linking into it.

## A. Runway rows for MDW

**1 row.**

## B. Runway row detail

| id | airport_id | designation | length_m | width_m | surface | notes (translated summary) |
|---|---|---|---|---|---|---|
| 12 | 12 | `13L/31R` | 1988 | 46 | Asphalt/Concrete | `[2025-06-12] Rename: the airport's former 13L/31R closed permanently 2025-06-12. The same day, this runway — designated 13C/31C until then — was renamed 13L/31R, per a City of Chicago press release (June 2025, chicago.gov — exact document URL not available, only the domain given). This database's historical name until the correction: 13C/31C.` |

MDW's Airport row: `id=12, iata_code='MDW', icao_code='KMDW', faa_code='MDW', name='Chicago Midway International Airport'`.

## C. RunwayEnd rows for MDW

**There is no `RunwayEnd` table or model in this codebase.** `app/models/airport.py` defines only `Airport` and `Runway`; `Runway.designation` is a single free-text field holding both ends of one physical runway (e.g. `"13L/31R"`). Grepping the full schema (`SELECT name FROM sqlite_master WHERE type='table'`) confirms the table list has no `runway_ends`: `airports, publishing_sources, runways, acquisition_sources, acquisition_runs, snapshots, sources, installations, signals, incidents, source_assertions, physical_installation_identities, installation_assertion_links`.

So item C/D of the requested inspection don't apply to this schema as literally asked; the closest equivalent is the per-end data living in `physical_installation_identities.runway_end` and `installations.runway_end` (both free-text `VARCHAR`, not a dedicated end table).

## D. (see C — no RunwayEnd table exists)

## E. PhysicalInstallationIdentity rows for MDW

**4 rows, all reviewed, all `runway_id = NULL`:**

| id | airport_id | runway_id | runway_end | created_at |
|---|---|---|---|---|
| 3 | 12 | `NULL` | `04R` | 2026-08-16 07:58:59.752903 |
| 4 | 12 | `NULL` | `22L` | 2026-08-16 07:58:59.755194 |
| 5 | 12 | `NULL` | `13L` | 2026-08-16 07:58:59.756720 |
| 6 | 12 | `NULL` | `31R` | 2026-08-16 07:58:59.757488 |

This is not accidental. `scripts/apply_mdw_current_presence_pilot.py` (added in commit `c4e56f3`, "Add MDW current EMAS identity pilot") creates these explicitly as `create_physical_installation_identity(session, airport_id=airport.id, runway_id=None, runway_end=end)`. The accompanying report, `docs/domain/evidence-installation-identity-slice6g-mdw-current-presence-report.md`, states the rationale directly:

> "Runway IDs are deliberately null. Existing runway history makes a canonical FK more speculative than useful; the reviewed runway-end identity is safer."

That report also documents a full apply-verification: a pre-apply backup was taken (`data/backups/runway_safe-pre-evidence-identity-slice6g-mdw-current-presence-20260816-075710.db`), and a row-by-row comparison after apply confirmed **no changes to Airports (86), Runways (59), legacy Installations (149), Incidents (26), Signals (68), Sources (69), or SourceAssertions (221)** — only `physical_installation_identities` and `installation_assertion_links` gained rows. I independently re-confirmed the current live counts match: 86 airports, 59 runways, and (DB-wide) **no airport has more than 1 `Runway` row** (`MAX(count) = 1` across all airports). That last fact matters: this isn't an MDW-specific gap, it's the whole table's seeding pattern.

## F. Legacy Installation rows referring to MDW

**3 rows** (airport_id=12), none with `runway_id` populated:

| id | runway_id | runway_end | type | status | install_year | notes (summary) |
|---|---|---|---|---|---|---|
| 26 | `NULL` | `NULL` | greenEMAS | active | `NULL` | "FAA map region: Map - Main\nFAA arresting-system data lists multiple EMAS-equipped ends here: 04R/22L/04R, 04R/22L/22L, 13L/31R/13L, 13L/31R/31R." |
| 74 | `NULL` | `22L` | greenEMAS | active | 2014 | Separate, newer record beyond the generic FAA map entry (id 26); PRWeb press release: first bed complete Nov 2014 on runway 22L, four beds total promised by end of 2016. |
| 109 | `NULL` | `NULL` | EMASMAX | active | 2006 | "2 systems (ESCO/EMASMAX product line specifically...)" per 2016 Fact Sheet; a footnote reference in the source document is flagged as undefined, not guessed at. |

Row 26 is exactly the source of the `04R/22L/04R, 04R/22L/22L, 13L/31R/13L, 13L/31R/31R` text quoted in the task brief — it's free text describing FAA arresting-system data that named ends across what are, physically, two different runways (`04R/22L` and `13L/31R`), which is why it was left as an unlinked, genuinely-ambiguous note by `scripts/import_faa_runway_ends.py`'s `enrich_installations()` (see commit `9112bac`) rather than linked to a single `Runway` row.

## G. What's exported publicly for MDW

Rebuilt the static site from scratch (`build_site()` against the live DB, not a cached export) and inspected the generated `airports/12.html` and `data.json`. The page renders **two separate cards**, sourced from two separate build.py fields:

- **"EMAS idag" card** (`airport.reviewed_identities`): shows all four reviewed ends as pills — `04R`, `13L`, `22L`, `31R`. This *is* present and correct on the public page; it is just a different section than "Banor".
- **"Banor" card** (`airport.runways`): shows the single seeded runway — `13L/31R`, `1988 × 46 m`.
- The historical-installations disclosure includes installation id 26's raw note verbatim ("FAA arresting-system data lists multiple EMAS-equipped ends here: 04R/22L/04R, 04R/22L/22L, 13L/31R/13L, 13L/31R/31R."), and installation 74's card shows a `Bana 22L` pill.

`data.json`'s per-airport runway array for MDW likewise contains exactly one entry (designation `13L/31R`, length 1988, width 46) — this is a straight mirror of what the HTML shows, not a separate bug.

## H. Exact template/build code deciding "Banor" content

`app/static_export/build.py`, in `_airport_view` (around line 620):

```python
runways=[
    SimpleNamespace(designation=r.designation, length_m=r.length_m, width_m=r.width_m)
    for r in airport.runways
],
```

This is a direct, unfiltered pass-through of the ORM relationship `Airport.runways` (all `Runway` rows with that `airport_id`). No dedup, no merge with `reviewed_identities` or `nasr_presence`, no filtering logic exists here — whatever is in the `runways` table for that airport is exactly what's exported.

`app/static_export/templates/airport_detail.html`, lines 164-170:

```jinja
<div class="card-header">Banor</div>
...
{% if airport.runways %}
{% for runway in airport.runways %}
  <strong class="mono">{{ runway.designation }}</strong><br>
  <span ...>{{ runway.length_m or "–" }} × {{ runway.width_m or "–" }} m</span>
...
```

This loops `airport.runways` with no additional logic — a faithful, correct rendering of whatever `build.py` handed it. Both are functioning exactly as designed.

## I. Why only 13L/31R renders

Because the `runways` table has exactly one row for MDW, and both the export code (H) and the template (H) render that table's contents verbatim with no merge against `physical_installation_identities` or `installations`. The second physical runway at MDW (`04R/22L`) has never had a `Runway` row created for it — not because of a bug, but because:

1. The whole `runways` table was seeded as a lightweight, **non-exhaustive**, one-row-per-airport placeholder — confirmed both by the DB-wide fact that no airport has more than one `Runway` row, and by commit `9112bac`'s own description of it as "hand-seeded placeholder Runway rows (one per airport, not exhaustive)".
2. The newer "reviewed current EMAS identity" feature (slice 6g, commit `c4e56f3`, applied to the live DB earlier today, 2026-08-16 07:58:59, per its own report) *deliberately* chose not to link or create `Runway` rows when recording current-presence evidence, reasoning that a canonical FK would be "more speculative than useful."
3. Commit `9112bac` (2026-07-25) already identified and partially addressed the general shape of this problem for *installations* (linking an `Installation.runway_id` when FAA data unambiguously resolves to one physical runway, and showing an explicit "Ingen bekräftad bankoppling" pill when it can't) — but that fix never touched, and wasn't intended to touch, the `runways` table's completeness itself.

## Root cause classification

**MIXED_CAUSE**, with two components that are really one coherent (if incomplete) design, not two independent bugs:

- **INTENTIONAL_CURRENT_MODEL_LIMITATION** (primary): the `runways` table is deliberately a non-exhaustive, one-row-per-airport placeholder, and the reviewed-current-identity feature deliberately avoids linking into it. "Banor" was deliberately kept as a pure passthrough of that placeholder table, kept separate from `reviewed_identities`/`nasr_presence`, specifically to avoid the two views contradicting each other (per `9112bac`'s commit message).
- **DATABASE_DATA_GAP** (secondary, a direct consequence of the above): MDW genuinely has two physical runways today, but only one has ever had a `Runway` row created for it. This isn't a data-entry mistake — it follows directly from decision (1) above — but the practical effect on the public page is a real gap: a reader sees "Banor: 13L/31R" and, separately, four reviewed EMAS ends, two of which (`04R`, `22L`) belong to a runway the page never names.

Explicitly ruled out: **EXPORT_LOGIC_BUG** and **TEMPLATE_RENDERING_BUG** — both `build.py` and `airport_detail.html` render the underlying tables exactly as intended, with no filtering, dedup, or off-by-one error found.

## Before or after the last known-good checkpoint (`b5215b0`)?

**Before.** Evidence:

- `git show b5215b0 -- app/static_export/templates/airport_detail.html` touches the left-column cards (added the "EMAS idag"/"Projekt" cards, restructured the historical-installations disclosure, etc.) but contains **no changes to the "Banor" block** — that block is untouched by the checkpoint commit.
- `build.py`'s `runways=[...]` passthrough and the general `Runway`-table seeding pattern trace back to commit `9112bac` (2026-07-25) and earlier, both well before the checkpoint.
- The four `physical_installation_identities` rows for MDW were created at `2026-08-16 07:58:59`, which is **before** the checkpoint commit's timestamp (`2026-08-16 12:41:13`) — the pilot that created them (commit `c4e56f3`, `07:58:59` DB timestamp matching its own documented apply time of `07:57:10`+) landed and was applied prior to the checkpoint.
- My own recent UI/polish work (uncommitted at the time of this diagnosis) touched only `app/static_export/static/style.css` and `app/static_export/templates/index.html` — the **dashboard** template and CSS — and never touched `airport_detail.html`, `build.py`'s runway logic, or the database.

So the exact behavior described in the task (only `13L/31R` under "Banor") was already present in the committed checkpoint and is unrelated to the recent dashboard UI work.

---

ROOT_CAUSE:
MIXED_CAUSE (INTENTIONAL_CURRENT_MODEL_LIMITATION — non-exhaustive `runways` seed table, reviewed-identity feature deliberately unlinked from it — plus the resulting DATABASE_DATA_GAP: MDW's second physical runway, 04R/22L, has no `Runway` row at all)

DATA_CHANGED_BY_RECENT_UI_WORK:
NO

SAFE_NEXT_ACTION:
Do not touch data or export/template logic yet. First get sign-off on intent: either (a) leave "Banor" as a strict physical-runway inventory and add a one-line note/link pointing readers from "Banor" to "EMAS idag" when reviewed ends reference a runway not listed there, or (b) deliberately create the missing `04R/22L` `Runway` row from the same governed FAA NASR evidence already used for the current-presence pilot (a new, narrow, dry-run-first script mirroring `scripts/apply_mdw_current_presence_pilot.py`'s pattern) and then decide whether `physical_installation_identities.runway_id` should be backfilled. Either path is a data/domain decision, not a UI fix, and should go through the same dry-run/backup/apply/verify discipline already used for the slice 6g pilot.

STOP.

## Follow-up: presentation-only "Banor" suppression

Per direct instruction, the public "Banor" section itself has since been
temporarily removed from `app/static_export/templates/airport_detail.html`
(and the corresponding unused `runways` field removed from
`app/static_export/build.py`'s public airport view / `data.json`) as a
narrow, presentation-only safety correction — no database rows, `Runway`
data, `PhysicalInstallationIdentity` data, or reconciliation semantics were
touched. "EMAS idag" (reviewed EMAS runway ends) is unaffected and continues
to show the governed data (MDW: `04R`, `13L`, `22L`, `31R`; CGF: `06`, `24`;
BGM: `Aktuell EMAS-status ej verifierad`).

TODO: Public runway inventory is intentionally suppressed until canonical
runway and runway-end coverage is governed and sufficiently complete.
