# Canonical Runway Inventory — U.S. Clean Deterministic Batch

**No write has been made to the real development database.** Every number
below comes from a freshly re-derived, read-only classification (never a
copied/hardcoded airport list) run against the current database plus
isolated in-memory tests. The real database's file size and last-write
timestamp are unchanged before and after this task
(`643072` bytes, `2026-08-16T19:07:08+02:00` — confirmed identical, not
assumed).

## 1. Exact clean-set derivation

A new, additive read-only function,
`app.services.runway_inventory.resolve_us_clean_batch()`, classifies every
`country == "USA"` airport by re-running the existing, **unmodified**
`plan_airport_inventory()` against the preserved NASR archive and applying
the same report-only heuristic already used and published in
`docs/domain/canonical-runway-us-wide-dry-run-report.md` §6. Nothing about
`plan_airport_inventory()`, `apply_plan()`, `normalize_end()`, or
`normalize_pair()` was changed — this is orchestration on top of them, not
a second reconciliation system.

Re-running this derivation against the real database today reproduces the
governing report's numbers **exactly**:

| | Governing report | Re-derived just now |
|---|---|---|
| Clean airports | 63 | **63** |
| Runway creates | 94 | **94** |
| Runway enrichments | 39 | **39** |
| RunwayEnd creates | 284 | **284** |
| Excluded (AMBIGUOUS) | 12 | **12** |
| Excluded (no identifier) | 1 | **1** |

**No discrepancy was found — the task proceeded past the STOP-on-mismatch
gate.** The full 63-airport ID set:

```
2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23,
24, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 37, 39, 40, 41, 42, 43, 44,
45, 48, 49, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 64, 65, 66, 67,
68, 69, 70, 72, 73, 74, 86
```

Includes airport id 12 (MDW) and id 57 (CGF), as required.

## 2. Excluded airports and reasons

**13 airports excluded, none by a new rule** — every exclusion is the
existing planner's own `AmbiguousRunwayDesignationError`, or a database
row with no identifier at all:

- **12 `AMBIGUOUS`** — each blocked by exactly one non-runway NASR record
  mixed into its `APT_RWY.csv` rows (helipad `RWY_ID`s like `H1`/`H2`/`H3`,
  or unpaired special-use strips like `00X`/`10X`/`19X`). Full list and
  root cause already documented in
  `docs/domain/canonical-runway-us-wide-dry-run-report.md` §7 — unchanged
  by this task. No filtering rule for `H1`/`00X`-style rows was added; the
  planner still raises exactly as before, and this batch simply excludes
  any airport whose plan raises.
- **1 no-identifier airport** — Allegheny County Airport Authority
  (`faa_code`/`iata_code`/`icao_code` all `NULL`), classified `UNRESOLVED`.

Zero `CONFLICT` and zero `PARTIAL_MATCH` airports were found — no
duplicate-row hazard, no cross-airport `ARPT_ID` collision, and no
orphaned legacy `Runway` designation exists anywhere in the U.S. scope
(same finding as the prior dry run, reconfirmed here).

## 3. Dry-run totals (real database, read-only)

| | Count |
|---|---|
| U.S. airports processed | 76 |
| Clean (deterministic) | **63** |
| Excluded | **13** |
| Runway rows that would be created | **94** |
| Runway rows that would be enriched | **39** |
| RunwayEnd rows that would be created | **284** |

## 4. Per-airport summary

Unchanged from `docs/domain/canonical-runway-us-wide-dry-run-report.md`
§6/§7 — re-derivation in this task reproduced identical per-airport
classifications, so that report's tables remain the authoritative
per-airport listing rather than being duplicated here. Section 1 above is
the fresh proof that nothing drifted since it was written.

## 5. Exact planned write totals

Same as §3: **94 Runway creates, 39 Runway enrichments, 284 RunwayEnd
creates**, scoped to exactly the 63 airport ids listed in §1 — no other
`Runway`/`RunwayEnd` row anywhere in the database would be touched.

## 6. Protected-table guarantees

