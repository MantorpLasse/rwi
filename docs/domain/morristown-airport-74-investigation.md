# Morristown Airport (id 74) Investigation

**Read-only investigation. No code, database, or archive modification.**
Every fact below comes from the real, current development database
(inspected read-only), tracked repository code/scripts, and the
already-preserved NASR archive
(`data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip`). No live network
access was needed — repository-preserved evidence alone was sufficient to
reach a deterministic conclusion, exactly as in the prior Allegheny
investigation (see
[`allegheny-unresolved-airport-investigation.md`](allegheny-unresolved-airport-investigation.md)).

## 1. Current Airport 74 row

Confirmed against the real database before any other work:

- Branch: `main`, HEAD `728d9899f689e3ea7b7538a03654ea99250df5a2`

```
id:            74
name:          Town Of Morristown
faa_code:      MMU
iata_code:     MMU
icao_code:     KMMU
city:          Morristown
state_region:  New Jersey
country:       USA
latitude:      NULL
longitude:     NULL
website_url:   NULL
notes:         "Name approximated from the USAspending grant recipient;
               no FAA Loc ID was available in the award description.
               Verify/correct manually if you find the airport's real
               identifiers."
created_at:    2026-07-22 18:17:22.695770
updated_at:    2026-07-25 15:08:55.851244
```

DB path: `data/runway_safe.db`. Size `667648` bytes, confirmed identical
before and after this investigation (§13).

**Related rows** (all confirmed via direct read-only query):

- `Runway`: 2 rows — id 154 (`5/23`, 1828m × 46m, ASPH), id 155 (`13/31`,
  1218m × 46m, ASPH)
- `RunwayEnd`: 4 rows — `5`, `23` (on runway 154), `13`, `31` (on runway
  155)
- `Signal`: 1 row (id 47) — "USAspending grant — $5.8M, FY2025",
  `source_id=21`, `estimated_total_value_usd=5,756,758`
- `Source`: id 21, title `"USAspending grant: Town Of Morristown"`,
  `external_id="usaspending:ASST_NON_33400230712025_069"`
- `SourceAssertion`: id 84, `airport_id=74`, `assertion_type="project_construction"`,
  `parser_identifier="legacy-source-backfill-v1"` (created by
  `scripts/backfill_legacy_source_assertions.py`, not by the ingestion
  fix from the previous task)
- `PhysicalInstallationIdentity`: **0 rows**
- `Installation`: **0 rows**
- `Incident`: **0 rows**

## 2. Provenance

### USAspending source/grant

`Source` id 21 / `Signal` id 47, both created 2026-07-22 by
`scripts/import_usaspending_grants.py` (pre-fail-closed-fix version):

| Field | Value |
|---|---|
| Award ID | `33400230712025` |
| USAspending URL | `https://www.usaspending.gov/award/ASST_NON_33400230712025_069` |
| Awarding agency | Department of Transportation |
| Award amount | $5,756,758 |
| Published date | 2025-09-19 |

Grant description (preserved verbatim in `Source.summary`,
`Signal.source_notes`, and `SourceAssertion.raw_relevant_text`):

> "PURPOSE: CONSTRUCT/EXTEND SAFETY AREA. ... **THIS PROJECT CONSTRUCTS A
> RUNWAY 5/23 SAFETY AREA AT BOTH RUNWAY ENDS** to enhance safety. ...
> THIS GRANT FUNDS PHASE 11, WHICH CONSISTS OF PROCUREMENT OF ENGINEERED
> MATERIALS ARRESTING SYSTEM (EMAS) BLOCKS FOR THE RUNWAY 23 DEPARTURE
> SAFETY AREA. INTENDED BENEFICIARY: THIS GRANT WILL PROVIDE FEDERAL
> FUNDING FOR AIRPORTS ASSOCIATED WITH MORRISTOWN, NEW JERSEY."

### Historical fallback mechanism

