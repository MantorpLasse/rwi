# FH-D4 Signal Disposition — D4D2: Schema Migration — Report

D4D2 of [fh-d4-signal-disposition-design.md](fh-d4-signal-disposition-design.md)
(§19 "Recommended implementation slices"). This slice implements ONLY the
additive schema migration required to create `signal_dispositions` and
`signal_disposition_members` (already reviewed, committed, and pushed as
D4D1 - [fh-d4-signal-disposition-d4d1-persistence-report.md](fh-d4-signal-disposition-d4d1-persistence-report.md)).
No real database access, no Fleet Health integration, no read-resolution
logic, no CLI, no disposition rows created.

## 1. Files read fresh

`app/models/signal_disposition.py`, `app/services/signal_disposition_persistence.py`,
the D4D1 report, the design doc, `app/models/reviewer_action.py`,
`scripts/migrate_reviewer_action_slice9b.py` (single-table additive
precedent), `scripts/migrate_reconciliation_confirmation_slice_r4b.py`
(rebuild-style precedent - confirmed NOT applicable here, since D4D2 adds
no column to any existing table), `scripts/migrate_evidence_identity_slice6c.py`
(the closest structural precedent - a genuine two-table additive migration
for `PhysicalInstallationIdentity`/`InstallationAssertionLink`, though it
predates the backup-before-write and row-count-gated-downgrade discipline
later migrations established).

## 2. Files created

- `scripts/migrate_signal_disposition_d4d2.py`
- `tests/test_signal_disposition_migration.py` (49 tests)
- `docs/architecture/fh-d4-signal-disposition-d4d2-migration-report.md`
  (this file)

No existing file was modified.

## 3. Migration API

```python
def inspect(database: Path) -> dict          # read-only, sqlite3.connect(mode=ro)
def backup_database(database, backup_directory=BACKUP_DIRECTORY) -> Path
def upgrade(database: Path) -> None           # idempotent, additive-only
def downgrade(database: Path) -> None         # conservative, row-count-gated
def main(argv=None) -> int                    # --database / --downgrade / --allow-database-write / --skip-backup
```

`IncompatibleExistingSchemaError(RuntimeError)` — raised by `upgrade()`
when a table with an expected name already exists but its shape does not
match `app.models.signal_disposition`'s current model.

## 4. Exact schema created

Both `CREATE TABLE`/`CREATE INDEX` statements are compiled directly from
`Base.metadata.tables["signal_dispositions"]`/
`Base.metadata.tables["signal_disposition_members"]` via SQLAlchemy's own
`CreateTable`/`CreateIndex`, reused verbatim from the already-reviewed,
already-committed D4D1 model — no CHECK/FK/UNIQUE clause is hardcoded as
literal text anywhere in this migration, so the created schema can never
drift from the model. Captured directly from a real migrated database:

```sql
CREATE TABLE signal_dispositions (
	id INTEGER NOT NULL,
	decision VARCHAR(30) NOT NULL,
	reason TEXT NOT NULL,
	reviewer VARCHAR(100) NOT NULL,
	created_at DATETIME NOT NULL,
	supersedes_id INTEGER,
	PRIMARY KEY (id),
	CONSTRAINT ck_signal_dispositions_decision CHECK (decision IN ('DISTINCT', 'SAME_REAL_WORLD_EFFORT')),
	FOREIGN KEY(supersedes_id) REFERENCES signal_dispositions (id)
)

CREATE TABLE signal_disposition_members (
	id INTEGER NOT NULL,
	disposition_id INTEGER NOT NULL,
	signal_id INTEGER NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_signal_disposition_members_disposition_signal UNIQUE (disposition_id, signal_id),
	FOREIGN KEY(disposition_id) REFERENCES signal_dispositions (id),
	FOREIGN KEY(signal_id) REFERENCES signals (id)
)
```

Plus the two indexes on `signal_dispositions.supersedes_id` and
`signal_disposition_members.disposition_id`/`.signal_id`.

