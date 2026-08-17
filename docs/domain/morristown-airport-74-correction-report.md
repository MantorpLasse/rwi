# Morristown Airport (id 74) — Identity Correction Report

Applies the deterministic correction identified in
[`docs/domain/morristown-airport-74-investigation.md`](morristown-airport-74-investigation.md)
to the real database. **This is the only real-database write performed in
this task** — exactly two fields on exactly one `Airport` row.

## 1. Pre-write state

```
id:            74
name:          Town Of Morristown
faa_code:      MMU
iata_code:     MMU
icao_code:     KMMU
city:          Morristown
state_region:  New Jersey
country:       USA
notes:         "Name approximated from the USAspending grant recipient;
               no FAA Loc ID was available in the award description.
               Verify/correct manually if you find the airport's real
               identifiers."
```

DB path: `data/runway_safe.db`. Size `667648` bytes, confirmed identical
immediately before this task's write and after the prior task's
investigation. `Runway`=180, `RunwayEnd`=360, U.S. planner = 76
`ALREADY_COMPLETE`/0 unresolved/0 ambiguous/0 conflict — all confirmed
read-only before any write.

## 2. Evidence basis

Full chain in
[`morristown-airport-74-investigation.md`](morristown-airport-74-investigation.md):
`Airport.name="Town Of Morristown"` came directly from the historical
USAspending recipient-name fallback (`Source.title = "USAspending grant:
Town Of Morristown"`, award `33400230712025`, description names "RUNWAY
5/23"). `faa_code`/`iata_code`/`icao_code` were already correct,
independently backfilled by two unrelated, earlier scripts
(`add_iija_fy2026_known_grants.py`, verified against a live FAA IIJA
grant PDF; `backfill_airport_codes.py`, hand-verified allowlist) — never
derived from the buggy fallback. Preserved FAA NASR (`ARPT_ID=MMU`,
`ICAO_ID=KMMU`, `ARPT_NAME=MORRISTOWN MUNI`) matches with no ambiguity;
its runway data (`05/23`, `13/31`) matches Airport 74's existing
canonical `Runway`/`RunwayEnd` inventory exactly. No duplicate/collision
exists for any of `MMU`/`MMU`/`KMMU` or the proposed new name.

## 3. Exact approved correction

| Field | Old value | New value |
|---|---|---|
| `name` | `Town Of Morristown` | `Morristown Municipal Airport` |
| `notes` | *(stale fallback-marker text above)* | `Identity confirmed via FAA NASR (ARPT_ID=MMU, ICAO_ID=KMMU, ARPT_NAME=MORRISTOWN MUNI) and the FAA IIJA Announcement 6 FY2026 grant PDF.` |

`faa_code`, `iata_code`, `icao_code`, `city`, `state_region`, `country`
were left unchanged — already correct.

## 4. Correction script safeguards

`scripts/correct_morristown_airport_identity.py`, following the
established `scripts/correct_allegheny_airport_identity.py` pattern:

- Dry-run by default; `--apply` requires `--allow-database-write`.
- Targets exactly `Airport.id = 74` — a coincidentally-matching row under
  a different id is never used.
- Fails closed (`MorristownCorrectionError`) if **any** of `name`,
  `faa_code`, `iata_code`, `icao_code`, `city`, `state_region`,
  `country`, or `notes` has drifted from the exact expected
  pre-correction snapshot — not just `name`.
- Fails closed if `faa_code="MMU"`, `iata_code="MMU"`, or
  `icao_code="KMMU"` is claimed by any *other* `Airport` row (checked
  even though this script never writes those fields — an unexpected
  claimant would mean the identity picture changed since the
  investigation).
- Writes exactly two fields (`name`, `notes`) on exactly one row; never
  touches `Runway`, `RunwayEnd`, or any other table.
- Creates a timestamped backup before any `--apply` write.
- Not idempotent by design — a second run fails closed (post-apply, the
  row no longer matches the expected snapshot) rather than silently
  re-applying or silently no-op'ing.

## 5. Focused test result

`tests/test_correct_morristown_airport_identity.py` — **16 passed**,
covering: dry-run produces no mutation; correct precondition yields the
exact name/notes plan; apply changes exactly one `Airport` row;
identifiers/city/state remain unchanged; related `Runway`/`RunwayEnd`
rows remain unchanged; unrelated `Airport` rows remain unchanged; drifted
name fails closed; drifted identifier fails closed; drifted notes fails
closed; `faa_code`/`icao_code` collision both fail closed; wrong target
id cannot be used; repeated dry-run is deterministic; second apply fails
closed (not idempotent by design).

## 6. Real DB dry-run result

Ran against the real database before any write. Output matched the
approved plan exactly: `preconditions_passed: true`, exactly 1 row would
change, `name`/`notes` proposed values exactly as in §3, all other
fields listed under `unchanged_fields`. DB size/mtime unchanged after the
dry-run (`667648` bytes, same mtime).

## 7. Backup path and verification

Two timestamped backups exist (one from the standalone verification step,
one auto-created by the `--apply` run itself, both before the write):

- `data/backups/runway_safe-pre-morristown-airport-identity-correction-20260817-184858.db`
- `data/backups/runway_safe-pre-morristown-airport-identity-correction-20260817-184920.db`
  (the one immediately preceding the real write; used for the backup-vs-
  live diff in §9)

Both verified: exist, size `667648` bytes (matches source exactly),
`sqlite3` opens cleanly, `PRAGMA integrity_check` → `ok`, `Airport 74`
inside the backup matches the exact pre-write state (§1), `Runway`=180,
`RunwayEnd`=360.

## 8. Exact apply command

```
PYTHONPATH=. python -m scripts.correct_morristown_airport_identity --apply --allow-database-write
```

(Real environment: `.venv\Scripts\python.exe -m scripts.correct_morristown_airport_identity --apply --allow-database-write`.)

Preconditions were re-resolved directly against the live database
immediately before this command — `Airport 74` exact values, zero
`MMU`/`MMU`/`KMMU` collisions, `Runway` rows for airport 74 unchanged —
all confirmed matching before the write proceeded.

## 9. Post-write state

```
id:            74
name:          Morristown Municipal Airport
faa_code:      MMU
iata_code:     MMU
icao_code:     KMMU
city:          Morristown
state_region:  New Jersey
notes:         Identity confirmed via FAA NASR (ARPT_ID=MMU, ICAO_ID=KMMU,
               ARPT_NAME=MORRISTOWN MUNI) and the FAA IIJA Announcement 6
               FY2026 grant PDF.
```

`Runway` rows for airport 74: `5/23`, `13/31` (unchanged). `RunwayEnd`
rows: `5`, `23`, `13`, `31` (unchanged). `Runway`=180, `RunwayEnd`=360
(unchanged).

### Backup-vs-live diff

Compared every one of the 14 tables in the schema, row-by-row, between
the pre-write backup (§7, the `-184920` one) and the post-write live
database. **The only row that differs anywhere in the database is
`airports.id=74`.** Within that row, exactly three columns differ:
`name`, `notes` (both exactly as approved, §3), and `updated_at`.

`updated_at` is **not** an unapproved side effect — it is the `Airport`
model's own pre-existing `onupdate=datetime.utcnow` behavior
(`app/models/airport.py:28`), which SQLAlchemy fires automatically on
*any* column write to a row, regardless of which columns changed. This is
the exact same behavior that already occurred when Airport 75's identity
was corrected in the earlier, already-approved-and-merged Allegheny
task — confirmed directly: Airport 75's own `updated_at` also carries
today's date, from that correction, not its original creation date. It
is a standard, unavoidable, benign property of writing through this
model, not a scope violation, not a hidden mutation, and not something
this task's script could avoid without bypassing the ORM entirely (which
would be a more invasive, higher-risk change than accepting the
timestamp bump).

