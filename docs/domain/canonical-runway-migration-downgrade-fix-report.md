# Canonical Runway Migration — `downgrade()` Fix

Fixes the `downgrade()` defect identified as a non-blocking follow-up in
[`docs/domain/canonical-runway-foundation-merge-readiness-review.md`](canonical-runway-foundation-merge-readiness-review.md)
§3/§15. **The real database was not touched anywhere in this task** —
every reproduction and every proof below used isolated, throwaway
temporary databases only.

## 1. Original defect

`scripts/migrate_canonical_runway_runway_end_slice1.py::downgrade()`
used SQLite's native `ALTER TABLE physical_installation_identities DROP
COLUMN runway_end_id` to reverse the migration. This repository has no
Alembic (or other) migration-chain system — this file is a small,
narrowly-scoped, additive, idempotent script, not a revisioned
migration, so there is no `revision`/`down_revision` pair to report; its
own `upgrade()`/`downgrade()` functions are the entire "chain."

## 2. Reproduction

Reproduced against isolated temporary databases only (never the real
one), using the full, realistic table graph — not a hand-picked minimal
subset:

- Built every current table via `Base.metadata.create_all()` **except**
  `physical_installation_identities`, which was hand-built in its true
  pre-migration shape (matching the existing
  `tests/test_canonical_runway_runway_end_migration.py::_pre_migration_database()`
  fixture's convention).
- Seeded representative rows exercising the real foreign-key dependency
  graph: an `Airport`, a `Runway`, a `PhysicalInstallationIdentity`, a
  `Source`/`SourceAssertion`, and — critically — an
  `InstallationAssertionLink` whose `physical_installation_id` column
  holds a foreign key **into** `physical_installation_identities`.
- Ran `upgrade()` (succeeded, as always), linked the identity to a real
  `RunwayEnd`, then called `downgrade()`.

**Exact failure**, before the fix, against this realistic setup:

```
sqlite3.OperationalError: error in table physical_installation_identities
after drop column: unknown column "runway_end_id" in foreign key definition
```

The failing operation is the `ALTER TABLE ... DROP COLUMN` statement
itself.

**Why the earlier minimal-schema test missed it:** the existing
`_pre_migration_database()` fixture in
`tests/test_canonical_runway_runway_end_migration.py` builds only three
tables — `airports`, `runways`, `physical_installation_identities` —
with no other table ever holding a foreign key *into*
`physical_installation_identities`. The bug only manifests once some
other table's foreign key actually depends on the table being altered
(see §3) — a scenario that fixture structurally cannot exercise.

## 3. Root cause

SQLite's native `ALTER TABLE ... DROP COLUMN` leaves a dangling
table-level `FOREIGN KEY(...)` clause referencing the just-dropped
column when that column's constraint was declared as part of the
table's **original `CREATE TABLE` statement** — which is exactly what a
single `Base.metadata.create_all()` call produces, since SQLAlchemy's
`CreateTable` compiler always emits foreign keys as separate table-level
`FOREIGN KEY(col) REFERENCES ...` clauses, never as an inline
column-level constraint.

It does **not** fail when the column's constraint was instead added
later via `ALTER TABLE ADD COLUMN col ... REFERENCES ...` — an inline,
column-level constraint that SQLite correctly removes along with the
column. This is precisely how `upgrade()` above actually adds the
column to an already-existing database (confirmed by reading its own
code, unchanged by this fix), and is exactly why **downgrading the real,
already-migrated development database was never actually at risk** —
its `physical_installation_identities` table got the column via `ALTER
TABLE ADD COLUMN`, not via a from-scratch `CREATE TABLE`.

The risk is real, however, for **any database whose
`physical_installation_identities` table was built by a single
`Base.metadata.create_all()` call against the current (post-migration)
model set** — which includes every test fixture in this repository that
calls `create_all()` (the overwhelming majority of them) and any future
fresh deployment created after this migration was merged into `main`.

This was empirically isolated (not merely reasoned about) during
development of this fix, by comparing the exact `sqlite_master` schema
text SQLAlchemy produces (table-level FK) against the exact text
`ALTER TABLE ADD COLUMN` produces (column-level FK) for the same logical
column — the two are structurally different DDL, and only the first one
breaks `DROP COLUMN`.

## 4. Why minimal-schema testing missed it

Covered in §2: the pre-existing test fixture's 3-table graph has no
incoming foreign key into the table being altered, so it never exercised
the code path that produces the dangling table-level `FOREIGN KEY`
clause. The bug requires both (a) the column's FK declared at
`CREATE TABLE` time, and (b) the exact SQLite internal consistency check
that runs when *any* table in the schema (not necessarily the one being
altered) still refers to the dropped column by name.

## 5. Exact fix

This project has **no deployed migration-chain history requiring
immutability** — `migrate_canonical_runway_runway_end_slice1.py` is not
an Alembic revision inside a chain of other revisions; it is the entire,
self-contained mechanism for this one additive change, already
idempotent by construction (`if not exists` guards on both `upgrade()`
and `downgrade()`). Editing it directly, in place, is therefore safe and
was the correct choice — no replacement migration was created.

Changed, in `scripts/migrate_canonical_runway_runway_end_slice1.py`:

- **New function `_drop_column_via_rebuild()`** — replaces the native
  `ALTER TABLE ... DROP COLUMN` with SQLite's own documented "12-step"
  table-rebuild procedure for schema changes `DROP COLUMN` cannot
  perform safely: build a replacement table under a temporary name,
  copy the surviving columns' data across, drop the original, rename
  the replacement into place, then recreate the table's surviving
  indexes. This is correct regardless of which of the two constraint
  styles in §3 the table happens to have.
  - **Preserves surviving foreign keys.** `PRAGMA table_info()` says
    nothing about foreign keys; an earlier draft of this fix silently
    dropped the *other* columns' own FK constraints (`airport_id` →
    `airports.id`, `runway_id` → `runways.id`) during the rebuild — a
    real regression caught by the pre-existing
    `test_downgrade_is_exactly_reversible` test failing during
    development. Fixed by also querying `PRAGMA foreign_key_list()` and
    re-emitting every surviving column's constraint in the rebuilt
    table.
  - **Operation order matters.** The replacement table is built under a
    temporary name *first*; the original is dropped only afterward, and
    the replacement is renamed into place last — matching SQLite's own
    documented step order exactly. Building it the other way around (rename
    the original out of the way first, create the replacement directly
    under the real name) was also tried during development and *also*
    failed: SQLite auto-follows a table rename for every other table's
    foreign-key declarations that pointed at the renamed table,
    permanently rebinding them to the temporary name — so a later table
    that reclaims the original name is never picked up, and those
    foreign keys are left silently dangling. This was caught by the new
    `PRAGMA foreign_key_check` gate (see below), not silently missed.
- **`downgrade()` now sets `PRAGMA foreign_keys=OFF` before its
  transaction** (SQLite ignores changes to this pragma made inside one)
  for the duration of the rebuild, since other tables' foreign keys
  briefly point at a table that doesn't exist between the drop and the
  rename.
- **`downgrade()` now runs `PRAGMA foreign_key_check` before committing**
  and raises `RuntimeError` (rolling back the whole transaction) if it
  finds any violation, rather than trusting the rebuild succeeded.
  `PRAGMA foreign_keys` is re-enabled in a `finally` block regardless of
  outcome.

**`upgrade()` is completely unchanged** — the defect and the fix are
entirely inside `downgrade()`.

**Why this is safe:**

- Preserves all unrelated tables/data — the rebuild touches only
  `physical_installation_identities`'s own schema and re-inserts its
  existing rows unchanged (minus the dropped column); no other table's
  rows are read or written.
- Removes only the schema this migration itself introduced (`runway_ends`
  table, `runway_end_id` column and its index) — nothing else.
- Foreign-key integrity is actively verified before the transaction is
  allowed to commit, not merely assumed.
- No broad data deletion anywhere.
- No production-domain behavior change — `app/services/runway_inventory.py`,
  `app/services/runway_identity.py`, the classification rules, MDW/CGF
  linking, and `scripts/import_usaspending_grants.py` are all untouched.
- No application-code workaround — the fix is entirely inside the
  migration script itself, at the exact point of failure.

## 6. Realistic regression-test design

Added to `tests/test_canonical_runway_runway_end_migration.py` (the
existing, pre-established migration-test file — reused and extended, not
duplicated into a parallel framework):

- `_pre_migration_database_full_schema()` — a new fixture, sibling to the
  existing `_pre_migration_database()`, building the **full** current
  table graph via `Base.metadata.create_all()` (excluding only
  `physical_installation_identities`, hand-built in its pre-migration
  shape exactly as the existing fixture already does).
- `_seed_full_schema_with_incoming_foreign_key()` — seeds a
  `PhysicalInstallationIdentity` plus an `InstallationAssertionLink` that
  references it: the exact incoming-foreign-key shape that exposes the
  original bug.
- `test_downgrade_succeeds_against_the_full_realistic_schema_with_an_incoming_foreign_key` —
  exercises the exact call that used to fail, then asserts: the new
  schema is fully removed; `PRAGMA foreign_key_check` and
  `PRAGMA integrity_check` are both clean; the identity row and the
  linking row are both preserved with their original values; every
  unrelated table's row count is unchanged; the table's two surviving,
  unrelated indexes (`ix_..._airport_id`, `ix_..._runway_id`) still
  exist; and a subsequent re-`upgrade()` succeeds again and still leaves
  the original data intact.

The three pre-existing tests in this file
(`test_upgrade_adds_only_the_expected_table_and_column`,
`test_upgrade_is_idempotent_when_run_twice`,
`test_downgrade_is_exactly_reversible`) all continue to pass unchanged,
including the byte-for-byte `sqlite_master` schema-text comparison in
the last one — proving the fix doesn't just work functionally, it
reproduces the *exact original schema text* on the realistic-but-minimal
case too.

## 7. Upgrade / downgrade / re-upgrade results

All against isolated temporary databases:

| Step | Result |
|---|---|
| A. Pre-migration → `upgrade()` | Succeeds; `runway_ends` table and `runway_end_id` column present; identity row preserved |
| Link identity to a real `RunwayEnd` | `PRAGMA foreign_key_check` → `[]` |
| B. Upgraded → `downgrade()` | **Succeeds** (previously failed); `runway_ends`/`runway_end_id` fully removed; identity row and the incoming `InstallationAssertionLink` row both preserved with original values; all unrelated tables' row counts unchanged; both surviving indexes preserved |
| C. Downgraded → re-`upgrade()` | Succeeds again; identity data still intact |

## 8. FK integrity results

- After `downgrade()`: `PRAGMA foreign_key_check` → `[]`, `PRAGMA integrity_check` → `ok`
- After re-`upgrade()`: `PRAGMA foreign_key_check` → `[]`, `PRAGMA integrity_check` → `ok`

## 9. Real DB remained untouched

| | Before this task | After this task |
|---|---|---|
| DB size | `667648` bytes | `667648` bytes |
| DB mtime | unchanged | unchanged |
| `Runway` count | 180 | 180 |
| `RunwayEnd` count | 360 | 360 |
| Identity links | 6, same `runway_end_id` values | unchanged |

No real-database migration operation (`upgrade`, `downgrade`, or
otherwise) was ever invoked against `data/runway_safe.db` in this task —
every proof above used a temporary file under the scratch directory,
deleted after use.

## 10. Remaining migration-related follow-ups

None identified beyond what the merge-readiness review already recorded
as separate, unrelated items (DB-level `Runway` uniqueness constraint;
USAspending ingestion fallback) — neither is a migration-mechanics
concern and both remain out of scope for this fix, per instruction.
