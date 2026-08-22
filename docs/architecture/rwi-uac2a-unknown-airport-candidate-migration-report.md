# RWI UAC2A — Unknown-Airport Candidate Schema Migration

Implementation report. Starting checkpoint: HEAD
`6b093eb550618edd04d9a09c24e3eec84624c21b` (== origin/main), real DB
SHA-256 `d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`
(1,822,720 bytes, `signal_dispositions`=10, `signal_disposition_members`=25,
FK check `[]`, integrity `ok`, and confirmed **no**
`unknown_airport_candidates`/`unknown_airport_candidate_reviews` tables
present) — all verified fresh at the start of this mission and confirmed
byte-identical, with the same two tables still absent, at the end. **No
migration was ever run against the real database in this mission.**

## Files read fresh

`docs/architecture/rwi-governed-new-airport-discovery-design.md`,
`docs/architecture/rwi-uac1-unknown-airport-candidate-persistence-report.md`,
`app/models/unknown_airport_candidate.py`,
`app/services/unknown_airport_candidate_persistence.py`,
`tests/test_unknown_airport_candidate_persistence.py`,
`scripts/migrate_signal_disposition_d4d2.py` and
`tests/test_signal_disposition_migration.py` (the primary precedent —
the most structurally similar prior two-table migration, already
carrying the strict schema-parity/partial-schema/incompatible-schema/
downgrade-refusal/DDL-atomicity discipline this mission's own §5–§9
require), `scripts/migrate_evidence_identity_slice6c.py` (the
one-script-two-tables precedent UAC1's own critical review already cited
for the migration-boundary decision).

## Files created

- `scripts/migrate_unknown_airport_candidates_uac2a.py`
- `tests/test_unknown_airport_candidate_migration.py` (64 tests)
- This report.

## Files modified

**None.** No production model or service file was changed — the
migration compiles schema directly from the already-committed ORM models
(`Base.metadata.tables[...]`), so no model change was necessary or made.

## Migration API

`inspect(database) -> dict`, `backup_database(database, backup_directory=...) -> Path`,
`upgrade(database) -> None`, `downgrade(database) -> None`,
`main(argv=None) -> int`, `IncompatibleExistingSchemaError(RuntimeError)`
— the exact shape `scripts/migrate_signal_disposition_d4d2.py` already
established, reused without inventing any new migration framework or
convention. `main()` requires `--allow-database-write`; `--database`
defaults to `data/runway_safe.db` (matching every prior migration
script's own identical default, mitigated entirely by the separate,
always-required write flag); a timestamped backup
(`runway_safe-pre-unknown-airport-candidates-uac2a-{timestamp}.db`) is
taken before any non-downgrade write unless `--skip-backup` is passed.

## Candidate-table schema verdict

**Exact parity with `app.models.unknown_airport_candidate.UnknownAirportCandidate`,
proven by test, not asserted.** All 14 columns (types, nullability, PK),
the single FK (`resolved_airport_id -> airports.id`), and the named
`UniqueConstraint` (`uq_unknown_airport_candidates_fingerprint`) are
compiled directly from `Base.metadata.tables["unknown_airport_candidates"]`
via `CreateTable`/`CreateIndex` — never hand-written SQL. Verified by
`TestCleanUpgrade::test_exact_candidate_columns`/`test_exact_foreign_keys_via_pragma`/
`test_fingerprint_unique_constraint_via_raw_sql`.

## Review-table schema verdict

**Exact parity with `UnknownAirportCandidateReview`, proven by test.**
All 8 columns, all 3 FKs (`candidate_id`, `matched_airport_id`,
`supersedes_review_id`), and all 3 named `CheckConstraint`s
(`ck_unknown_airport_candidate_reviews_action`,
`_match_target_required`, `_match_target_only_for_match`) are compiled
from the live model. `ON DELETE`/`ON UPDATE` both confirmed `NO ACTION`
(`test_no_on_delete_cascade`) — no cascade exists anywhere in this
schema, matching the ORM's own lack of a `cascade=` argument.

## inspect() verdict

