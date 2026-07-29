"""One-time correction of Chicago Midway's (MDW) runway designation: the
airport's old 13L/31R was permanently closed 2025-06-12, and the existing
13C/31C - the one Runway row this database already tracks for MDW - was
renamed to 13L/31R at the same time, per the City of Chicago's official
press release (June 2025, chicago.gov - no specific document URL available,
only the domain).

Same pattern as scripts/rename_sandiford_to_standiford.py: renames the
structured field (Runway.designation here, Airport.name there) and records
the old name plus the reason/date/source as a note on the row, rather than
silently overwriting history. Signal 12 (MDW's "Future EMAS lifecycle
watch") is linked to this Runway via runway_id and needs no separate edit -
its runway_designation is derived from Runway.designation, so it picks up
"13L/31R" automatically once this script runs.

Safe to re-run: guarded by checking the current designation first - a
no-op once applied.
"""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Airport, Runway

OLD_DESIGNATION = "13C/31C"
NEW_DESIGNATION = "13L/31R"

RENAME_NOTE = (
    "[2025-06-12] Namnbyte: flygplatsens tidigare 13L/31R stängdes permanent "
    "2025-06-12. Samma dag döptes denna bana - fram tills dess betecknad "
    "13C/31C - om till 13L/31R, enligt City of Chicagos officiella "
    "pressmeddelande (juni 2025, chicago.gov - exakt dokument-URL ej "
    "tillgänglig, endast domänen angiven). Historiskt namn i denna databas "
    "fram till rättelsen: 13C/31C."
)


def rename_mdw_runway(session: Session) -> dict:
    stats = {"runway_renamed": False}

    mdw = session.scalar(
        select(Airport).where(or_(Airport.iata_code == "MDW", Airport.icao_code == "MDW", Airport.faa_code == "MDW"))
    )
    if mdw is None:
        raise SystemExit("No airport with code=MDW.")

    runway = session.scalar(
        select(Runway).where(Runway.airport_id == mdw.id, Runway.designation == OLD_DESIGNATION)
    )
    if runway is None:
        return stats  # already renamed (or never existed under the old name) - no-op

    runway.designation = NEW_DESIGNATION
    if RENAME_NOTE not in (runway.notes or ""):
        runway.notes = f"{runway.notes}\n{RENAME_NOTE}" if runway.notes else RENAME_NOTE
    stats["runway_renamed"] = True

    session.commit()
    return stats


def main() -> None:
    with SessionLocal() as session:
        stats = rename_mdw_runway(session)
    print(stats)


if __name__ == "__main__":
    main()
