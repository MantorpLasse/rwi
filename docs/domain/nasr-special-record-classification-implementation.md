# FAA NASR Special-Record Classification — Implementation

Implements the smallest safe caller-boundary filtering step identified by
[`docs/domain/nasr-special-record-classification-investigation.md`](nasr-special-record-classification-investigation.md).
**No database write occurred.** No airport was applied. The one
no-identifier airport was not touched. `normalize_pair()`,
`plan_airport_inventory()`'s matching/reconciliation behavior, and
`apply_plan()` are all unchanged in semantics.

## 1. Exact rule implemented

A NASR `APT_RWY.csv`/`APT_RWY_END.csv` row is eligible to enter canonical
`Runway`/`RunwayEnd` planning only if its `RWY_ID` is **exactly two
non-empty, whitespace-trimmed tokens separated by a single `/`**. Nothing
else about the value is inspected — no character, prefix, or suffix is
tested. Implemented as `is_two_ended_pair_shape()` in
`app/services/runway_identity.py`:

```python
def is_two_ended_pair_shape(designation: str | None) -> bool:
    if not designation:
        return False
    parts = designation.split("/")
    return len(parts) == 2 and all(part.strip() for part in parts)
```

## 2. Why it is FAA/NASR-backed

This is the exact structural property FAA's own runway-numbering
convention guarantees for a genuine two-directional fixed-wing runway,
and guarantees *against* for every non-runway category found in the
investigation (helicopter pads, balloonport pads, empty placeholder
records) — verified against the entire preserved national dataset
(23,196 `APT_RWY.csv` rows) with zero unexplained exceptions. It is not a
new invention: `normalize_pair()` already required exactly this shape as
its own first, fail-closed check before this task — the only change is
that the check is now also usable, standalone, as an *input eligibility*
predicate applied before a row reaches the planner, not only as an
in-planner validation that aborts on failure.

## 3. Where filtering occurs

`app/services/runway_inventory.py::classify_airport_batch()` — the single
choke point every U.S.-wide classification call already passes through
(`resolve_us_clean_batch()` calls it once per airport). Immediately after
the existing "no NASR match at all" check and before calling
`plan_airport_inventory()`:

```python
canonical_runway_rows = [r for r in runway_rows if is_canonical_runway_candidate(r.values["RWY_ID"])]
canonical_runway_end_rows = [r for r in runway_end_rows if is_canonical_runway_candidate(r.values["RWY_ID"])]

if not canonical_runway_rows:
    return AirportBatchClassification(..., UNRESOLVED, "no canonical two-ended runway rows for this airport (only special/non-runway NASR records)")

plans = plan_airport_inventory(session, airport, canonical_runway_rows, canonical_runway_end_rows)
```

`is_canonical_runway_candidate(rwy_id)` (new, in `app/services/runway_inventory.py`)
is a thin, NASR-facing wrapper around `is_two_ended_pair_shape()` —
kept in the runway-inventory module (not `runway_identity.py`, which is
deliberately source-agnostic) because it names its purpose in NASR/
canonical-inventory terms.

**No other caller was touched.** `scripts/apply_canonical_runway_inventory_mdw_cgf_pilot.py`
and `scripts/dry_run_canonical_runway_inventory.py` still call
`plan_airport_inventory()` directly, unfiltered — see §4.

## 4. Why the core planner was not changed

`plan_airport_inventory()`, `normalize_pair()`, `normalize_end()`, and
`apply_plan()` are byte-for-byte unchanged in behavior. `normalize_pair()`
was refactored to call the newly-extracted `is_two_ended_pair_shape()`
internally instead of repeating the same two-line check inline — same
error message, same exception type, verified by the full existing
`tests/test_runway_identity_normalization.py` suite passing unchanged.

