"""One-time rename of existing usaspending_grant Signal titles to the new,
per-grant-unique format (see scripts/import_usaspending_grants.py's
signal_title()).

Every USAspending-sourced Signal used to be titled
"USAspending grant: {recipient} EMAS" - identical across every grant at the
same airport, since the recipient (the airport owner) doesn't change
between grants. In the signals list this reads as duplicate rows even
though each is a distinct award. The fix in import_usaspending_grants.py
only affects future imports; this backfills the ~25 Signal rows already in
the database using fields already stored on each Signal
(estimated_total_value_usd, planning_year) - no need to re-fetch from
USAspending.gov.

Safe to re-run: skips any Signal whose title already starts with
"USAspending grant — $" (the new format).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Signal, Source
from scripts.import_usaspending_grants import _format_amount

_NEW_TITLE_PREFIX = "USAspending grant — $"


def _new_title(signal: Signal) -> str:
    amount_text = _format_amount(signal.estimated_total_value_usd)
    if signal.planning_year:
        return f"USAspending grant — {amount_text}, FY{signal.planning_year}"
    return f"USAspending grant — {amount_text}"


def rename_titles(session: Session) -> dict:
    stats = {"renamed": 0, "already_renamed": 0}
    signals = session.scalars(
        select(Signal).join(Source, Signal.source_id == Source.id).where(Source.source_type == "usaspending_grant")
    ).all()

    for signal in signals:
        if signal.title.startswith(_NEW_TITLE_PREFIX):
            stats["already_renamed"] += 1
            continue
        signal.title = _new_title(signal)
        stats["renamed"] += 1

    session.commit()
    return stats


def main() -> None:
    with SessionLocal() as session:
        stats = rename_titles(session)
    print(stats)


if __name__ == "__main__":
    main()