`PRAGMA foreign_key_list` confirms both member FKs carry `ON DELETE
NO ACTION` / `ON UPDATE NO ACTION` — SQLite's default, equivalent to a
hard block on delete while a referencing row exists, with `PRAGMA
foreign_keys=ON` — no `CASCADE` anywhere.

## 5. Decision CHECK

Attacked directly via raw SQL, bypassing every Python-level validation:
`INSERT ... VALUES ('MAYBE', ...)` raises `sqlite3.IntegrityError: CHECK
constraint failed: ck_signal_dispositions_decision` immediately at
`execute()` time (SQLite enforces `CHECK` eagerly, not deferred to
`commit()` — a real behavioral detail this task's own test-writing
surfaced and corrected, see §14). Both `'DISTINCT'` and
`'SAME_REAL_WORLD_EFFORT'` insert cleanly.

## 6. Foreign keys — verified via `PRAGMA foreign_key_list`

| Table | Column | References |
|---|---|---|
| `signal_dispositions` | `supersedes_id` | `signal_dispositions.id` |
| `signal_disposition_members` | `disposition_id` | `signal_dispositions.id` |
| `signal_disposition_members` | `signal_id` | `signals.id` |

No `ON DELETE CASCADE` anywhere — confirmed by direct `PRAGMA
foreign_key_list` inspection of the `on_delete`/`on_update` fields
(`test_no_on_delete_cascade`).

## 7. UNIQUE member constraint

`UNIQUE(disposition_id, signal_id)` attacked via raw SQL: a second insert
of the identical `(disposition_id, signal_id)` pair raises
`sqlite3.IntegrityError: UNIQUE constraint failed` immediately.
The SAME Signal id in TWO DIFFERENT dispositions is correctly permitted
(`test_same_signal_in_different_dispositions_allowed`) — the constraint is
scoped to one disposition's own member list, never global.

## 8. Minimum-cardinality boundary

Deliberately **not** enforced at the schema level. SQLite has no native
cross-table row-count `CHECK`, and this migration does not fabricate one
via a trigger (no trigger objects exist anywhere in the created schema —
verified directly, `test_no_trigger_objects_created_by_migration`). A
disposition with a single member row is accepted by the raw schema itself
(`test_migration_does_not_enforce_group_cardinality_at_db_level`) — the
`>=2` invariant remains exclusively `record_signal_group_disposition()`'s
own, already-reviewed, service-level responsibility (D4D1 §6), unchanged
and unbypassed by this migration's own `upgrade()` path.

## 9. `inspect()` verdict

Read-only (`sqlite3.connect(..., mode=ro)`), never mutates (verified
directly by hashing the DB file before/after two consecutive `inspect()`
calls — byte-identical). Returns `tables_exist`, `columns`,
`foreign_keys`, `named_constraints_present`, `counts`, a composed `ready`
boolean, and `foreign_key_check` — designed for direct reuse by a future
D4D4/D4D5 schema gate exactly like the existing R4D-established `inspect()`
functions already compose (`check_schema_readiness()` in
`scripts/run_data_health_check.py`/`scripts/review_reconciliation_item.py`).

## 10. `upgrade()` verdict

Idempotent (`test_second_upgrade_is_safe_no_op`: identical `inspect()`
result before/after a repeated call; `test_second_upgrade_preserves_rows`:
real disposition rows survive a second upgrade call untouched). Never
populates either table under any circumstance (§27). Every pre-existing
table's rows verified byte-identical across a representative multi-table
domain fixture (§23/§17 below).

## 11. Partial-schema verdict

Each table is verified **independently** against the current ORM model —
the presence or absence of the *other* table never affects whether an
individual table is accepted:

- **Neither table exists** → full create, both tables created.
- **Header exists, correctly shaped, member absent** → the member table is
  created; the pre-existing header is left completely untouched (still
  verified against the model first, not assumed correct).
- **Member exists, correctly shaped, header absent** — a real, SQLite-
  constructible state, since `CREATE TABLE` does not validate FK targets
  at creation time — is likewise accepted: the header is created, the
  pre-existing member table is verified and left untouched.

