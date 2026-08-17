# Canonical Runway / Runway-End Foundation — Slice 1 Report

Implements the smallest next slice from
`docs/domain/canonical-runway-runway-end-design.md`. **No write was made to
the real development database at any point.** Every real-database
interaction in this slice was read-only (see "Exact real DB state
inspected" and "No-write confirmation" below). Schema changes were built,
tested, and verified only against isolated in-memory databases and a
disposable copy of the real database file.

## 1. Schema changes

**New model:** `app/models/runway_end.py`

```python
class RunwayEnd(Base):
    __tablename__ = "runway_ends"
    __table_args__ = (UniqueConstraint("runway_id", "designation", name="uq_runway_ends_runway_designation"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    runway_id: Mapped[int] = mapped_column(ForeignKey("runways.id"), index=True)
    designation: Mapped[str] = mapped_column(String(10))
```

No lifecycle/history/alias fields, per the design doc's explicit restraint
(§15/§16). `heading`/`resa_length_m` (from the historical 2026-07-17
`RunwayEnd` model) were deliberately not restored — `resa_length_m` has no
confirmed source in the NASR files actually inspected, and `heading` isn't
needed by anything built in this slice.

**New relationship:** `Runway.runway_ends` (`app/models/airport.py`), cascade
`all, delete-orphan` — deleting a Runway deletes its ends, matching the
`Airport.runways` cascade convention already used.

**New nullable column:** `PhysicalInstallationIdentity.runway_end_id`
(`app/models/physical_installation_identity.py`), FK → `runway_ends.id`,
indexed, with a relationship attribute named `canonical_runway_end` (kept
distinct from the pre-existing free-text `runway_end` string column, which
is untouched). **Nothing sets this column in this slice** — every existing
identity, and every identity this slice's own tests create, ends up with
`runway_end_id = NULL` unless a test explicitly and deliberately sets it to
prove the column works.

**Validation symmetry:** `app/services/physical_installation_reconciliation.py`'s
`_validate_identity()` now also rejects a `runway_end_id` that doesn't
belong to the same airport, mirroring the existing `runway_id` check
exactly. `create_physical_installation_identity()` gained an optional
`runway_end_id` parameter (default `None`) for this same reason — this
makes a *future*, separately-approved linking step possible without
touching this file again; nothing in this slice calls it with a value.

`app/models/__init__.py` exports `RunwayEnd`.

## 2. Normalization rules

`app/services/runway_identity.py` — deliberately source-agnostic (no FAA
concept lives here, per design doc §5/§11):

- `normalize_end("06") == normalize_end("6") == "6"`; `normalize_end("04R")
  == normalize_end("4R") == "4R"` — reuses the exact leading-zero regex
  `scripts/import_faa_runway_ends.py::_normalize_designation` already
  proved safe in production (it deduplicated MHT/HYA), rather than
  reinventing it.
- `normalize_pair("22L/04R") == normalize_pair("04R/22L") == "4R/22L"` —
  reciprocal order is resolved by sorting both ends by ascending numeric
  heading. Checked against every real MDW/CGF designation extracted from
  NASR and against both existing legacy DB rows (`13L/31R`, `6/24`): **zero
  existing rows require reordering** — NASR and the legacy seed already
  agree on "lower heading first."
- **Fails closed**, never guesses: raises `AmbiguousRunwayDesignationError`
  on anything that isn't exactly two distinct, numerically-headed ends
  (empty string, one end, three ends, identical ends, a token with no
  numeric heading).
- Display form is preserved separately from canonical form using fields
  that already existed for this purpose (`SourceAssertion.raw_runway_value`/
  `raw_runway_end_value`) — no new capability was needed for that part.

## 3. Parser / upsert design

**Preserved-artifact reader:** `app/evidence/nasr_apt_rwy.py`. Reads
`APT_RWY.csv` (one row per runway pair) and `APT_RWY_END.csv` (one row per
end) from the already-downloaded, SHA-256-verified
`data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip` — the exact same
artifact `app/evidence/nasr_apt_ars.py` already reads for EMAS presence.
**No network code anywhere in this module** — it opens a local zip and
raises if the SHA-256 doesn't match, exactly like the existing EMAS reader.
Deliberately kept separate from `nasr_apt_ars.py`: canonical runway
inventory and EMAS presence evidence are different claims (design doc §4),
even from the same NASR cycle. Uses a subset-column check (not the existing
exact-tuple check `nasr_apt_ars.py` uses) since `APT_RWY_END.csv` alone has
~76 columns and only 7 are used here — a missing required column still
fails closed; an unrelated FAA column addition elsewhere in the file no
longer breaks this reader.

**Planner:** `app/services/runway_inventory.py`.
- `plan_airport_inventory(session, airport, runway_rows, runway_end_rows)` —
  **read-only**, computes what canonical `Runway`/`RunwayEnd` rows already
  exist (matched via normalized designation, never by airport alone —
  confirmed by a dedicated test), would be created, or (for an existing
  Runway missing length/width/surface) would be enriched. Degrades cleanly
  to "no existing ends" if `runway_ends` doesn't exist yet (real,
  not-yet-migrated database) instead of raising.
