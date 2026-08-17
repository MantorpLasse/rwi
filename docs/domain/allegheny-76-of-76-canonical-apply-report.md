# Allegheny Identity Correction + AGC Canonical Runway Apply — 76/76 Complete

Records the real, approved database write completing U.S. canonical
runway coverage. Approval basis:
[`docs/domain/allegheny-unresolved-airport-investigation.md`](allegheny-unresolved-airport-investigation.md)
(evidence-backed identification) and
[`docs/domain/allegheny-deterministic-correction-dry-run.md`](allegheny-deterministic-correction-dry-run.md)
(dry-run verification, unchanged, preserved as the historical record of
what was approved before this apply).

## 1. Approval basis

The dry-run report's exact plan was re-verified immediately before this
apply and reproduced identically: 1 Airport row to correct (id 75), then
2 Runway creates / 0 enrichments / 4 RunwayEnd creates for the newly-
identified `AGC` airport. No discrepancy was found — no STOP condition
was triggered anywhere in this task.

## 2. Backup path(s)

Two backups were created automatically, one before each of the two
writes in this task:

1. `data\backups\runway_safe-pre-allegheny-airport-identity-correction-20260817-161913.db`
   (before the `Airport` identity write) — `667648` bytes, verified
   byte-identical to the pre-write database, verified readable
   (`name`/`faa_code`/`icao_code` confirmed as the exact old values;
   `Runway`/`RunwayEnd` counts confirmed `178`/`356`).
2. `data\backups\runway_safe-pre-canonical-runway-us-newly-clean-12-batch-apply-20260817-162054.db`
   (before the `Runway`/`RunwayEnd` write). Its filename inherits the
   name of the reused batch-apply module's own `_backup_name()` function
   (that module's naming was written for the earlier 12-airport batch and
   was reused unmodified here, per instruction not to invent a new
   application path) — the file itself is correct and was created and
   verified by the same `backup_database()` mechanism used throughout
   this project.

## 3. Exact commands executed

**Step 1 — Airport identity correction:**

```
.venv\Scripts\python.exe -m scripts.correct_allegheny_airport_identity --apply --allow-database-write
```

**Step 2 — AGC canonical runway/runway-end apply**, using the existing,
unmodified `scripts.apply_canonical_runway_inventory_us_newly_clean_batch`
module's own `backup_database()` and `run()` functions directly. That
module's own CLI defaults its expected-snapshot parameters to the
already-applied 12-airport batch's numbers (`12`/`22`/`6`/`62`), which
would correctly abort against the current state (airport 75 is now the
*only* newly-clean airport, expecting `1`/`2`/`0`/`4`) — its own
fail-closed check working exactly as designed. Per instruction not to
invent a new application path, this reused the exact same, unmodified
`run()` function (which already accepts an approved-snapshot override as
parameters, precisely for this situation — see that module's own
docstring), driven by a short script that does nothing but:

```python
from app.database import SessionLocal
from scripts.apply_canonical_runway_inventory_us_newly_clean_batch import backup_database, run

backup_path = backup_database()
with SessionLocal() as session:
    result = run(
        session, apply=True,
        expected_airport_count=1, expected_runway_creates=2,
        expected_runway_enrich=0, expected_runway_end_creates=4,
    )
```

No source file was modified to do this — `backup_database()` and
`run()` are called exactly as already written and already tested.

## 4. Airport correction result

```json
{
  "target_airport_id": 75,
  "old_values": {
    "name": "Allegheny County Airport Authority",
    "faa_code": null, "iata_code": null, "icao_code": null,
    "city": "West Mifflin", "state_region": "Pennsylvania", "country": "USA"
  },
  "new_values": {
    "name": "Allegheny County Airport", "faa_code": "AGC", "icao_code": "KAGC"
  },
  "rows_changed": 1
}
```

Verified immediately by direct read-only re-query:
`("Allegheny County Airport", "AGC", None, "KAGC", "West Mifflin", "Pennsylvania")`
— exact match.

## 5. Canonical runway apply result

```json
{
  "aggregate": {
    "airport_count": 0,
    "runways_would_create": 0,
    "runways_would_enrich": 0,
    "runway_ends_would_create": 0
  },
  "airport_ids": [],
  "excluded": []
}
```

(This is the immediate **post-apply** re-dry-run the `run()` function
itself returns — confirming nothing was left to do.) The actual write
created exactly:

