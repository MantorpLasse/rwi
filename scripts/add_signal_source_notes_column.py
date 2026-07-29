"""One-off migration: add signals.source_notes (see app/models/signal.py).

Splits Signal's previously-overloaded `notes` field in two: `notes` stays
private (personal, unverified annotations via scripts/annotate_signal.py,
excluded from the public static export), and this new `source_notes` column
is public - sourced research findings with a citation, the Signal
equivalent of Installation.notes ("Detaljer från källan").

Base.metadata.create_all() only creates missing tables, not missing columns
on tables that already exist - this ALTER TABLE is the one-off for that,
same pattern as scripts/add_created_updated_timestamps.py and
scripts/annotate_signal.py's ensure_manual_year_estimate_column. Idempotent:
safe to re-run, only adds the column if it isn't there yet. Existing rows
are left NULL - moving each row's actual content from notes to source_notes
is a separate, deliberately-reviewed step (see docs/ investigation of which
existing notes are sourced research vs personal annotation), not part of
this schema migration.

Back up the database first - see data/backups/ for the naming convention
already used before schema changes in this project
(runway_safe-pre-<change>-<timestamp>.db).

Usage:
    python -m scripts.add_signal_source_notes_column
"""
from __future__ import annotations

from sqlalchemy import inspect, text

from app.database import engine


def add_source_notes_column(bind=engine) -> bool:
    existing = {c["name"] for c in inspect(bind).get_columns("signals")}
    if "source_notes" in existing:
        return False
    with bind.begin() as connection:
        connection.execute(text("ALTER TABLE signals ADD COLUMN source_notes TEXT"))
    return True


def main() -> None:
    added = add_source_notes_column()
    if added:
        print("signals: added source_notes.")
    else:
        print("Nothing to do - signals.source_notes already exists.")


if __name__ == "__main__":
    main()
