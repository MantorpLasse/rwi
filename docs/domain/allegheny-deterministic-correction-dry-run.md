# Allegheny Deterministic Identity Correction — Dry Run

**No database write occurred.** Every number below comes from either a
direct read-only inspection of the real database, an isolated in-memory
test, or a throwaway file-system copy of the real database used purely
for simulation and deleted immediately afterward. The real database file
itself was never opened for writing anywhere in this task.

## 1. Baseline

Confirmed before any other work:

- Branch: `feature/canonical-runway-runway-end-foundation`, HEAD `41eae9d`
- DB path: `C:\Runwaysafe\runway-safe-intelligence\data\runway_safe.db`
- DB size/mtime at start: `667648` bytes
- Airport id 75: `name="Allegheny County Airport Authority"`,
  `faa_code=NULL`, `iata_code=NULL`, `icao_code=NULL`,
  `city="West Mifflin"`, `state_region="Pennsylvania"`
- `Runway` count: **178**, `RunwayEnd` count: **356**
- `resolve_us_clean_batch()` reproduces **75 `ALREADY_COMPLETE`, 1
  `UNRESOLVED`, 0 `AMBIGUOUS`, 0 `CONFLICT`** — the one `UNRESOLVED` row
  is airport id 75

All match exactly what
[`docs/domain/allegheny-unresolved-airport-investigation.md`](allegheny-unresolved-airport-investigation.md)
recorded — no discrepancy, no STOP condition.

## 2. Approved deterministic identity correction

Design basis: the evidence-backed conclusion of the linked investigation.

| Field | Old | New |
|---|---|---|
| `name` | `Allegheny County Airport Authority` | `Allegheny County Airport` |
| `faa_code` | `NULL` | `AGC` |
| `icao_code` | `NULL` | `KAGC` |
| `iata_code` | `NULL` | *(unchanged)* |
| `city` | `West Mifflin` | *(unchanged)* |
| `state_region` | `Pennsylvania` | *(unchanged)* |

## 3. Implementation approach

New script: `scripts/correct_allegheny_airport_identity.py`. A **one-off,
narrowly-scoped correction for exactly this one row** — not a generic
"resolve an airport from its operator name" mechanism, and it implements
no canonical-runway matching/classification logic of its own. It writes
only to the single `airports` row for id 75, exactly the three fields in
§2. It never imports or touches `Runway`, `RunwayEnd`, or any evidence
table — canonical-runway ingestion for the newly-identified AGC airport
remains a separate, later, explicitly approved step, reusing the
existing, unmodified `classify_airport_batch()`/`apply_plan()` once this
identity correction is applied and committed.

Follows this repository's established maintenance-script convention
exactly: default is dry run; `--apply` requires `--allow-database-write`;
a timestamped backup is created automatically the moment a real write is
requested, before anything is touched.

## 4. Fail-closed preconditions

Before proposing or applying anything, `_check_preconditions()` requires:

1. Airport id 75 exists at all.
2. Every one of `name`, `faa_code`, `iata_code`, `icao_code`, `city`,
   `state_region`, `country` matches the exact pre-correction snapshot
   from §1/§2 — any drift (including a `faa_code` that's already been set
   to *anything*, not just something wrong) aborts before any write.

Verified by test: `test_dry_run_fails_closed_on_unexpected_current_name`,
`test_dry_run_fails_closed_when_faa_code_already_set`,
`test_dry_run_fails_closed_when_target_airport_does_not_exist` (proves
the script only ever considers *exactly* id 75 — a different row that
happens to match every field is not used).

## 5. Collision protection

Before proposing `faa_code="AGC"` or `icao_code="KAGC"`, the script
queries for any *other* `Airport` row already holding either value and
aborts if one exists. Verified by
`test_dry_run_fails_closed_on_faa_code_collision` and
`test_dry_run_fails_closed_on_icao_code_collision`. Confirmed against the
real database (§7 of the investigation, re-confirmed here): no existing
row holds either identifier.

## 6. Tests

`tests/test_correct_allegheny_airport_identity.py` — **12 passed**,
isolated in-memory databases only:

- Dry run reports the exact plan when preconditions match.
- Proposed values are exactly `name`/`faa_code`/`icao_code` as in §2 —
  nothing else.
- `iata_code`/`city`/`state_region` are asserted absent from the
  proposed-change set.
- Precondition failure on unexpected current name.
- Precondition failure when `faa_code` is already non-`None` (row already
  changed since investigation).
- Collision failure on existing `faa_code="AGC"`.
- Collision failure on existing `icao_code="KAGC"`.
- Target-id enforcement: a row under a *different* id that happens to
  match every field is not used — only id 75 is ever considered.
- Dry run performs zero DB mutation (`session.new`/`session.dirty` both
  empty).
- Repeated dry run is deterministic (identical result twice).
- Isolated `apply()` writes exactly the three fields, leaves every other
  field and every other `Airport` row untouched.
- A second `apply()` attempt fails closed (not silently idempotent) —
  this is a one-off correction, not a repeatable batch; re-running after
  a successful apply correctly refuses rather than guessing whether it's
  safe to re-apply.

