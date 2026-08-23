"""Add the EB1 EvidenceBag-persistence tables
(docs/architecture/rwi-full-evidencebag-persistence-design.md, EB2 of
that design's own §18 slice sequence - see
docs/architecture/rwi-eb1-evidencebag-persistence-foundation-report.md
for the reviewed, committed models this migration creates schema for).

Additive only: creates exactly two new tables (and their indexes/CHECK/
FK/UNIQUE constraints) - `source_assertion_evidence_bags` and
`identity_guard_evaluations` - reused directly from the ORM models
(app.models.source_assertion_evidence_bag.SourceAssertionEvidenceBag/
app.models.identity_guard_evaluation.IdentityGuardEvaluation) via
SQLAlchemy's own CreateTable/CreateIndex compilation, the same technique
scripts/migrate_signal_disposition_d4d2.py already established for this
exact "two new tables, one with a hard dependency on the other" shape -
so the created schema can never drift from the model definition; no
CHECK/FK/UNIQUE clause is ever hardcoded here as literal text. No
existing table, column, or row is read, changed, merged, or deleted - no
ALTER TABLE appears anywhere in this script, and none is needed, since
nothing existing changes shape.

NO BACKFILL, EVER: `upgrade()` never inserts a row into either new table
under any circumstance. Both tables are always empty immediately after a
normal upgrade. This migration never imports, calls, or depends on
app.services.evidence_bag_serialization, app.services.discovery_evidence_persistence,
app.services.unknown_airport_discovery_integration,
app.services.evidence_attachment_guard, or any promotion/Airport-creation
logic - it is a pure schema operation, exactly like every additive
migration in this repository.

COMPOSITE-FK-AWARE SCHEMA COMPARISON (the single highest-priority
correctness property of this migration - see the module docstring's own
critical-review addition): `identity_guard_evaluations`'s own
`fk_identity_guard_evaluations_snapshot_matches_assertion` composite
foreign key (`(evidence_bag_snapshot_id, source_assertion_id) ->
(source_assertion_evidence_bags.id, source_assertion_evidence_bags.source_assertion_id)`)
is THE constraint that makes it structurally impossible for a future
evaluation to claim it concerns SourceAssertion A while actually
referencing a snapshot belonging to a different SourceAssertion B - the
entire causal-integrity property EB1's own adversarial review fixed.
`PRAGMA foreign_key_list(table)` reports each COLUMN of a multi-column
FK as its own row, all sharing the same leading `id` value to indicate
they belong to one constraint TOGETHER. A naive comparison that flattens
every FK row into one set of `(from_column, ref_table, to_column)`
tuples - the shape scripts/migrate_signal_disposition_d4d2.py's own
`_expected_foreign_keys()` uses, sufficient for that migration's own
single-column-only FKs - CANNOT DISTINGUISH one genuine two-column
composite constraint from two coincidentally-matching, entirely
INDEPENDENT single-column FKs that happen to target the same two
columns separately (which would NOT enforce causal integrity at all,
since each column would then be validated against ANY matching row
independently, not the SAME row together). This migration's own
`_expected_foreign_key_groups()`/`_actual_foreign_key_groups()` instead
compare a SET OF FROZENSETS - one frozenset per constraint, grouping
`PRAGMA foreign_key_list`'s own leading `id` column (actual side)
against `Table.foreign_key_constraints` (expected side, one Python
`ForeignKeyConstraint` object per real constraint) - genuinely proving
which columns are constrained TOGETHER, not merely which column-pairs
exist somewhere in the table. Verified directly against a raw-SQL
malformed-composite-FK attack (see tests/test_evidence_bag_migration.py's
own `TestSchemaMismatchAttackMatrix`).

INDEX/UNIQUE-CONSTRAINT COMPARISON: SQLite compiles every `UniqueConstraint`
(including `source_assertion_evidence_bags`'s own
`uq_source_assertion_evidence_bags_id_source_assertion_id`, the
constraint the composite FK above depends on existing on the PARENT
side) into an auto-named `sqlite_autoindex_...` index whose exact name is
not predictable from the ORM model and is not stable across
recreate/rebuild history. `_expected_index_shapes()`/`_actual_index_shapes()`
therefore compare by `(frozenset(column_names), unique)` shape, never by
name - a genuinely missing supporting UNIQUE constraint (mission's own
named attack "H") is caught by this shape comparison exactly like a
missing plain index would be, without depending on SQLite's own
unpredictable autoindex naming.

PARTIAL-SCHEMA SAFETY, FK-DEPENDENCY-AWARE: `upgrade()` treats "the table
already exists" as requiring proof it is the RIGHT table, not just any
table with the right name - reusing the identical
`_verify_existing_table_matches_expected_schema()`/`_schema_mismatch_reasons()`
shared-implementation pattern scripts/migrate_signal_disposition_d4d2.py
established (inspect() and upgrade() ask the exact same structural
question, so inspect()["ready"] can never disagree with what upgrade()
would actually do). Processed strictly in dependency order - the parent
`source_assertion_evidence_bags` is verified/created BEFORE
`identity_guard_evaluations` is ever touched, since the child's own
composite FK requires the parent's exact shape (specifically its
supporting UNIQUE constraint) to already exist correctly; an incompatible
or missing parent means the child is never even attempted, regardless of
the child's own on-disk state.

CONSERVATIVE DOWNGRADE - A DELIBERATE, DOCUMENTED DEVIATION FROM THE
ORIGINAL DESIGN DOCUMENT'S OWN SUGGESTION: `docs/architecture/rwi-full-evidencebag-persistence-design.md`
S17 originally suggested downgrade should refuse only "if any
identity_guard_evaluations row exists." This migration's own mission
explicitly asked for that recommendation to be challenged rather than
followed blindly - and it does not survive the challenge:
`SourceAssertionEvidenceBag` rows are THEMSELVES governed, immutable
audit history (the model's own docstring: "auditable and cannot be
deleted"), independent of whether any evaluation ever reads them. A
downgrade that dropped a populated `source_assertion_evidence_bags` table
merely because zero evaluations happened to exist yet would destroy real,
permanent, immutable evidence a schema operation must never be the
mechanism for discarding. `downgrade()` therefore refuses outright if
EITHER table contains any row at all - not only `identity_guard_evaluations`.
Only when both tables are confirmed completely empty does downgrade
proceed, dropping `identity_guard_evaluations` before
`source_assertion_evidence_bags` (child before parent, the FK-safe
order).

A timestamped backup is required before this script writes to the real
database (matching the discipline every prior migration in this
repository already establishes). This script is never run against the
real database in this implementation task (EB2); every test uses an
isolated temp-file SQLite database (tmp_path); the real migration remains
explicitly deferred to a separate, later, explicitly-authorized EB6
slice.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

from app.database import Base
from app import models as _models  # noqa: F401 - registers all metadata, including the two EB1 tables

DEFAULT_DATABASE = Path("data/runway_safe.db")
BACKUP_DIRECTORY = Path("data/backups")

# Parent (snapshot) before child (evaluation) - the composite FK on
# identity_guard_evaluations depends on source_assertion_evidence_bags'
# own exact shape already being correct. downgrade() reverses this order.
TABLES = ("source_assertion_evidence_bags", "identity_guard_evaluations")

# Named CHECK/FK/UNIQUE constraints this migration's own target schema
# declares, keyed by table - used only to verify an ALREADY-EXISTING
# table's stored CREATE TABLE text still names them (a cheap, robust
# presence check via substring search, immune to whitespace/formatting
# differences between SQLite's own stored SQL and SQLAlchemy's compiled
# text). Never used to CREATE anything - table creation always compiles
# fresh from Base.metadata.tables, never from this literal list.
_EXPECTED_NAMED_CONSTRAINTS = {
    "source_assertion_evidence_bags": (
        "uq_source_assertion_evidence_bags_id_source_assertion_id",
    ),
    "identity_guard_evaluations": (
        "ck_identity_guard_evaluations_outcome",
        "fk_identity_guard_evaluations_snapshot_matches_assertion",
    ),
}


class IncompatibleExistingSchemaError(RuntimeError):
    """Raised by upgrade() when a table with the expected name already
    exists but its persisted shape does not match the current EB1 ORM
    models - this migration never drops, rebuilds, or otherwise silently
    reconciles an unexpected existing table; a human must resolve the
    collision by hand."""


def _backup_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"runway_safe-pre-evidence-bag-persistence-eb2-{timestamp}.db"


def backup_database(database: Path, backup_directory: Path = BACKUP_DIRECTORY) -> Path:
    database = database.resolve()
    if not database.is_file():
        raise FileNotFoundError(f"Database not found: {database}")
    backup_directory.mkdir(parents=True, exist_ok=True)
    destination = backup_directory / _backup_name()
    shutil.copy2(database, destination)
    if destination.stat().st_size != database.stat().st_size:
        raise RuntimeError("Database backup size does not match the source database.")
    return destination


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
    )


def _expected_columns(name: str) -> dict:
    """(col_name -> (type_affinity_str, notnull, is_pk)) derived fresh
    from the live ORM model - never hardcoded, so it can never drift from
    app.models.source_assertion_evidence_bag/app.models.identity_guard_evaluation."""
    table = Base.metadata.tables[name]
    return {
        column.name: (str(column.type), not column.nullable, bool(column.primary_key))
        for column in table.columns
    }


def _expected_foreign_key_groups(name: str) -> "frozenset[frozenset[tuple[str, str, str]]]":
    """One frozenset of (from_column, ref_table, to_column) per REAL
    constraint - derived from Table.foreign_key_constraints, which
    SQLAlchemy already groups correctly (a composite ForeignKeyConstraint
    produces exactly one ForeignKeyConstraint object with multiple
    elements; independent single-column FKs each produce their own
    separate, one-element object). See the module docstring's own
    "COMPOSITE-FK-AWARE SCHEMA COMPARISON" section for why grouping - not
    a flat set of column-pairs - is required for correctness here."""
    table = Base.metadata.tables[name]
    return frozenset(
        frozenset((element.parent.name, element.column.table.name, element.column.name) for element in constraint.elements)
        for constraint in table.foreign_key_constraints
    )


def _actual_foreign_key_groups(connection: sqlite3.Connection, name: str) -> "frozenset[frozenset[tuple[str, str, str]]]":
    """Mirrors _expected_foreign_key_groups() from the actual on-disk
    schema: PRAGMA foreign_key_list(table) reports one row per COLUMN of
    a (possibly multi-column) FK, with the leading `id` column shared by
    every row belonging to the SAME constraint - grouping by it
    reconstructs the real constraint membership, never merely the flat
    set of column-pairs that exist somewhere in the table."""
    rows = connection.execute(f"PRAGMA foreign_key_list({name})").fetchall()
    groups: "dict[int, set[tuple[str, str, str]]]" = {}
    for row in rows:
        constraint_id, _seq, ref_table, from_column, to_column = row[0], row[1], row[2], row[3], row[4]
        groups.setdefault(constraint_id, set()).add((from_column, ref_table, to_column))
    return frozenset(frozenset(group) for group in groups.values())


def _expected_index_shapes(name: str) -> "frozenset[tuple[frozenset[str], bool]]":
    """(frozenset(column_names), unique) per expected index OR unique
    constraint - deliberately never compared by name, since SQLite
    auto-names UniqueConstraint-derived indexes unpredictably
    (`sqlite_autoindex_...`), and that generated name is not something
    the ORM model can predict or this migration should depend on."""
    table = Base.metadata.tables[name]
    shapes: "set[tuple[frozenset[str], bool]]" = set()
    for index in table.indexes:
        shapes.add((frozenset(column.name for column in index.columns), bool(index.unique)))
    for constraint in table.constraints:
        if type(constraint).__name__ == "UniqueConstraint":
            shapes.add((frozenset(column.name for column in constraint.columns), True))
    return frozenset(shapes)


def _actual_index_shapes(connection: sqlite3.Connection, name: str) -> "frozenset[tuple[frozenset[str], bool]]":
    shapes: "set[tuple[frozenset[str], bool]]" = set()
    for index_row in connection.execute(f"PRAGMA index_list({name})").fetchall():
        index_name, is_unique = index_row[1], bool(index_row[2])
        columns = frozenset(
            info_row[2] for info_row in connection.execute(f'PRAGMA index_info("{index_name}")').fetchall()
        )
        shapes.add((columns, is_unique))
    return frozenset(shapes)


def _schema_mismatch_reasons(connection: sqlite3.Connection, name: str) -> "list[str]":
    """The single source of truth for "does this already-existing table
    genuinely match the current EB1 ORM models" - returns an empty list
    if it matches exactly, or one human-readable reason string per
    mismatch found otherwise. Both `upgrade()` (via
    `_verify_existing_table_matches_expected_schema()`, which raises
    using this same list) and `inspect()` (via a boolean wrapper) call
    this one function, so `inspect()["ready"]` can never disagree with
    what `upgrade()` would actually do - the exact discipline
    scripts/migrate_signal_disposition_d4d2.py's own review checkpoint
    already established, reused verbatim here."""
    reasons: "list[str]" = []

    actual_columns_raw = connection.execute(f"PRAGMA table_info({name})").fetchall()
    actual_columns = {
        row[1]: (row[2], bool(row[3]), bool(row[5])) for row in actual_columns_raw  # name: (type, notnull, pk)
    }
    expected_columns = _expected_columns(name)
    if actual_columns != expected_columns:
        reasons.append(
            f"columns do not match the expected EB1 schema - expected {expected_columns!r}, "
            f"found {actual_columns!r}"
        )

    actual_fk_groups = _actual_foreign_key_groups(connection, name)
    expected_fk_groups = _expected_foreign_key_groups(name)
    if actual_fk_groups != expected_fk_groups:
        reasons.append(
            f"foreign key constraint groups do not match the expected EB1 schema (composite-FK-aware "
            f"comparison) - expected {expected_fk_groups!r}, found {actual_fk_groups!r}"
        )

    actual_index_shapes = _actual_index_shapes(connection, name)
    expected_index_shapes = _expected_index_shapes(name)
    if actual_index_shapes != expected_index_shapes:
        reasons.append(
            f"index/unique-constraint shapes do not match the expected EB1 schema - expected "
            f"{expected_index_shapes!r}, found {actual_index_shapes!r}"
        )

    stored_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    stored_sql = stored_sql_row[0] if stored_sql_row else ""
    missing_constraints = [
        constraint_name
        for constraint_name in _EXPECTED_NAMED_CONSTRAINTS[name]
        if constraint_name not in stored_sql
    ]
    if missing_constraints:
        reasons.append(
            f"missing expected named constraint(s) {missing_constraints!r} - found stored schema: {stored_sql!r}"
        )

    return reasons


def _table_matches_expected_schema(connection: sqlite3.Connection, name: str) -> bool:
    """Boolean wrapper over `_schema_mismatch_reasons()` - the one
    function `inspect()`'s own `ready` computation calls, so it can never
    report a table "ready" that `upgrade()` would actually refuse."""
    return not _schema_mismatch_reasons(connection, name)


def _verify_existing_table_matches_expected_schema(connection: sqlite3.Connection, name: str) -> None:
    reasons = _schema_mismatch_reasons(connection, name)
    if reasons:
        raise IncompatibleExistingSchemaError(
            f"table {name!r} already exists but is incompatible with the expected EB1 schema: "
            f"{'; '.join(reasons)}. Refusing to alter or rebuild an existing table automatically; "
            "resolve this collision by hand before re-running upgrade()."
        )


def inspect(database: Path) -> dict:
    """Read-only, via its own `sqlite3.connect(..., mode=ro)` connection -
    never the caller's ORM engine. Never mutates anything, and NEVER
    RAISES for any on-disk schema shape, however malformed - a pure
    diagnostic tool must always return a structured result. `ready` is
    computed via the exact same strict, structural
    `_table_matches_expected_schema()` check `upgrade()` itself uses - a
    table with every expected column NAME present but a wrong type, a
    missing composite FK, or a missing supporting UNIQUE constraint is
    never reported ready.

    ADVERSARIAL-REVIEW FIX: `PRAGMA foreign_key_check` (unlike the
    table-scoped PRAGMAs used elsewhere in this module) validates the
    ENTIRE database's FK graph at once, and SQLite itself raises a raw
    `sqlite3.OperationalError` ("foreign key mismatch") from that PRAGMA
    when some table's stored FK definition can no longer be resolved
    against its parent's actual on-disk shape (e.g. a composite FK whose
    parent table was directly, incorrectly altered out from under it,
    bypassing this migration entirely). `upgrade()` never reaches this
    failure mode in practice, because `_schema_mismatch_reasons()` (a
    purely table-scoped comparison) already refuses the incompatible
    parent table BEFORE upgrade() would ever re-verify the child - but
    `inspect()` is a standalone diagnostic with no such early exit, and
    the original implementation let this specific PRAGMA's own exception
    propagate uncaught, violating this function's own "never mutates,
    never crashes" contract. Caught here and represented in the result
    instead (`foreign_key_check` becomes `None`,
    `foreign_key_check_error` carries the message, and `ready` is forced
    `False` - a database SQLite itself cannot even self-consistency-check
    can never be "ready" by definition)."""
    database = database.resolve()
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        tables_exist = {name: _table_exists(connection, name) for name in TABLES}
        counts = {
            name: (connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0] if tables_exist[name] else 0)
            for name in TABLES
        }
        matches_expected_schema = {
            name: (tables_exist[name] and _table_matches_expected_schema(connection, name))
            for name in TABLES
        }
        try:
            foreign_key_check = connection.execute("PRAGMA foreign_key_check").fetchall()
            foreign_key_check_error = None
        except sqlite3.OperationalError as exc:
            foreign_key_check = None
            foreign_key_check_error = str(exc)
        ready = foreign_key_check_error is None and all(matches_expected_schema[name] for name in TABLES)
        return {
            "database": str(database),
            "tables_exist": tables_exist,
            "counts": counts,
            "matches_expected_schema": matches_expected_schema,
            "ready": ready,
            "foreign_key_check": foreign_key_check,
            "foreign_key_check_error": foreign_key_check_error,
        }
    finally:
        connection.close()


def upgrade(database: Path) -> None:
    """Idempotent: creates only the tables that do not yet exist. A table
    that already exists is verified against the current ORM models
    (`_verify_existing_table_matches_expected_schema`) rather than
    assumed correct - a genuine mismatch raises
    `IncompatibleExistingSchemaError` and leaves the database completely
    untouched (the whole operation runs inside one transaction). Always
    processes `source_assertion_evidence_bags` before
    `identity_guard_evaluations` - the child's own composite FK requires
    the parent's exact shape to already be correct. Never inserts a row
    into either table under any circumstance."""
    connection = sqlite3.connect(database.resolve())
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        for name in TABLES:
            if _table_exists(connection, name):
                _verify_existing_table_matches_expected_schema(connection, name)
                continue
            table = Base.metadata.tables[name]
            connection.execute(str(CreateTable(table).compile(dialect=sqlite.dialect())))
            for index in table.indexes:
                connection.execute(str(CreateIndex(index).compile(dialect=sqlite.dialect())))
        try:
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        except sqlite3.OperationalError as exc:
            # Mirrors inspect()'s own identical fix - a database-wide FK
            # graph SQLite itself cannot resolve is always a refusal, not
            # a raw driver-level crash. Still fully rolled back by the
            # enclosing except/rollback below either way; this only makes
            # the raised exception type consistent and informative.
            raise RuntimeError(f"upgrade() would leave an unresolvable foreign-key graph: {exc}") from exc
        if violations:
            raise RuntimeError(f"upgrade() would leave foreign-key violations: {violations}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def downgrade(database: Path) -> None:
    """Refuses outright (raises, no partial change committed) if EITHER
    table contains any row - both are governed, immutable audit history
    in their own right (see the module docstring's own "CONSERVATIVE
    DOWNGRADE" section for why this is a deliberate, documented departure
    from the original design document's own narrower suggestion). Only
    when both tables are confirmed completely empty does this drop them,
    in FK-safe order (`identity_guard_evaluations` before
    `source_assertion_evidence_bags`)."""
    connection = sqlite3.connect(database.resolve())
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        row_counts = {
            name: (
                connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                if _table_exists(connection, name)
                else 0
            )
            for name in TABLES
        }
        nonempty = {name: count for name, count in row_counts.items() if count > 0}
        if nonempty:
            raise RuntimeError(
                "downgrade() refused: the following table(s) contain governed, immutable evidence "
                f"and would be destroyed: {nonempty!r}. No table was dropped. This applies to "
                "source_assertion_evidence_bags rows even if identity_guard_evaluations is empty - "
                "snapshots are audit history in their own right, not merely a dependency of "
                "evaluations."
            )
        for name in reversed(TABLES):
            if _table_exists(connection, name):
                connection.execute(f"DROP TABLE {name}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--downgrade", action="store_true")
    parser.add_argument("--allow-database-write", action="store_true")
    parser.add_argument("--skip-backup", action="store_true", help="isolated/temp DBs only")
    args = parser.parse_args(argv)
    if not args.allow_database_write:
        parser.error("--allow-database-write is required")
    if not args.downgrade and not args.skip_backup:
        backup = backup_database(args.database)
        print("Backup created:", backup)
    (downgrade if args.downgrade else upgrade)(args.database)
    print(inspect(args.database))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
