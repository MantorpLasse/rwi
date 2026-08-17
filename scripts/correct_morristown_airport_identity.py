"""Deterministic identity correction for RWI Airport id 74.

Currently named "Town Of Morristown" - the USAspending grant *recipient*
name, used as a placeholder by
scripts/import_usaspending_grants.py::resolve_airport() (pre-fail-closed
fix) when no FAA Loc ID was embedded in the grant description and no
existing airport matched the beneficiary city/state (see that row's own
`notes` field). Evidence-backed investigation
(docs/domain/morristown-airport-74-investigation.md) identified this row,
with no remaining ambiguity, as Morristown Municipal Airport - FAA LID
MMU, IATA MMU, ICAO KMMU - already correctly set on the row (backfilled
independently, and earlier, by scripts/add_iija_fy2026_known_grants.py
and scripts/backfill_airport_codes.py, neither of which touched `name`).
Corroborated by the grant's own text (which names "RUNWAY 5/23"), the
already-correct identifiers, and FAA NASR's own APT_BASE.csv/APT_RWY.csv.

This is a ONE-OFF, narrowly-scoped correction for exactly this one row -
not a generic "rename an airport" mechanism, and it does not implement or
weaken any canonical-runway matching/classification logic. It writes ONLY
to the single `airports` row for id 74, exactly two fields (name, notes).
It never touches runways, runway_ends, or any other table - the row's
existing Runway/RunwayEnd inventory was already independently verified
correct against NASR and requires no change.

Default is dry run. --apply requires --allow-database-write. Fails
closed if the airport's current values don't exactly match the expected
pre-correction snapshot, if airport id 74 doesn't exist, or if the
existing faa_code/iata_code/icao_code are claimed by any other Airport
row (identifiers themselves are never written by this script - only
checked for collision as an added safety gate).
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Airport

DEFAULT_DATABASE = Path("data/runway_safe.db")
BACKUP_DIRECTORY = Path("data/backups")

TARGET_AIRPORT_ID = 74

# The exact pre-correction snapshot this script requires before it will
# propose or apply anything - if the real row has drifted from this at
# all, the row may already have been corrected or investigated further,
# and this script must not guess.
EXPECTED_CURRENT = {
    "name": "Town Of Morristown",
    "faa_code": "MMU",
    "iata_code": "MMU",
    "icao_code": "KMMU",
    "city": "Morristown",
    "state_region": "New Jersey",
    "country": "USA",
    "notes": (
        "Name approximated from the USAspending grant recipient; no FAA "
        "Loc ID was available in the award description. Verify/correct "
        "manually if you find the airport's real identifiers."
    ),
}

# Only these two fields change. faa_code/iata_code/icao_code/city/
# state_region are deliberately absent here - they stay exactly as
# EXPECTED_CURRENT says; they were already independently corrected by
# other scripts before this one.
PROPOSED_NEW = {
    "name": "Morristown Municipal Airport",
    "notes": (
        "Identity confirmed via FAA NASR (ARPT_ID=MMU, ICAO_ID=KMMU, "
        "ARPT_NAME=MORRISTOWN MUNI) and the FAA IIJA Announcement 6 "
        "FY2026 grant PDF."
    ),
}

# Checked for collision even though this script never writes them -
# an unexpected duplicate claimant would mean the identity picture has
# changed since the investigation, and this script must not proceed
# blindly.
_COLLISION_FIELDS = ("faa_code", "iata_code", "icao_code")


class MorristownCorrectionError(ValueError):
    """Raised when a precondition or collision check fails - this
    correction is never applied against unexpected or ambiguous state."""


def _backup_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"runway_safe-pre-morristown-airport-identity-correction-{timestamp}.db"


def backup_database(database: Path = DEFAULT_DATABASE, backup_directory: Path = BACKUP_DIRECTORY) -> Path:
    database = database.resolve()
    if not database.is_file():
        raise FileNotFoundError(f"Database not found: {database}")
    backup_directory.mkdir(parents=True, exist_ok=True)
    destination = backup_directory / _backup_name()
    shutil.copy2(database, destination)
    if destination.stat().st_size != database.stat().st_size:
        raise RuntimeError("Database backup size does not match the source database.")
    return destination


def _check_preconditions(session: Session) -> Airport:
    airport = session.get(Airport, TARGET_AIRPORT_ID)
    if airport is None:
        raise MorristownCorrectionError(f"Airport id {TARGET_AIRPORT_ID} does not exist - refusing to proceed.")

    for field, expected in EXPECTED_CURRENT.items():
        actual = getattr(airport, field)
        if actual != expected:
            raise MorristownCorrectionError(
                f"Precondition failed: Airport {TARGET_AIRPORT_ID}.{field} is {actual!r}, "
                f"expected {expected!r}. The row may have already changed - refusing to proceed."
            )

    for field in _COLLISION_FIELDS:
        value = EXPECTED_CURRENT[field]
        column = getattr(Airport, field)
        collision = session.scalar(select(Airport).where(column == value, Airport.id != TARGET_AIRPORT_ID))
        if collision is not None:
            raise MorristownCorrectionError(
                f"Collision: another Airport (id {collision.id}, name={collision.name!r}) "
                f"already has {field}={value!r} - refusing to proceed."
            )

    return airport


def dry_run(session: Session) -> dict[str, Any]:
    airport = _check_preconditions(session)
    return {
        "target_airport_id": TARGET_AIRPORT_ID,
        "preconditions_passed": True,
        "old_values": {field: getattr(airport, field) for field in EXPECTED_CURRENT},
        "proposed_new_values": PROPOSED_NEW,
        "unchanged_fields": {
            field: getattr(airport, field) for field in EXPECTED_CURRENT if field not in PROPOSED_NEW
        },
        "rows_that_would_change": 1,
    }


def apply(session: Session) -> dict[str, Any]:
    airport = _check_preconditions(session)
    old_values = {field: getattr(airport, field) for field in EXPECTED_CURRENT}
    for field, value in PROPOSED_NEW.items():
        setattr(airport, field, value)
    session.commit()
    return {
        "target_airport_id": TARGET_AIRPORT_ID,
        "old_values": old_values,
        "new_values": PROPOSED_NEW,
        "rows_changed": 1,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write the correction (default is dry run only)"
    )
    parser.add_argument("--allow-database-write", action="store_true", help="required together with --apply")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args(argv)

    if args.apply and not args.allow_database_write:
        parser.error("--apply requires --allow-database-write")

    if args.apply:
        backup_path = backup_database(args.database)
        print("Backup created:", backup_path)

    with SessionLocal() as session:
        result = apply(session) if args.apply else dry_run(session)

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
