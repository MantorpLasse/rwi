"""Add source_assertions.signal_id.

See docs/architecture/human-approved-governed-signal-creation-slice9c-report.md
and docs/architecture/reviewer-action-human-signal-promotion-slice9-design.md
S5. Additive only: one new nullable, indexed FK column on the existing
source_assertions table (the same table
scripts/migrate_discovery_governed_evidence_slice1.py,
scripts/migrate_intelligence_review_persistence_slice4.py,
scripts/migrate_promotion_policy_persistence_slice7.py, and
scripts/migrate_reviewer_action_slice9b.py's own reviewer_actions.source_assertion_id
FK all already touch or reference). No existing row in any table is read,
changed, merged, or deleted. The column is added NULL on every existing row
and stays NULL unless a future
app/services/governed_signal_creation.py::create_signal_from_approved_review()
call sets it - no ingestion path and no identity-guard/intelligence-review/
promotion-policy/reviewer-action persistence path is touched by this
migration or reads/writes this column. A timestamped backup is required
before this script writes to the development database.

No CHECK constraint is added - a plain nullable FK, exactly like
identity_guard_decision/intelligence_review_decision/promotion_policy_decision
before it; the governed-signal-creation service is the sole writer and only
ever writes a real Signal.id it just created or is reusing, enforced in
Python.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_DATABASE = Path("data/runway_safe.db")
BACKUP_DIRECTORY = Path("data/backups")
ALTERED_TABLE = "source_assertions"
NEW_COLUMN = "signal_id"


def _backup_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"runway_safe-pre-governed-signal-creation-slice9c-{timestamp}.db"


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
            "signal_id_column_exists": has_column,
            "source_assertions_count": connection.execute(f"SELECT count(*) FROM {ALTERED_TABLE}").fetchone()[0],
            "source_assertions_with_signal_id": (
                connection.execute(f"SELECT count(*) FROM {ALTERED_TABLE} WHERE signal_id IS NOT NULL").fetchone()[0]
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
            connection.execute(
                f"ALTER TABLE {ALTERED_TABLE} ADD COLUMN {NEW_COLUMN} INTEGER REFERENCES signals(id)"
            )
            connection.execute(f"CREATE INDEX ix_source_assertions_signal_id ON {ALTERED_TABLE}({NEW_COLUMN})")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _drop_column_via_rebuild(connection: sqlite3.Connection, table: str, column: str) -> None:
    """Reused verbatim (structure) from
    scripts/migrate_reviewer_action_slice9b.py's own precedent chain -
    ultimately scripts/migrate_canonical_runway_runway_end_slice1.py,
    docs/domain/canonical-runway-migration-downgrade-fix-report.md. Needed
    here because source_assertions IS the target of two incoming foreign
    keys (installation_assertion_links.assertion_id,
    reviewer_actions.source_assertion_id) that SQLite's native
    `ALTER TABLE ... DROP COLUMN` would not safely preserve. The caller must
    already have `PRAGMA foreign_keys=OFF` in effect (set outside the
    current transaction - SQLite ignores changes to this pragma made inside
    one)."""
    info = connection.execute(f"PRAGMA table_info({table})").fetchall()
    remaining = [row for row in info if row[1] != column]
    if len(remaining) == len(info):
        return  # column already absent - nothing to do

    # Unlike every prior migration that reused this rebuild technique (none
    # of whose additive columns were themselves indexed), this migration's
    # own signal_id column has a dedicated index - so, unlike those, the
    # naive "replay every existing index's stored CREATE INDEX text"
    # approach would try to recreate an index on a column that no longer
    # exists. Exclude any index that references the column being dropped,
    # determined by real column membership (PRAGMA index_info), not by
    # text-matching the stored SQL.
    index_names = [
        row[1]
        for row in connection.execute(f"PRAGMA index_list({table})").fetchall()
        if row[3] != "pk"  # exclude the implicit autoindex backing the PRIMARY KEY, if any
    ]
    surviving_index_sql = [
        row[0]
        for name in index_names
        for row in connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=? AND sql IS NOT NULL", (name,)
        ).fetchall()
        if column not in {info_row[2] for info_row in connection.execute(f"PRAGMA index_info({name})").fetchall()}
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