`apply_plan()` (unmodified) writes only to `runways` and `runway_ends` —
this was already true before this task and remains true; the new batch
script (`scripts/apply_canonical_runway_inventory_us_clean_batch.py`)
calls no other write path. Proven directly in
`tests/test_apply_canonical_runway_inventory_us_clean_batch.py::test_first_apply_creates_expected_inventory_and_protects_everything_else`:
after an isolated apply, `airports` row count is unchanged, the excluded
`BAD` airport's pre-existing legacy `Runway` row is byte-for-byte
untouched, and `PRAGMA foreign_key_check` returns `[]`. No code path in
the new script imports or calls anything from
`physical_installation_identity_linking.py`, so
`physical_installation_identities`/`installation_assertion_links` cannot
be touched even indirectly. `installations`, `signals`, `incidents`,
`sources`, `source_assertions` are never referenced by this script at all.

## 7. MDW/CGF idempotency

Both remain classified `ALREADY_COMPLETE` — `0` creates, `0`
enrichments, `0` end-creates each, confirmed in §1's re-derivation. The
isolated test suite's fixture seeds MDW/CGF in their real, already-applied
shape (4/1 runways, 8/2 ends, 6 identity links) and asserts the same
zero-write result after a full batch apply.

## 8. Isolated apply result

`tests/test_apply_canonical_runway_inventory_us_clean_batch.py::test_first_apply_creates_expected_inventory_and_protects_everything_else`,
against a 4-airport isolated fixture (MDW/CGF already-complete, TST a
fresh clean-create airport, BAD excluded by a helipad-shaped row):

- TST: exactly 1 `Runway` (`9/27`) + 2 `RunwayEnd` created, matching the
  fixture's approved snapshot (1 create / 0 enrich / 2 end-creates).
- BAD: **zero** rows touched — still its one pre-existing `OLD-LEGACY`
  `Runway` row, zero `RunwayEnd` rows, exactly as before the apply.
- MDW/CGF: unchanged (already complete).
- All 6 `PhysicalInstallationIdentity` rows: unchanged, still linked to
  their original `RunwayEnd` ids.
- `PRAGMA foreign_key_check`: `[]` (clean).
- Immediately re-running `dry_run()` after the apply reports `0/0/0` for
  all four airports (3 clean, 1 excluded) — nothing left to do.

## 9. Repeat-apply idempotency

`test_second_apply_is_idempotent_zero_writes`: applying a second time
(fresh session, simulating a separate process) with an expected snapshot
of `0/0/0` produces **zero** `Runway`/`RunwayEnd`/`Airport`/
`PhysicalInstallationIdentity` row-count change of any kind — confirmed by
direct before/after count comparison, not just by trusting the reported
stats.

## 10. Transaction/failure tests

Three distinct abort paths, each proven to leave **zero** partial writes
(row counts identical before and after the failed call):

- **`test_apply_aborts_and_writes_nothing_when_snapshot_does_not_match`** —
  passing a deliberately wrong expected Runway-create count aborts before
  anything is added to the session.
- **`test_apply_aborts_when_clean_set_membership_changes_before_write`** —
  simulates the clean-set differing between the initial resolve and the
  immediate pre-write re-check (one airport goes from clean to
  `AMBIGUOUS`); the whole batch aborts with `"membership changed"`.
- **`test_apply_aborts_when_aggregate_plan_changes_before_write`** — same
  membership on both resolutions, but the plan itself grew between them;
  aborts with `"aggregate plan changed"`.

All three assert `_counts(session)` (airports/runways/runway_ends/
identities) is byte-for-byte identical before and after the aborted call.

## 11. Public-export safety

`test_public_export_after_apply_still_suppresses_banor_and_leaks_nothing`:
after a real clean-batch apply against the isolated fixture, a fresh
`build_site()` run confirms — across every generated `.html` page and
`data.json` — **zero** occurrences of `"Banor"`, `"runway_end_id"`, or
`"RunwayEnd"`. Combined with the unmodified, still-passing
`tests/test_static_export.py` suite (which already independently proves
this generically for any linked `RunwayEnd`), public "Banor" runway
inventory remains suppressed and no canonical-runway internals leak
through the public boundary. No public UI code was touched by this task.

