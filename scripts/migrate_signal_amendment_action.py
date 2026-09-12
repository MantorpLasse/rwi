"""Add the Signal amendment audit trail tables (RWI HQ "Signal Amendment
Audit Trail — Append-Only Governance" mission, implementing the approved
design in docs/architecture/rwi-signal-amendment-audit-trail-design.md).

Additive only: creates exactly two new tables (and their indexes/UNIQUE
constraint) - `signal_amendment_actions` and `signal_amendment_field_changes`
- reused directly from the ORM models
(app.models.signal_amendment.SignalAmendmentAction/SignalAmendmentFieldChange)
via SQLAlchemy's own CreateTable/CreateIndex compilation, the same
technique scripts/migrate_signal_disposition_d4d2.py already uses for the
structurally identical two-table precedent this migration is modeled on
line for line - so the created schema can never drift from the model
definition; no FK/UNIQUE clause is ever hardcoded here as literal text. No
existing table, column, or row is read, changed, merged, or deleted - no
ALTER TABLE appears anywhere in this script, and none is needed, since
nothing existing changes shape. In particular: no Signal row is rewritten,
no SourceAssertion row is altered, no ReviewerAction row is altered, no
publication state changes, and FH-D4's own tables are never touched.

NO BACKFILL, EVER: `upgrade()` never inserts a row into either new table
under any circumstance - not from BGM/PHL/FTY/LEX/ASE's historical
corrections, not from anything else. Both tables are always empty
immediately after a normal upgrade (matching D4D2's own explicit
non-goal and this mission's own explicit "NO BACKFILL" instruction - see
docs/architecture/rwi-signal-amendment-audit-trail-design.md §19 for why:
those five corrections never captured a structured reviewer/reason/
timestamp at the time, and reconstructing one now would misrepresent when
they were actually decided).

PARTIAL-SCHEMA SAFETY: `upgrade()` treats "the table already exists" as
requiring proof it is the RIGHT table, not just any table with the right
name - `_verify_existing_table_matches_expected_schema()` compares the
persisted column set (name/type/nullable), primary key, foreign-key
targets, and the presence of the named UNIQUE constraint this migration's
own target schema declares, against what is actually on disk. A mismatch
on either table raises `IncompatibleExistingSchemaError` before anything is
touched - this script never drops, rebuilds, or otherwise "fixes" a
pre-existing table with an unexpected shape; a human must resolve that by
hand. A single missing table (the other one already present with a
verified-matching shape) is a legitimate, safely completable partial state
- the missing one is simply created, exactly as a normal full upgrade
would have created it.

CONSERVATIVE DOWNGRADE: mirrors migrate_signal_disposition_d4d2.py's own
downgrade() exactly - refuses outright, raising, no partial change
committed, if EITHER new table contains any row at all. Recorded
amendments are exactly the kind of audit history this whole mission exists
to make append-only and permanent; a schema downgrade must never be the
mechanism that silently discards it. Only when both tables are confirmed
empty does downgrade proceed, dropping `signal_amendment_field_changes`
before `signal_amendment_actions` (children before parent, the FK-safe
order).

A timestamped backup is required before this script writes to the real
database (matching the discipline scripts/migrate_signal_disposition_d4d2.py/
scripts/migrate_promotion_policy_persistence_slice7.py established, reused
verbatim). This mission authorizes code/migration IMPLEMENTATION, not
deployment - this script is verified in this mission only against isolated
temp-file SQLite databases (tmp_path); it is NOT run against the real
production database as part of this mission (see this mission's own Part
13/15 - applying it to production, if desired, remains a separate, later,
explicitly-authorized manual step, exactly like every other migration
script in this repository).
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
from app import models as _models  # noqa: F401 - registers all metadata, including SignalAmendmentAction

DEFAULT_DATABASE = Path("data/runway_safe.db")
BACKUP_DIRECTORY = Path("data/backups")

# Parent (header) before child (field-change) - the order every create/
# verify step below relies on; downgrade() reverses it (child before
# parent, the FK-safe drop order).
TABLES = ("signal_amendment_actions", "signal_amendment_field_changes")

# The named UNIQUE constraint this migration's own target schema declares -
# used only to verify an ALREADY-EXISTING table's stored CREATE TABLE text
# still names it (a cheap, robust presence check via substring search,
# immune to whitespace/formatting differences between SQLite's own stored
# SQL and SQLAlchemy's compiled text - see migrate_signal_disposition_d4d2.py's
# own identical precedent for why a full-text comparison was deliberately
# not used instead). Never used to CREATE anything - table creation always
# compiles fresh from Base.metadata.tables, never from this literal list.
# signal_amendment_actions has no named CHECK/UNIQUE constraint of its own
# (see the model's own docstring: no supersedes_id, no vocabulary CHECK -
# this table is deliberately simpler than every other append-only table in
# this codebase).
_EXPECTED_NAMED_CONSTRAINTS = {
    "signal_amendment_actions": (),
    "signal_amendment_field_changes": ("uq_signal_amendment_field_changes_action_field",),
}


class IncompatibleExistingSchemaError(RuntimeError):
    """Raised by upgrade() when a table with the expected name already
    exists but its persisted shape does not match
    app.models.signal_amendment's own current model - this migration never
    drops, rebuilds, or otherwise silently reconciles an unexpected
    existing table; a human must resolve the collision by hand."""


def _backup_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"runway_safe-pre-signal-amendment-action-{timestamp}.db"


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


def inspect(database: Path) -> dict:
    """Read-only, via its own `sqlite3.connect(..., mode=ro)` connection -
    never the caller's ORM engine. Returns enough deterministic
    information to compose alongside the existing D4D2-established
    `inspect()` precedent: table existence, column sets, the
    named-constraint presence check, foreign-key targets, row counts, and
    a real `PRAGMA foreign_key_check` result. Never mutates anything."""
    database = database.resolve()
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        tables_exist = {name: _table_exists(connection, name) for name in TABLES}
        columns = {
            name: (
                {row[1] for row in connection.execute(f"PRAGMA table_info({name})")}
                if tables_exist[name]
                else set()
            )
            for name in TABLES
        }
        foreign_keys = {
            name: (
                sorted(
                    (row[3], row[2], row[4])  # (from_column, ref_table, to_column)
                    for row in connection.execute(f"PRAGMA foreign_key_list({name})")
                )
                if tables_exist[name]
                else []
            )
            for name in TABLES
        }
        stored_sql = {
            name: (
                connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
                ).fetchone()[0]
                if tables_exist[name]
                else None
            )
            for name in TABLES
        }
        named_constraints_present = {
            name: {
                constraint_name: (stored_sql[name] is not None and constraint_name in stored_sql[name])
                for constraint_name in _EXPECTED_NAMED_CONSTRAINTS[name]
            }
            for name in TABLES
        }
        counts = {
            name: (connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0] if tables_exist[name] else 0)
            for name in TABLES
        }
        matches_expected_schema = {
            name: (tables_exist[name] and _table_matches_expected_schema(connection, name))
            for name in TABLES
        }
        ready = matches_expected_schema["signal_amendment_actions"] and matches_expected_schema["signal_amendment_field_changes"]
        return {
            "database": str(database),
            "tables_exist": tables_exist,
            "columns": columns,
            "foreign_keys": foreign_keys,
            "named_constraints_present": named_constraints_present,
            "matches_expected_schema": matches_expected_schema,
            "counts": counts,
            "ready": ready,
            "foreign_key_check": connection.execute("PRAGMA foreign_key_check").fetchall(),
        }
    finally:
        connection.close()


def _expected_columns(name: str) -> dict:
    """(col_name -> (type_affinity_str, notnull, is_pk)) derived fresh from
    the live ORM model - never hardcoded, so it can never drift from
    app.models.signal_amendment."""
    table = Base.metadata.tables[name]
    return {
        column.name: (str(column.type), not column.nullable, bool(column.primary_key))
        for column in table.columns
    }


def _expected_foreign_keys(name: str) -> "set[tuple[str, str, str]]":
    table = Base.metadata.tables[name]
    return {
        (fk.parent.name, fk.column.table.name, fk.column.name)
        for column in table.columns
        for fk in column.foreign_keys
    }


def _schema_mismatch_reasons(connection: sqlite3.Connection, name: str) -> "list[str]":
    """The single source of truth for "does this already-existing table
    genuinely match app.models.signal_amendment's current model" - returns
    an empty list if it matches exactly, or one human-readable reason
    string per mismatch found otherwise. Both `upgrade()` (via
    `_verify_existing_table_matches_expected_schema()`, which raises using
    this same list) and `inspect()` (via `_table_matches_expected_schema()`,
    a boolean wrapper) call this one function, mirroring D4D2's own
    review-checkpoint-fixed precedent exactly - `inspect()` can never
    disagree with `upgrade()` about whether an existing table is genuinely
    compatible.

    Deliberately compares STRUCTURED facts (column name/type/nullability/
    PK, foreign-key targets, and named-constraint presence via a substring
    search over the table's own stored `sqlite_master.sql` text) rather
    than a full raw-text comparison against a freshly compiled CREATE
    TABLE statement - SQLite's own stored SQL and SQLAlchemy's compiled
    output can legitimately differ in whitespace/quoting/clause order for
    an otherwise IDENTICAL table, which a naive full-text comparison would
    misreport as incompatible."""
    reasons: "list[str]" = []

    actual_columns_raw = connection.execute(f"PRAGMA table_info({name})").fetchall()
    actual_columns = {
        row[1]: (row[2], bool(row[3]), bool(row[5])) for row in actual_columns_raw  # name: (type, notnull, pk)
    }
    expected_columns = _expected_columns(name)
    if actual_columns != expected_columns:
        reasons.append(
            f"columns do not match the expected schema - expected {expected_columns!r}, "
            f"found {actual_columns!r}"
        )

    actual_fks = {
        (row[3], row[2], row[4]) for row in connection.execute(f"PRAGMA foreign_key_list({name})")
    }
    expected_fks = _expected_foreign_keys(name)
    if actual_fks != expected_fks:
        reasons.append(
            f"foreign keys do not match the expected schema - expected {expected_fks!r}, "
            f"found {actual_fks!r}"
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
    """Boolean wrapper over `_schema_mismatch_reasons()` - the one function
    `inspect()`'s own `ready` computation calls, so it can never report a
    table "ready" that `upgrade()` would actually refuse."""
    return not _schema_mismatch_reasons(connection, name)


def _verify_existing_table_matches_expected_schema(connection: sqlite3.Connection, name: str) -> None:
    """Raises IncompatibleExistingSchemaError if a table named `name`
    exists but its persisted shape does not match
    app.models.signal_amendment's current model - called only when the
    table already exists (upgrade() never calls this for a table it is
    about to create fresh)."""
    reasons = _schema_mismatch_reasons(connection, name)
    if reasons:
        raise IncompatibleExistingSchemaError(
            f"table {name!r} already exists but is incompatible with the expected schema: "
            f"{'; '.join(reasons)}. Refusing to alter or rebuild an existing table automatically; "
            "resolve this collision by hand before re-running upgrade()."
        )


def upgrade(database: Path) -> None:
    """Idempotent: creates only the tables that do not yet exist. A table
    that already exists is verified against the current ORM model
    (`_verify_existing_table_matches_expected_schema`) rather than assumed
    correct - a genuine mismatch raises `IncompatibleExistingSchemaError`
    and leaves the database completely untouched (the whole operation runs
    inside one transaction). Never inserts a row into either table under
    any circumstance."""
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
    """Refuses outright (raises, no partial change committed) if EITHER
    table contains any row - recorded amendment history is permanent audit
    history, never discardable by a schema downgrade. Only when both
    tables are confirmed empty does this drop them, in FK-safe order
    (`signal_amendment_field_changes` before `signal_amendment_actions`)."""
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
                "downgrade() refused: the following table(s) contain recorded amendment history and "
                f"would be destroyed: {nonempty!r}. No table was dropped."
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
