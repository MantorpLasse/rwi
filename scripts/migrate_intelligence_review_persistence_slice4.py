"""Add source_assertions.intelligence_review_decision and .intelligence_review_reason.

See docs/architecture/intelligence-review-persistence-slice4-report.md and
docs/architecture/evidence-to-signal-semantics-design.md S17/S18.
Additive only: two new nullable columns on the existing source_assertions
table (the same table scripts/migrate_discovery_governed_evidence_slice1.py
already added identity_guard_decision/identity_guard_reason to). No
existing row in any table is read, changed, merged, or deleted. Both
columns are added NULL on every existing row and stay NULL unless a
future app/services/intelligence_review_persistence.py write sets them -
no existing ingestion path (NASR, USAspending, FAA acquisition) and no
identity-guard write path is touched by this migration or reads/writes
either column. A timestamped backup is required before this script writes
to the development database.

No DB-level CHECK constraint bounds intelligence_review_decision to the
six SignalCandidateOutcome values in this slice - SQLite's ADD COLUMN
cannot add a CHECK constraint as safely/simply as a plain nullable
column, and doing so would require the same full table-rebuild procedure
downgrade() already needs for DROP COLUMN, which is more than "smallest
additive migration" calls for here (identical reasoning to
migrate_discovery_governed_evidence_slice1.py's own identity_guard_decision
column). The persistence service is the only writer of this column and
only ever writes a real SignalCandidateOutcome.value, enforced in Python.
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
NEW_COLUMNS = ("intelligence_review_decision", "intelligence_review_reason")


def _backup_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"runway_safe-pre-intelligence-review-persistence-slice4-{timestamp}.db"


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
        return {
            "database": str(database),
            "intelligence_review_decision_column_exists": "intelligence_review_decision" in columns,
            "intelligence_review_reason_column_exists": "intelligence_review_reason" in columns,
            "source_assertions_count": connection.execute(f"SELECT count(*) FROM {ALTERED_TABLE}").fetchone()[0],
            "source_assertions_with_intelligence_review_decision": (
                connection.execute(
                    f"SELECT count(*) FROM {ALTERED_TABLE} WHERE intelligence_review_decision IS NOT NULL"
                ).fetchone()[0]
                if "intelligence_review_decision" in columns
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
        if "intelligence_review_decision" not in columns:
            connection.execute(f"ALTER TABLE {ALTERED_TABLE} ADD COLUMN intelligence_review_decision VARCHAR(30)")
        if "intelligence_review_reason" not in columns:
            connection.execute(f"ALTER TABLE {ALTERED_TABLE} ADD COLUMN intelligence_review_reason TEXT")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _drop_column_via_rebuild(connection: sqlite3.Connection, table: str, column: str) -> None:
    """Reused verbatim from
    scripts/migrate_discovery_governed_evidence_slice1.py::_drop_column_via_rebuild()
    (itself reused from scripts/migrate_canonical_runway_runway_end_slice1.py,
    docs/domain/canonical-runway-migration-downgrade-fix-report.md) - SQLite's
    native `ALTER TABLE ... DROP COLUMN` can leave a dangling table-level
    FOREIGN KEY clause on tables whose constraints were declared as part of
    the original CREATE TABLE (exactly what a fresh Base.metadata.create_all()
    produces). Neither new column here is itself part of any foreign key, but
    source_assertions IS the target of an incoming foreign key
    (installation_assertion_links.assertion_id) - this rebuild preserves
    that and every other surviving constraint/index exactly, regardless of
    which column is being dropped. The caller must already have
    `PRAGMA foreign_keys=OFF` in effect (set outside the current
    transaction - SQLite ignores changes to this pragma made inside one)."""
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
        for column in NEW_COLUMNS:
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({ALTERED_TABLE})")}
            if column in columns:
                _drop_column_via_rebuild(connection, ALTERED_TABLE, column)
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
