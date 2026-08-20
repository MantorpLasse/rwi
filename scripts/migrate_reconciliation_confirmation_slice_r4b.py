"""Add reviewer_actions.reconciliation_fingerprint and the CONFIRM_DISTINCT_SIGNAL
action vocabulary/CHECK constraints (R4B of
docs/architecture/existing-signal-reconciliation-r4-human-resolution-design.md;
see docs/architecture/existing-signal-reconciliation-r4b-reviewer-action-report.md).

Additive only: one new nullable column
(reviewer_actions.reconciliation_fingerprint, sized for a SHA-256 hex
digest) and one new action value in the existing action vocabulary/CHECK
constraint. No existing table other than reviewer_actions is touched. Every
existing reviewer_actions row is preserved byte-for-byte - the new column is
simply NULL for all of them, which the new CHECK constraints already permit
for every pre-existing action value.

SQLite cannot ALTER TABLE to add or change a CHECK constraint (`ALTER TABLE
... ADD CONSTRAINT` does not exist in SQLite, and `ALTER TABLE ... ADD
COLUMN` may only add a constraint scoped to that single new column - it
cannot widen `action`'s own CHECK). Both the widened `action` vocabulary
CHECK and the two new fingerprint CHECKs therefore require a full table
rebuild, using the same documented 12-step ALTER TABLE procedure already
established in scripts/migrate_canonical_runway_runway_end_slice1.py's
`_drop_column_via_rebuild` for the equivalent DROP COLUMN case. Unlike that
helper, this migration writes the target CHECK-constraint text explicitly
(hardcoded below) rather than deriving it from the live SQLAlchemy model -
deliberately, so this script's own upgrade()/downgrade() behavior can never
silently drift if app.models.reviewer_action gains further, later,
unrelated changes.

A timestamped backup is required before this script writes to the
development database, matching every migration since
scripts/migrate_promotion_policy_persistence_slice7.py established that
discipline. This script is never run against the real database in this
implementation task - every test uses an isolated temp-file SQLite database
(tmp_path).
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_DATABASE = Path("data/runway_safe.db")
BACKUP_DIRECTORY = Path("data/backups")
TABLE = "reviewer_actions"
NEW_COLUMN = "reconciliation_fingerprint"

# Exact mirrors of app.models.reviewer_action.ReviewerAction.__table_args__,
# reproduced literally (not imported) - see the module docstring above for
# why. Keep these two lists in sync with that model by hand whenever either
# changes; tests/test_reviewer_action_persistence.py::
# test_reviewer_actions_vocabulary_is_exactly_the_five_approved_actions and
# its R4B sibling already guard the Python-side vocabulary against drift.
_CHECK_CLAUSES_PRE_R4B = (
    "CONSTRAINT ck_reviewer_actions_action CHECK (action IN "
    "('APPROVE_SIGNAL','REJECT_SIGNAL','DEFER','NEEDS_MORE_EVIDENCE','MARK_DUPLICATE'))",
    "CONSTRAINT ck_reviewer_actions_duplicate_target_required CHECK "
    "((action != 'MARK_DUPLICATE') OR duplicate_of_signal_id IS NOT NULL)",
    "CONSTRAINT ck_reviewer_actions_duplicate_target_only_for_duplicate CHECK "
    "((action = 'MARK_DUPLICATE') OR duplicate_of_signal_id IS NULL)",
)
_CHECK_CLAUSES_POST_R4B = (
    "CONSTRAINT ck_reviewer_actions_action CHECK (action IN "
    "('APPROVE_SIGNAL','REJECT_SIGNAL','DEFER','NEEDS_MORE_EVIDENCE',"
    "'MARK_DUPLICATE','CONFIRM_DISTINCT_SIGNAL'))",
    "CONSTRAINT ck_reviewer_actions_duplicate_target_required CHECK "
    "((action != 'MARK_DUPLICATE') OR duplicate_of_signal_id IS NOT NULL)",
    "CONSTRAINT ck_reviewer_actions_duplicate_target_only_for_duplicate CHECK "
    "((action = 'MARK_DUPLICATE') OR duplicate_of_signal_id IS NULL)",
    "CONSTRAINT ck_reviewer_actions_fingerprint_required CHECK "
    "((action != 'CONFIRM_DISTINCT_SIGNAL') OR reconciliation_fingerprint IS NOT NULL)",
    "CONSTRAINT ck_reviewer_actions_fingerprint_only_for_confirm_distinct CHECK "
    "((action = 'CONFIRM_DISTINCT_SIGNAL') OR reconciliation_fingerprint IS NULL)",
)


def _backup_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"runway_safe-pre-reconciliation-confirmation-slice-r4b-{timestamp}.db"


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
        table_exists = bool(
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
            ).fetchone()
        )
        columns = (
            {row[1] for row in connection.execute(f"PRAGMA table_info({TABLE})")} if table_exists else set()
        )
        return {
            "database": str(database),
            "reviewer_actions_table_exists": table_exists,
            "reconciliation_fingerprint_column_exists": NEW_COLUMN in columns,
            "reviewer_actions_count": (
                connection.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0] if table_exists else 0
            ),
            "foreign_key_check": connection.execute("PRAGMA foreign_key_check").fetchall(),
        }
    finally:
        connection.close()


def _column_defs_and_fks(connection: sqlite3.Connection, table: str, *, exclude: str | None = None):
    info = connection.execute(f"PRAGMA table_info({table})").fetchall()
    surviving_index_sql = [
        row[0]
        for row in connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table,)
        ).fetchall()
    ]
    fks = [
        row
        for row in reversed(connection.execute(f"PRAGMA foreign_key_list({table})").fetchall())
        if row[3] != exclude  # row[3] is the local ("from") column name
    ]

    column_defs: list[str] = []
    pk_columns: list[str] = []
    column_names: list[str] = []
    for _cid, name, coltype, notnull, dflt_value, pk in info:
        if name == exclude:
            continue
        column_names.append(name)
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
    for _id, _seq, ref_table, from_col, to_col, *_rest in fks:
        column_defs.append(f"FOREIGN KEY({from_col}) REFERENCES {ref_table} ({to_col})")

    return column_defs, column_names, surviving_index_sql


def _rebuild(connection: sqlite3.Connection, *, add_column: bool) -> None:
    """Rebuilds `reviewer_actions` using SQLite's documented 12-step ALTER
    TABLE procedure (create replacement -> copy -> drop original -> rename
    replacement into place -> recreate indexes), the same order established
    by scripts/migrate_canonical_runway_runway_end_slice1.py's
    `_drop_column_via_rebuild` and for the same reason: renaming the
    original out of the way first would permanently rebind any other
    table's foreign keys that point at it.

    `add_column=True` (upgrade): adds `reconciliation_fingerprint` and
    switches to the 5-constraint post-R4B CHECK set.
    `add_column=False` (downgrade): drops `reconciliation_fingerprint` and
    restores the original 3-constraint pre-R4B CHECK set. If any row has
    action='CONFIRM_DISTINCT_SIGNAL', the pre-R4B action CHECK constraint
    (which does not include that value) rejects the copy on INSERT,
    surfacing as a normal exception the caller's transaction wrapper rolls
    back - downgrading is refused rather than silently discarding that
    row's meaning.
    """
    if add_column:
        column_defs, column_names, surviving_index_sql = _column_defs_and_fks(connection, TABLE)
        # Insert the new column definition before any table-level constraint
        # clause (PRIMARY KEY, FOREIGN KEY) - SQLite requires every column
        # definition to precede table-level constraints in a CREATE TABLE
        # statement, so it cannot simply be appended. The new column is
        # deliberately absent from column_names - it is not copied from the
        # old table (it doesn't exist there) and lands NULL on every
        # existing row via SQLite's implicit default for an omitted column.
        insert_at = sum(
            1 for d in column_defs if not d.startswith("PRIMARY KEY") and not d.startswith("FOREIGN KEY")
        )
        column_defs.insert(insert_at, f"{NEW_COLUMN} VARCHAR(64)")
        check_clauses = _CHECK_CLAUSES_POST_R4B
    else:
        column_defs, column_names, surviving_index_sql = _column_defs_and_fks(connection, TABLE, exclude=NEW_COLUMN)
        check_clauses = _CHECK_CLAUSES_PRE_R4B

    all_clauses = column_defs + list(check_clauses)
    column_list = ", ".join(column_names)

    new_table = f"_{TABLE}_r4b_rebuild"
    connection.execute(f"CREATE TABLE {new_table} ({', '.join(all_clauses)})")
    connection.execute(f"INSERT INTO {new_table} ({column_list}) SELECT {column_list} FROM {TABLE}")
    connection.execute(f"DROP TABLE {TABLE}")
    connection.execute(f"ALTER TABLE {new_table} RENAME TO {TABLE}")
    for index_sql in surviving_index_sql:
        connection.execute(index_sql)


def upgrade(database: Path) -> None:
    connection = sqlite3.connect(database.resolve())
    try:
        # Must be set before BEGIN - SQLite ignores changes to this pragma
        # made inside a transaction. Required so other tables' foreign keys
        # into reviewer_actions (there are none today, but this matches the
        # established defensive convention) survive the brief window where
        # the table doesn't exist between DROP and RENAME.
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
        ).fetchone():
            raise RuntimeError(
                f"{TABLE} does not exist - run scripts/migrate_reviewer_action_slice9b.py first"
            )
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({TABLE})")}
        if NEW_COLUMN not in columns:
            _rebuild(connection, add_column=True)
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"upgrade() would leave foreign-key violations: {violations}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.close()


def downgrade(database: Path) -> None:
    connection = sqlite3.connect(database.resolve())
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        columns = (
            {row[1] for row in connection.execute(f"PRAGMA table_info({TABLE})")}
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
            ).fetchone()
            else set()
        )
        if NEW_COLUMN in columns:
            _rebuild(connection, add_column=False)
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
