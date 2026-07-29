"""One-off migration: add created_at/updated_at to signals, installations,
sources, and incidents (see docs/utredning_senast_uppdaterat.md).

Base.metadata.create_all() only creates missing tables, not missing columns
on tables that already exist - this ALTER TABLE is the one-off for that,
same pattern as scripts/annotate_signal.py's ensure_manual_year_estimate_column
and scripts/graduate_signal_to_installation.py's installation_id column.
Idempotent: safe to re-run, only adds a column that isn't there yet.

Existing rows are left NULL on both columns - SQLite's ALTER TABLE ADD
COLUMN with no DEFAULT clause does this automatically, and that's the
point: ~305 existing rows were created over the preceding week, not at
migration time, and stamping them with "now" would be a visible falsehood
in the data. New rows and future edits get a real value going forward via
each model's Python-side default/onupdate (app/models/signal.py,
installation.py, source.py, incident.py).

Back up the database first - see data/backups/ for the naming convention
already used before schema changes in this project
(runway_safe-pre-<change>-<timestamp>.db).

Usage:
    python -m scripts.add_created_updated_timestamps
"""
from __future__ import annotations

from sqlalchemy import inspect, text

from app.database import engine

_TABLES = ("signals", "installations", "sources", "incidents")
_COLUMNS = ("created_at", "updated_at")


def add_timestamp_columns(bind=engine) -> dict[str, list[str]]:
    added: dict[str, list[str]] = {}
    for table in _TABLES:
        existing = {c["name"] for c in inspect(bind).get_columns(table)}
        missing = [column for column in _COLUMNS if column not in existing]
        if not missing:
            continue
        with bind.begin() as connection:
            for column in missing:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} DATETIME"))
        added[table] = missing
    return added


def main() -> None:
    added = add_timestamp_columns()
    if not added:
        print("Nothing to do - all four tables already have created_at/updated_at.")
        return
    for table, columns in added.items():
        print(f"{table}: added {', '.join(columns)}")
    print("Existing rows left NULL on purpose - see docs/utredning_senast_uppdaterat.md.")


if __name__ == "__main__":
    main()
