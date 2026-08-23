# RWI EB2 — EvidenceBag Persistence Schema Migration

Implementation report. Slice 2 of
`docs/architecture/rwi-full-evidencebag-persistence-design.md`. Synthetic
implementation only — not committed by this mission; a separate
adversarial EB2 review checkpoint commits/pushes if sound.

## 1. Scope

EB2 implements exactly one thing: the migration script for both EB1
tables (`source_assertion_evidence_bags`, `identity_guard_evaluations`),
migrated as one atomic unit. No discovery-persistence wiring, no
EvidenceBag write service, no re-evaluation service, no CLI changes, no
real database migration.

## 2. Files

**Created:**
- `scripts/migrate_evidence_bag_persistence_eb2.py`
- `tests/test_evidence_bag_migration.py` (52 tests)
- This report.

**Modified:** none.

## 3. Files read fresh

`docs/architecture/rwi-full-evidencebag-persistence-design.md`,
`docs/architecture/rwi-eb1-evidencebag-persistence-foundation-report.md`
(including its critical-review addendum),
`app/models/source_assertion_evidence_bag.py`,
`app/models/identity_guard_evaluation.py`, `app/models/source_assertion.py`,
`app/models/airport.py`, `app/models/__init__.py`,
`app/services/evidence_bag_serialization.py`,
`scripts/migrate_unknown_airport_candidates_uac2a.py`,
`scripts/migrate_source_assertion_unknown_airport_uac2b.py`,
`scripts/migrate_signal_disposition_d4d2.py` (the primary template — the
closest existing precedent for "two new tables, one with a hard
dependency on the other, migrated atomically"), plus that migration's own
test file for CLI/backup/atomicity test conventions.

## 4. Migration API

`inspect(database) -> dict`, `upgrade(database) -> None`,
`downgrade(database) -> None`, `backup_database(database, backup_directory=...) -> Path`,
`main(argv=None) -> int` — the identical five-function shape every prior
migration in this repository already establishes. `TABLES = ("source_assertion_evidence_bags", "identity_guard_evaluations")`
— parent before child, reflecting the composite-FK dependency.

## 5. Snapshot-table schema verdict

Compiled fresh from `Base.metadata.tables["source_assertion_evidence_bags"]`
— never hand-typed. Verified via direct `PRAGMA table_info` inspection:
6 columns (`id`, `source_assertion_id`, `evidence_bag_json`,
`evidence_bag_hash`, `schema_version`, `created_at`), all `NOT NULL`,
`id` the sole PK. FK to `source_assertions.id`. The composite-FK-
supporting `UniqueConstraint("id", "source_assertion_id")` compiles to
an auto-named SQLite index (`sqlite_autoindex_..._1`), alongside the
plain `unique=True` on `source_assertion_id` itself
(`sqlite_autoindex_..._2`) and the named `evidence_bag_hash` index —
confirmed present via `PRAGMA index_list`/`index_info`.

## 6. Evaluation-table schema verdict

Compiled fresh from `Base.metadata.tables["identity_guard_evaluations"]`.
8 columns, `triggering_review_id` the sole nullable one, `id` the sole
PK. Four FK targets on three columns (`evaluated_against_airport_id` →
`airports.id`, `triggering_review_id` → `unknown_airport_candidate_reviews.id`,
plus `source_assertion_id` participating in BOTH a plain FK to
`source_assertions.id` AND the composite FK below). The `outcome` CHECK
constraint's exact vocabulary (`ATTACH_CONFIRMED`, `ATTACH_PROVISIONAL`,
`REVIEW_REQUIRED`, `REJECT_CROSS_AIRPORT`, `INSUFFICIENT_IDENTITY`)
confirmed present verbatim in the stored `sqlite_master.sql` text.

## 7. Composite-FK verdict — the primary requirement