This keeps the planner itself exactly as fail-closed as before: any
caller that still hands it a non-two-ended `RWY_ID` (the two scripts named
above, or any future caller) still gets
`AmbiguousRunwayDesignationError`, exactly as today. This slice makes one
specific caller — the U.S.-wide clean-batch classification path — supply
better-filtered input, rather than making the shared planner more
permissive for everyone.

## 5. Why designation-prefix heuristics were rejected

The investigation found that a prefix/suffix check (`startswith("H")`,
`startswith("B")`, `endswith("X")`) would both be incomplete (it would
miss `HA`, `H-A`, and any future non-`/`-shaped record that doesn't
happen to match those specific characters) and ungrounded (no FAA field
or documented convention promises those specific characters mean
anything). The implemented rule tests none of them — confirmed directly
by `test_is_two_ended_pair_shape_does_not_depend_on_special_prefix_characters`,
which asserts `"H1/H2"` (contains `"H"`, but is pair-shaped) is
**accepted**, and `"ZZ"` (contains no special character, but isn't
pair-shaped) is **rejected** — classification tracks structure, not
character content.

## 6. Preservation of raw records

Nothing about how NASR rows are read, parsed, or preserved was touched.
`app/evidence/nasr_apt_rwy.py` still yields every row from
`APT_RWY.csv`/`APT_RWY_END.csv` unfiltered — including helipad,
balloonport, and placeholder rows. The preserved archive
(`data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip`) and its sidecar were
not touched by this task (confirmed unchanged, same bytes/mtime as
before). `APT_ARS.csv` evidence reading (`app/evidence/nasr_apt_ars.py`)
is untouched — filtering only applies inside the canonical-runway
classification path, not to any evidence-reading path. The new
eligibility check only decides what enters *canonical inventory
planning* input; every row remains fully present and readable in raw
form for any future purpose.

## 7. Original 63 regression result

Re-ran `resolve_us_clean_batch()` read-only against the real, current
database (all 76 U.S. airports, unfiltered NASR rows as input — the
function does its own filtering internally now):

- All 63 previously-clean airport IDs remain classified clean — zero
  regressions, zero unexpected classification changes.
- MDW (id 12): `ALREADY_COMPLETE`, `(0, 0, 0)` creates/enrich/end-creates — unchanged.
- CGF (id 57): `ALREADY_COMPLETE`, `(0, 0, 0)` — unchanged.

This is not only empirically confirmed but structurally guaranteed: an
airport is only classified clean today because none of its NASR rows
ever failed `normalize_pair()`'s two-ended check — meaning the new
filter, which removes only rows failing that exact same check, cannot
remove anything a previously-clean airport's plan ever used.

## 8. 12-airport result

All 12 previously-`AMBIGUOUS` airports (Aspen/Pitkin, Greater Binghamton,
McClellan-Palomar, Groton-New London, DeKalb/Peachtree, Chicago O'Hare,
Newark Liberty, Trenton-Mercer, LaGuardia, Republic, Greenville Downtown,
Reagan National) now classify `CLEAN_ENRICH` (9) or `CLEAN_CREATE` (3) —
**zero remain `AMBIGUOUS`, zero fall into `UNRESOLVED`**. Aggregate
proposed changes for this group of 12, matching the investigation's
prediction exactly:

| | Investigation prediction | Implemented result |
|---|---|---|
| Runway creates | 22 | **22** |
| Runway enrichments | 6 | **6** |
| RunwayEnd creates | 62 | **62** |

## 9. U.S.-wide result

| | Before this slice | After this slice |
|---|---|---|
| Total U.S. airports | 76 | 76 |
| Already complete | 2 (unapplied) / 63 applied+classified* | 63 |
| Clean create/enrich (not yet applied) | 0 | 12 (9 enrich + 3 create) |
| Ambiguous | 12 | **0** |
| Unresolved | 1 | **1** (unchanged) |
| Clean airports total | 63 | **75** |