- `apply_plan(session, airport, plans)` — writes. Used only by isolated-DB
  tests in this slice; never called against the real database.
- `evaluate_identity_links(session, airport, plans)` — **read-only**,
  proposes (never applies) a `runway_end_id` for each still-unlinked
  `PhysicalInstallationIdentity` whose free-text `runway_end` normalizes to
  exactly one planned end for that airport. Zero or ambiguous matches are
  silently excluded, not guessed. Requires the migrated schema.
- `evaluate_identity_links_from_raw(identity_rows, plans)` — same matching
  rule, for a real database that hasn't been migrated yet (no
  `runway_end_id` column to `SELECT`, so identities are read as plain
  tuples instead of ORM rows). A dedicated test proves this path agrees
  exactly with the ORM path.

**Dry-run script:** `scripts/dry_run_canonical_runway_inventory.py` —
resolves an airport by FAA/IATA/ICAO code, inspects current rows via a
`mode=ro` sqlite3 connection (same pattern
`scripts/migrate_canonical_runway_runway_end_slice1.py::inspect()` uses),
parses the preserved NASR files filtered to that airport, and prints the
full plan. Never calls `session.add()`/`commit()`/`apply_plan()`.

## 4. MDW dry-run inventory (real development database, read-only)

Command: `python -m scripts.dry_run_canonical_runway_inventory --airport MDW`