## 12. Test results

- New clean-batch classification tests (`tests/test_runway_inventory_clean_batch_classification.py`): **11 passed**
- New clean-batch apply-script tests (`tests/test_apply_canonical_runway_inventory_us_clean_batch.py`): **10 passed**
- Focused canonical-runway/inventory tests (11 files, incl. the two new ones above): **94 passed**
- Full suite: **439 passed** (418 before this task + 21 new)
- Python compilation: `app/services/runway_inventory.py`, `scripts/apply_canonical_runway_inventory_us_clean_batch.py`, and both new test files — clean
- `git diff --check`: exit 0 (only a benign LF→CRLF line-ending notice, no actual whitespace error)
- Static-export/public-boundary regression: unchanged suite still passes, plus the new apply-specific check in §11

## 13. Exact real-DB state before/after dry run

| | Before | After |
|---|---|---|
| Resolved path | `C:\Runwaysafe\runway-safe-intelligence\data\runway_safe.db` | same |
| Size | `643072` bytes | `643072` bytes |
| Last-write time | `2026-08-16T19:07:08.5387545+02:00` | `2026-08-16T19:07:08.5387545+02:00` |

Identical — confirmed by direct comparison both before and after every
read-only operation performed in this task (the classification-module
verification, the CLI dry run via
`python -m scripts.apply_canonical_runway_inventory_us_clean_batch`, and
the full test suite run). No backup was created, because no write was
attempted against the real database — the backup discipline
(`backup_database()`) exists in the new script and is exercised by
`test_backup_database_copies_file_byte_identical` against a throwaway
temp file only.

---

## STOP — before real write

**1. Resolved DB path**

```
C:\Runwaysafe\runway-safe-intelligence\data\runway_safe.db
```

**2. Exact clean airport count**

**63** (airport ids listed in full in §1), including MDW (id 12) and CGF (id 57).

**3. Runway creates**

**94**

**4. Runway enrichments**

**39**

**5. RunwayEnd creates**

**284**

**6. Excluded airport count/reasons**

**13 total**: 12 `AMBIGUOUS` (each blocked by one helipad/special-use NASR
record — full list in `docs/domain/canonical-runway-us-wide-dry-run-report.md`
§7, unchanged), plus 1 with no FAA/IATA/ICAO identifier (Allegheny County
Airport Authority).

**7. Backup naming/path that will be used**

```
data\backups\runway_safe-pre-canonical-runway-us-clean-batch-apply-<YYYYMMDD-HHMMSS>.db
```

Created automatically by `backup_database()` the moment `--apply
--allow-database-write` is passed, before any write is attempted — byte-size
verified against the source immediately after copy, exactly like the
MDW/CGF pilot's precedent.

**8. Exact future apply command**

```
.venv\Scripts\python.exe -m scripts.apply_canonical_runway_inventory_us_clean_batch --apply --allow-database-write
```

This command re-derives the clean batch from the current database and the
preserved NASR archive at the moment it runs, compares it against the
approved snapshot in §3–§5 (`63` / `94` / `39` / `284`), re-resolves a
second time immediately before writing, and aborts the entire batch with
zero writes if anything has drifted at all since this report was
approved.

**9. Protected tables**

```
airports, installations, signals, incidents, sources, source_assertions,
physical_installation_identities, installation_assertion_links
```

None of these are written by `apply_plan()` or by the new script — only
`runways` and `runway_ends`, and only for the 63 approved airport ids.

**10. Confirmation MDW/CGF identity links remain untouched**

Confirmed twice: (a) by inspection — the new script imports nothing from
`physical_installation_identity_linking.py` and never touches
`PhysicalInstallationIdentity` anywhere in its code; (b) by test — the
isolated apply test asserts all 6 existing identity rows keep their exact
original `runway_end_id` values after a real batch apply.

**Waiting for explicit approval before running the command in item 8.**

No commit. No push. No deployment. No database write has occurred.
