"""Add the canonical RunwayEnd table and PhysicalInstallationIdentity.runway_end_id.

See docs/domain/canonical-runway-runway-end-design.md and
docs/domain/canonical-runway-runway-end-slice1-report.md. Additive only:
creates one new table (runway_ends) and one new nullable column
(physical_installation_identities.runway_end_id). No existing row in any
table is read, changed, merged, or deleted. No PhysicalInstallationIdentity
is linked - runway_end_id is added NULL on every existing row and stays
NULL until a separate, explicitly approved linking step. A timestamped
backup is required before this script writes to the development database.
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
from app import models as _models  # noqa: F401 - registers all metadata

DEFAULT_DATABASE = Path("data/runway_safe.db")
BACKUP_DIRECTORY = Path("data/backups")
NEW_TABLE = "runway_ends"
ALTERED_TABLE = "physical_installation_identities"
NEW_COLUMN = "runway_end_id"


def _backup_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"runway_safe-pre-canonical-runway-runway-end-slice1-{timestamp}.db"


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


def inspect(database: Path) -> dict:
    database = database.resolve()
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        has_table = bool(
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (NEW_TABLE,)
            ).fetchone()
        )
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({ALTERED_TABLE})")}
        return {
            "database": str(database),
            "runway_ends_table_exists": has_table,
            "runway_end_id_column_exists": NEW_COLUMN in columns,
            "runway_ends_count": (
                connection.execute(f"SELECT count(*) FROM {NEW_TABLE}").fetchone()[0] if has_table else 0
            ),
            "physical_installation_identities_count": connection.execute(
                f"SELECT count(*) FROM {ALTERED_TABLE}"
            ).fetchone()[0],
            "physical_installation_identities_with_runway_end_id": (
                connection.execute(
                    f"SELECT count(*) FROM {ALTERED_TABLE} WHERE {NEW_COLUMN} IS NOT NULL"
                ).fetchone()[0]
                if NEW_COLUMN in columns
                else None
            ),
            "foreign_key_check": connection.execute("PRAGMA foreign_key_check").fetchall(),
        }
    finally:
        connection.close()


def upgrade(database: Path) -> None:
    connection = sqlite3.connect(database.resolve())
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (NEW_TABLE,)
        ).fetchone():
            table = Base.metadata.tables[NEW_TABLE]
            connection.execute(str(CreateTable(table).compile(dialect=sqlite.dialect())))
            for index in table.indexes:
                connection.execute(str(CreateIndex(index).compile(dialect=sqlite.dialect())))
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({ALTERED_TABLE})")}
        if NEW_COLUMN not in columns:
            connection.execute(
                f"ALTER TABLE {ALTERED_TABLE} ADD COLUMN {NEW_COLUMN} INTEGER REFERENCES {NEW_TABLE}(id)"
            )
            connection.execute(
                f"CREATE INDEX IF NOT EXISTS ix_{ALTERED_TABLE}_{NEW_COLUMN} ON {ALTERED_TABLE}({NEW_COLUMN})"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def downgrade(database: Path) -> None:
    connection = sqlite3.connect(database.resolve())
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({ALTERED_TABLE})")}
        if NEW_COLUMN in columns:
            connection.execute(f"DROP INDEX IF EXISTS ix_{ALTERED_TABLE}_{NEW_COLUMN}")
            connection.execute(f"ALTER TABLE {ALTERED_TABLE} DROP COLUMN {NEW_COLUMN}")
        if connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?", (NEW_TABLE,)
        ).fetchone()[0]:
            connection.execute(f"DROP TABLE {NEW_TABLE}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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
