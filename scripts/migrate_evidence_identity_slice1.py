"""Repair source foreign keys and add the SourceAssertion foundation.

This is a one-time SQLite migration. It only changes schema: all legacy rows
are copied byte-for-byte into replacement tables and no Installation is read,
reconciled, merged, or created. A timestamped backup is required before this
script writes to the development database.
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
CORE_TABLES = ("installations", "signals", "incidents")


def _backup_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"runway_safe-pre-evidence-identity-slice1-{timestamp}.db"


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


def investigate(database: Path) -> dict:
    database = database.resolve()
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        foreign_key_lists = {
            table: connection.execute(f"PRAGMA foreign_key_list({table})").fetchall()
            for table in CORE_TABLES
        }
        counts = {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in (*CORE_TABLES, "sources")}
        logical_orphans = {
            table: connection.execute(
                f"SELECT count(*) FROM {table} WHERE source_id IS NOT NULL "
                "AND source_id NOT IN (SELECT id FROM sources)"
            ).fetchone()[0]
            for table in CORE_TABLES
        }
        return {
            "foreign_keys": foreign_keys,
            "foreign_key_lists": foreign_key_lists,
            "counts": counts,
            "logical_orphans": logical_orphans,
            "foreign_key_check": connection.execute("PRAGMA foreign_key_check").fetchall(),
        }
    finally:
        connection.close()


def _rebuild_source_fk_tables(connection: sqlite3.Connection) -> None:
    for table in CORE_TABLES:
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        index_sql = [
            row[0]
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table,)
            )
        ]
        replacement = f"{table}__slice1_new"
        replacement_sql = table_sql.replace(f"CREATE TABLE {table}", f"CREATE TABLE {replacement}")
        replacement_sql = replacement_sql.replace('"sources_old"', '"sources"').replace("sources_old", "sources")
        connection.execute(replacement_sql)
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
        quoted = ", ".join(f'"{column}"' for column in columns)
        connection.execute(f'INSERT INTO "{replacement}" ({quoted}) SELECT {quoted} FROM "{table}"')
        connection.execute(f'DROP TABLE "{table}"')
        connection.execute(f'ALTER TABLE "{replacement}" RENAME TO "{table}"')
        for statement in index_sql:
            connection.execute(statement)


def migrate(database: Path) -> None:
    database = database.resolve()
    result = investigate(database)
    if any(result["logical_orphans"].values()):
        raise RuntimeError("Cannot repair source foreign keys with logical source orphans present.")

    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        _rebuild_source_fk_tables(connection)
        assertion_table = Base.metadata.tables["source_assertions"]
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_assertions'"
        ).fetchone()
        if not exists:
            connection.execute(str(CreateTable(assertion_table).compile(dialect=sqlite.dialect())))
            for index in assertion_table.indexes:
                connection.execute(str(CreateIndex(index).compile(dialect=sqlite.dialect())))
        connection.commit()
        connection.execute("PRAGMA foreign_keys=ON")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Foreign-key repair failed: {violations!r}")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--allow-database-write", action="store_true")
    args = parser.parse_args(argv)
    if not args.allow_database_write:
        parser.error("--allow-database-write is required")
    database = args.database.resolve()
    print("Target database:", database)
    before = investigate(database)
    print("Foreign-key violations before repair:", len(before["foreign_key_check"]))
    backup = backup_database(database)
    print("Backup created:", backup)
    migrate(database)
    after = investigate(database)
    print("Foreign-key violations after repair:", len(after["foreign_key_check"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
