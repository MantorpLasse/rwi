"""One-off backfill for the design pass in DESIGN_BRIEF.md's "Auto-genererade
titlar" section.

app/models/incident.py's after_insert listener used to title every
incident-triggered Signal exactly "Replacement expected after incident on
{date}" - identical wording across dozens of unrelated airports. It now
includes the airport name (and runway, when known) instead. That change only
affects signals created from here on; this script rewrites the ones already
sitting in the database with the old wording, using the same airport_id/
runway_id the signal was created with (the date is parsed back out of the old
title text, since Signal has no incident_id to join back through).

Safe to re-run: only titles matching the exact old pattern are touched, so a
second run finds nothing left to update.
"""

from __future__ import annotations

import argparse
import re
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Airport, Runway, Signal

_OLD_TITLE = re.compile(r"^Replacement expected after incident on (\d{4}-\d{2}-\d{2})$")


def new_title(airport_name: str, runway_designation: str | None, incident_date: str) -> str:
    if runway_designation:
        return (
            f"{airport_name} — Runway {runway_designation} EMAS-ersättning väntas "
            f"efter incident ({incident_date})"
        )
    return f"{airport_name} — EMAS-ersättning väntas efter incident ({incident_date})"


def backfill(session: Session) -> dict:
    stats = {"updated": 0}
    signals = session.scalars(
        select(Signal).where(Signal.title.like("Replacement expected after incident on%"))
    ).all()
    for signal in signals:
        match = _OLD_TITLE.match(signal.title)
        if not match:
            continue
        airport = session.get(Airport, signal.airport_id)
        runway = session.get(Runway, signal.runway_id) if signal.runway_id else None
        signal.title = new_title(airport.name, runway.designation if runway else None, match.group(1))
        stats["updated"] += 1
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-database-write", action="store_true")
    args = parser.parse_args(argv)

    if not args.allow_database_write:
        print("Refusing: --allow-database-write is required.", file=sys.stderr)
        return 2

    with SessionLocal() as session:
        stats = backfill(session)
        session.commit()

    print(f"Signal titles rewritten: {stats['updated']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