\* The 63 previously-clean airports were already applied to the real
database in an earlier task, so they now classify `ALREADY_COMPLETE`
rather than `CLEAN_CREATE`/`CLEAN_ENRICH` — expected and unrelated to
this slice.

## 10. One remaining unresolved airport

**Airport id 75, Allegheny County Airport Authority** —
`faa_code`/`iata_code`/`icao_code` all `NULL` in RWI's own `Airport` row.
Not touched, not solved, kept explicitly separate from this slice's
scope, exactly as instructed.

## 11. Test results

- New/updated tests in `tests/test_runway_identity_normalization.py`:
  `is_two_ended_pair_shape()` accept/reject coverage (including every
  example the task specified — real pairs, helipad/balloonport/placeholder
  records, empty/whitespace-only, leading/trailing/double slash,
  three-part malformed, single token, `None`), whitespace-trimming
  behavior, and an explicit "not dependent on special prefix characters"
  test.
- New/updated tests in `tests/test_runway_inventory_clean_batch_classification.py`:
  `is_canonical_runway_candidate()` accept/reject coverage; a non-paired
  row alongside a valid pair now plans the valid pair instead of
  aborting the whole airport (previously this test expected `AMBIGUOUS` —
  updated to expect `CLEAN_CREATE`, reflecting the corrected, intended
  behavior); an all-non-canonical-rows airport now classifies
  `UNRESOLVED` instead of `AMBIGUOUS`; a genuine non-numeric-heading case
  (`"AB/CD"` — pair-shaped, so it reaches the planner, but fails deeper
  normalization) still correctly classifies `AMBIGUOUS`, proving the
  shape filter and the planner's own fail-closed behavior are distinct
  and both still work.
- Full focused run (14 files covering identity normalization, NASR
  reading, inventory planning, clean-batch classification, migration,
  MDW/CGF pilot, U.S. clean-batch apply, identity linking, `RunwayEnd`
  model, model contract, static export, FAA discovery, FAA acquisition,
  NASR acquisition-and-preserve): **188 passed**.
- Full suite: **508 passed** (470 before this slice + 38 net new/updated
  tests — the `is_two_ended_pair_shape()`/`is_canonical_runway_candidate()`
  coverage described above).
- Python compilation: `app/services/runway_identity.py`,
  `app/services/runway_inventory.py`, and both modified test files —
  clean.
- `git diff --check`: exit 0 (only the pre-existing benign LF→CRLF
  notices).
- Public-boundary regression: a fresh `build_site()` run against the
  real, current database found **zero** occurrences of `"Banor"`,
  `"runway_end_id"`, or `"RunwayEnd"` across all 157 generated pages, and
  the `"EMAS idag"` label still renders on all 86 airport pages,
  unchanged.
- Real database: size/mtime confirmed byte-identical before and after
  every step in this task (`651264` bytes) — no write occurred anywhere.

## 12. Smallest safe apply step

**No 12-airport-scoped apply command exists yet.** The only existing
apply script, `scripts/apply_canonical_runway_inventory_us_clean_batch.py`,
hardcodes its approved-snapshot expectation to the original 63-airport
shape (`EXPECTED_CLEAN_AIRPORT_COUNT = 63`, `94`/`39`/`284` creates/
enrich/end-creates) and its own fail-closed pre-write check would
correctly *refuse* to run today, since the real U.S.-wide plan has
changed shape (75 clean, not 63) — exactly the collision it's designed to
catch. Per instruction, that script's scope was **not** broadened in this
task.

**Recommended next slice**: a new, separately-scoped apply script (or a
parameterized variant of the existing one) targeting exactly the 12
newly-clean airport IDs, with its own approved snapshot
(`expected_clean_airport_count=12`, `expected_runway_creates=22`,
`expected_runway_enrich=6`, `expected_runway_end_creates=62`), following
the same dry-run-first, backup-first, re-resolve-immediately-before-write
discipline already proven for the 63-airport batch. That is future work,
not performed here.
