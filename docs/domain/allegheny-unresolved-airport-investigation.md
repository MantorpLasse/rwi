# Allegheny Unresolved Airport Investigation

**Read-only investigation. No code, database, or archive modification.**
Every fact below comes from the real, current development database
(inspected read-only), tracked repository documentation, and the
already-preserved NASR archive
(`data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip`). No live network
access was needed — repository-preserved evidence alone was sufficient to
reach a deterministic conclusion.

## 1. Current unresolved state

Confirmed against the real database before any other work:

- Branch: `feature/canonical-runway-runway-end-foundation`, HEAD `41eae9d`
- Airport id 75 exists, `country = "USA"`
- `Runway` count: **178**, `RunwayEnd` count: **356** — both match the
  already-applied state exactly
- Re-running `resolve_us_clean_batch()` reproduces **75 `ALREADY_COMPLETE`,
  1 `UNRESOLVED`, 0 `AMBIGUOUS`, 0 `CONFLICT`**
- The one `UNRESOLVED` row is airport id 75, error `"no FAA/IATA/ICAO
  identifier"`

DB path: `C:\Runwaysafe\runway-safe-intelligence\data\runway_safe.db`.
Size/mtime recorded before this investigation (`667648` bytes) and
confirmed identical after (§9).

## 2. Airport row

```
id:            75
name:          Allegheny County Airport Authority
faa_code:      NULL
iata_code:     NULL
icao_code:     NULL
city:          West Mifflin
state_region:  Pennsylvania
country:       USA
latitude:      NULL
longitude:     NULL
notes:         "Name approximated from the USAspending grant recipient;
                no FAA Loc ID was available in the award description.
                Verify/correct manually if you find the airport's real
                identifiers."
created_at:    2026-07-22 18:17:23
```

The `notes` field is itself the single most important clue: whoever wrote
the ingestion code that created this row already flagged it as an
approximation needing manual verification.

## 3. Provenance trace

The row was created by `scripts/import_usaspending_grants.py::resolve_airport()`.
That function resolves each USAspending grant to an `Airport`, trying two
patterns in order:

1. An embedded FAA Location Identifier inside the grant's own description
   text (`AIRPORT\s*\(([A-Z0-9]{3,4})\)` — e.g. "...CARTERSVILLE-BARTOW
   COUNTY AIRPORT (VPC), LOCATED IN...").
2. A standard beneficiary sentence present on every per-airport grant
   record: `"...FEDERAL FUNDING FOR AIRPORTS ASSOCIATED WITH <CITY>,
   <STATE>."` — matched against existing `Airport.city`/`Airport.state_region`.

For this specific grant, pattern 1 did not match (no Loc ID embedded in
the description text), pattern 2 matched with beneficiary `"WEST MIFFLIN,
PENNSYLVANIA"`, and no existing `Airport` row had that city/state — so the
function fell through to its create-a-new-row fallback
(`import_usaspending_grants.py:140-153`), which names the new `Airport`
after `grant.recipient_name.title()` — **the grant recipient
organization's name, not the airport's name**. The recipient here was
"Allegheny County Airport Authority" — a real organization (the public
authority that operates both Pittsburgh International Airport and
Allegheny County Airport), not itself an airport.

This is not a hypothesis — it is what the code does, read directly, and
it is corroborated by the row's own self-documenting `notes` field.

## 4. Associated RWI evidence

One `Signal` (id 54) and one `SourceAssertion` (id 91), both attached to
airport id 75, both derived from one `Source` (id 28):

| Field | Value |
|---|---|
| Source title | `USAspending grant: Allegheny County Airport Authority` |
| Source URL | `https://www.usaspending.gov/award/ASST_NON_34200820502023_069` |
| Award ID | `34200820502023` |
| Published date | 2023-08-24 |
| Signal title | `USAspending grant — $2.8M, FY2023` |
| Grant amount | $2,832,935 |

The grant's own description text (preserved verbatim in both `Signal.source_notes`
and `SourceAssertion.raw_relevant_text`) reads, in full:

> "PURPOSE: CONSTRUCT/EXTEND/IMPROVE SAFETY AREA. ... **THIS PROJECT
> CONSTRUCTS THE RUNWAY 10/28 SAFETY AREA** to enhance and improve the
> level of safety of operations at the airport. ... THIS GRANT FUNDS THE
> FIFTH PHASE, WHICH INCLUDES THE PROCUREMENT OF 4,000 SQUARE YARDS OF
> ENGINEERED MATERIAL ARRESTING SYSTEM. INTENDED BENEFICIARY: THIS GRANT
> WILL PROVIDE FEDERAL FUNDING FOR AIRPORTS ASSOCIATED WITH WEST MIFFLIN,
> PENNSYLVANIA."

