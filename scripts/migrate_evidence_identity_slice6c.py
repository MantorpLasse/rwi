"""Add the Slice 6C physical-installation reconciliation tables.

The migration is additive and creates no identities, decisions, or changes to
legacy domain rows. It is intentionally not run against the development
database without explicit approval.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

from app.database import Base
from app import models as _models  # noqa: F401

DEFAULT_DATABASE = Path("data/runway_safe.db")
TABLES = ("physical_installation_identities", "installation_assertion_links")


def inspect(database: Path) -> dict:
    database = database.resolve()
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        tables = {
            name: bool(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone())
            for name in TABLES
        }
        return {
            "database": str(database),
            "tables": tables,
            "counts": {
                name: connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                if exists else 0
                for name, exists in tables.items()
            },
            "foreign_key_check": connection.execute("PRAGMA foreign_key_check").fetchall(),
        }
    finally:
        connection.close()


def upgrade(database: Path) -> None:
    connection = sqlite3.connect(database.resolve())
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        for name in TABLES:
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone():
                continue
            table = Base.metadata.tables[name]
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
        for name in reversed(TABLES):
            count = connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()[0]
            if count:
                connection.execute(f"DROP TABLE {name}")
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
    args = parser.parse_args(argv)
    if not args.allow_database_write:
        parser.error("--allow-database-write is required")
    (downgrade if args.downgrade else upgrade)(args.database)
    print(inspect(args.database))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