**Exact rule**: a table is accepted (and left alone) if and only if its
persisted columns (name/type/nullability/PK), foreign-key targets, and
every named `CHECK`/`UNIQUE` constraint this migration's own target schema
declares are all present — verified structurally, never by name alone.
Any table matching the expected name but failing this check is treated as
a genuine collision, not a partial migration state.

## 12. Incompatible-schema verdict

Three distinct mismatch shapes attacked, each raising
`IncompatibleExistingSchemaError` before anything is touched, and each
leaving the *other* table uncreated too (the whole `upgrade()` call is one
transaction):

- Wrong columns entirely (`wrong_column TEXT` instead of the real schema).
- Right columns, but `supersedes_id`'s foreign key points at `signals`
  instead of `signal_dispositions`.
- Right columns and FK, but the named `CHECK` constraint is missing
  entirely (a hand-edited or manually-restored table).

A fourth attack — a table with the *expected name* but a *completely
unrelated* shape holding real, irreplaceable data — confirmed the refusal
never drops or rebuilds it: the pre-existing row's own data was read back
unchanged after the refused `upgrade()` call
(`test_misleading_same_name_incompatible_table_never_dropped_or_rebuilt`).

## 13. Downgrade-empty verdict

Both tables empty → `downgrade()` succeeds, drops
`signal_disposition_members` before `signal_dispositions` (FK-safe order),
and the resulting table set is confirmed identical to the true pre-D4D2
table set (`test_downgrade_restores_pre_d4d2_schema`).

## 14. Downgrade-nonempty verdict

Attacked with three shapes — header has a row (member empty), member has a
row (header empty is actually impossible once a member row legitimately
exists via the service, since that requires a real header first, but the
*table* can still be probed independently), and both nonempty — every case
raises `RuntimeError: downgrade() refused: ...` naming the offending
table(s) and their row counts, and neither table is dropped in any case.

## 15. Downgrade-atomicity verdict

`test_downgrade_refusal_atomicity_schema_and_rows_intact`: a full
`inspect()` snapshot taken immediately before a refused `downgrade()` call
is byte-for-byte identical to one taken immediately after — no partial
`DROP`, confirmed by comparing the complete structured result, not merely
a row count.

## 16. Write-gate verdict

`main()` without `--allow-database-write` calls `parser.error(...)`
(`SystemExit`) before `upgrade()`/`downgrade()` is ever invoked — confirmed
the target table was never created after this refusal
(`test_main_requires_allow_database_write`). AST-verified: no
`SessionLocal` reference and no ORM `create_engine` anywhere in this
module's actual code (only raw `sqlite3.connect`, matching every migration
script's own established convention) — `test_main_no_sessionlocal_reference_ast`.

## 17. Backup verdict

`backup_database()` creates a byte-identical copy before any write
(`test_backup_created_before_write`); the backup is independently readable
via its own `mode=ro` connection and reflects the genuinely pre-D4D2
schema (no `signal_dispositions`/`signal_disposition_members` table, real
domain tables present — `test_backup_is_independently_readable_pre_d4d2_schema`);
a backup taken before `upgrade()` continues to show the pre-migration row
count and schema even after the source database is subsequently migrated
(`test_backup_matches_source_before_mutation`); `main()`'s own default
write path (no `--skip-backup`) prints `"Backup created: ..."` and
succeeds end-to-end against an isolated temp database
(`test_main_creates_backup_on_write`).

## 18. Wrong-DB verdict

Two independent temp databases (`target.db`/`protected.db`), both starting
from an identical pre-D4D2 schema. `upgrade()` against `target.db` alone
leaves `protected.db` byte-identical (`test_migration_touches_only_the_named_database`)
and confirms `protected.db` never gained the new tables.

## 19. Existing-data-preservation verdict