This grant's description contains **no embedded FAA Loc ID** (no
`AIRPORT (XXX)` pattern), only the standard beneficiary sentence naming
`"MORRISTOWN, NEW JERSEY"`. At creation time (2026-07-22), no existing
`Airport` row had `city="Morristown"`/`state_region="New Jersey"`, so
`resolve_airport()`'s (now-removed) zero-match fallback created a new
`Airport` named after the grant's recipient — `grant.recipient_name.title()`.
The `Source.title` field independently confirms the exact recipient
string used: `"USAspending grant: Town Of Morristown"` — this is where
`Airport.name = "Town Of Morristown"` came from, **directly and
verifiably**, not inferred from the current identifiers. This is the
same code path, and the same bug, that separately created Airport 75
("Allegheny County Airport Authority") — see
[`docs/domain/usaspending-airport-resolution-fail-closed-report.md`](usaspending-airport-resolution-fail-closed-report.md).

### Identifier enrichment history

Two separate, later, unrelated scripts backfilled the row's identifiers —
**neither of them touched `name`**:

1. **`scripts/add_iija_fy2026_known_grants.py::ensure_morristown_faa_code()`**
   (its own docstring): *"scripts/import_usaspending_grants.py created
   this airport from a grant's recipient name/beneficiary sentence, with
   no Loc ID available at the time (see the airport's own notes). The
   IIJA finding confirms it as 'MMU' — filling this in lets
   faa_code-based matching find it going forward."* It matched the row by
   **`city="Morristown"` AND `state_region="New Jersey"`** (not by
   name), then set `faa_code="MMU"` — independently confirmed via a
   live-verified FAA IIJA Announcement 6 (FY2026) PDF naming "MMU
   (Morristown)". This matches the row's `updated_at` timestamp
   (2026-07-25, three days after creation).
2. **`scripts/backfill_airport_codes.py`** — a hand-verified allowlist
   (`VERIFIED_CODES["MMU"] = ("MMU", "KMMU")`, comment: `# Morristown
   Municipal, NJ`) that only fires once `faa_code` is already present,
   backfilling `iata_code="MMU"` and `icao_code="KMMU"`.

**All three identifiers (`faa_code`, `iata_code`, `icao_code`) are
independently, deterministically correct** — none of them came from the
buggy name fallback; they came from a separately-verified FAA IIJA grant
PDF and a hand-checked identifier allowlist. Only `name` still carries
the original fallback's output, and `notes` still describes the
now-resolved "no Loc ID" state.

## 3. RWI evidence vs. NASR match

| Evidence | Source | Value |
|---|---|---|
| A. Recipient name (RWI, `Source.title`) | `import_usaspending_grants.py` (pre-fix) | "Town Of Morristown" |
| A. `faa_code` (RWI) | `add_iija_fy2026_known_grants.py`, verified via live FAA IIJA PDF | `MMU` |
| A. `iata_code`/`icao_code` (RWI) | `backfill_airport_codes.py`, hand-verified allowlist | `MMU` / `KMMU` |
| B. `ARPT_ID` (NASR `APT_BASE.csv`) | preserved 2026-08-06 archive | `MMU` |
| B. `ICAO_ID` (NASR) | preserved archive | `KMMU` |
| B. `ARPT_NAME` (NASR) | preserved archive | `MORRISTOWN MUNI` |
| B. `CITY`/`STATE_CODE` (NASR) | preserved archive | `MORRISTOWN` / `NJ` |
| B. `OWNERSHIP_TYPE_CODE`/`FACILITY_USE_CODE` (NASR) | preserved archive | `PU`/`PU` (publicly owned, public use) |
| B. Runway `05/23` (NASR `APT_RWY.csv`) | preserved archive | 5,998 ft × 150 ft, ASPH |
| B. Runway `13/31` (NASR `APT_RWY.csv`) | preserved archive | 3,997 ft × 150 ft, ASPH |

**Exactly one NASR airport matches** `ARPT_ID=MMU`/`ICAO_ID=KMMU` — no
ambiguity. Both of its NASR runways (`05/23`, `13/31`) match the two
`Runway` rows already in the database (154/155) almost exactly in length
(5998 ft ≈ 1828 m, 3997 ft ≈ 1218 m — the small rounding is the existing
feet→meter conversion already used throughout this project's canonical
runway data, not a discrepancy) and surface type (ASPH). **The existing
canonical `Runway`/`RunwayEnd` inventory for Airport 74 is already fully
correct and needs no change.**

## 4. Candidate/facility comparison

| | RWI evidence | NASR `MMU` | Agreement |
|---|---|---|---|
| Identifiers | `faa_code=MMU`, `iata_code=MMU`, `icao_code=KMMU` | `ARPT_ID=MMU`, `ICAO_ID=KMMU` | **Exact match** |
| Location | city=Morristown, state=New Jersey | CITY=MORRISTOWN, STATE_CODE=NJ | **Exact match** |
| Runway designation named in grant text | `5/23` | `05/23` present | **Exact match** |
| Canonical runway inventory | `5/23` (1828m×46m ASPH), `13/31` (1218m×46m ASPH) | `05/23` (5998ft×150ft ASPH), `13/31` (3997ft×150ft ASPH) | **Exact match** |
| Real-world facility name | (currently `Town Of Morristown`, the grant recipient) | `MORRISTOWN MUNI` | Airport's real name, not yet reflected in RWI |

`MUNI` is FAA NASR's standard abbreviation for "Municipal" — confirmed
by corroborating search of the same preserved archive: **1,073** other
`APT_BASE.csv` rows use the identical `... MUNI` naming pattern (e.g.
`ABBEVILLE MUNI`, `ATMORE MUNI`, `BAY MINETTE MUNI`), so "Morristown
Municipal" is not an invented expansion — it is FAA's own unambiguous,
widely-used naming convention, the same kind of natural-name expansion
already applied to Airport 75 (NASR `ARPT_NAME="ALLEGHENY COUNTY"` →
applied `name="Allegheny County Airport"`, see
[`allegheny-unresolved-airport-investigation.md`](allegheny-unresolved-airport-investigation.md)
§10).

No optional live authoritative verification was needed — repository-
preserved NASR evidence alone, corroborated 1,073 times over within the
same archive, resolves the naming question with no remaining ambiguity.

## 5. Duplicate/collision check

Searched the real database for any row that would collide with the
proposed correction:

- No other `Airport` row has `faa_code='MMU'`, `iata_code='MMU'`, or
  `icao_code='KMMU'`.
- No other row has `city LIKE '%Morristown%'` or `name LIKE '%Morristown%'`.
- No existing row is already named exactly `"Morristown Municipal Airport"`.

**Airport 74 is the unique canonical row for this facility.** No merge is
required or implicated.

## 6. Root cause

Confirmed, not assumed — matches the expected shape stated in the task:
the historical USAspending recipient-name fallback (the same defect fixed
in `import_usaspending_grants.py` in the prior task) created this
`Airport` using the grant recipient's organization/municipality name
("Town Of Morristown") instead of the airport's real name. Unlike
Allegheny, this recipient was a **municipality name**, not an operating
*authority* name — but the underlying defect is identical: a
non-airport-identity string became the canonical `Airport.name`.

Everything else about the row is already correct:

- **Identifiers**: correct — independently verified via a live FAA IIJA
  grant PDF (`faa_code`) and a hand-checked allowlist (`iata_code`/
  `icao_code`), neither derived from the buggy fallback.
- **City/state**: correct — `Morristown`/`New Jersey` matches NASR's
  `CITY`/`STATE_CODE` exactly.
- **Runway inventory**: correct — matches NASR's `MMU` runway data
  exactly, already canonically complete (part of the 76/76 milestone).
- **Only `name` is wrong.**
- **`notes` is stale** — it still describes the "no FAA Loc ID was
  available... verify/correct manually" state, even though the Loc ID
  was in fact found and backfilled three days later. It no longer
  accurately describes the row's current state.
- **No duplicate/collision.**
- **No evidence attached to the wrong entity** — `Signal`/`Source`/
  `SourceAssertion` id 47/21/84 are all correctly about this same real
  airport (the grant's own text names `RUNWAY 5/23`, which is this
  airport's actual runway); only the *label* on the `Airport` row itself
  is wrong, not the linkage.

## 7. Recommended smallest safe correction

**Option A: rename `Airport.name` only**, plus a `notes` cleanup
performed in the same future correction (not two separate ones, since
both fields describe the same resolved state and there's no reason to
split them):

| Field | Old value | Proposed new value | Evidence |
|---|---|---|---|
| `name` | `"Town Of Morristown"` | `"Morristown Municipal Airport"` | NASR `APT_BASE.csv` `ARPT_NAME` ("MORRISTOWN MUNI"), expanded per FAA's own widely-used `MUNI`→`Municipal` convention (§4), matching the project's existing naming convention already applied to Airport 75 |
| `notes` | *(fallback-marker text, quoted in §1)* | Clear it (`NULL`), or replace with a short note recording that identity was resolved via NASR + the IIJA grant PDF | The current text actively misdescribes the row's state — it says "no FAA Loc ID was available" when one has been present and correct since 2026-07-25 |
| `faa_code` / `iata_code` / `icao_code` | `MMU` / `MMU` / `KMMU` | *(leave unchanged)* | Already correct (§2, §3) |
| `city` / `state_region` | `Morristown` / `New Jersey` | *(leave unchanged)* | Already correct, matches NASR exactly |
| `Runway`/`RunwayEnd` rows | 154/155, 285–288 | *(leave unchanged)* | Already canonically correct, matches NASR exactly (§3) |

This is deterministic to the same evidentiary standard already applied to
Airport 75: exactly one NASR candidate, exact identifier match, exact
location match, exact runway-designation match with the grant's own
text, no ambiguity, no competing candidate, no duplicate/collision.

**This was not applied.** No `Airport` row was modified in this task, per
instruction.

## 8. Canonical runway / evidence safety (read-only verification)

A `name`-only (and `notes`-only) correction, read-only-verified against
the schema, would touch:

- `Airport.id` (74) — **unaffected**, primary key never changes.
- `faa_code`/`iata_code`/`icao_code` — **unaffected**, left unchanged.
- `Runway`/`RunwayEnd` rows — **unaffected**; both are keyed by
  `airport_id` (a foreign key to `Airport.id`), never by `Airport.name`;
  confirmed by reading `app/models/airport.py` and
  `app/models/runway_end.py` — neither has any column derived from or
  matched against `Airport.name`.
- `PhysicalInstallationIdentity` — **unaffected**; 0 rows exist for
  Airport 74 (§1), and the model links via `airport_id`, not `name`.
- `Source`/`SourceAssertion`/`Signal` — **unaffected**; all three link
  via `airport_id`/`source_id` foreign keys, never `Airport.name`.
  Confirmed no model anywhere in `app/models/` matches or denormalizes
  `Airport.name` into another table.
- `Installation`/`Incident` — **unaffected**; 0 rows exist for Airport 74.
- **Public/static site** — the only expected effect is display-only: the
  corrected name would simply render wherever `Airport.name` is shown
  (e.g. the airport detail page, search results). Confirmed via
  read-only grep that the only code anywhere matching against
  `Airport.name` (`app/main.py`, three `Airport.name.ilike(pattern)`
  call sites) is dev-server free-text search filtering, not identity or
  FK-matching logic — a rename only changes what search text matches
  this row, and cannot break any linkage.

No canonical re-planning was performed or is needed — the `Runway`/
`RunwayEnd` inventory is unaffected by a name-only correction, confirmed
directly by re-running the read-only U.S. classification (§13).

## 9. DB read-only proof

| | Before this investigation | After this investigation |
|---|---|---|
| DB size | `667648` bytes | `667648` bytes |
| DB mtime | unchanged | unchanged |
| `Runway` count | 180 | 180 |
| `RunwayEnd` count | 360 | 360 |
| U.S. planner classification | 76 `ALREADY_COMPLETE`, 0 unresolved, 0 ambiguous, 0 conflict | unchanged |
| Airport 74 row | as documented in §1 | identical, byte-for-byte unchanged |

No database write occurred anywhere in this task. All NASR inspection
used only `zipfile`/`csv` read access against the already-preserved
archive; no new download was performed or needed; no live network access
was used.