**Sound, and deliberately shares one strict comparison function with
`upgrade()`.** `_schema_mismatch_reasons()` is the single source of truth
both `inspect()["ready"]`/`["matches_expected_schema"]` and `upgrade()`'s
own `_verify_existing_table_matches_expected_schema()` call — reused
verbatim from the `migrate_signal_disposition_d4d2.py` precedent
specifically *because* that precedent's own review checkpoint already
found and fixed the exact failure mode this mission's §5 warns against
(a table with every expected column NAME present but a wrong type,
missing constraint, or wrong FK target reported `ready: True`).
`TestInspectTrustworthiness` proves `inspect()` can never disagree with
`upgrade()` about compatibility in this migration either.

## upgrade() verdict

**Purely additive, atomic, fail-closed on incompatibility.** Fresh
upgrade against a full pre-UAC1 schema creates both tables with `ready:
True` and `counts: {0, 0}` — no backfill (proven in §"zero-backfill
verdict" below). Both `CREATE TABLE`/`CREATE INDEX` statements for both
tables run inside one `BEGIN IMMEDIATE ... COMMIT` block; a real
mid-upgrade failure (injected via `monkeypatch`) leaves **neither** table
created (`TestDdlAtomicity::test_upgrade_failure_between_tables_leaves_neither_table_created`).

## Partial-schema verdict

**Each table is independently verified; a correctly-shaped table missing
its sibling is safely completed.** `unknown_airport_candidates` present
+ correct, `unknown_airport_candidate_reviews` absent → the missing one
is created, `ready` becomes `True`
(`test_candidate_exists_correctly_review_absent_safe_completion`). The
reverse (review table present with no candidate table yet — an unusual
but SQLite-permitted state, since SQLite does not validate FK targets at
`CREATE TABLE` time) is likewise safely completed
(`test_review_exists_correctly_candidate_absent_safe_completion`).

## Incompatible-schema verdict

**Fails closed, never drops/rebuilds, whole operation is one
transaction.** Six distinct incompatibility shapes are each proven to
raise `IncompatibleExistingSchemaError` and leave the database otherwise
untouched: wrong columns, wrong FK target, an extra unexpected column,
wrong nullability, wrong type, and a missing named `CHECK` constraint.
`test_misleading_same_name_incompatible_table_never_dropped_or_rebuilt`
additionally proves a same-named-but-wholly-different table's own
pre-existing row survives the refusal untouched — this migration never
"fixes" a collision automatically.

## Idempotency verdict

**Sound.** A second `upgrade()` call against an already-migrated,
empty-tables database is a byte-for-byte no-op
(`before == after` on `inspect()`'s own full result). A second call
against a database already carrying a real candidate + review row
(created through the UAC1 service) leaves both rows and counts
unchanged.

## Downgrade-empty verdict

**Sound.** Both tables drop cleanly when empty, restoring the exact
pre-migration table set (`test_downgrade_restores_pre_uac1_schema`), and
a full upgrade→downgrade→upgrade round trip ends `ready: True` with
`counts: {0, 0}`.

## Downgrade-nonempty verdict

**Fails closed independently for either table, and for both together.**
Candidate rows alone refuse the downgrade
(`test_downgrade_candidate_rows_refused`); review rows alone refuse it
(`test_downgrade_review_rows_refused`); both nonempty refuse it too. The
refusal is atomic — `test_downgrade_refusal_atomicity_schema_and_rows_intact`
proves `inspect()`'s full result is byte-identical before and after the
refused call, so no partial `DROP` and no row loss occurs.

## DDL-atomicity verdict

**Proven directly for both directions**, not merely assumed from the
`BEGIN IMMEDIATE` wrapping. A real exception injected between the first
and second table's creation leaves **neither** table created
(SQLite's own transactional DDL rollback, exercised for real). A real
exception injected between the first and second table's drop during
downgrade leaves **both** tables intact and still `ready: True`.

## Backup/write-gate verdict

**Sound, matching precedent exactly.** `main()` refuses without
`--allow-database-write` (`SystemExit`, verified no table is created);
`--database` silently defaults to the real path (a pre-existing,
already-accepted risk mitigated entirely by the separate write flag,
identically documented in the D4D2 precedent); a timestamped backup is
created before any write, confirmed independently readable, integrity-
clean, FK-clean, containing genuine pre-migration schema/data, and
**not** yet containing either new table
(`test_backup_is_independently_readable_pre_uac1_schema`). A malformed
database file and a nonexistent database path both fail closed with the
correct exception types.

## Wrong-DB verdict

**Sound.** `upgrade()` run against `target.db` leaves a separately
constructed `protected.db` byte-identical
(`test_migration_touches_only_the_named_database`) — nothing infers or
falls back to any other path.

## Existing-data preservation verdict

**Sound.** A representative synthetic pre-UAC database (`Airport`,
`Source`, `Signal`, `SourceAssertion`, `ReviewerAction` rows) is
field-snapshotted before and after `upgrade()`; the two are identical.
Exactly two new tables appear, both empty
(`test_only_the_two_new_empty_tables_appear`).

## ORM/service parity verdict

**Sound — the migrated schema genuinely interoperates with the committed
UAC1 service, not merely with `Base.metadata.create_all()`.** Every
synthetic database in `TestModelMigrationParity` and `TestRawSqlAttacks`
is built via `migration.upgrade()`, never `create_all()`. Against that
migrated schema: `find_or_create_unknown_airport_candidate()` and
`record_unknown_airport_candidate_review()` both work correctly;
fingerprint uniqueness still converges/separates correctly; the UAC1
review-checkpoint field-level immutability guard on
`UnknownAirportCandidate` still fires; the review table's append-only
guard still fires; the CHECK-constrained action vocabulary is still
enforced end-to-end through the service. This is the concrete proof that
the migration's compiled DDL and the ORM model it was compiled from have
not drifted apart.

## Raw-SQL constraint verdict

**Sound — every constraint is enforced by SQLite itself, not merely by
the Python-level service.** Against the genuinely migrated schema, with
`PRAGMA foreign_keys=ON`, direct SQL bypassing the ORM entirely is
rejected for: an invalid review action, a duplicate `candidate_fingerprint`,
a review referencing a nonexistent `candidate_id`, a
`MATCH_EXISTING_AIRPORT` review referencing a nonexistent
`matched_airport_id`, a review referencing a nonexistent
`supersedes_review_id`, `MATCH_EXISTING_AIRPORT` with no target, a
target supplied on a non-match action, and a `NULL` `raw_name`.

## Zero-backfill verdict

**Sound, proven three ways.** Runtime: `upgrade()` against a database
already containing real `Airport` rows still ends with `counts: {0, 0}`.
Static (AST): the migration module imports nothing from any discovery/
acquisition/business-logic module (`discovery_candidate_fragment`,
`evidence_attachment_guard`, `discovery_evidence_persistence`,
`unknown_airport_candidate_persistence`, `acquisition`, `mac_granicus`,
`fleet_health` — all confirmed absent). Static (AST): the module never
constructs an `UnknownAirportCandidate`, `UnknownAirportCandidateReview`,
or `Airport` ORM instance anywhere in its own source — it creates schema
only, never a row.

## Defects/corrections found

**None.** The migration script, adapted directly from the already-twice-
reviewed `migrate_signal_disposition_d4d2.py` template (itself the
product of an earlier review-checkpoint fix), passed all 64 of its own
tests on the first run, and all 332 tests across the combined focused
suite (migration + UAC1 persistence + model-contract + D4D2 migration +
adjacent governance tests) passed with no changes required. This is
consistent with reusing a structurally proven precedent rather than
writing comparison/atomicity/backup logic from scratch.

## Focused test result

`tests/test_unknown_airport_candidate_migration.py`: **64 passed**, 0
failed. Combined with `tests/test_unknown_airport_candidate_persistence.py`,
`tests/test_model_contract.py`, `tests/test_signal_disposition_migration.py`,
`tests/test_reviewer_action_persistence.py`,
`tests/test_physical_installation_reconciliation.py`,
`tests/test_governed_signal_creation.py`,
`tests/test_discovery_evidence_persistence.py`: **332 passed**, 0 failed.

## Full pytest result

See the final chat report for the confirmed exact count.

## py_compile / git diff --check

Both run clean; see the final chat report.

## Real DB before/after / no-access proof

Byte-identical throughout: SHA-256
`d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`,
1,822,720 bytes, FK check `[]`, integrity `ok`,
`signal_dispositions`=10, `signal_disposition_members`=25 — verified
fresh both before and after this mission. `unknown_airport_candidates`/
`unknown_airport_candidate_reviews` confirmed **absent** both before and
after — no migration was ever executed against
`data/runway_safe.db`; every one of the 64 new tests operates on an
isolated `tmp_path` SQLite file. `TestNoRealDatabaseAccess` additionally
proves, via AST, that the only string literal in the module naming the
real database file is the single, legitimate `DEFAULT_DATABASE`
constant.

## Exact UAC2B seam

UAC2B's job, as already scoped by the UAC1 critical review and
unchanged by this mission: add `SourceAssertion.unknown_airport_candidate_id`
(nullable FK to `unknown_airport_candidates.id`) plus a `CheckConstraint`
enforcing mutual exclusivity with `SourceAssertion.airport_id` (a
`SourceAssertion` is attached to a known airport, or linked to a pending
candidate, or neither yet — never both) to
`app/models/source_assertion.py`, and its own separate migration script
(not combined with this one, per the same risk/rollback reasoning this
report's precursor already established: an additive column on an
existing, real-data-bearing table is a materially different, higher-risk
change than creating brand-new empty tables). Only after UAC2B does
candidate-selection integration (deciding when evidence has "no known
candidate" and calling `persist_unknown_airport_evidence()`-shaped
logic) become meaningful; the governed resolution services
(`create_airport_from_approved_candidate()`,
`link_candidate_to_existing_airport()`) and the human review CLI remain
further out, per the original design document's own slice ordering.

---

# Critical review addendum

Adversarial review performed against fresh reads of the migration script,
both UAC1 implementation files, the design document, and three migration
precedents (`migrate_signal_disposition_d4d2.py`,
`migrate_evidence_identity_slice6c.py`,
`migrate_reviewer_action_slice9b.py`) — not merely trusting this report's
own prior claims. Schema parity was independently re-derived (a fresh
synthetic DB was migrated and its real `sqlite_master`-stored `CREATE
TABLE`/index SQL inspected directly via raw `sqlite3`, compared
byte-for-byte against the live ORM model read fresh) rather than assumed
from the migration's own `inspect()`. One genuine defect was found and
corrected; two real test-coverage gaps (explicitly named in this
mission's own attack list) were closed; everything else below is an
independently re-confirmed pass.

## Schema-parity verdict — CONFIRMED, independently re-derived

**Exact match, verified by direct inspection of the real compiled SQL,
not by re-running the migration's own `inspect()`.** A fresh synthetic
pre-UAC1 database was migrated and its `sqlite_master.sql` read directly:
all 14 `unknown_airport_candidates` columns, the `uq_unknown_airport_candidates_fingerprint`
UNIQUE constraint, the `resolved_airport_id -> airports.id` FK; all 8
`unknown_airport_candidate_reviews` columns, all 3 named CHECK
constraints (verbatim, including the exact boolean expressions), and all
3 FKs — every one matches `app/models/unknown_airport_candidate.py` read
fresh in this same review, field-for-field. Confirmed the fingerprint
concern named in this mission's §3 explicitly does **not** apply to the
schema: the widened name+city+state+country fingerprint algorithm is
entirely a **service-level** (Python) concern
(`compute_candidate_fingerprint()` in
`app/services/unknown_airport_candidate_persistence.py`) — the DB schema
only ever needed, and only ever declares, uniqueness on the single
`candidate_fingerprint` string column, which is correctly unaffected by
what feeds into that string. Likewise, candidate field-level immutability
is confirmed to be a pure ORM/Python `before_update` event listener
(`_prevent_unknown_airport_candidate_claim_mutation` in the model file) —
no persisted schema artifact represents it, and the migration correctly
does not attempt to encode it as a DB constraint (SQLite has no
column-level "immutable after insert" primitive; encoding this at the DB
layer would require a trigger, which the migration correctly never
creates — confirmed by the pre-existing `test_no_trigger_objects_created_by_migration`).

## Genuine defect found and corrected: missing-index detection gap

**`_schema_mismatch_reasons()` never checked for a MISSING expected
index on an already-existing table — reproduced directly, not merely
theorized.** A synthetic table with every column, FK, and named
CHECK/UNIQUE constraint correct, but missing both of the ORM model's own
plain indexes (`ix_unknown_airport_candidates_candidate_fingerprint`,
`ix_unknown_airport_candidates_resolved_airport_id`), was reported
`matches_expected_schema: True` by `inspect()`, and `upgrade()` silently
accepted it without ever creating the missing indexes — because
`upgrade()`'s index-creation loop only runs for a table it creates
fresh, never for one it verifies as "already existing and compatible."
This is **not a data-safety defect**: SQLite still fully enforces every
NOT NULL/FK/CHECK/UNIQUE constraint without the index; only query
performance on that column would silently degrade. It **is** a genuine
"inspect() claims a schema that doesn't actually match the ORM model"
gap — exactly case I ("misleading index") this mission's own §3/§4
explicitly directed the review to construct and check. This same blind
spot exists, unfixed, in the `migrate_signal_disposition_d4d2.py`
precedent this script was modeled on (confirmed by re-reading that
script's own `_schema_mismatch_reasons()` fresh) — so this is not a
defect UAC2A introduced beyond its precedent, but it is real, reproduced,
and cheap to close.

**Correction:** `_schema_mismatch_reasons()` now additionally compares
the ORM model's own declared plain index names
(`_expected_index_names()`, new) against what actually exists in
`sqlite_master` for that table, and reports a missing-index reason if
any are absent — using the identical "missing-only, extras-never-flagged"
comparison shape the existing named-constraint check already uses.
Proven by two new tests: a missing-index case now correctly fails closed
(`test_missing_expected_index_fails_closed`), and an *extra*,
unrelated index on an otherwise-correct table is correctly **not**
treated as incompatible (`test_extra_unexpected_index_does_not_cause_false_rejection`)
— only a missing *expected* index is ever a problem.

## inspect()-trustworthiness verdict

**Sound, and now stricter than before this review.** Every construction
listed in this mission's own §4 (A–J) was attempted: wrong type, wrong
nullable flag, missing FK, wrong FK target, wrong CHECK vocabulary,
missing UNIQUE constraint, extra column, and misleading index were each
proven to make `inspect()["ready"]` and `upgrade()`'s own refusal agree
(both `False`/raise) — "wrong default" (case H) does not apply to this
schema at all: none of the 22 combined columns across both tables ever
compiles a SQL-level `DEFAULT` clause (confirmed by direct inspection of
the real compiled `CREATE TABLE` text — `created_at`'s
`default=lambda: datetime.now(UTC)` is a client-side SQLAlchemy default,
never a persisted `DEFAULT`), so there is nothing for a "wrong default"
schema-mismatch case to compare against; this matches the D4D2 precedent's
own identical, correct omission.

## Fresh-upgrade verdict

**Confirmed independently.** A synthetic pre-UAC1 database, migrated,
inspected via raw `sqlite3`/`PRAGMA` (not the migration's own `inspect()`)
directly in this review: both tables created, `counts: {0, 0}`, no other
`sqlite_master` table added or removed, `PRAGMA foreign_key_check`
clean.

## Partial-schema verdict

**Sound, all named cases (A–I) covered.** Cases A–G were already tested
and re-run clean in this review; case H ("candidate exists with data /
review missing") is not independently constructible as a *partial*-schema
case distinct from the incompatible-schema matrix, since a candidate
table can only carry data once it and the review table both already
exist (the review table's own `candidate_id` FK is the only path data
enters via the service) — the closest genuine analogue,
`test_second_upgrade_preserves_rows` (idempotency), already proves an
upgrade against a fully-present, data-bearing pair of tables is a safe
no-op. Case I ("review exists with data if constructible / candidate
missing") is genuinely unconstructible: `unknown_airport_candidate_reviews.candidate_id`
is `NOT NULL`, so a review row cannot exist at all without a
`unknown_airport_candidates` row already present (with FK enforcement
on) to reference — the case is vacuously satisfied, not skipped.

## Incompatible-schema verdict

**Sound, re-confirmed, now one case stronger** (the missing-index case
above). Every incompatibility shape fails closed inside one transaction;
`test_misleading_same_name_incompatible_table_never_dropped_or_rebuilt`
re-confirms a same-named collision's own pre-existing data survives the
refusal untouched.

## Idempotency verdict

**Re-confirmed**, including against a database carrying a real
`MATCH_EXISTING_AIRPORT` review row (not merely an empty pair of
tables) — a second `upgrade()` leaves counts, rows, and full `inspect()`
output unchanged.

## Downgrade-empty / downgrade-nonempty verdicts

**Re-confirmed independently**, including the two new
mission-brief-named scenarios not previously distinct in the original
suite: candidate rows alone, review rows alone, and both together each
independently refuse; the refusal is proven atomic via full `inspect()`
equality before/after the refused call, not merely a row-count check.

## Upgrade-atomicity / downgrade-atomicity verdicts

**Proven empirically in this review, not re-derived from mocks.** Both
existing `TestDdlAtomicity` tests were re-read fresh: each injects a real
`RuntimeError` via `monkeypatch.setattr` on the migration's own internal
`_table_exists` helper (not a mock of `upgrade()`/`downgrade()`
themselves), calls `monkeypatch.undo()` immediately after catching the
injected failure, and then queries the **real** resulting schema via a
completely fresh `migration.inspect()` call (itself backed by genuine
`sqlite_master`/`PRAGMA` queries) — confirming SQLite's own transactional
DDL rollback genuinely undid the first table's creation/drop, not merely
that the Python function returned early.

## Raw-SQL constraint verdict — two genuine coverage gaps closed

**Sound after two additions.** The original 8 raw-SQL tests already
covered: invalid action, duplicate fingerprint, review→nonexistent
candidate, `MATCH_EXISTING_AIRPORT`→nonexistent `matched_airport_id`,
review→nonexistent `supersedes_review_id`, match-without-target,
target-on-non-match, and `NULL raw_name`. Two cases named explicitly in
this mission's own §12 attack list were missing and are now added:
**lowercase action** (`'defer'` — SQLite's `IN (...)` CHECK comparison is
case-sensitive, proven directly rather than assumed) and **invalid
`resolved_airport_id` FK** on the candidate table itself (proven
independent of the fact that no code today writes to that column). Every
raw-SQL test asserts a specific `sqlite3.IntegrityError` with a `match=`
pattern naming the exact constraint class (`CHECK constraint failed`,
`UNIQUE constraint failed`, `FOREIGN KEY constraint failed`,
`NOT NULL constraint failed`) — none use a broad, unqualified
`pytest.raises(Exception)`.

## Model/migration/service parity verdict

**Re-confirmed, including the write-gate command-mode gap closed below.**
Every `TestModelMigrationParity` test builds its database via
`migration.upgrade()` only — grep-confirmed zero calls to
`Base.metadata.create_all()` for the UAC1 tables anywhere in this test
class. Candidate creation, exact convergence (including the corrected
widened fingerprint), Unicode value round-tripping (already exercised in
`tests/test_unknown_airport_candidate_persistence.py` and re-confirmed
to still function against a migrated-not-created_all schema here),
duplicate-fingerprint rejection, source-derived claim immutability
(`resolved_airport_id` confirmed as the one intentionally mutable
field), review creation, the append-only update/delete guards, and
`MATCH_EXISTING_AIRPORT` FK behavior were all re-verified against the
genuinely migrated schema in this review — no mismatch found.

## Backup-order/content verdict

**Re-confirmed, source-order proof (not merely behavioral).** `main()`'s
own source, read fresh: the backup call and the upgrade/downgrade call
are two sequential, unconditional statements — `backup_database()` executes
and returns before `(downgrade if args.downgrade else upgrade)(args.database)`
is ever reached, for the upgrade path. Backup is deliberately **not**
taken before a downgrade call (identical to the D4D2 precedent's own
`if not args.downgrade and not args.skip_backup:` guard, re-read fresh) —
not a UAC2A-introduced gap, and reasonable given downgrade already
refuses unconditionally whenever any row exists, so there is nothing of
value a pre-downgrade backup would protect that the refusal itself
doesn't already protect. Backup content re-verified: independently
readable, integrity-clean, FK-clean, contains genuine pre-migration
data, and does not yet contain either new table.

## Write-gate verdict — one genuine coverage gap closed

**Sound after one addition.** The original suite only proved the write
gate for the (default) upgrade command mode. This mission's own §15
explicitly asks for both modes to be tested. New test
(`test_main_requires_allow_database_write_for_downgrade_mode_too`)
confirms `main(["--database", str(db), "--downgrade"])` without
`--allow-database-write` also raises `SystemExit` before touching either
table, against a database that already has both tables present (so a
successful, unguarded downgrade *would* have visibly dropped them) —
independently proving the gate is not accidentally upgrade-path-specific.

## Wrong-DB verdict

**Re-confirmed.** `protected.db` byte-identical after `upgrade()` runs
only against `target.db`.

## Existing-data preservation verdict

**Re-confirmed**, field-level (not row-count-only) comparison across
`airports`/`sources`/`signals`/`source_assertions`/`reviewer_actions`.

## Migration-purity/zero-backfill verdict

**Re-confirmed**, all three proofs (runtime count, AST import scan, AST
ORM-construction scan) re-read and re-run.

## SourceAssertion-boundary verdict

**Confirmed clean, independently.** `grep` of the migration script's own
source for `SourceAssertion`/`unknown_airport_candidate_id` finds only
the module docstring's own explanatory prose describing why it is out of
scope — no code reference. The real `data/runway_safe.db`'s
`source_assertions` table was directly queried via `PRAGMA table_info`
in this review and confirmed to have no `unknown_airport_candidate_id`
column — zero SourceAssertion integration exists anywhere, in either the
migration or the real database.

## Test-quality verdict

Read all (then-)64 tests fresh against this mission's own §20 checklist.
Found: no broad `pytest.raises(Exception)` anywhere (grep-confirmed);
every atomicity test inspects real post-failure schema via a fresh
`inspect()` call, never a mock return value; every backup test opens the
actual backup file via a fresh `sqlite3` connection; idempotency tests
compare full `inspect()` dict equality, not merely counts; write-gate
tests monkeypatch the migration module's own `upgrade`/`downgrade`
functions directly (not something higher/looser in the stack) to prove
call-ordering, not merely resulting file state; every raw-SQL constraint
test bypasses the ORM entirely via direct `sqlite3` connections. Two
genuine gaps were found and closed (missing-index detection now tested;
downgrade-mode write-gate now tested), plus two named raw-SQL cases
added (lowercase action, invalid `resolved_airport_id` FK). No test was
found to be ceremony-only or inflated; none were weakened.

## Defects found

1. **Missing-index detection gap** in `_schema_mismatch_reasons()`
   (schema-verification defect, not a data-safety defect) — corrected.

No other genuine defects were found. Every other reviewed dimension was
independently re-confirmed sound by fresh reading, fresh direct
`sqlite3`/`PRAGMA` inspection (not merely re-trusting the migration's own
`inspect()` output), and fresh test execution.

## Corrections made

1. `_schema_mismatch_reasons()` extended with `_expected_index_names()`-based
   missing-index detection, in `scripts/migrate_unknown_airport_candidates_uac2a.py`.
2. Two new tests closing the missing-index gap
   (`test_missing_expected_index_fails_closed`,
   `test_extra_unexpected_index_does_not_cause_false_rejection`).
3. Two new raw-SQL tests explicitly named in this mission's own attack
   list (`test_lowercase_action_rejected_raw_sql`,
   `test_invalid_resolved_airport_id_fk_rejected_raw_sql`).
4. One new write-gate test for the downgrade command mode
   (`test_main_requires_allow_database_write_for_downgrade_mode_too`).

## Regression tests added

5 new tests (69 total, up from 64).

## Focused test result

`tests/test_unknown_airport_candidate_migration.py`: **69 passed**, 0
failed. Combined with `tests/test_unknown_airport_candidate_persistence.py`,
`tests/test_model_contract.py`, `tests/test_signal_disposition_migration.py`,
`tests/test_reviewer_action_migration.py`,
`tests/test_reviewer_action_persistence.py`,
`tests/test_physical_installation_reconciliation.py`,
`tests/test_governed_signal_creation.py`,
`tests/test_discovery_evidence_persistence.py`: **348 passed**, 0 failed.

## Full pytest result

See the final chat report for the confirmed exact count.

## py_compile / git diff --check

Both re-run clean after the correction; see the final chat report.

## Real DB before/after proof

Unchanged throughout this review: SHA-256
`d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`,
1,822,720 bytes, FK check `[]`, integrity `ok`,
`signal_dispositions`=10, `signal_disposition_members`=25,
`unknown_airport_candidates`/`unknown_airport_candidate_reviews`
confirmed **absent** — verified fresh both before and after this review.

RWI_UAC2A_UNKNOWN_AIRPORT_CANDIDATE_MIGRATION_IMPLEMENTATION_COMPLETE