A representative multi-table fixture (`Airport`, `Source`, `Signal`,
`SourceAssertion` with all three governance decisions set,
`ReviewerAction`) is snapshotted field-by-field before `upgrade()` and
re-read after — byte-for-byte identical
(`test_representative_domain_rows_unchanged_after_upgrade`). A separate
table-set diff confirms the *only* two new tables to appear anywhere in
`sqlite_master` are `signal_dispositions`/`signal_disposition_members`
(`test_only_the_two_new_empty_tables_appear`).

## 20. Model/migration parity verdict

A fresh SQLAlchemy `Session` against a database that was migrated via this
script (never `Base.metadata.create_all()`) successfully calls the
already-reviewed, already-committed D4D1
`record_signal_group_disposition()` end-to-end, producing exactly the
expected header + member rows
(`test_fresh_session_can_read_and_write_via_d4d1_service`). The D4D1
review-checkpoint's own member-set-immutability guard (the `before_insert`
seal listener, a pure Python/ORM-level mechanism, not persisted schema)
was independently re-confirmed to still fire correctly against this
migrated (not `create_all`'d) database
(`test_member_set_immutability_guard_still_works_post_migration`) — this
migration does not weaken or bypass that guarantee in any way.

## 21. Signal-delete FK verdict

Attacked both via the ORM (with a real `PRAGMA foreign_keys=ON`
connect-event listener, mirroring `app/database.py`'s own real one) and
via raw SQL directly — both raise `FOREIGN KEY constraint failed` when
attempting to delete a Signal that is a disposition member; the Signal and
its membership both survive a rollback. Confirms the migrated schema's FK
shape genuinely matches what D4D1's own reviewed model expects.

## 22. Supersession-FK verdict

A real `D1`→`D2` supersession chain round-trips correctly against the
migrated schema (`test_self_fk_works_for_valid_supersession`). A raw SQL
insert naming a nonexistent `supersedes_id` target raises `FOREIGN KEY
constraint failed` immediately (`test_invalid_supersedes_target_fails_via_raw_sql_fk`).

## 23. Zero-backfill proof

A database seeded with real FH-D4-shaped data (two co-located Signals,
same airport, no `runway_id` claimed by either — the exact structural
pattern the pure FH-D4 rule detects) still ends a normal `upgrade()` with
`signal_dispositions`/`signal_disposition_members` both at count 0
(`test_upgrade_never_inserts_a_row_even_with_real_fh_d4_shaped_data_present`).
A second, structural test confirms this migration module never even
imports anything from `app.services.fleet_health_review_rules` or any
other `fleet_health*` module — there is no code path by which it *could*
read current health findings, let alone backfill from them
(`test_no_ast_reference_to_fleet_health_modules`).

## 24. Defects/corrections found (original implementation)

Two test-authoring corrections were made during this slice's own
development (not affecting `scripts/migrate_signal_disposition_d4d2.py`):
the original drafts of the `CHECK`-constraint and `UNIQUE`-constraint raw-
SQL attack tests wrapped `conn.commit()` in `pytest.raises(...)`, assuming
SQLite defers constraint enforcement to commit time — but SQLite raises
`IntegrityError` immediately at `execute()` time for both `CHECK` and
`UNIQUE` violations (not deferred, unlike SQLite's own foreign-key
enforcement, which genuinely can be deferred depending on constraint
declaration - though this project's FKs are all immediate). Both tests
were corrected to wrap the `execute()` call itself; the underlying
migration behavior was never in question, only the test's own assertion
placement.

**A genuine production defect was found during the critical review that
followed this implementation** — see §32 below.

## 25. Regression/tests added (original implementation)

`tests/test_signal_disposition_migration.py`: **49 tests** across
`TestCleanUpgrade` (9), `TestMinimumCardinalityBoundary` (2),
`TestIdempotency` (2), `TestPartialAndIncompatibleSchema` (7),
`TestDowngrade` (7), `TestWriteGate` (2), `TestBackup` (4),
`TestWrongDatabaseIsolation` (1), `TestExistingDataPreservation` (2),
`TestModelMigrationParity` (2), `TestSignalDeleteFkPostMigration` (2),
`TestSupersessionFkPostMigration` (2), `TestRawSqlAttacks` (2),
`TestZeroBackfill` (2), `TestInspect` (2), `TestNoRealDatabaseAccess` (1).
16 more added at the review checkpoint — see §32.

## 26. Focused test result (original implementation)

**203 passed**, 0 failed — see §32 for the post-review re-run total.

## 27. Full pytest result (original implementation)

**2162 passed** (2113 D4D1 checkpoint baseline + 49 new) — see §32 for the
post-review re-run total.

## 28. py_compile result

Clean on `scripts/migrate_signal_disposition_d4d2.py` and
`tests/test_signal_disposition_migration.py`, both before and after the
review checkpoint's own fix.

## 29. `git diff --check` result

Clean (exit 0, no whitespace errors), both before and after the review
checkpoint.

## 30. Explicit real-DB no-access proof

- Every test in `tests/test_signal_disposition_migration.py` operates on
  an isolated `tmp_path`-scoped SQLite file - never `data/runway_safe.db`.
  Verified both by direct code inspection and by a dedicated AST-based
  test (`TestNoRealDatabaseAccess`) confirming the only appearance of the
  literal string `"data/runway_safe.db"` anywhere in the migration
  module's own compiled source is the expected, legitimate
  `DEFAULT_DATABASE` constant - the same default every prior migration
  script in this project already declares, only ever reached when a
  caller *also* explicitly passes `--allow-database-write`.
- `data/runway_safe.db`'s SHA-256/size/mtime were captured immediately
  before running the full pytest suite for this slice and matched the
  checkpoint established at the start of this session (unchanged,
  because nothing in this implementation or its tests ever opens that
  path).
- The real migration (`upgrade()` invoked with `--database
  data/runway_safe.db --allow-database-write`) was never run anywhere in
  this task - that is explicitly deferred to a separate, later,
  independently-authorized D4D6 slice.

## 32. Critical review checkpoint

A fresh adversarial review of D4D2 (before commit/push) explicitly
distrusted every claim above and independently re-verified them. **One
genuine, real defect was found and fixed** — `inspect()` could report a
subtly incompatible table as `ready`. Everything else re-checked out
sound, including two atomicity claims that were previously only structural
(never actually attacked) and are now independently proven.

### 32.1 Genuine defect found and fixed

**`inspect()`'s own `ready` flag could disagree with `upgrade()` about
whether an existing table is genuinely compatible.** The original
`inspect()` computed `ready` from a looser, independent check: it verified
only that specific column *names* were present in the table (via `"id" in
columns[...]`, etc. — never comparing type, nullability, or primary-key
status) and that each named `CHECK`/`UNIQUE` constraint's *name* appeared
somewhere in the table's stored SQL text (never verifying the constraint's
own body was correct). `upgrade()`'s own
`_verify_existing_table_matches_expected_schema()` used a much stricter,
fully structural comparison (exact column tuples, exact FK target sets,
named-constraint presence) to decide whether to refuse an existing table.

Reproduced directly: a `signal_dispositions` table with every expected
column *name* present, but `decision` typed `INTEGER` instead of
`VARCHAR(30)` and carrying one genuinely extra, unexpected column, together
with a correctly-shaped `signal_disposition_members` table, produced
`inspect(db)["ready"] == True` — while `upgrade(db)` on the exact same
database correctly raised `IncompatibleExistingSchemaError`. A future
D4D4/D4D5 schema gate trusting `inspect()["ready"]` to mean "safe to
proceed" (the mission's own stated design intent for this function - "for
later D4D4/D4D5 schema-gate reuse") would have been deceived by exactly
this class of subtly-wrong table.

**Fix**: extracted the full structural comparison into one shared function,
`_schema_mismatch_reasons()`, returning a list of human-readable mismatch
reasons (empty if the table matches exactly). `_verify_existing_table_matches_expected_schema()`
now raises using this list (unchanged behavior, same error text shape);
`inspect()`'s own `ready` computation now calls a new boolean wrapper,
`_table_matches_expected_schema()`, over the identical function - the two
can never disagree again, by construction, since they share one source of
truth. `inspect()` remains fully read-only (every call `_schema_mismatch_reasons()`
makes is a `PRAGMA`/`SELECT`, never a write) - no behavioral change to its
read-only guarantee. A new `matches_expected_schema` field was added to
`inspect()`'s own return dict, exposing the per-table verdict directly
(previously only the aggregate `ready` boolean existed). Verified after
the fix: the exact reproduced attack now reports `ready: False` and
`matches_expected_schema: {'signal_dispositions': False,
'signal_disposition_members': True}`; a genuinely healthy migrated
database still reports `ready: True` (no false-positive introduced in the
other direction). Four permanent regression tests added
(`TestInspectTrustworthiness`), plus three more attacking the specific
partial-mismatch shapes named in the mission's own §8 (extra column, wrong
nullability, wrong type - `TestPartialAndIncompatibleSchema`'s three new
tests).

### 32.2 Atomicity claims — previously structural, now independently proven

Both `upgrade()` and `downgrade()` were already documented as running
inside one `BEGIN IMMEDIATE ... COMMIT`/`ROLLBACK` transaction, but neither
D4D1 (n/a) nor the original D4D2 report had actually *injected* a failure
mid-operation to prove SQLite's real transactional-DDL rollback behavior
works as assumed - the original claim rested on reading the code, not on
an attack. This review closed that gap directly:

- **Upgrade atomicity**: `_table_exists()` was monkeypatched to raise
  immediately before the second table (`signal_disposition_members`) would
  have been created, with the first table (`signal_dispositions`) already
  genuinely created earlier in the same call. After the injected failure,
  `inspect()` confirmed **neither** table exists - SQLite's own real
  transactional DDL rolled back the first table's `CREATE TABLE` too, not
  merely refusing the second one. Verified directly, not assumed
  (`test_upgrade_failure_between_tables_leaves_neither_table_created`).
- **Downgrade atomicity**: the identical technique, injecting a failure
  after the first (`signal_disposition_members`) `DROP TABLE` and before
  the second (`signal_dispositions`) one. After the injected failure, both
  tables are confirmed still present and the database still reports
  `ready: True` (`test_downgrade_failure_between_tables_leaves_both_tables_intact`).

No SQLite DDL-transactionality limitation was found - both operations are
genuinely atomic, not merely "atomic if nothing goes wrong."

### 32.3 Re-verified sound (no change needed)

- **Schema parity**: exact column/type/nullability/PK/FK/CHECK/UNIQUE
  shape re-confirmed via fresh `PRAGMA` inspection against a freshly
  migrated database, matching `app.models.signal_disposition` exactly - no
  drift, no extra columns, no missing constraints.
- **CHECK attack**: `'MAYBE'`, and by extension any value outside the
  two-value vocabulary, rejected immediately at `execute()` time (not
  deferred); both real values accepted.
- **FK attack matrix**: all six named cases (member→nonexistent
  disposition, member→nonexistent Signal, supersedes→nonexistent
  disposition, deleting a referenced Signal, deleting a referenced
  disposition [via the pre-existing header immutability listener - a
  model-layer guarantee, not a migration-schema one], a valid historical
  supersession chain) all independently re-verified against the raw
  migrated schema via direct SQL - the two cases (member→nonexistent
  disposition, member→nonexistent Signal) that were previously only
  covered indirectly through D4D1's own `create_all()`-based test suite
  now have their own direct raw-SQL regression tests against the migrated
  schema specifically, closing a real test-coverage gap this review's own
  §5 instruction ("Freshly attack") flagged.
- **UNIQUE constraint**: re-attacked via raw SQL; same-signal-different-
  dispositions re-confirmed allowed.
- **Idempotency**: re-confirmed via full `inspect()` dict equality (not
  merely counts) across repeated `upgrade()` calls, including with real
  rows present.
- **Partial-schema safety**: all nine named shapes (A-I) now have explicit
  coverage - the three previously-implicit ones (H extra column, I wrong
  nullability, and a third wrong-type variant) now have dedicated tests
  rather than being merely implied by the general structural-comparison
  logic.
- **Downgrade-nonempty / atomicity**: re-attacked with header-only,
  member-only, and both-nonempty row shapes; full `inspect()` snapshot
  equality before/after a refused downgrade confirmed no partial state.
- **Backup timing/content**: `main()`'s own operational order (auth →
  backup → mutation) re-confirmed by reading the code; backup content
  strengthened with a genuine `PRAGMA integrity_check`/`foreign_key_check`
  test against the backup file itself (`test_backup_passes_integrity_check`)
  - the original report's own byte-identity test already implied this but
    never verified it directly.
