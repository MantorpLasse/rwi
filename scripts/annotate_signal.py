"""Add a personal annotation to an existing Signal without touching official data.

Appends to Signal.notes (with a date stamp, preserving whatever was there
before) and optionally sets Signal.manual_year_estimate - a personal, unverified
year guess kept separate from the officially sourced target_year/
planning_year/procurement_year fields.

Usage:
    python -m scripts.annotate_signal --id 42 --note "text"
    python -m scripts.annotate_signal --id 42 --note "text" --estimated-year 2027
"""
from __future__ import annotations

import argparse
from datetime import date

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine
from app.models import Signal


def ensure_manual_year_estimate_column(bind=engine) -> None:
    """Add signals.manual_year_estimate if an older database doesn't have it yet.

    Base.metadata.create_all() only creates missing tables, not missing
    columns on ones that already exist - idempotent, safe to call every run.
    """
    existing = {c["name"] for c in inspect(bind).get_columns("signals")}
    if "manual_year_estimate" not in existing:
        with bind.begin() as connection:
            connection.execute(text("ALTER TABLE signals ADD COLUMN manual_year_estimate INTEGER"))


def append_note(existing_notes: str | None, note: str, *, on: date) -> str:
    entry = f"[{on.isoformat()}] {note}"
    return f"{existing_notes}\n{entry}" if existing_notes else entry


def annotate(
    session: Session,
    signal_id: int,
    *,
    note: str | None,
    estimated_year: int | None,
    today: date | None = None,
) -> Signal:
    signal = session.get(Signal, signal_id)
    if signal is None:
        raise SystemExit(f"No signal with id={signal_id}")

    if note:
        signal.notes = append_note(signal.notes, note, on=today or date.today())
    if estimated_year is not None:
        signal.manual_year_estimate = estimated_year

    session.commit()
    session.refresh(signal)
    return signal


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", type=int, required=True, dest="signal_id")
    parser.add_argument("--note", type=str, default=None)
    parser.add_argument("--estimated-year", type=int, default=None, dest="estimated_year")
    args = parser.parse_args(argv)
    if args.note is None and args.estimated_year is None:
        parser.error("provide --note and/or --estimated-year")
    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    ensure_manual_year_estimate_column(engine)

    with SessionLocal() as session:
        signal = annotate(
            session,
            args.signal_id,
            note=args.note,
            estimated_year=args.estimated_year,
        )

    print(f"Signal {signal.id} ({signal.title!r}) updated.")
    if args.note:
        print("notes:")
        print(signal.notes)
    if args.estimated_year is not None:
        print(f"manual_year_estimate: {signal.manual_year_estimate}")


if __name__ == "__main__":
    main()
