"""Add signals.published and backfill it to preserve current public output.

See docs/architecture/reviewer-action-human-signal-promotion-slice9-design.md
S8 and docs/architecture/signal-publication-separation-slice9a-report.md.
Before this migration, app/static_export/build.py::_is_public_signal()
decided publication by a hardcoded exclusion of Signal ids (52, 54) - every
other Signal was public. This migration replaces that with an explicit,
per-row column while preserving the exact same effective output.

Unlike the additive-nullable-column migrations this repository has used for
source_assertions (identity_guard_*, intelligence_review_*,
promotion_policy_*: added NULL on every row, populated later by a service),
this column cannot be left NULL - "is this Signal public" must have a real
answer for every existing row on day one, since the static export reads it
immediately. So this migration adds the column NOT NULL with a SQL-level
DEFAULT of 1 (true) - which SQLite uses to backfill every existing row in the
same ALTER TABLE statement - and then explicitly flips the two
previously-excluded ids back to 0 (false). The result is byte-for-byte
equivalent public/private Signal partitioning to before this migration; see
the report's "legacy public-set preservation" section for the before/after
comparison this claim is checked against.

No other table is touched. A timestamped backup is required before this
script writes to the development database.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_DATABASE = Path("data/runway_safe.db")
BACKUP_DIRECTORY = Path("data/backups")
ALTERED_TABLE = "signals"
NEW_COLUMN = "published"

# The exact hardcoded exclusion app/static_export/build.py::_is_public_signal()
# used before this migration. This is the sole source of truth for which
# existing rows must be backfilled to published=0 rather than the column's
# own default of 1 - not re-derived from any other heuristic.
LEGACY_EXCLUDED_SIGNAL_IDS = (52, 54)


def _backup_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"runway_safe-pre-signal-publication-slice9a-{timestamp}.db"


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
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({ALTERED_TABLE})")}
        has_column = NEW_COLUMN in columns
        return {
            "database": str(database),
            "published_column_exists": has_column,
            "signals_count": connection.execute(f"SELECT count(*) FROM {ALTERED_TABLE}").fetchone()[0],
            "published_true_count": (
                connection.execute(f"SELECT count(*) FROM {ALTERED_TABLE} WHERE published=1").fetchone()[0]
                if has_column
                else None
            ),
            "published_false_count": (
                connection.execute(f"SELECT count(*) FROM {ALTERED_TABLE} WHERE published=0").fetchone()[0]
                if has_column
                else None
            ),
            "legacy_excluded_ids_published_false": (
                all(
                    connection.execute(
                        f"SELECT published FROM {ALTERED_TABLE} WHERE id=?", (signal_id,)
                    ).fetchone()
                    == (0,)
                    for signal_id in LEGACY_EXCLUDED_SIGNAL_IDS
                    if connection.execute(f"SELECT 1 FROM {ALTERED_TABLE} WHERE id=?", (signal_id,)).fetchone()
                )
                if has_column
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
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({ALTERED_TABLE})")}
        if NEW_COLUMN not in columns:
            # NOT NULL with a DEFAULT backfills every existing row to 1
            # (published) as part of this single statement.
            connection.execute(f"ALTER TABLE {ALTERED_TABLE} ADD COLUMN {NEW_COLUMN} BOOLEAN NOT NULL DEFAULT 1")
        # Idempotent regardless of whether the column already existed: always
        # re-assert the two legacy-excluded rows are published=0, matching
        # _is_public_signal()'s pre-migration output exactly.
        placeholders = ", ".join("?" for _ in LEGACY_EXCLUDED_SIGNAL_IDS)
        connection.execute(
            f"UPDATE {ALTERED_TABLE} SET {NEW_COLUMN}=0 WHERE id IN ({placeholders})",
            LEGACY_EXCLUDED_SIGNAL_IDS,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _drop_column_via_rebuild(connection: sqlite3.Connection, table: str, column: str) -> None:
    """Reused verbatim (structure) from
    scripts/migrate_promotion_policy_persistence_slice7.py::_drop_column_via_rebuild()
    - see that module's docstring for why SQLite's native
    `ALTER TABLE ... DROP COLUMN` is not used directly. The caller must
    already have `PRAGMA foreign_keys=OFF` in effect (set outside the
    current transaction - SQLite ignores changes to this pragma made inside
    one)."""
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
        # Must be set before BEGIN - SQLite ignores changes to this pragma
        # made inside a transaction.
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({ALTERED_TABLE})")}
        if NEW_COLUMN in columns:
            _drop_column_via_rebuild(connection, ALTERED_TABLE, NEW_COLUMN)
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