- **Write gate / `DEFAULT_DATABASE` risk**: confirmed `--database`
  silently defaults to the real database path when omitted, matching
  every prior migration script's own identical, already-reviewed
  convention (`migrate_reviewer_action_slice9b.py`,
  `migrate_reconciliation_confirmation_slice_r4b.py`,
  `migrate_evidence_identity_slice6c.py` all share this exact default) -
  not a new risk this migration introduces, and the mitigation
  (`--allow-database-write` always required, independently) was directly
  proven by monkeypatching `upgrade()`/`downgrade()` to fail loudly if
  ever called without it
  (`test_write_gate_is_the_only_thing_preventing_default_path_mutation`).
  Left unchanged, per this review's own instruction not to silently
  broaden scope without a genuine safety defect - there is none here.
  Nonexistent-path and malformed-database-file inputs both fail closed
  with clear exceptions (`FileNotFoundError`,
  `sqlite3.DatabaseError`) - newly tested.
- **Wrong-DB isolation**: re-confirmed via full byte-identity comparison
  of the untouched database (the strongest possible check, already present
  in the original test).
- **Existing-data preservation**: re-confirmed via full field-by-field
  snapshot comparison (not row counts alone) across five representative
  tables.
- **Zero-backfill**: re-confirmed behaviorally (real FH-D4-shaped data
  present, still 0/0 after upgrade) and structurally (AST scan proving no
  `fleet_health*` import exists anywhere in the migration module).
