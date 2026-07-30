"""Standalone, read-only sanity check comparing two SQLite snapshots of this
project's database - e.g. a pre-fix backup in data/backups/ against the
current data/runway_safe.db - to make sure a one-off data-editing script
(like scripts/remove_docs_and_field_leaks_from_notes.py) only touched the
fields it meant to and didn't drop or corrupt anything else.

Does not import the app or any ORM models, and opens both databases with
sqlite3's mode=ro URI flag, so there is no code path in this script that can
write to either file.

For each of airports/installations/incidents/signals/sources, reports:
  - row count in each snapshot, and whether the current one is smaller
    (a dropped row that isn't in "missing ids" would still show up here,
    e.g. if ids were also reused - the two checks are complementary)
  - ids present in the old snapshot but absent from the new one (deleted)
  - ids present in the new snapshot but absent from the old one (added)
  - for ids present in both: every column that differs, split into:
      * intentional content changes (Signal.source_notes /
        Installation.notes - the two fields the recent leak-fix scripts
        deliberately rewrote)
      * expected timestamp side effects (updated_at bumped by SQLAlchemy's
        onupdate on a row that also had an intentional content change -
        not itself evidence of a problem)
      * unexpected diffs - anything else. This is the one bucket that
        would actually indicate accidental data loss/corruption.

Usage:
    python -m scripts.compare_db_snapshots [old_db] [new_db]

Both arguments are optional; they default to the backup taken immediately
before the 2026-07-30 docs/field-leak fix and the current live database.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

DEFAULT_OLD_DB = "data/backups/runway_safe-pre-docs-and-field-leak-fix-20260730-165414.db"
DEFAULT_NEW_DB = "data/runway_safe.db"

# table name -> primary key column
TABLES_TO_COMPARE = {
    "airports": "id",
    "installations": "id",
    "incidents": "id",
    "signals": "id",
    "sources": "id",
}

# Fields the recent one-off scripts deliberately rewrote - a diff here is
# the intended outcome, not something to flag.
INTENTIONAL_CONTENT_COLUMNS = {
    "signals": {"source_notes"},
    "installations": {"notes"},
}

TIMESTAMP_COLUMN = "updated_at"


def open_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    # mode=ro makes sqlite3 refuse any write at the driver level, on top of
    # this script simply never issuing one.
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]


def fetch_rows(conn: sqlite3.Connection, table: str) -> tuple[list[str], dict[int, sqlite3.Row]]:
    columns = table_columns(conn, table)
    cur = conn.execute(f"SELECT * FROM {table}")
    rows = {row["id"]: row for row in cur.fetchall()}
    return columns, rows


class TableReport:
    def __init__(self, table: str):
        self.table = table
        self.old_count = 0
        self.new_count = 0
        self.missing_ids: list[int] = []
        self.added_ids: list[int] = []
        self.intentional_changes: list[tuple[int, str]] = []
        self.expected_timestamp_changes: list[tuple[int, str]] = []
        self.unexpected_diffs: list[tuple[int, str, object, object]] = []

    @property
    def row_count_decreased(self) -> bool:
        return self.new_count < self.old_count


def compare_table(old_conn: sqlite3.Connection, new_conn: sqlite3.Connection, table: str) -> TableReport:
    old_columns, old_rows = fetch_rows(old_conn, table)
    new_columns, new_rows = fetch_rows(new_conn, table)

    report = TableReport(table)
    report.old_count = len(old_rows)
    report.new_count = len(new_rows)
    report.missing_ids = sorted(set(old_rows) - set(new_rows))
    report.added_ids = sorted(set(new_rows) - set(old_rows))

    intentional_columns = INTENTIONAL_CONTENT_COLUMNS.get(table, set())
    # Only compare columns both snapshots actually have - a schema change
    # between snapshots is a separate concern from data loss/corruption.
    shared_columns = [c for c in old_columns if c in new_columns]

    for row_id in sorted(set(old_rows) & set(new_rows)):
        old_row = old_rows[row_id]
        new_row = new_rows[row_id]

        touched_intentionally = False
        column_diffs: list[str] = []
        for column in shared_columns:
            if old_row[column] != new_row[column]:
                column_diffs.append(column)
                if column in intentional_columns:
                    touched_intentionally = True

        for column in column_diffs:
            if column in intentional_columns:
                report.intentional_changes.append((row_id, column))
            elif column == TIMESTAMP_COLUMN and touched_intentionally:
                report.expected_timestamp_changes.append((row_id, column))
            else:
                report.unexpected_diffs.append((row_id, column, old_row[column], new_row[column]))

    return report


def print_report(reports: list[TableReport], old_path: Path, new_path: Path) -> None:
    print("Read-only DB comparison (no data was modified by this script)")
    print(f"  old: {old_path}")
    print(f"  new: {new_path}")
    print()

    any_row_count_decrease = False
    any_missing_rows = False
    any_unexpected_diffs = False

    for report in reports:
        print(f"=== {report.table} ===")
        print(f"  rows: old={report.old_count}  new={report.new_count}", end="")
        if report.row_count_decreased:
            print("  <-- DECREASED")
            any_row_count_decrease = True
        else:
            print()

        if report.missing_ids:
            any_missing_rows = True
            print(f"  MISSING ids (in old, gone from new): {report.missing_ids}")
        else:
            print("  missing ids: none")

        if report.added_ids:
            print(f"  added ids (in new, not in old): {report.added_ids}")

        if report.intentional_changes:
            by_column: dict[str, list[int]] = {}
            for row_id, column in report.intentional_changes:
                by_column.setdefault(column, []).append(row_id)
            for column, ids in by_column.items():
                print(f"  intentional content change in '{column}': {len(ids)} rows {sorted(ids)}")

        if report.expected_timestamp_changes:
            ids = sorted({row_id for row_id, _ in report.expected_timestamp_changes})
            print(f"  expected updated_at bump (row also had an intentional content change): {len(ids)} rows {ids}")

        if report.unexpected_diffs:
            any_unexpected_diffs = True
            print(f"  UNEXPECTED field diffs: {len(report.unexpected_diffs)}")
            for row_id, column, old_value, new_value in report.unexpected_diffs:
                print(f"    id={row_id} column={column!r}: {old_value!r} -> {new_value!r}")
        else:
            print("  unexpected field diffs: none")
        print()

    print("=== summary ===")
    print(f"  any table's row count decreased: {any_row_count_decrease}")
    print(f"  any row present in old but missing from new: {any_missing_rows}")
    print(f"  any unexpected (non notes/source_notes, non-timestamp-side-effect) field diff: {any_unexpected_diffs}")
    if not (any_row_count_decrease or any_missing_rows or any_unexpected_diffs):
        print("  No evidence of data loss or unintended field changes.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("old_db", nargs="?", default=DEFAULT_OLD_DB, type=Path)
    parser.add_argument("new_db", nargs="?", default=DEFAULT_NEW_DB, type=Path)
    args = parser.parse_args()

    old_conn = open_readonly(args.old_db)
    new_conn = open_readonly(args.new_db)
    try:
        reports = [compare_table(old_conn, new_conn, table) for table in TABLES_TO_COMPARE]
    finally:
        old_conn.close()
        new_conn.close()

    print_report(reports, args.old_db, args.new_db)


if __name__ == "__main__":
    main()
