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


def _drop_column_via_rebuild(connection: sqlite3.Connection, table: str, column: str) -> None:
    """SQLite's native `ALTER TABLE ... DROP COLUMN` leaves a dangling
    table-level `FOREIGN KEY(...)` clause - and then fails with "unknown
    column ... in foreign key definition" - when the dropped column's FK
    constraint was declared as part of the table's original `CREATE
    TABLE` statement (e.g. a fresh `Base.metadata.create_all()`, which
    always emits FKs as separate table-level clauses). It works fine when
    the constraint was instead added later via `ALTER TABLE ADD COLUMN
    ... REFERENCES ...` (an inline, column-level constraint that is
    removed along with the column) - which is how upgrade() above
    actually adds NEW_COLUMN to an already-existing database, and why
    downgrading the real, already-upgraded development database was
    never at risk. A brand-new database created after this migration was
    merged (every test fixture in this repository, and any future fresh
    deployment) has the first, unsafe shape instead. See
    docs/domain/canonical-runway-migration-downgrade-fix-report.md.

    This rebuilds the table using SQLite's own documented "12-step"
    procedure for schema changes `DROP COLUMN` cannot perform safely,
    which is correct regardless of which of the two shapes above the
    table has. The caller must already have `PRAGMA foreign_keys=OFF` in
    effect (set outside the current transaction - SQLite ignores changes
    to this pragma made inside one) for the duration of the rebuild,
    since other tables' foreign keys briefly point at a table that
    doesn't exist between the rename and the final `INSERT`."""
    info = connection.execute(f"PRAGMA table_info({table})").fetchall()
    remaining = [row for row in info if row[1] != column]
    if len(remaining) == len(info):
        return  # column already absent - nothing to do

    surviving_index_sql = [
        row[0]
        for row in connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table,)
        ).fetchall()
    ]
    # PRAGMA table_info() says nothing about foreign keys - without this,
    # every *surviving* column's own FK constraint (e.g. airport_id ->
    # airports.id) would silently disappear during the rebuild too,
    # which is exactly as unsafe as the bug this function fixes. Only
    # keep constraints whose local column is not the one being dropped.
    # SQLite returns foreign_key_list in reverse declaration order -
    # reversed back here purely so the rebuilt CREATE TABLE text matches
    # the original byte-for-byte; has no effect on behavior either way.
    surviving_fks = [
        row
        for row in reversed(connection.execute(f"PRAGMA foreign_key_list({table})").fetchall())
        if row[3] != column  # row[3] is the local ("from") column name
    ]

    column_defs: list[str] = []
    pk_columns: list[str] = []
    for _cid, name, coltype, notnull, dflt_value, pk in remaining:
        definition = f"{name} {coltype}"
        if notnull:
            definition += " NOT NULL"
        if dflt_value is not None:
            definition += f" DEFAULT {dflt_value}"
        column_defs.append(definition)
        if pk:
            pk_columns.append(name)
    if pk_columns:
        column_defs.append(f"PRIMARY KEY ({', '.join(pk_columns)})")
    for _id, _seq, ref_table, from_col, to_col, *_rest in surviving_fks:
        column_defs.append(f"FOREIGN KEY({from_col}) REFERENCES {ref_table} ({to_col})")
    column_names = ", ".join(row[1] for row in remaining)

    # Order matters: build the replacement under a temporary name FIRST,
    # then DROP the original (which still holds the real table's name
    # right up to that point), and only THEN rename the replacement into
    # place - exactly SQLite's own documented step order. Doing it the
    # other way around (rename the original out of the way first, create
    # the replacement directly under the real name) breaks other tables'
    # foreign keys: SQLite auto-follows a RENAME for every other table's
    # FK declarations that pointed at the renamed table, permanently
    # rebinding them to the new (temporary) name - so a later table that
    # reclaims the original name is never picked up, and those foreign
    # keys are left dangling. Confirmed by this exact failure mode during
    # development of this fix - see
    # docs/domain/canonical-runway-migration-downgrade-fix-report.md.
    new_table = f"_{table}_downgrade_new"
    connection.execute(f"CREATE TABLE {new_table} ({', '.join(column_defs)})")
    connection.execute(f"INSERT INTO {new_table} ({column_names}) SELECT {column_names} FROM {table}")
    connection.execute(f"DROP TABLE {table}")
    connection.execute(f"ALTER TABLE {new_table} RENAME TO {table}")
    for index_sql in surviving_index_sql:
        connection.execute(index_sql)


def downgrade(database: Path) -> None:
    connection = sqlite3.connect(database.resolve())
    try:
        # Must be set before BEGIN - SQLite ignores changes to this
        # pragma made inside a transaction. Required for the table
        # rebuild in _drop_column_via_rebuild(); re-enabled and verified
        # with PRAGMA foreign_key_check before commit, below.
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({ALTERED_TABLE})")}
        if NEW_COLUMN in columns:
            connection.execute(f"DROP INDEX IF EXISTS ix_{ALTERED_TABLE}_{NEW_COLUMN}")
            _drop_column_via_rebuild(connection, ALTERED_TABLE, NEW_COLUMN)
        if connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?", (NEW_TABLE,)
        ).fetchone()[0]:
            connection.execute(f"DROP TABLE {NEW_TABLE}")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"downgrade() would leave foreign-key violations: {violations}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys=ON")
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
