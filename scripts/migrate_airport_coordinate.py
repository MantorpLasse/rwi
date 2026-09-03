"""Add the airport_coordinates table (Mission #26F architecture recon,
approved for implementation in Mission #26G; app.models.airport_coordinate
for the exact row shape and full design rationale).

Additive only: creates exactly one new table (and its indexes/CHECK/FK
constraints) - `airport_coordinates` - reused directly from the ORM model
via SQLAlchemy's own CreateTable/CreateIndex compilation, the same
technique scripts/migrate_airport_identifier.py (this repository's own
most recent precedent for an additive single-table migration) already
established, reused verbatim here rather than reinvented. No existing
table, column, or row is read, changed, merged, or deleted anywhere in
this script - no ALTER TABLE appears, and app/models/airport.py's own
schema (including latitude/longitude) is completely untouched by this
migration itself - those columns are only ever written later, at
runtime, by app.services.airport_coordinate's own strictly-gated write
path, never by this script.

NO BACKFILL, EVER: `upgrade()` never inserts a row into the new table
under any circumstance, and never touches any existing Airport row's
coordinate columns - not the 70 legacy FAA/Tableau-backed Airports, not
the 11 Mission #26C/#26D/#26E targets, not any other Airport. Accepting a
governed coordinate remains the job of app.services.airport_coordinate,
never this script.

PARTIAL-SCHEMA SAFETY / CONSERVATIVE DOWNGRADE / BACKUP DISCIPLINE:
identical in shape to the airport-identifier migration - `upgrade()`
verifies an already-existing table against the current ORM model rather
than assuming it is correct; `downgrade()` refuses if the table contains
any row; a timestamped backup is required before any real write.
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
from app import models as _models  # noqa: F401 - registers all metadata, including this new table

DEFAULT_DATABASE = Path("data/runway_safe.db")
BACKUP_DIRECTORY = Path("data/backups")

TABLE_NAME = "airport_coordinates"

_EXPECTED_NAMED_CONSTRAINTS = (
    "ck_airport_coordinates_status",
    "ck_airport_coordinates_latitude_range",
    "ck_airport_coordinates_longitude_range",
)


class IncompatibleExistingSchemaError(RuntimeError):
    """Raised by upgrade() when a table named `airport_coordinates`
    already exists but its persisted shape does not match
    app.models.airport_coordinate's own current model - this migration
    never drops, rebuilds, or otherwise silently reconciles an unexpected
    existing table; a human must resolve the collision by hand."""


def _backup_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"runway_safe-pre-airport-coordinate-{timestamp}.db"


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


def _expected_index_names() -> "set[str]":
    table = Base.metadata.tables[TABLE_NAME]
    return {index.name for index in table.indexes}


def _schema_mismatch_reasons(connection: sqlite3.Connection) -> "list[str]":
    reasons: "list[str]" = []

    actual_columns_raw = connection.execute(f"PRAGMA table_info({TABLE_NAME})").fetchall()
    actual_columns = {
        row[1]: (row[2], bool(row[3]), bool(row[5])) for row in actual_columns_raw
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

    actual_index_names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (TABLE_NAME,)
        )
    }
    missing_indexes = _expected_index_names() - actual_index_names
    if missing_indexes:
        reasons.append(f"missing expected index(es) {sorted(missing_indexes)!r}")

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
    database = database.resolve()
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        table_exists = _table_exists(connection, TABLE_NAME)
        columns = (
            {row[1] for row in connection.execute(f"PRAGMA table_info({TABLE_NAME})")}
            if table_exists else set()
        )
        foreign_keys = (
            sorted(
                (row[3], row[2], row[4])
                for row in connection.execute(f"PRAGMA foreign_key_list({TABLE_NAME})")
            )
            if table_exists else []
        )
        stored_sql = (
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE_NAME,)
            ).fetchone()[0]
            if table_exists else None
        )
        named_constraints_present = {
            constraint_name: (stored_sql is not None and constraint_name in stored_sql)
            for constraint_name in _EXPECTED_NAMED_CONSTRAINTS
        }
        count = connection.execute(f"SELECT count(*) FROM {TABLE_NAME}").fetchone()[0] if table_exists else 0
        matches_expected_schema = table_exists and _table_matches_expected_schema(connection)
        return {
            "database": str(database),
            "table_exists": table_exists,
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
    """Refuses outright if the table contains any row - persisted
    coordinate governance/provenance evidence is permanent audit
    evidence, never discardable by a schema downgrade. Only when confirmed
    empty does this drop it. Never touches Airport.latitude/.longitude."""
    connection = sqlite3.connect(database.resolve())
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        count = (
            connection.execute(f"SELECT count(*) FROM {TABLE_NAME}").fetchone()[0]
            if _table_exists(connection, TABLE_NAME) else 0
        )
        if count > 0:
            raise RuntimeError(
                f"downgrade() refused: {TABLE_NAME!r} contains {count} recorded coordinate row(s) and "
                "would be destroyed. No table was dropped."
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