This single already-preserved piece of evidence names an exact runway
designation (**10/28**) and an exact location (**West Mifflin,
Pennsylvania**) — both independently checkable against FAA NASR.

Separately, a prior, already-committed RWI research document —
[`docs/utreding_status_flygplatser.md`](../utreding_status_flygplatser.md)
(lines 331–338) — already documents this exact identification, in
Swedish, translated here:

> *"Allegheny County Airport (AGC), West Mifflin, PA (the database's
> 'Allegheny County Airport Authority' row without a code — verified
> identity: Allegheny County Airport, FAA-LID AGC, operated by Allegheny
> County Airport Authority, which also operates Pittsburgh Intl)"*

That document also independently confirms a directed web search for
`site:airportimprovement.com` EMAS coverage was already performed for AGC
among a list of "weak" (uncertain) EMAS-status airports
([`docs/utredning_svaga_poster.md`](../utredning_svaga_poster.md) lines
206–216) with no new findings beyond what's already known.

**This investigation reproduces and independently corroborates a
conclusion the repository had already reached in prior research — it was
simply never applied to the `Airport` row itself.**

## 5. NASR candidate search

Read-only search of the preserved `2026-08-06` archive's `APT_BASE.csv`
and `APT_RWY.csv`:

| Evidence | Source | Value |
|---|---|---|
| `ARPT_ID` | NASR `APT_BASE.csv` | `AGC` |
| `ARPT_NAME` | NASR `APT_BASE.csv` | `ALLEGHENY COUNTY` |
| `CITY` (FAA postal city) | NASR `APT_BASE.csv` | `PITTSBURGH` |
| `STATE_CODE` | NASR `APT_BASE.csv` | `PA` |
| `COUNTY_NAME` | NASR `APT_BASE.csv` | `ALLEGHENY` |
| `ICAO_ID` | NASR `APT_BASE.csv` | `KAGC` |
| `OWNERSHIP_TYPE_CODE` / `FACILITY_USE_CODE` | NASR `APT_BASE.csv` | `PU` / `PU` (publicly owned, public use) |
| Runway `10/28` | NASR `APT_RWY.csv` | 6,501 ft × 150 ft, CONC surface |
| Runway `13/31` | NASR `APT_RWY.csv` | 3,825 ft × 100 ft, CONC surface |
| Non-runway record | NASR `APT_RWY.csv` | `H1` (helipad, excluded by the existing structural classification rule) |

**Exactly one FAA NASR airport matches**, and its runway inventory
includes **runway `10/28`**, the exact designation the grant description
itself names.

A nationwide search of `APT_BASE.csv` for any other `ALLEGHENY`-named
facility found only heliports attached to hospitals (`SITE_TYPE_CODE='H'`
— Allegheny Hospitals Canonsburg, Allegheny Valley Hospital, Allegheny
General Hospital, Allegheny Wexford rooftop) — none are airports, none
compete as candidates. A search for any Pennsylvania facility with `CITY`
containing "MIFFLIN" found only Mifflinburg and Mifflintown, both
unrelated towns with no connection to Allegheny County. **No ambiguity —
exactly one plausible candidate.**

## 6. Candidate comparison

| | RWI evidence | NASR `AGC` | Agreement |
|---|---|---|---|
| Organization/operator | Allegheny County Airport Authority (grant recipient) | — | AGC is one of the two airports this authority operates |
| Location (RWI notes) | West Mifflin, Pennsylvania | County: Allegheny; postal city: Pittsburgh | Consistent — West Mifflin is a borough within Allegheny County, near Pittsburgh; FAA's postal-city convention commonly lists the larger nearby metro |
| Runway designation named in grant text | `10/28` | `10/28` present | **Exact match** |
| Project type | "Runway 10/28 safety area" / EMAS procurement | Public-use, county-owned airport, plausible EMAS candidate | Consistent |

Three independent sources agree: RWI's own prior research doc, the
grant's own first-party description text, and FAA NASR's structured
runway data. No source disagrees with any other.

## 7. Duplicate/collision check

Searched the real database for any existing `Airport` row that would
collide with the proposed identifiers:

