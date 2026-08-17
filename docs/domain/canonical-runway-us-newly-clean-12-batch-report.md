# Canonical Runway Inventory — U.S. Newly-Clean 12-Airport Batch

**No write has been made to the real development database.** Every
number below comes from a freshly re-derived, read-only classification
(never a copied/hardcoded airport list) run against the current database
and isolated in-memory tests. The real database's file size and
last-write timestamp are unchanged before and after this task
(`651264` bytes — confirmed identical, not assumed).

## 1. Exact derivation of the 12-airport set

**No airport ID is hardcoded anywhere in this batch's code or tests.**
The set is derived purely from classification output:
`resolve_us_clean_batch()` (unmodified) classifies every U.S. airport;
this batch targets exactly those classified `CLEAN_CREATE` or
`CLEAN_ENRICH` — deliberately **excluding** `ALREADY_COMPLETE` (the
original 63-airport batch, already applied) and every non-clean
classification (`AMBIGUOUS`, `CONFLICT`, `UNRESOLVED`, `PARTIAL_MATCH`).
This is a classification-based filter, not a list comparison against a
remembered "original 63" — it works correctly regardless of what's
already in the database.

Re-running this derivation against the real database today:

| | Expected (from the governing investigation) | Re-derived just now |
|---|---|---|
| Airport count | 12 | **12** |
| Runway creates | 22 | **22** |
| Runway enrichments | 6 | **6** |
| RunwayEnd creates | 62 | **62** |

**Exact match — no discrepancy, no STOP condition triggered.** The
12-airport ID set:

```
1, 6, 7, 25, 36, 38, 46, 47, 50, 51, 63, 71
```

## 2. Per-airport plan

Unchanged from
[`docs/domain/nasr-special-record-classification-investigation.md`](nasr-special-record-classification-investigation.md)
§2 and
[`docs/domain/nasr-special-record-classification-implementation.md`](nasr-special-record-classification-implementation.md)
§8 — re-derivation in this task reproduced identical per-airport
classifications and counts, so those reports remain the authoritative
per-airport listing (Aspen/Pitkin, Greater Binghamton, McClellan-Palomar,
Groton-New London, DeKalb/Peachtree, Chicago O'Hare, Newark Liberty,
Trenton-Mercer, LaGuardia, Republic, Greenville Downtown, Reagan
National) rather than being duplicated here.

## 3. Exact create/enrich/end totals

**22 Runway creates, 6 Runway enrichments, 62 RunwayEnd creates**,
scoped to exactly the 12 airport ids in §1 — no other `Runway`/
`RunwayEnd` row anywhere in the database would be touched, including
none of the already-applied 63.

## 4. Unresolved airport exclusion

Airport id 75, Allegheny County Airport Authority, is the only airport
outside this batch's target set aside from the already-applied 63 —
classified `UNRESOLVED`, error `"no FAA/IATA/ICAO identifier"`. Not
processed, not touched, exactly as required. **Zero** airports classify
`AMBIGUOUS` or `CONFLICT` — the special-record classification rule (see
the investigation/implementation reports) already resolved all 12 that
were previously ambiguous.

## 5. Original 63 regression verification

Confirmed by direct comparison in §1's re-derivation: none of the
original 63 airports appear in this batch's target set — they are all
`ALREADY_COMPLETE` (0 creates, 0 enrichments, 0 end-creates each) and
therefore excluded by this script's own classification filter, not by
any hardcoded exclusion list. MDW (id 12) and CGF (id 57) specifically
remain `ALREADY_COMPLETE`.

## 6. Protected-table guarantees

`apply_plan()` (unmodified, reused exactly as-is) writes only to
`runways` and `runway_ends` — proven directly in
`tests/test_apply_canonical_runway_inventory_us_newly_clean_batch.py::test_first_apply_touches_only_the_newly_clean_airport_and_protects_everything_else`:
after an isolated apply, `airports` row count is unchanged, the
`UNRESOLVED` `BAD` airport's pre-existing legacy `Runway` row is
byte-for-byte untouched, `MDW`/`CGF`'s already-applied shape is
untouched, and `PRAGMA foreign_key_check` returns `[]`. The new script
imports nothing from `physical_installation_identity_linking.py` and
never touches `PhysicalInstallationIdentity` anywhere in its code — the
test additionally asserts all 6 pre-existing identity rows keep their
exact original `runway_end_id` values after the apply.

## 7. Isolated apply result

Fixture: 5 airports — MDW/CGF already-complete (their real, already-applied
shape), TST a newly-clean airport whose NASR data mixes a real runway
pair with a helipad (`H1`) row (proving the special-record rule is what
makes it eligible, not a hardcoded ID), BAD an airport with *only* a
helipad row (must classify `UNRESOLVED`, untouched), NOID with no
identifier (excluded, untouched).

`test_first_apply_touches_only_the_newly_clean_airport_and_protects_everything_else`:

- TST: exactly 1 `Runway` (`9/27`) + 2 `RunwayEnd` created — the `H1` row
  never became a `Runway`.
- BAD: **zero** rows touched — still its one pre-existing `OLD-LEGACY`
  `Runway` row, zero `RunwayEnd` rows.
- MDW/CGF: unchanged (4 runways / 1 runway respectively, matching their
  already-applied shape).
- All 6 `PhysicalInstallationIdentity` rows: unchanged.
- `PRAGMA foreign_key_check`: `[]` (clean).
- Immediately re-running `dry_run()` after the apply reports `0` airports
  / `0/0/0` — nothing left to do.