No row was inserted or deleted anywhere. No other table differs at all.

## 10. FK/integrity checks

- `PRAGMA foreign_key_check` → `[]`
- `PRAGMA integrity_check` → `ok`

## 11. Canonical planner result

Re-run read-only against the live post-write database:
`{'ALREADY_COMPLETE': 76}` — 76 `ALREADY_COMPLETE`, 0 unresolved, 0
ambiguous, 0 conflict. `Runway`=180, `RunwayEnd`=360. Unchanged from
before the write.

## 12. Protected-link verification

The 6 protected MDW/CGF `PhysicalInstallationIdentity` links (ids 1–6,
`airport_id`∈{57, 12}, `runway_end_id` values 9, 10, 3, 4, 5, 6) are all
present and unchanged.

Airport 75 confirmed unchanged: `name="Allegheny County Airport"`,
`faa_code="AGC"`, `icao_code="KAGC"`.

## 13. Idempotency result

Re-ran the correction script in dry-run mode after the write. It failed
closed exactly as designed:

```
MorristownCorrectionError: Precondition failed: Airport 74.name is
'Morristown Municipal Airport', expected 'Town Of Morristown'. The row
may have already changed - refusing to proceed.
```

No second write was attempted or possible.

## 14. Historical fallback cleanup status

Read-only scan (same marker/provenance mechanism used to originally
identify both ids 74 and 75) after this correction:

- **Zero** `Airport` rows remain carrying the historical fallback's
  `notes` marker with an incorrect `name` — Airport 74's `name` is now
  corrected (this task), Airport 75's `name` was already corrected in
  the earlier Allegheny task.
- **One** `Airport` row (id 75) still carries the *stale notes text*
  (its `name`/`faa_code`/`icao_code` were corrected earlier, but that
  correction was scoped to identifiers only and never touched `notes`).
  This is a separate, cosmetic, out-of-scope item — not a naming/identity
  defect, and per this task's explicit instruction ("Do NOT modify any
  other Airport"), it was **not** touched here. Reported only.
- **Zero** `Airport` rows remain with all three of `faa_code`/
  `iata_code`/`icao_code` `NULL` (the fully-unresolved shape) — no
  outstanding unresolved fallback survivor exists.

**No known historical USAspending recipient-name naming defect remains.**

## 15. Test results

- Focused: `tests/test_correct_morristown_airport_identity.py` (16),
  `tests/test_import_usaspending_grants.py` (18),
  `tests/test_correct_allegheny_airport_identity.py` (12) — **46
  passed**.
- Full suite: **550 passed** (534 baseline + 16 new tests from
  `test_correct_morristown_airport_identity.py`).
- `python -m py_compile` clean on both new files.
- `git diff --check` — exit 0.
