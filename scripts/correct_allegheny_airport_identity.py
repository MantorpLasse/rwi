"""Deterministic identity correction for RWI Airport id 75.

Currently named "Allegheny County Airport Authority" - the USAspending
grant *recipient* name, used as a placeholder by
scripts/import_usaspending_grants.py::resolve_airport() when no FAA Loc
ID was embedded in the grant description and no existing airport matched
the beneficiary city/state (see that row's own `notes` field). Evidence-
backed investigation
(docs/domain/allegheny-unresolved-airport-investigation.md) identified
this row, with no remaining ambiguity, as Allegheny County Airport - FAA
LID AGC, ICAO KAGC - corroborated by the grant's own text (which names
"RUNWAY 10/28"), a prior independent RWI research document, and FAA
NASR's own APT_BASE.csv/APT_RWY.csv.

This is a ONE-OFF, narrowly-scoped correction for exactly this one row -
not a generic "resolve an airport from its operator/authority name"
mechanism, and it does not implement or weaken any canonical-runway
matching/classification logic. It writes ONLY to the single `airports`
row for id 75, exactly three fields (name, faa_code, icao_code). It never
touches runways, runway_ends, or any other table - canonical-runway
ingestion for the newly-identified AGC airport (2 Runway creates, 4
RunwayEnd creates - see the dry-run report) is a separate, later,
explicitly approved step, reusing the existing, unmodified planner/apply
scripts once this identity correction is applied and committed.

Default is dry run. --apply requires --allow-database-write. Fails
closed if the airport's current values don't exactly match the expected
pre-correction snapshot, if airport id 75 doesn't exist, or if the
proposed faa_code/icao_code collide with any other Airport row.
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

TARGET_AIRPORT_ID = 75

# The exact pre-correction snapshot this script requires before it will
# propose or apply anything - if the real row has drifted from this at
# all, the row may already have been corrected or investigated further,
# and this script must not guess.
EXPECTED_CURRENT = {
    "name": "Allegheny County Airport Authority",
    "faa_code": None,
    "iata_code": None,
    "icao_code": None,
    "city": "West Mifflin",
    "state_region": "Pennsylvania",
    "country": "USA",
}

# Only these three fields change. iata_code/city/state_region are
# deliberately absent here - they stay exactly as EXPECTED_CURRENT says.
PROPOSED_NEW = {
    "name": "Allegheny County Airport",
    "faa_code": "AGC",
    "icao_code": "KAGC",
}


class AlleghenyCorrectionError(ValueError):
    """Raised when a precondition or collision check fails - this
    correction is never applied against unexpected or ambiguous state."""


def _backup_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"runway_safe-pre-allegheny-airport-identity-correction-{timestamp}.db"


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
        raise AlleghenyCorrectionError(f"Airport id {TARGET_AIRPORT_ID} does not exist - refusing to proceed.")

    for field, expected in EXPECTED_CURRENT.items():
        actual = getattr(airport, field)
        if actual != expected:
            raise AlleghenyCorrectionError(
                f"Precondition failed: Airport {TARGET_AIRPORT_ID}.{field} is {actual!r}, "
                f"expected {expected!r}. The row may have already changed - refusing to proceed."
            )

    for field in ("faa_code", "icao_code"):
        value = PROPOSED_NEW[field]
        column = getattr(Airport, field)
        collision = session.scalar(select(Airport).where(column == value, Airport.id != TARGET_AIRPORT_ID))
        if collision is not None:
            raise AlleghenyCorrectionError(
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