**Sound, and correctly distinguished from a superficially similar but
semantically weaker alternative.** `PRAGMA foreign_key_list(identity_guard_evaluations)`
reports the composite constraint as two rows sharing the same leading
`id` value (SQLite's own grouping mechanism for multi-column FKs) —
confirmed by direct inspection. A naive flat-set comparison of
`(from_column, ref_table, to_column)` tuples (the shape
`migrate_signal_disposition_d4d2.py`'s own `_expected_foreign_keys()`
uses, sufficient for that migration's exclusively single-column FKs)
cannot distinguish one genuine two-column composite constraint from two
entirely independent single-column FKs that coincidentally target the
same two columns separately — the latter would NOT enforce causal
integrity at all (each column would validate against ANY matching row
independently, not the SAME row together). This migration's own
`_expected_foreign_key_groups()`/`_actual_foreign_key_groups()` instead
compare a set of frozensets, one per real constraint (expected side:
`Table.foreign_key_constraints`, which SQLAlchemy already groups
correctly; actual side: `PRAGMA foreign_key_list` rows grouped by their
own leading `id` column). Verified directly: a hand-crafted attack table
using two separate single-column FKs instead of the genuine composite
constraint was correctly detected as a mismatch and refused
(`IncompatibleExistingSchemaError`) during implementation-phase manual
probing, and is now a permanent regression test
(`TestPartialAndIncompatibleSchema`/`TestCompositeForeignKey`). A raw-SQL
cross-assertion insert attempt against a genuinely migrated schema is
rejected with `FOREIGN KEY` `IntegrityError`; the correct same-assertion
pairing succeeds.

## 8. inspect-trustworthiness verdict

`inspect()["ready"]` is computed via the exact same
`_table_matches_expected_schema()`/`_schema_mismatch_reasons()` function
`upgrade()` itself calls to decide whether an existing table is
compatible — the identical shared-implementation discipline
`migrate_signal_disposition_d4d2.py`'s own review checkpoint established,
reused verbatim. `inspect()` can never report `ready: True` for a schema
`upgrade()` would refuse, because both ask the literal same question.

## 9. Fresh-upgrade verdict

Sound: both tables created, both empty, `ready: True`,
`foreign_key_check: []`. No existing table's rows were touched
(`SourceAssertion`/`Airport` field-level snapshots compared before/after,
identical).

## 10. Partial-schema verdict

Sound, dependency-ordered: `source_assertion_evidence_bags` is always
verified/created before `identity_guard_evaluations` is ever touched.
Snapshot-correct-evaluation-missing completes safely (creates only the
missing table). Snapshot-incompatible fails the ENTIRE upgrade before
evaluation is even inspected (proven: the evaluation table is confirmed
absent from `sqlite_master` after the refused attempt). Snapshot-correct-
evaluation-incompatible fails closed while leaving the snapshot table's
own already-correct state genuinely undisturbed.

## 11. Incompatible-schema verdict

Sound: both-incompatible refuses before any table is touched; a single
wrong column type/nullability, a missing FK, a missing composite FK
group, a missing supporting UNIQUE constraint, or an extra/missing
column-shaped index are each independently caught by the four-part
`_schema_mismatch_reasons()` comparison (columns, FK groups, index
shapes, named-constraint substring presence).

## 12. Idempotency verdict

Sound: a second `upgrade()` call against an already-correct schema is a
genuine no-op, verified both structurally (`inspect()` result identical
before/after) and at the row level — a populated fixture (Unicode/
comma/quote/newline `EvidenceBag`, multiple evaluation history rows)
was snapshotted field-for-field before and after a second `upgrade()`
call; identical, including `created_at` timestamps and the exact
serialized payload/hash bytes.

## 13. Downgrade policy — deliberately strengthened beyond the original design doc

**A documented, deliberate departure from `rwi-full-evidencebag-persistence-design.md`'s
own original §17 suggestion** ("refuse only if any `identity_guard_evaluations`
row exists"), per this mission's own explicit instruction to challenge
that recommendation rather than follow it blindly. `SourceAssertionEvidenceBag`
rows are themselves governed, immutable audit history in their own right
— the model's own docstring already says exactly this ("auditable and
cannot be deleted") — independent of whether any evaluation ever reads
them. `downgrade()` therefore refuses if EITHER table contains any row
at all, not only the evaluation table. Verified: a snapshot-only
populated database (zero evaluations) correctly refuses downgrade;
an evaluation-populated database also refuses; both-empty downgrades
cleanly in FK-safe order (`identity_guard_evaluations` dropped before
`source_assertion_evidence_bags`).

## 14. Upgrade atomicity

Real SQLite transactional DDL, not mocked: a failure injected between
creating the two tables (via `monkeypatch` on `_table_exists`, matching
`migrate_signal_disposition_d4d2.py`'s own proven injection technique)
is followed by a confirmed rollback — `inspect()` afterward shows
NEITHER table exists, not a half-created state.

## 15. Downgrade atomicity

Same technique, reversed: a failure injected between dropping the two
tables leaves BOTH tables fully intact (`ready: True` afterward) — no
half-dropped schema survives.

## 16. Raw-SQL snapshot constraints

Verified against a genuinely migrated schema with `PRAGMA foreign_keys=ON`:
valid snapshot insert accepted; a second snapshot for the same
`SourceAssertion` rejected (`UNIQUE`); a snapshot for a nonexistent
`SourceAssertion` rejected (`FOREIGN KEY`); the identical evidence
content across two genuinely different assertions accepted (uniqueness
boundary is assertion identity, never content hash — matching EB1's own
already-proven behavior). No hash-format CHECK exists on
`evidence_bag_hash` (correctly — the ORM model itself declares none;
this migration invents no new constraint beyond what the committed
model already specifies).

## 17. Raw-SQL evaluation constraints

Verified: a valid evaluation succeeds; an invalid/lowercase/padded
decision string is rejected by the CHECK constraint; a nonexistent
`SourceAssertion`/snapshot/Airport reference is rejected by the relevant
FK; the cross-assertion snapshot mismatch is rejected by the composite
FK (§7); a legitimate repeated-identical evaluation and a later changed-
decision evaluation both succeed (append-only, never deduplicated).

## 18. ORM/migration parity

The database in every parity test was built via `migration.upgrade()`
ONLY — `Base.metadata.create_all()` is never used for the two EB tables
anywhere in this test file (grep-confirmed: `test_evidence_bag_migration.py`
calls `_pre_eb2_db()`, which explicitly excludes the two EB tables from
its own `create_all()` scope, then `migration.upgrade()` creates them
for real). Against that genuinely migrated schema: `SourceAssertionEvidenceBag`
insert and one-to-one UNIQUE enforcement work; the ORM's own
`before_update` immutability listener fires; append-only evaluation
history (2 rows for one assertion) persists correctly; the `outcome`
CHECK rejects an invalid value via the ORM insert path; the composite
causal FK rejects a cross-assertion ORM insert; `SourceAssertion`/
`Airport`/snapshot deletion is blocked by FK when governed rows
reference them (with `PRAGMA foreign_keys=ON` genuinely confirmed active
on the connection under test, not merely assumed from event-listener
source).

## 19. Serialization/migration parity

A full `EvidenceBag` fixture — every one of the eleven identity-relevant
fields populated, including Swedish/Portuguese/Japanese/Arabic names,
embedded commas, quotes, a newline, a tab, and an emoji document title —
was serialized via the real `serialize_evidence_bag()`, persisted into a
genuinely migrated database, and read back through a completely fresh
engine/session. `deserialize_evidence_bag()` reconstructed it to exact
equality; `hash_serialized_evidence_bag()` on the re-read column value
matched the original hash exactly. A second test reads the raw
`evidence_bag_json` TEXT column value directly via `sqlite3` (bypassing
the ORM entirely) and confirms it is byte-for-byte identical to what
`serialize_evidence_bag()` produced — proving the migrated `TEXT`
column type neither truncates nor alters the payload, even for a long
repeated-Unicode string.

## 20. Payload/hash/schema-version consistency boundary

**Confirmed unchanged from EB1's own already-reviewed, already-accepted
boundary — this migration neither invents nor forecloses the future EB3
enforcement.** A direct raw-SQL insert with a `evidence_bag_hash` that
does not match `evidence_bag_json` succeeds today against the migrated
schema, exactly as it does against the ORM-only `create_all()` schema in
EB1's own test suite — the migrated column types (`TEXT`, `VARCHAR(64)`,
`INTEGER`) impose no normalization, truncation, or default-rewriting of
any kind that would make a future `@validates`-style consistency check
harder to add later. Documented, not silently glossed over:
`TestPayloadHashSchemaBoundary`.

## 21. Legacy-row verdict

Sound: a `SourceAssertion` seeded before `upgrade()` runs is
field-for-field identical afterward (including `identity_guard_decision`/
`identity_guard_reason`, `created_at`, and every raw/audit field);
zero snapshot rows and zero evaluation rows are automatically created for
it. Absence remains the sole completeness signal, exactly as EB1's own
design requires.

## 22. Existing-data preservation

A representative multi-domain fixture (`SourceAssertion`, `Airport`,
`UnknownAirportCandidate`, `UnknownAirportCandidateReview`) was
snapshotted across every pre-existing table's own row count before
`upgrade()`; after, the only table-set delta is exactly the two new EB
tables, and every pre-existing row count and every individually-checked
row's own identity are unchanged.

## 23. FK/delete safety

Verified against a genuinely migrated schema with `PRAGMA foreign_keys=ON`:
deleting a `SourceAssertion` with a linked snapshot is blocked; deleting
an `Airport` referenced by an evaluation is blocked; deleting a snapshot
referenced by an evaluation is blocked; deleting a genuinely unreferenced,
unrelated row (a spare `Airport`) succeeds normally, confirming the FK
blocks above are specific to genuinely-referenced rows, not a blanket
delete failure.

## 24. Backup/write-gate verdict

Sound, matching every prior migration's own proven pattern: `--allow-database-write`
required before any mutation (`SystemExit` otherwise, verified both by
direct `main()` invocation and by monkeypatching `upgrade()`/`downgrade()`
to fail loudly if ever called without it); a timestamped backup is
created before any write via `main()`; the backup is independently
readable, passes `PRAGMA integrity_check`/`foreign_key_check`, contains
the genuine pre-EB2 schema and data, and — critically — remains frozen
at its own pre-migration state even after the source database is
subsequently mutated by `upgrade()`.

## 25. Wrong-DB verdict

Sound: migrating a `target.db` leaves a separate, untouched `protected.db`
byte-identical, confirmed both by raw byte comparison and by re-running
`inspect()` against each (target reports `ready: True`, protected still
reports `ready: False`).

## 26. Zero-backfill/migration-purity verdict

Sound: `upgrade()` never inserts a row into either new table under any
circumstance, verified even against a database with multiple pre-existing
`SourceAssertion` rows already present. AST-confirmed: no import of
`discovery_evidence_persistence`, `unknown_airport_discovery_integration`,
`evidence_attachment_guard`, `evidence_bag_serialization`,
`unknown_airport_candidate_resolution`, `governed_signal_creation`,
`promotion_policy`, or `intelligence_review` anywhere in the migration
module.

## 27. Defects/corrections found

**No production defects found.** Two test-construction issues were found
and fixed during implementation, neither a production defect:
1. `test_orm_composite_causal_fk_against_migrated_schema`'s first draft
   opened its own `create_engine()` without registering the
   `PRAGMA foreign_keys=ON` connect listener (the identical class of
   fixture bug EB1's own adversarial review already found and fixed once
   before) — the composite-FK protection itself was never in doubt (the
   raw-SQL sibling test in the same file, and an ad-hoc manual probe
   during implementation, both already proved it works); only this one
   ORM-level test's own fixture was incomplete. Fixed by adding the
   listener.
2. The initial "no real database path literal" AST check flagged the
   migration's own legitimate, required `DEFAULT_DATABASE = Path("data/runway_safe.db")`
   constant — the exact same convention every prior migration script in
   this repository already uses. Corrected to allow specifically that
   one top-level assignment's own string node, while still failing on
   any OTHER occurrence of the literal anywhere else in the module.

## 28. Focused tests

`tests/test_evidence_bag_migration.py`: **52 passed**, 0 failed. Combined
broader suite (EB2 + EB1 + model-contract + D4D2/UAC2A migration
precedents): **266 passed**, 0 failed.

## 29. Full pytest

See the final chat report for the confirmed exact count.

## 30. py_compile / git diff --check

Both clean.

## 31. Real database safety

Verified before and after this mission: SHA-256
`d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`, size
1,822,720 bytes, `signal_dispositions`=10, `signal_disposition_members`=25,
`PRAGMA foreign_key_check`=[], `PRAGMA integrity_check`=ok, UAC schema
and both EB tables remain entirely absent. All fixtures in this slice's
tests use `tmp_path`-scoped SQLite files only. No internet access was
used or required.

## 32. Commit policy (implementation phase)

Not committed, not pushed by the implementation phase. See the review
addendum below for the actual commit/push disposition.

---

# Critical review addendum

Adversarial review performed against fresh reads of the design doc, EB1
report, EB2 report, the actual production code, and extensive direct
empirical attacks (not merely re-trusting this report's own claims). One
genuine production defect was found and fixed; every other named attack
matrix confirmed the implementation already sound, including several
attacks that revealed my own earlier probing had been flawed rather than
the production code.

## Independent ORM/schema parity verdict

**Sound, re-derived from scratch.** Read `app/models/source_assertion_evidence_bag.py`/
`app/models/identity_guard_evaluation.py` fresh and independently
compiled the expected SQLite shape via direct Python introspection
(`Base.metadata.tables[...]`, `PRAGMA table_info`/`foreign_key_list`/
`index_list`/`index_info` against a freshly migrated database) rather
than trusting EB2's own comparison functions. Confirmed exact match on
columns/types/nullability/PK for both tables, the four FK targets on
`identity_guard_evaluations` (three single-column, one composite), and
the full index/UNIQUE-constraint shape on `source_assertion_evidence_bags`
(one named plain index, one composite UNIQUE, one single-column UNIQUE).

## Composite-FK grouping verdict — primary attack

**Sound, attacked directly with the full named matrix, not merely the
report's own claims re-read.** Constructed and tested: (B) two
independent single-column FKs with column-pair targets identical to the
genuine composite's own members — correctly refused by both
`inspect()`/`upgrade()`, since the FK-group comparison (not a flat
tuple-set) correctly distinguishes "two separate constraints" from "one
constraint spanning two columns." (C/D) reversed child/parent column
order — both are genuine semantic mismatches (not merely formatting;
reversing one side without the other changes which column maps to
which), correctly refused. (E) one correct + one wrong member — SQLite
itself accepted this specific malformed shape at DDL time in isolated
re-testing (contradicting an initial ad-hoc probe during this same
review that appeared to show SQLite refusing it outright — the
discrepancy was investigated directly, not assumed; see "Corrections
made" below), but the migration's own comparison independently and
correctly rejects it regardless, since the resulting FK group targets
the wrong parent column. (G) correct composite plus an extra, unexpected
single-column FK — correctly refused (an extra, unreviewed constraint is
itself a mismatch). `PRAGMA foreign_key_list` was independently
re-confirmed to group multi-column FK rows by a shared leading `id`
value — the exact mechanism `_actual_foreign_key_groups()` relies on.

## Raw-SQL causal-integrity verdict

**Sound, re-attacked directly against a genuinely migrated database.**
Two `SourceAssertion`s, two snapshots; an `IdentityGuardEvaluation`
insert naming assertion A's id alongside assertion B's own snapshot id
fails with `sqlite3.IntegrityError` whose message names `FOREIGN KEY`
specifically (confirmed via direct exception inspection, not merely
"any exception") — never a CHECK or NOT NULL violation, confirming the
correct constraint is the one firing. The correct same-assertion pairing
succeeds.

## Supporting-UNIQUE verdict

**Sound, re-attacked with the full named matrix, one genuine test-
authoring gap in my own re-verification found and corrected along the
way (see "Corrections made" - not a production defect).** (A) missing
composite UNIQUE — refused. (B) a non-unique index covering the same two
columns instead of a UNIQUE constraint — refused (the shape comparison
correctly tracks the `unique` flag, not merely column membership). (D/E)
correct UNIQUE constraint with its declared column order reversed
(`UNIQUE(source_assertion_id, id)` instead of `UNIQUE(id,
source_assertion_id)`) is CORRECTLY ACCEPTED as equivalent — a UNIQUE
constraint has no directionality, unlike the composite FK's own from/to
pairing, and the shape comparison's own `frozenset(column_names)`
representation is rightly order-insensitive here. An extra, unrelated
UNIQUE constraint is correctly refused.

## Schema-mismatch matrix verdict

**Sound**, all named categories re-confirmed via the composite-FK and
supporting-UNIQUE matrices above plus the pre-existing wrong-type/
wrong-nullability/missing-column/extra-column/missing-CHECK coverage
already in the implementation's own test suite (re-read fresh, confirmed
genuine, not merely asserted).

## inspect()/upgrade() agreement verdict — ONE GENUINE DEFECT FOUND AND FIXED

**Confirmed sound for every case EXCEPT one, found by direct attack, not
by re-reading the implementation's own claims.** `inspect()`'s own
docstring promises it "never mutates anything" — a pure, always-safe
diagnostic. Direct testing found a real violation of that contract: when
`identity_guard_evaluations` already exists (created correctly by an
earlier, genuine `upgrade()` call) and `source_assertion_evidence_bags`
is later, separately altered out from under it (bypassing this migration
entirely — a realistic scenario for any direct, unreviewed schema edit),
`inspect()`'s own unconditional, whole-database `PRAGMA foreign_key_check`
call raised a raw, uncaught `sqlite3.OperationalError` ("foreign key
mismatch") instead of returning a structured result. `upgrade()` was
already safe in the identical scenario — its own per-table
`_schema_mismatch_reasons()` check refuses the incompatible parent BEFORE
ever reaching its own final whole-database check — but `inspect()` had
no equivalent early exit.

**Fixed**: `inspect()`'s own `PRAGMA foreign_key_check` call is now
wrapped; a caught `OperationalError` is represented in the result
(`foreign_key_check: None`, `foreign_key_check_error: <message>`,
`ready` forced `False`) rather than propagating. `upgrade()`'s own
identical final check was given the same treatment for symmetry and
clearer error semantics, even though it was already transactionally safe
(the enclosing rollback already fully protected it) — the fix there only
replaces a raw driver-level exception type with a `RuntimeError` carrying
a clear message, changing no safety property. Verified:
`TestInspectNeverCrashes` (2 new tests) — `inspect()` now returns a
structured, non-`ready` result for the exact scenario that used to crash
it; `upgrade()`'s own already-correct fail-closed behavior for the same
scenario is independently re-confirmed.

This is the review's own single most important finding: `inspect()` is
the function every future caller (a CLI schema gate, exactly like
`scripts/review_unknown_airport_candidate.py`'s own established
`check_schema_readiness()` pattern) will call FIRST, before ever
attempting a write — a diagnostic tool that can itself crash on a
malformed database undermines the entire "fail closed with a clear
signal" philosophy this migration family exists to provide.

## Partial-schema verdict

**Sound**, re-confirmed for every named case including "populated
correct snapshot / evaluation missing" (safely completable) and
"snapshot incompatible / evaluation absent" (fails the entire upgrade
before evaluation is even inspected) - re-verified directly, not merely
re-read from the implementation's own claims.

## Fresh-upgrade verdict

Sound, re-confirmed: both tables created empty, `ready: True`,
`foreign_key_check: []`, zero existing-table mutation.

## Idempotency verdict

Sound, re-confirmed with a populated fixture including Unicode/comma/
quote/tab/newline content and repeated evaluation history — a second
`upgrade()` call changes zero rows, zero timestamps, zero payload bytes.

## Downgrade-policy verdict — challenged directly, confirmed correct

**The strengthened policy (refuse if EITHER table has any row, not only
`identity_guard_evaluations`) is independently re-confirmed correct, not
merely accepted from the implementation's own reasoning.** Re-read
`SourceAssertionEvidenceBag`'s own model docstring fresh: "auditable and
cannot be deleted" — this is asserted unconditionally, with no carve-out
for "unless no evaluation references it yet." A downgrade that dropped a
populated snapshot table merely because zero evaluations happened to
exist would destroy real, immutable evidence with no way to recover it
outside a database backup. Additionally attacked: multiple snapshots
across different assertions (refused, count correctly reflects all of
them); and — the review's own explicit new attack — a genuinely orphaned
snapshot row (referencing a nonexistent `SourceAssertion`, constructible
only with `PRAGMA foreign_keys=OFF`) also correctly blocks downgrade,
because downgrade's own row-count check deliberately does not attempt to
distinguish "valid governed history" from "malformed garbage" — either
way, a human must resolve the state by hand, never have it silently
vanish via a schema downgrade.

## Upgrade-atomicity verdict

Sound, re-confirmed via real injected mid-DDL failure (not mocked) — a
crash between creating the two tables leaves neither one present after
rollback.

## Downgrade-atomicity verdict

Sound, re-confirmed via the same real-failure-injection technique — a
crash between dropping the two tables leaves both fully intact, `ready:
True` afterward.

## Raw-SQL snapshot verdict

Sound, extended with two new attacks the implementation's own test suite
had not yet covered: an arbitrary, non-hex, wrong-length
`evidence_bag_hash` string is accepted (no format CHECK exists on the
committed model, and none should be invented here); a syntactically
invalid JSON string is accepted into `evidence_bag_json` at the DB layer
(no JSON-validity CHECK exists either) but correctly raises when passed
through the real `deserialize_evidence_bag()` — explicitly distinguishing
DB-layer structural guarantees from the separate, already-proven Python-
layer guard.

## Raw-SQL evaluation verdict

Sound, re-confirmed: invalid/lowercase/padded decision strings rejected
by the CHECK; nonexistent `SourceAssertion`/snapshot/Airport references
rejected by the relevant FK; the cross-assertion mismatch rejected by the
composite FK; repeated-identical and changed-decision evaluations both
succeed (append-only).

## ORM/migration parity verdict

Sound, re-confirmed: every parity test builds its database via
`migration.upgrade()` only (grep-confirmed no `Base.metadata.create_all()`
call for the two EB tables anywhere in the test file) — snapshot insert,
one-to-one UNIQUE, ORM immutability, append-only evaluation history, the
decision CHECK, the composite causal FK, and delete/FK safety all
genuinely work against that migrated (not `create_all()`-only) schema.

## Serialization flight-recorder verdict

Sound, extended with an NFC/NFD Unicode-normalization attack (not
previously present in EB2's own test file, only in EB1's) proving the
migrated `TEXT` column does not silently normalize two canonically-
equivalent but codepoint-distinct strings into one.

## EB3-boundary verdict

Confirmed precisely, unchanged from the implementation's own claim: the
migration neither normalizes payload, regenerates hash, coerces
schema_version, nor introduces any DB trigger — a direct raw insert with
a mismatched hash or a malformed JSON payload succeeds today, exactly as
the committed EB1 ORM model itself specifies (no CHECK exists for either),
correctly deferring semantic consistency enforcement to EB3's own future
writer service without foreclosing it.

## Legacy/existing-data preservation verdict

Sound, re-confirmed field-for-field, including `identity_guard_decision`/
`identity_guard_reason` on a legacy `SourceAssertion` remaining byte-
identical after `upgrade()`.

## FK/delete verdict

Sound, re-confirmed against a genuinely migrated schema with `PRAGMA
foreign_keys=ON` verified active on the actual connection under test:
`SourceAssertion`-with-snapshot, `Airport`-referenced-by-evaluation, and
snapshot-referenced-by-evaluation deletions are all blocked; an
unrelated, unreferenced row's deletion succeeds normally (baseline).

## Backup/write-gate verdict

Sound, re-confirmed: `--allow-database-write` required before any
mutation; backup created before write via `main()`; backup independently
readable, passes `integrity_check`/`foreign_key_check`, remains frozen at
its pre-migration state even after the source is later mutated.

## Wrong-DB verdict

Sound, re-confirmed: migrating `target.db` leaves `protected.db`
byte-identical.

## Migration-purity verdict

Sound, re-confirmed via AST import inspection: no discovery/candidate/
guard/reevaluation/promotion/Signal/Airport-creation import anywhere in
the migration module.

## Test-quality verdict

Read every test in the file, including all new additions, against the
mission's own checklist. Found and fixed: (1) the `inspect()` crash gap
itself was UNTESTED before this review — closed; (2) one of my own new
attack tests during this review (`test_sqlite_itself_refuses_a_composite_fk_targeting_the_same_parent_column_twice`)
was built on an incorrect assumption about SQLite's own DDL-time
validation behavior, discovered and corrected during this same review
(see "Corrections made"). Confirmed already sound: no test in the file
builds its migrated-schema fixtures via `create_all()`; every FK-
dependent test either explicitly enables `PRAGMA foreign_keys=ON` on its
own raw `sqlite3` connection or registers the connect-event listener on
its own ORM engine (spot-checked several, all correct); the atomicity
tests inject a real failure via `monkeypatch` on `_table_exists`, not a
mock of the DDL execution itself; no test in the file uses a broad
`except Exception`/`pytest.raises(Exception)` for an assertion where a
specific exception type is knowable and meaningful (the two
`pytest.raises(Exception, match=...)` uses in `TestModelMigrationParity`
are for SQLAlchemy-wrapped `IntegrityError`s where SQLAlchemy's own
wrapping type can vary by call path — matched against message content
instead, a deliberate and reasonable choice, not a laxness).

## Defects found

**One genuine production defect, found and fixed**: `inspect()` could
raise an uncaught `sqlite3.OperationalError` for a specific class of
internally-inconsistent on-disk schema, violating its own "never
mutates, never crashes" documented contract. `upgrade()`'s identical
final check received the same defensive treatment for symmetry, though
it was already transactionally safe.

## Corrections made

1. `scripts/migrate_evidence_bag_persistence_eb2.py`: `inspect()`'s
   `PRAGMA foreign_key_check` call wrapped to catch `sqlite3.OperationalError`
   and represent it in the result rather than raising; `upgrade()`'s
   identical final check given the same treatment for consistency.
2. Two of my own new test-authoring mistakes during this review, neither
   a production defect: a malformed-JSON raw-SQL insert test supplied one
   extra parameter binding than its own SQL string declared placeholders
   for (fixed by removing the redundant literal); a "SQLite itself
   refuses this DDL" test was built on an assumption (that SQLite always
   rejects a composite FK referencing the same parent column twice) that
   did not hold up under a clean, isolated re-test, even though an
   earlier ad-hoc manual probe during this same review session had
   appeared to show it failing — the discrepancy was investigated
   directly rather than either blindly trusted or blindly dismissed, and
   the test was corrected to assert the property that actually matters
   (this migration's own comparison logic rejects the shape, regardless
   of what SQLite's own DDL parser permits).

## Regression tests added

15 new tests (67 total, up from 52):
`test_two_separate_single_column_fks_are_not_accepted_as_composite`,
`test_composite_fk_with_wrong_parent_column_order_rejected`,
`test_correct_composite_fk_plus_extra_redundant_single_column_fk_rejected`,
`test_composite_fk_referencing_the_same_parent_column_twice_rejected`,
`test_missing_supporting_composite_unique_rejected`,
`test_non_unique_index_instead_of_unique_constraint_rejected`,
`test_correct_unique_plus_extra_unrelated_unique_rejected`,
`test_supporting_unique_column_order_is_semantically_irrelevant_and_accepted`,
`test_migration_permits_arbitrary_malformed_hash_string_no_db_format_check`,
`test_migration_permits_malformed_json_payload_no_db_level_json_validation`,
`test_nfc_nfd_unicode_normalization_forms_survive_migrated_columns_distinctly`,
`test_downgrade_refuses_with_multiple_snapshots_across_different_assertions`,
`test_downgrade_refuses_even_for_orphan_rows_created_with_fk_disabled`,
`test_inspect_never_raises_for_an_internally_inconsistent_schema`,
`test_upgrade_raises_a_clean_runtime_error_not_a_raw_operational_error`.

## Focused tests

`tests/test_evidence_bag_migration.py`: **67 passed**, 0 failed. Combined
broader suite (EB2 + EB1 + model-contract + D4D2/UAC2A/UAC2B migration
precedents): **337 passed**, 0 failed.

## Full pytest

See the final chat report for the confirmed exact count.

## py_compile / git diff --check

Both re-run clean after the corrections.

## Real DB before/after proof

Unchanged throughout this review: SHA-256
`d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`,
1,822,720 bytes, FK check `[]`, integrity `ok`,
`signal_dispositions`=10, `signal_disposition_members`=25, UAC schema and
both EB tables confirmed **absent** — verified fresh both before and
after this review.

RWI_EB2_EVIDENCEBAG_PERSISTENCE_MIGRATION_REVIEWED_COMMITTED_AND_PUSHED