## 8. Repeat-apply idempotency

`test_second_apply_is_idempotent_zero_writes`: applying a second time
(fresh session, simulating a separate process) with an expected snapshot
of `0` airports / `0/0/0` produces **zero** row-count change of any kind
across `Airport`/`Runway`/`RunwayEnd`/`PhysicalInstallationIdentity` —
confirmed by direct before/after count comparison.

## 9. Failure/transaction tests

Four distinct abort paths, each proven to leave **zero** partial writes:

- **`test_apply_aborts_and_writes_nothing_when_snapshot_does_not_match`** —
  a deliberately wrong expected Runway-create count aborts before
  anything is added to the session.
- **`test_apply_aborts_when_membership_changes_before_write`** —
  simulates the newly-clean set differing between the initial resolve
  and the immediate pre-write re-check; aborts with `"membership
  changed"`.
- **`test_apply_aborts_when_aggregate_plan_changes_before_write`** — same
  membership on both resolutions, but the plan itself grew between them;
  aborts with `"aggregate plan changed"`.
- Both simulated-drift tests assert `_counts(session)` (airports/
  runways/runway_ends/identities) is byte-for-byte identical before and
  after the aborted call.

## 10. Public-export safety

`test_public_export_after_apply_still_suppresses_banor_and_leaks_nothing`:
after a real newly-clean-batch apply against the isolated fixture, a
fresh `build_site()` run confirms **zero** occurrences of `"Banor"`,
`"runway_end_id"`, or `"RunwayEnd"` across every generated page and
`data.json`. No public UI code was touched by this task.

## 11. Test results

- New isolated apply-safety tests (`tests/test_apply_canonical_runway_inventory_us_newly_clean_batch.py`): **10 passed**
- Combined focused run (this file + identity/classification + inventory planning + both existing apply scripts + static export): **106 passed**
- Full suite: **518 passed** (508 baseline + 10 net new tests — this task added no other test files)
- Python compilation: `scripts/apply_canonical_runway_inventory_us_newly_clean_batch.py` and its test file — clean
- `git diff --check`: exit 0 (only the pre-existing benign LF→CRLF notices)
- **`scripts/apply_canonical_runway_inventory_us_clean_batch.py` was not modified** (confirmed via `git diff --stat` — no output)

## 12. Real DB dry-run state

| | Before | After |
|---|---|---|
| Resolved path | `C:\Runwaysafe\runway-safe-intelligence\data\runway_safe.db` | same |
| Size | `651264` bytes | `651264` bytes |
| mtime | unchanged | unchanged |

Real dry-run output (`python -m scripts.apply_canonical_runway_inventory_us_newly_clean_batch`, no flags):

```json
{
  "aggregate": {
    "airport_count": 12,
    "runways_would_create": 22,
    "runways_would_enrich": 6,
    "runway_ends_would_create": 62
  },
  "airport_ids": [1, 6, 7, 25, 36, 38, 46, 47, 50, 51, 63, 71],
  "excluded": [
    {"airport_id": 75, "classification": "UNRESOLVED", "error": "no FAA/IATA/ICAO identifier"}
  ]
}
```

Exact match to the expected values: 12 airports, 22/6/62, 0 unresolved
beyond the one known airport, 0 ambiguous, 0 conflicts.

---

## STOP — before real apply

**1. Resolved DB path**

```
C:\Runwaysafe\runway-safe-intelligence\data\runway_safe.db
```

**2. Exact 12-airport set**

```
1, 6, 7, 25, 36, 38, 46, 47, 50, 51, 63, 71
```

**3. Runway creates**

**22**

**4. Runway enrichments**

**6**

**5. RunwayEnd creates**

**62**

**6. Backup naming/path that will be used**

```
data\backups\runway_safe-pre-canonical-runway-us-newly-clean-12-batch-apply-<YYYYMMDD-HHMMSS>.db
```

Created automatically by `backup_database()` the moment `--apply
--allow-database-write` is passed, before any write is attempted.

**7. Exact future apply command**

```
.venv\Scripts\python.exe -m scripts.apply_canonical_runway_inventory_us_newly_clean_batch --apply --allow-database-write
```

This command re-derives the newly-clean set from the current database
and the preserved NASR archive at the moment it runs, compares it against
the approved snapshot (`12` / `22` / `6` / `62`), re-resolves a second
time immediately before writing, and aborts the entire batch with zero
writes if anything has drifted since this report was approved.

**8. Protected tables**

```
airports, installations, signals, incidents, sources, source_assertions,
physical_installation_identities, installation_assertion_links
```

None of these are written by `apply_plan()` or by this new script —
only `runways` and `runway_ends`, and only for the 12 approved airport
ids.

**9. Confirmation original 63 remain untouched**

Confirmed structurally (this batch's own classification filter excludes
every `ALREADY_COMPLETE` airport by definition — the original 63 have
zero pending work) and empirically (§1's re-derivation, §5, and the
isolated `test_first_apply_touches_only_the_newly_clean_airport_and_protects_everything_else`
test all show MDW/CGF's already-applied shape unchanged).

**10. Confirmation unresolved airport remains untouched**

Confirmed: airport id 75 is excluded by classification (`UNRESOLVED`),
never appears in the target set, and is not processed by this script
under any code path.

**Waiting for explicit approval before running the command in item 7.**

No commit. No push. No deployment. No database write has occurred.