- **Model/service parity, post-migration member-insert guard,
  Signal-delete FK, supersession FK**: all re-confirmed against a
  genuinely migrated (never `create_all()`'d) database - the D4D1
  review-checkpoint's own `before_insert` seal guard fires correctly
  post-migration, proving this migration does not weaken that guarantee.
- **Real-DB non-access**: re-confirmed - no test opens
  `data/runway_safe.db`; its SHA-256/size/mtime are unchanged across this
  entire review.

### 32.4 Final test totals

`tests/test_signal_disposition_migration.py`: **65 tests** (49 original +
16 review-checkpoint additions: `TestInspectTrustworthiness` ×4,
`TestDdlAtomicity` ×2, three new `TestPartialAndIncompatibleSchema` cases,
two new `TestRawSqlAttacks` FK-matrix cases, three new `TestWriteGate`
cases, one new `TestBackup` integrity case). Focused regression suite
(this file + D4D1 persistence + `test_reviewer_action_persistence.py` +
`test_reviewer_action_migration.py` + `test_reconciliation_confirmation_migration.py`
+ `test_model_contract.py`): **219 passed**. Full pytest: see the
validation run recorded at commit time - expected 2162 (pre-review total)
+ 16 new = **2178**, zero regressions. `py_compile`: clean. `git diff
--check`: clean.

## 33. Conclusion

D4D2, after this critical review, is sound: a purely additive, genuinely
idempotent, genuinely atomic (proven by injected failure, not assumed),
fail-closed-on-incompatible-schema, row-count-gated-on-downgrade migration
for the two D4D1 tables, deriving its entire schema from the live ORM
model so it can never drift, with zero backfill and zero weakening of any
D4D1 guarantee. One genuine defect (`inspect()`/`upgrade()` disagreement
on schema compatibility) was found and fixed with the smallest change that
closes it - a single shared comparison function, no new column, no new
mechanism. Ready to commit and push.