## 7. Real-DB dry-run result

`python -m scripts.correct_allegheny_airport_identity` (no flags), run
against the real database:

```json
{
  "target_airport_id": 75,
  "preconditions_passed": true,
  "old_values": {
    "name": "Allegheny County Airport Authority",
    "faa_code": null, "iata_code": null, "icao_code": null,
    "city": "West Mifflin", "state_region": "Pennsylvania", "country": "USA"
  },
  "proposed_new_values": {
    "name": "Allegheny County Airport", "faa_code": "AGC", "icao_code": "KAGC"
  },
  "unchanged_fields": {
    "iata_code": null, "city": "West Mifflin",
    "state_region": "Pennsylvania", "country": "USA"
  },
  "rows_that_would_change": 1
}
```

All preconditions passed. Directly confirmed `session.new`/`session.dirty`/
`session.deleted` are all empty after the dry run — genuinely read-only.
Exactly **1** row would change; no other `Airport` row, no `Runway` row,
no `RunwayEnd` row, and no evidence-table row are touched by this script
under any code path (it has no import of those models at all).

## 8. Isolated canonical planner result

Simulated (isolated in-memory DB only) with the corrected identity
already applied — `faa_code="AGC"`, `icao_code="KAGC"` — against the
real, preserved NASR archive's `AGC` rows, using the existing, unmodified
`classify_airport_batch()`:

| | Value |
|---|---|
| Classification | `CLEAN_CREATE` |
| Runway creates | **2** (`10/28`, `13/31`) |
| Runway enrichments | **0** |
| RunwayEnd creates | **4** (`10`, `28`, `13`, `31`) |
| Ambiguous | 0 |
| Conflicts | 0 |

The `H1` helipad record present in NASR's `AGC` data is excluded by the
existing, unmodified structural classification rule
(`is_canonical_runway_candidate()`) — no special-casing was added or
needed.

## 9. Simulated U.S.-wide post-correction state

A throwaway file-system copy of the real database (`shutil.copy2`, a
temp path under the scratch directory, deleted immediately after use —
the original file was never reopened for writing) was used to simulate,
in sequence: (a) the identity correction, (b) the AGC canonical
runway/runway-end apply, (c) a fresh U.S.-wide `resolve_us_clean_batch()`
run against that fully-corrected copy:

| | Before (real DB) | Simulated after (copy only) |
|---|---|---|
| Clean airports | 75 | **76** |
| Excluded (unresolved/ambiguous/conflict) | 1 | **0** |
| Classification breakdown | 75 `ALREADY_COMPLETE` + 1 `UNRESOLVED` | **76 `ALREADY_COMPLETE`** |
| `Runway` count | 178 | **180** |
| `RunwayEnd` count | 356 | **360** |

**Exact match to the intended milestone — 76/76 deterministic U.S.
airport coverage, 0 unresolved, 0 ambiguous, 0 conflicts.**

## 10. Protected existing state

Compared directly, real database vs. the simulated corrected copy,
before the copy was deleted:

| | Result |
|---|---|
| `installations` | row-for-row identical |
| `signals` | row-for-row identical |
| `incidents` | row-for-row identical |
| `sources` | row-for-row identical |
| `source_assertions` | row-for-row identical |
| `physical_installation_identities` | row-for-row identical (all 6 MDW/CGF links, same `runway_end_id` values) |
| `installation_assertion_links` | row-for-row identical |
| `airports` | **only row id 75 differs** — every other airport byte-identical |
| MDW (`Runway` count) | 4, identical in both |
| CGF (`Runway` count) | 1, identical in both |
| New `Runway` rows | belong **only** to airport 75 |

No public/static-export code was touched by this task — no export was
run, none was needed, since no source file governing export behavior was
modified.

## 11. Proof the real DB remained unchanged

| | Before this task | After this task |
|---|---|---|
| DB path | `C:\Runwaysafe\runway-safe-intelligence\data\runway_safe.db` | same |
| Size | `667648` bytes | `667648` bytes |
| mtime | unchanged | unchanged |
| Airport 75 `name`/`faa_code`/`icao_code` | old values (§1) | **still the old values** |
| `Runway` count | 178 | **178** |
| `RunwayEnd` count | 356 | **356** |

Confirmed by direct re-inspection at the end of this task, not assumed.

## 12. Exact future apply command

**Not run in this task.**

```
.venv\Scripts\python.exe -m scripts.correct_allegheny_airport_identity --apply --allow-database-write
```

This creates a timestamped backup
(`data\backups\runway_safe-pre-allegheny-airport-identity-correction-<YYYYMMDD-HHMMSS>.db`)
before writing, re-checks every precondition and collision guard
immediately before the write, and changes exactly the one `Airport` row's
three fields — nothing else. A separate, later, explicitly approved step
would then apply the resulting AGC canonical runway/runway-end plan
(2 creates / 0 enrich / 4 end-creates), reusing the existing,
already-proven apply mechanism.