| `Runway` id | designation | length_m | width_m | surface |
|---|---|---|---|---|
| 181 | `10/28` | 1982 | 46 | CONC |
| 182 | `13/31` | 1166 | 30 | CONC |

| `RunwayEnd` id | runway_id | designation |
|---|---|---|
| 357 | 181 | `10` |
| 358 | 181 | `28` |
| 359 | 182 | `13` |
| 360 | 182 | `31` |

## 6. Before/after counts

| | Before this task | After this task |
|---|---|---|
| `Runway` | 178 | **180** |
| `RunwayEnd` | 356 | **360** |
| U.S. planner | 75 `ALREADY_COMPLETE` + 1 `UNRESOLVED` | **76 `ALREADY_COMPLETE`, 0 UNRESOLVED, 0 ambiguous, 0 conflicts** |

## 7. Backup-vs-live comparison

Compared the live database against backup #1 (§2), the true pre-task
snapshot:

| Table | Result |
|---|---|
| `installations` | row-for-row identical |
| `signals` | row-for-row identical |
| `incidents` | row-for-row identical |
| `sources` | row-for-row identical |
| `source_assertions` | row-for-row identical |
| `physical_installation_identities` | row-for-row identical (all 6 rows, same `runway_end_id` values) |
| `installation_assertion_links` | row-for-row identical |
| `airports` | **exactly one row differs — id 75** |
| `runways` | **exactly 2 new rows (ids 181, 182), both `airport_id=75`; every other row byte-identical; zero rows removed** |
| `runway_ends` | **exactly 4 new rows (ids 357–360), all under the 2 new runways; zero rows removed** |

**No unexpected differences of any kind were found.**

## 8. MDW/CGF protected-link verification

All 6 `PhysicalInstallationIdentity` rows confirmed identical before and
after, same `runway_end_id` values:

```
(1, 57, '06', 9)
(2, 57, '24', 10)
(3, 12, '04R', 3)
(4, 12, '22L', 4)
(5, 12, '13L', 5)
(6, 12, '31R', 6)
```

MDW (4 runways) and CGF (1 runway) row sets confirmed byte-identical to
their pre-task state.

## 9. Integrity checks

- `PRAGMA foreign_key_check`: `[]` (zero violations)
- `PRAGMA integrity_check`: `ok`

## 10. Idempotency

- Re-running the identity correction (`python -m scripts.correct_allegheny_airport_identity`,
  dry run) now **fails closed**: `"Precondition failed: Airport 75.name
  is 'Allegheny County Airport', expected 'Allegheny County Airport
  Authority'."` — this is the correct, designed "safe recognition"
  behavior for a one-off correction (see
  [`docs/domain/allegheny-deterministic-correction-dry-run.md`](allegheny-deterministic-correction-dry-run.md)
  §6/§11): it refuses rather than silently re-proposing or silently
  no-op'ing, and its own error message is itself proof the correction
  already succeeded. No write is attempted on this path.
- Re-running `scripts.apply_canonical_runway_inventory_us_clean_batch`
  (dry run) reports `76` clean airports, `0` excluded, `0`/`0`/`0`
  pending.
- Re-running `scripts.apply_canonical_runway_inventory_us_newly_clean_batch`
  (dry run) reports `0` newly-clean airports remaining, `0`/`0`/`0`
  pending — airport 75 is now indistinguishable from the rest of the
  already-applied 75.
- No additional real write was performed merely to prove this.

## 11. Tests

- Focused (Allegheny correction + classification + both batch-apply
  scripts + static export): **75 passed** (all isolated in-memory,
  unaffected by the real DB change).
- Full suite: **530 passed** (unchanged from before this task — no new
  tests were added in this apply-only task).
- Python compilation: clean.
- `git diff --check`: exit 0.

## 12. Public-boundary safety

A fresh `build_site()` run against the real, now-corrected database
(local scratch output only — no deployment, no public site touched)
found **zero** occurrences of `"Banor"`, `"runway_end_id"`, or
`"RunwayEnd"` anywhere in the generated output. AGC/Allegheny content
appears only as ordinary public airport listing content (its name/code
on its own detail page and index listings) — no canonical-runway
internals are newly exposed. No public UI or static-export code was
modified in this task.

## 13. Final milestone

**76 of 76 U.S. airports are now canonically deterministic.** `Runway`
count `180`, `RunwayEnd` count `360`. Zero unresolved, zero ambiguous,
zero conflicts anywhere in the U.S. canonical runway inventory.
