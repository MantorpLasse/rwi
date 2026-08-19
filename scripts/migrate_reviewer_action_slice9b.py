"""Add the Slice 9B reviewer_actions table.

See docs/architecture/reviewer-action-persistence-slice9b-report.md and
docs/architecture/reviewer-action-human-signal-promotion-slice9-design.md.
Additive only: creates exactly one new table (and its indexes/CHECK
constraints), reused directly from the ORM model
(app.models.reviewer_action.ReviewerAction) via SQLAlchemy's own
CreateTable/CreateIndex compilation - the same technique
scripts/migrate_evidence_identity_slice6c.py already uses for the
structurally identical InstallationAssertionLink precedent, so the created
schema can never drift from the model definition. No existing table, column,
or row is read, changed, merged, or deleted. A timestamped backup is
required before this script writes to the development database (matching
the backup discipline scripts/migrate_promotion_policy_persistence_slice7.py
established; migrate_evidence_identity_slice6c.py itself predates that
discipline and has none - this migration adds it back since this slice's own
governing instruction requires it explicitly).

No SourceAssertion.signal_id, no Signal schema change, no automatic Signal
creation - this migration creates only the append-only decision table
itself.
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
from app import models as _models  # noqa: F401 - registers all metadata, including ReviewerAction

DEFAULT_DATABASE = Path("data/runway_safe.db")
BACKUP_DIRECTORY = Path("data/backups")
TABLE = "reviewer_actions"


def _backup_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"runway_safe-pre-reviewer-action-slice9b-{timestamp}.db"


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
        exists = bool(
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
            ).fetchone()
        )
        return {
            "database": str(database),
            "reviewer_actions_table_exists": exists,
            "reviewer_actions_count": connection.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0]
            if exists
            else 0,
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
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
        ).fetchone():
            table = Base.metadata.tables[TABLE]
            connection.execute(str(CreateTable(table).compile(dialect=sqlite.dialect())))
            for index in table.indexes:
                connection.execute(str(CreateIndex(index).compile(dialect=sqlite.dialect())))
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
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
        ).fetchone():
            connection.execute(f"DROP TABLE {TABLE}")
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