- No row has `faa_code = 'AGC'`, `iata_code = 'AGC'`, or `icao_code`
  containing `AGC`.
- No row has `city LIKE '%Mifflin%'` or `name LIKE '%Allegheny%'` other
  than airport 75 itself.

**No duplicate exists.** Setting `faa_code = "AGC"` on airport 75 would
enrich an existing, otherwise-empty row — not create a collision, and not
require merging with any other `Airport` row. Airport 75 currently has
**zero** existing `Runway` rows and **zero** `PhysicalInstallationIdentity`
rows, confirmed directly — there is nothing pre-existing to reconcile.

## 8. Root cause

**An ingestion/source-mapping problem**, precisely: `import_usaspending_grants.py`'s
`resolve_airport()` city/state fallback path names a newly-created
`Airport` after the grant's `recipient_name` when no FAA Loc ID is
embedded in the grant description and no existing airport matches the
beneficiary city/state. For this grant, the recipient is an airport
*authority* (an organization operating multiple airports), not the
airport itself, so the created row's name describes the organization
rather than the facility. The row nonetheless does represent a real,
identifiable physical airport — it is not a case of an organization being
modeled as an airport in any deeper sense; it is a naming/identifier gap
on an otherwise-correct row, exactly as the row's own `notes` field
already anticipated.

## 9. Deterministic vs. non-deterministic conclusion

**Deterministic.** Three independent lines of evidence (prior RWI
research, the grant's own first-party text naming an exact runway, and
FAA NASR's structured data) converge on exactly one candidate with no
competing alternative and no ambiguity found anywhere in a nationwide
search. This clears the same "reviewed, evidence-based, non-guessed"
threshold this project has applied throughout the canonical-runway work —
it is not a coin-flip or best-guess association.

## 10. Recommended smallest safe next step

**Option A: deterministic identifier enrichment of airport id 75.**
No rename, no merge, no model change — set the three identifier/name
fields the row's own `notes` field already asked a future investigator to
find:

| Field | Old value | Proposed new value | Evidence |
|---|---|---|---|
| `faa_code` | `NULL` | `"AGC"` | NASR `APT_BASE.csv` `ARPT_ID` |
| `icao_code` | `NULL` | `"KAGC"` | NASR `APT_BASE.csv` `ICAO_ID` |
| `name` | `"Allegheny County Airport Authority"` | `"Allegheny County Airport"` | NASR `APT_BASE.csv` `ARPT_NAME` ("ALLEGHENY COUNTY"), matching the airport's commonly-used name distinct from the operating authority |
| `iata_code` | `NULL` | *(leave `NULL`)* | AGC is a small public-use/GA airport with no assigned IATA code in NASR — this is an accurate absence, not a gap |
| `city` / `state_region` | `West Mifflin` / `Pennsylvania` | *(leave unchanged)* | Already correct for the airport's physical location; NASR's own `CITY` field ("PITTSBURGH") reflects FAA's postal-city convention, not a contradiction requiring resolution |

**Expected NASR match after correction**: `faa_code = "AGC"` resolves
immediately via the existing, unmodified matching convention
(`{faa_code, iata_code, icao_code} - {None}` against NASR `ARPT_ID`).

**Expected canonical Runway/RunwayEnd plan after correction** — simulated
read-only in an isolated in-memory database (never the real one), using
the existing, unmodified `classify_airport_batch()`:

| | Value |
|---|---|
| Classification | `CLEAN_CREATE` |
| Runway creates | **2** (`10/28`, `13/31`) |
| Runway enrichments | 0 (no existing rows) |
| RunwayEnd creates | **4** (`10`, `28`, `13`, `31`) |

The `H1` helipad record at AGC is correctly excluded by the already-
implemented structural classification rule — no special-casing needed.

**This was not applied.** No `Airport` row was modified in this task, per
instruction.

## 11. DB/read-only verification

| | Before this investigation | After this investigation |
|---|---|---|
| DB size | `667648` bytes | `667648` bytes |
| DB mtime | unchanged | unchanged |
| `Runway` count | 178 | 178 |
| `RunwayEnd` count | 356 | 356 |
| MDW/CGF `PhysicalInstallationIdentity` links | 6 rows, same `runway_end_id` values | unchanged |
| NASR archive/sidecar | `8034151` / `538` bytes | unchanged |

No database write occurred anywhere in this task. All NASR inspection
used only `zipfile`/`csv` read access against the already-preserved
archive; no new download was performed or needed.
