"""Add the Signal lifecycle assessment table (RWI HQ "SLT2 - Governed
Signal Lifecycle Assessment" mission, implementing the SLT2 slice already
specified in docs/architecture/rwi-signal-temporal-relevance-opportunity-
lifecycle-design.md §8 "Option E", §9, §14, §16).

Additive only: creates exactly one new table -
`signal_lifecycle_assessments` - reused directly from the ORM model
(app.models.signal_lifecycle_assessment.SignalLifecycleAssessment) via
SQLAlchemy's own CreateTable/CreateIndex compilation, the same technique
scripts/migrate_signal_amendment_action.py already uses for the structurally
identical single/two-table precedent this migration is modeled on line for
line - so the created schema can never drift from the model definition; no
FK/CHECK clause is ever hardcoded here as literal text. No existing table,
column, or row is read, changed, merged, or deleted - no ALTER TABLE appears
anywhere in this script, and none is needed, since nothing existing changes
shape. In particular: no Signal row is rewritten, no SourceAssertion row is
altered, no ReviewerAction row is altered, no publication state changes, and
FH-D4's own tables are never touched.

NO BACKFILL, EVER: `upgrade()` never inserts a row into the new table under
any circumstance - not for MSP Signal 67, not for anything else. The table
is always empty immediately after a normal upgrade (matching this mission's
own explicit "NO REAL MSP OVERRIDE WRITE YET" instruction and the design
doc's own §14 "zero forced backfill" analysis) - recording MSP's own real
governed assessment, if and when a human decides to, remains a separate,
later, explicitly-authorized step via
scripts/record_signal_lifecycle_assessment.py, never this migration.

PARTIAL-SCHEMA SAFETY: `upgrade()` treats "the table already exists" as
requiring proof it is the RIGHT table, not just any table with the right
name - `_verify_existing_table_matches_expected_schema()` compares the
persisted column set (name/type/nullable), primary key, foreign-key
targets, and the presence of the named CHECK constraint this migration's
own target schema declares, against what is actually on disk. A mismatch
raises `IncompatibleExistingSchemaError` before anything is touched - this
script never drops, rebuilds, or otherwise "fixes" a pre-existing table with
an unexpected shape; a human must resolve that by hand.

CONSERVATIVE DOWNGRADE: mirrors migrate_signal_amendment_action.py's own
downgrade() exactly - refuses outright, raising, no partial change
committed, if the new table contains any row at all. Recorded lifecycle
assessments are exactly the kind of governance history this whole mission
exists to make append-only and permanent; a schema downgrade must never be
the mechanism that silently discards it. Only when the table is confirmed
empty does downgrade proceed.

A timestamped backup is required before this script writes to the real
database (matching the discipline every migration script in this repository
already establishes). This mission authorizes code/migration
IMPLEMENTATION, not deployment - this script is verified in this mission
only against isolated temp-file SQLite databases (tmp_path); it is NOT run
against the real production database as part of this mission - applying it
to production, if desired, remains a separate, later, explicitly-authorized
manual step, exactly like every other migration script in this repository.
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
from app import models as _models  # noqa: F401 - registers all metadata, including SignalLifecycleAssessment

DEFAULT_DATABASE = Path("data/runway_safe.db")
BACKUP_DIRECTORY = Path("data/backups")

TABLE_NAME = "signal_lifecycle_assessments"

# The named CHECK constraint this migration's own target schema declares -
# used only to verify an ALREADY-EXISTING table's stored CREATE TABLE text
# still names it (a cheap, robust presence check via substring search,
# immune to whitespace/formatting differences between SQLite's own stored
# SQL and SQLAlchemy's compiled text - see migrate_signal_amendment_action.py's
# own identical precedent for why a full-text comparison was deliberately
# not used instead). Never used to CREATE anything - table creation always
# compiles fresh from Base.metadata.tables, never from this literal string.
_EXPECTED_NAMED_CONSTRAINTS = ("ck_signal_lifecycle_assessments_state",)


class IncompatibleExistingSchemaError(RuntimeError):
    """Raised by upgrade() when a table named `signal_lifecycle_assessments`
    already exists but its persisted shape does not match
    app.models.signal_lifecycle_assessment's own current model - this
    migration never drops, rebuilds, or otherwise silently reconciles an
    unexpected existing table; a human must resolve the collision by
    hand."""


def _backup_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"runway_safe-pre-signal-lifecycle-assessment-{timestamp}.db"


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


def _expected_columns() -> dict:
    """(col_name -> (type_affinity_str, notnull, is_pk)) derived fresh from
    the live ORM model - never hardcoded, so it can never drift from
    app.models.signal_lifecycle_assessment."""
    table = Base.metadata.tables[TABLE_NAME]
    return {
        column.name: (str(column.type), not column.nullable, bool(column.primary_key))
        for column in table.columns
    }


def _expected_foreign_keys() -> "set[tuple[str, str, str]]":
    table = Base.metadata.tables[TABLE_NAME]
    return {
        (fk.parent.name, fk.column.table.name, fk.column.name)
        for column in table.columns
        for fk in column.foreign_keys
    }


def _schema_mismatch_reasons(connection: sqlite3.Connection) -> "list[str]":
    """The single source of truth for "does this already-existing table
    genuinely match app.models.signal_lifecycle_assessment's current
    model" - returns an empty list if it matches exactly, or one
    human-readable reason string per mismatch found otherwise. Both
    `upgrade()` (via `_verify_existing_table_matches_expected_schema()`,
    which raises using this same list) and `inspect()` (via
    `_table_matches_expected_schema()`, a boolean wrapper) call this one
    function, so they can never disagree about whether an existing table is
    genuinely compatible."""
    reasons: "list[str]" = []

    actual_columns_raw = connection.execute(f"PRAGMA table_info({TABLE_NAME})").fetchall()
    actual_columns = {
        row[1]: (row[2], bool(row[3]), bool(row[5])) for row in actual_columns_raw  # name: (type, notnull, pk)
    }
    expected_columns = _expected_columns()
    if actual_columns != expected_columns:
        reasons.append(
            f"columns do not match the expected schema - expected {expected_columns!r}, "
            f"found {actual_columns!r}"
        )

    actual_fks = {
        (row[3], row[2], row[4]) for row in connection.execute(f"PRAGMA foreign_key_list({TABLE_NAME})")
    }
    expected_fks = _expected_foreign_keys()
    if actual_fks != expected_fks:
        reasons.append(
            f"foreign keys do not match the expected schema - expected {expected_fks!r}, "
            f"found {actual_fks!r}"
        )

    stored_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE_NAME,)
    ).fetchone()
    stored_sql = stored_sql_row[0] if stored_sql_row else ""
    missing_constraints = [
        constraint_name for constraint_name in _EXPECTED_NAMED_CONSTRAINTS if constraint_name not in stored_sql
    ]
    if missing_constraints:
        reasons.append(
            f"missing expected named constraint(s) {missing_constraints!r} - found stored schema: {stored_sql!r}"
        )

    return reasons


def _table_matches_expected_schema(connection: sqlite3.Connection) -> bool:
    return not _schema_mismatch_reasons(connection)


def _verify_existing_table_matches_expected_schema(connection: sqlite3.Connection) -> None:
    reasons = _schema_mismatch_reasons(connection)
    if reasons:
        raise IncompatibleExistingSchemaError(
            f"table {TABLE_NAME!r} already exists but is incompatible with the expected schema: "
            f"{'; '.join(reasons)}. Refusing to alter or rebuild an existing table automatically; "
            "resolve this collision by hand before re-running upgrade()."
        )


def inspect(database: Path) -> dict:
    """Read-only, via its own `sqlite3.connect(..., mode=ro)` connection -
    never the caller's ORM engine. Returns enough deterministic information
    to compose alongside the existing signal_amendment migration's own
    `inspect()` precedent: table existence, column set, the named-constraint
    presence check, foreign-key targets, row count, and a real `PRAGMA
    foreign_key_check` result. Never mutates anything."""
    database = database.resolve()
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        exists = _table_exists(connection, TABLE_NAME)
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({TABLE_NAME})")} if exists else set()
        foreign_keys = (
            sorted(
                (row[3], row[2], row[4])  # (from_column, ref_table, to_column)
                for row in connection.execute(f"PRAGMA foreign_key_list({TABLE_NAME})")
            )
            if exists
            else []
        )
        stored_sql = (
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE_NAME,)
            ).fetchone()[0]
            if exists
            else None
        )
        named_constraints_present = {
            constraint_name: (stored_sql is not None and constraint_name in stored_sql)
            for constraint_name in _EXPECTED_NAMED_CONSTRAINTS
        }
        count = connection.execute(f"SELECT count(*) FROM {TABLE_NAME}").fetchone()[0] if exists else 0
        matches_expected_schema = exists and _table_matches_expected_schema(connection)
        return {
            "database": str(database),
            "table_exists": exists,
            "columns": columns,
            "foreign_keys": foreign_keys,
            "named_constraints_present": named_constraints_present,
            "matches_expected_schema": matches_expected_schema,
            "count": count,
            "ready": matches_expected_schema,
            "foreign_key_check": connection.execute("PRAGMA foreign_key_check").fetchall(),
        }
    finally:
        connection.close()


def upgrade(database: Path) -> None:
    """Idempotent: creates the table only if it does not yet exist. A table
    that already exists is verified against the current ORM model
    (`_verify_existing_table_matches_expected_schema`) rather than assumed
    correct - a genuine mismatch raises `IncompatibleExistingSchemaError`
    and leaves the database completely untouched (the whole operation runs
    inside one transaction). Never inserts a row under any circumstance."""
    connection = sqlite3.connect(database.resolve())
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        if _table_exists(connection, TABLE_NAME):
            _verify_existing_table_matches_expected_schema(connection)
        else:
            table = Base.metadata.tables[TABLE_NAME]
            connection.execute(str(CreateTable(table).compile(dialect=sqlite.dialect())))
            for index in table.indexes:
                connection.execute(str(CreateIndex(index).compile(dialect=sqlite.dialect())))
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"upgrade() would leave foreign-key violations: {violations}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def downgrade(database: Path) -> None:
    """Refuses outright (raises, no partial change committed) if the table
    contains any row - recorded lifecycle-assessment history is permanent
    audit history, never discardable by a schema downgrade. Only when the
    table is confirmed empty does this drop it."""
    connection = sqlite3.connect(database.resolve())
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        row_count = (
            connection.execute(f"SELECT count(*) FROM {TABLE_NAME}").fetchone()[0]
            if _table_exists(connection, TABLE_NAME)
            else 0
        )
        if row_count > 0:
            raise RuntimeError(
                f"downgrade() refused: table {TABLE_NAME!r} contains {row_count} recorded lifecycle "
                "assessment(s) and would be destroyed. No table was dropped."
            )
        if _table_exists(connection, TABLE_NAME):
            connection.execute(f"DROP TABLE {TABLE_NAME}")
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