| | Current DB (before) | Source-derived (NASR 2026-08-06) |
|---|---|---|
| Runway rows | **1** (`13L/31R`, id 12, 1988m×46m, "Asphalt/Concrete") | **4**: `04L/22R` (1679m×46m, ASPH), `04R/22L` (1964m×46m, ASPH-CONC), `13L/31R` (1988m×46m, ASPH-CONC), `13R/31L` (1176m×18m, ASPH) |
| RunwayEnd rows | **0** (table doesn't exist yet) | **8**: `04L, 22R, 04R, 22L, 13L, 31R, 13R, 31L` |
| PhysicalInstallationIdentity rows | **4** (ids 3–6: `04R, 22L, 13L, 31R`, all `runway_id = NULL`) | n/a (not touched) |

Counts were verified from source, not hardcoded — confirmed independently
by `tests/test_nasr_apt_rwy_evidence.py::test_real_preserved_artifact_gives_mdw_four_runways_eight_ends`,
which reads the real preserved zip directly.

Planned actions:

- **Would create (3 runways):** `04L/22R`, `04R/22L`, `13R/31L`.
- **Would remain unchanged (1 runway):** `13L/31R` (id 12) — already has
  length_m, width_m, *and* surface populated, so no enrichment needed
  either.
- **Would create (8 runway ends):** all of them — the table doesn't exist
  in the real database yet.

## 5. Proposed MDW identity-to-runway-end mappings (proposal only, not applied)

| Identity id | Current `runway_end` | → Matched runway | → Matched end | Target runway_id | Target runway_end_id |
|---|---|---|---|---|---|
| 3 | `04R` | `4R/22L` | `4R` | *(would be created)* | *(would be created)* |
| 4 | `22L` | `4R/22L` | `22L` | *(would be created)* | *(would be created)* |
| 5 | `13L` | `13L/31R` | `13L` | **12** (existing) | *(would be created)* |
| 6 | `31R` | `13L/31R` | `31R` | **12** (existing) | *(would be created)* |

All 4 existing MDW identities match deterministically (exact airport, exact
normalized designation, unambiguous) — none were linked. `unresolved_identities: []`.

## 6. CGF control result (real development database, read-only)

Command: `python -m scripts.dry_run_canonical_runway_inventory --airport CGF`

| | Current DB (before) | Source-derived (NASR 2026-08-06) |
|---|---|---|
| Runway rows | **1** (`6/24`, id 58, length/width/surface all `NULL`) | **1**: `06/24` → normalizes to `6/24`, matched to the existing row |
| RunwayEnd rows | **0** | **2**: `06, 24` |
| PhysicalInstallationIdentity rows | **2** (ids 1–2: `06, 24`, both `runway_id = NULL`) | n/a |

CGF's legacy row (`"6/24"`, no leading zero) is correctly recognized as the
same runway as NASR's `"06/24"` via the same normalization used everywhere
else — **action: enrich** (length/width/surface all currently NULL), not
create. No duplicate. Both identities propose a deterministic link
(`06`→`6/24` end `6` id 58; `24`→`6/24` end `24` id 58). **No special-casing
was needed anywhere in the planner for CGF's simpler, single-runway shape**
— confirmed by `tests/test_runway_inventory_planning.py::test_plan_reuses_existing_legacy_runway_via_normalization_not_duplicate`.

## 7. Migration test result

`scripts/migrate_canonical_runway_runway_end_slice1.py` (additive,
hand-rolled — this project uses `create_all()`, not Alembic; see git
history 2026-07-22 `823cbde`). Tested exclusively against **disposable
copies**, never the real file:

- **Isolated copy of the actual real database** (`data/runway_safe.db`
  copied to a scratch path, migrated there): `upgrade()` created exactly
  `runway_ends` + its index and the `runway_end_id` column + its index on
  `physical_installation_identities`; `PRAGMA foreign_key_check` returned
  `[]`; a full `sqlite_master` DDL diff showed **only those two tables'
  entries changed, nothing else**; all 6 existing
  `physical_installation_identities` rows compared byte-for-byte identical
  to the real database on their pre-existing 5 columns, with
  `runway_end_id` NULL on all 6. `downgrade()` then restored the schema to
  be **byte-identical** to the pre-migration dump (56 `sqlite_master`
  entries, exact match), with data intact throughout. The real
  `data/runway_safe.db` file was never opened for writing at any point
  (separately confirmed: unchanged mtime, see §9).
- **pytest-isolated temp DBs** (`tests/test_canonical_runway_runway_end_migration.py`,
  3 tests): upgrade adds only the expected table/column (schema diff
  assertion), is idempotent when run twice, and downgrade is exactly
  reversible (schema and seeded data both compared before/after).

## 8. Tests

All new, all passing, all against isolated in-memory/temp databases or the
read-only preserved NASR artifact — **none touch the real development
database**:

| File | Count | Covers |
|---|---|---|
| `tests/test_runway_identity_normalization.py` | 6 | leading-zero, reciprocal pair order, existing-seed compatibility, fail-closed cases |
| `tests/test_nasr_apt_rwy_evidence.py` | 8 | fixture shape, synthetic-zip parsing, SHA mismatch fails closed, missing-column fails closed, **real preserved artifact** MDW=4/8 and CGF=1/2, EMAS-subset-of-inventory cross-check |
| `tests/test_runway_inventory_planning.py` | 9 | MDW plan (4/8, all new), CGF reuse-not-duplicate via normalization, idempotent apply→replan, never matches across airports, identity-link proposals (all 4 MDW matches, zero/ambiguous excluded, already-linked excluded, ORM/raw paths agree), degrades cleanly with no `runway_ends` table |
| `tests/test_canonical_runway_runway_end_migration.py` | 3 | upgrade adds only the expected schema, idempotent, exact downgrade reversibility |
| `tests/test_runway_end_model.py` | 7 | RunwayEnd contract, `UNIQUE(runway_id, designation)`, cross-airport rejection, existing identities stay NULL, cascade delete |
| `tests/test_static_export.py` (+1) | 1 | RunwayEnd/runway_end_id, even when populated and linked, never appear in any generated HTML page or `data.json` |
| `tests/test_model_contract.py` (updated) | 5 | schema lock now includes `runway_ends` and `runway_end_id` |

**Run results:**

- Focused (all files above): all pass.
- Full suite: **401 passed** (was 363 before this slice; +38 new tests, all
  passing).
- Python compilation: all new/modified files compile cleanly.
- Migration isolated upgrade/downgrade: passed (§7).
- `git diff --check`: passed (exit 0).

## 9. Public safety

- No template, `app/static_export/build.py`, or presentation file was
  touched.
- `grep -rn "RunwayEnd\|runway_end_id" app/static_export/` returns nothing.
- New regression test `test_build_site_does_not_expose_runway_end_or_runway_end_id_anywhere`
  seeds a `RunwayEnd` row **and** a `PhysicalInstallationIdentity` with
  `runway_end_id` actually set, builds the full site, and asserts neither
  `"runway_end_id"` nor `"RunwayEnd"` appears in any generated HTML page or
  `data.json`. "Banor" remains suppressed (untouched from the prior slice).
- No UI, route, or public-data-selection code was changed.

## 10. Exact real DB state inspected (read-only)

Resolved path: `C:\Runwaysafe\runway-safe-intelligence\data\runway_safe.db`.

- `runways` table: MDW has 1 row (id 12, `13L/31R`); CGF has 1 row (id 58,
  `6/24`).
- `runway_ends` table: **does not exist** in the real database (confirmed
  by both the dry-run script and `PRAGMA table_info`/`sqlite_master`
  checks) — this slice's migration has not been applied there.
- `physical_installation_identities`: 6 rows total (MDW ids 3–6; CGF ids
  1–2), all `runway_id = NULL`; the `runway_end_id` column does not exist
  in the real database yet.
- All reads used either a `mode=ro` sqlite3 connection or a normal
  SQLAlchemy session that only ever executed `SELECT`s (verified by
  reading every call site in `scripts/dry_run_canonical_runway_inventory.py`
  and `app/services/runway_inventory.py`'s read-only functions — no
  `session.add()`, `session.flush()` writing new rows, or `session.commit()`
  appears on any path reachable from the dry-run script).

## 11. No-write confirmation

`data/runway_safe.db` last-write timestamp before this slice's work began
and after every dry run, test run, and isolated-copy migration test in this
slice: **`2026-08-16 09:58:59`, unchanged throughout.** This is the same
timestamp confirmed unchanged in every prior audit this session. No
`--allow-database-write` flag was ever passed against `data/runway_safe.db`
itself.

---

## 12. STOP — before real DB migration

Everything above is implemented, tested, and verified read-only. Per
instruction, stopping here for explicit approval before touching the real
database.

**1. Resolved DB path**

```
C:\Runwaysafe\runway-safe-intelligence\data\runway_safe.db
```

**2. Exact backup path that would be used**

```
data/backups/runway_safe-pre-canonical-runway-runway-end-slice1-<UTC timestamp, e.g. 20260816-HHMMSS>.db
```
(generated automatically by `backup_database()` at run time, `shutil.copy2`
+ size verification, same convention as every prior slice's backup)

**3. Exact migration/apply command**

```
.venv\Scripts\python.exe -m scripts.migrate_canonical_runway_runway_end_slice1 --allow-database-write
```
(creates a timestamped backup first, then adds `runway_ends` and
`physical_installation_identities.runway_end_id` — schema only, no row is
read, linked, merged, or deleted; downgrade available via `--downgrade` if
ever needed)

**4. Dry-run counts (from §4/§6, verified from source)**

| | MDW | CGF |
|---|---|---|
| Runways would create | 3 | 0 |
| Runways would enrich | 0 | 1 |
| Runways unchanged | 1 | 0 |
| RunwayEnds would create | 8 | 2 |
| Existing runway matches (reused, not duplicated) | 1 (`13L/31R`, id 12) | 1 (`6/24`, id 58) |
| Unresolved/ambiguous identities | 0 | 0 |

**5. Proposed MDW identity mappings** — see §5 table. (CGF's are in §6.)
**None would be applied by the migration command above** — that command
only adds schema. Linking `runway_end_id` on any existing identity, and
creating the canonical Runway/RunwayEnd rows themselves, are separate,
not-yet-built apply steps requiring their own explicit approval, per the
design doc's §7 human-review discipline.

**6. Protected tables guaranteed unchanged by the migration command above**

`airports`, `runways` (rows unchanged; schema unchanged), `signals`,
`installations`, `incidents`, `sources`, `source_assertions`,
`installation_assertion_links`, and every row currently in
`physical_installation_identities` (all 6 keep their exact current
`airport_id`/`runway_id`/`runway_end`/`created_at` values; only the new
`runway_end_id` column is added, NULL on all of them).

**Waiting for explicit approval before running the command in §3.**

No commit. No push. No deployment.
