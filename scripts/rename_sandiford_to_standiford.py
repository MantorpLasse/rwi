"""One-time correction of the "Sandiford" (Louisville, KY) airport name to
"Standiford" - flagged as a likely misspelling in
docs/utredning_faa_factsheet_resten.md and left uncorrected there ("out of
scope for a data-only import"). Both FAA Fact Sheets (2011, 2016) print
"Sandiford" too, so this isn't a transcription error made in this codebase -
it's the underlying source's own apparent typo for Standiford Field, the
historic name of Louisville Muhammad Ali International (IATA/ICAO/FAA SDF).
Not renamed to the airport's current official name (Louisville Muhammad Ali
International) - just the specific misspelling this database inherited.

Renames Airport.name and rewrites the identity-anomaly sentence in the one
Installation row (the 2015 Fact-Sheet-sourced one) that discussed the
now-fixed spelling, without touching the verbatim source quote earlier in
that same note ("Sandiford, Louisville, KY, 1, fall 2015" is what the FAA
document itself says, and stays quoted as printed).

Safe to re-run: both updates are guarded by checking the current value
first - a no-op once applied.
"""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Airport, Installation

OLD_NAME = "Sandiford"
NEW_NAME = "Standiford"

OLD_NOTE_FRAGMENT = (
    "IDENTITETSANMARKNING: bade fact sheet och var egen databas stavar namnet 'Sandiford', "
    "men detta ar med mycket stor sannolikhet en felstavning av 'Standiford' (Standiford Field, "
    "det historiska namnet pa Louisville Muhammad Ali International, IATA/ICAO/FAA SDF) - "
    "oberoende sokning bekraftar ett $18,8M Runway 11-29 Safety Area Improvement-projekt med "
    "EMAS, fardigstallt 'by late 2015', vilket matchar 'fall 2015' nastan exakt. Flygplatsnamnet "
    "ar INTE andrat i denna korning (utanfor omfanget for en dataimport) - flaggat for framtida "
    "rattelse."
)
NEW_NOTE_FRAGMENT = (
    "IDENTITETSANMARKNING (RATTAD): FAA:s kalldokument stavar fortfarande namnet 'Sandiford', "
    "men det ar med mycket stor sannolikhet en felstavning av 'Standiford' (Standiford Field, "
    "det historiska namnet pa Louisville Muhammad Ali International, IATA/ICAO/FAA SDF) - "
    "oberoende sokning bekraftar ett $18,8M Runway 11-29 Safety Area Improvement-projekt med "
    "EMAS, fardigstallt 'by late 2015', vilket matchar 'fall 2015' nastan exakt. Flygplatsnamnet "
    "i var databas ar rattat till 'Standiford' (scripts/rename_sandiford_to_standiford.py) - "
    "docs/utredning_faa_factsheet_resten.md flaggade detta ursprungligen."
)


def rename(session: Session) -> dict:
    stats = {"airport_renamed": False, "installation_note_updated": False}

    airport = session.scalar(
        select(Airport).where(or_(Airport.iata_code == "SDF", Airport.icao_code == "SDF", Airport.faa_code == "SDF"))
    )
    if airport is None:
        raise SystemExit("No airport with code=SDF.")

    if airport.name == OLD_NAME:
        airport.name = NEW_NAME
        stats["airport_renamed"] = True

    installations = session.scalars(select(Installation).where(Installation.airport_id == airport.id)).all()
    for installation in installations:
        if installation.notes and OLD_NOTE_FRAGMENT in installation.notes:
            installation.notes = installation.notes.replace(OLD_NOTE_FRAGMENT, NEW_NOTE_FRAGMENT)
            stats["installation_note_updated"] = True

    session.commit()
    return stats


def main() -> None:
    with SessionLocal() as session:
        stats = rename(session)
    print(stats)


if __name__ == "__main__":
    main()
