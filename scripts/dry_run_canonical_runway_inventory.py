"""Read-only canonical runway/runway-end inventory dry run for one airport.

Resolves an airport against the real development database (read-only:
current rows are inspected via a mode=ro sqlite3 connection, matching
scripts/migrate_canonical_runway_runway_end_slice1.py's inspect(); the ORM
planner below only ever SELECTs - session.add()/commit() are never called),
parses the preserved NASR APT_RWY.csv/APT_RWY_END.csv for that airport, and
reports what a canonical-inventory upsert would do. It never writes
anything, to the database or otherwise.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.evidence.nasr_apt_rwy import runway_end_rows, runway_rows
from app.models import Airport
from app.services.runway_inventory import evaluate_identity_links_from_raw, plan_airport_inventory

DEFAULT_ZIP = Path("data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip")
DEFAULT_DATABASE = Path("data/runway_safe.db")


def _current_db_rows_readonly(database: Path, airport_id: int) -> dict:
    """Raw, read-only (mode=ro) inspection of current rows - no ORM session."""
    connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    try:
        runways = connection.execute(
            "SELECT id, designation, length_m, width_m, surface FROM runways WHERE airport_id=?", (airport_id,)
        ).fetchall()
        runway_ids = [row[0] for row in runways]
        runway_ends_table_exists = bool(
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runway_ends'"
            ).fetchone()
        )
        ends = []
        if runway_ids and runway_ends_table_exists:
            placeholders = ",".join("?" * len(runway_ids))
            ends = connection.execute(
                f"SELECT id, runway_id, designation FROM runway_ends WHERE runway_id IN ({placeholders})",
                runway_ids,
            ).fetchall()
        identities = connection.execute(
            "SELECT id, airport_id, runway_id, runway_end FROM physical_installation_identities WHERE airport_id=?",
            (airport_id,),
        ).fetchall()
        return {
            "runways": runways,
            "runway_ends": ends,
            "identities": identities,
            "runway_ends_table_exists": runway_ends_table_exists,
        }
    finally:
        connection.close()


def dry_run(*, airport_code: str, database: Path = DEFAULT_DATABASE, zip_path: Path = DEFAULT_ZIP) -> dict:
    metadata_path = Path(str(zip_path) + ".metadata.json")

    with SessionLocal() as session:  # ORM session: only ever SELECTs below, never add()/commit()
        airport = session.scalar(
            select(Airport).where(
                (Airport.faa_code == airport_code)
                | (Airport.iata_code == airport_code)
                | (Airport.icao_code == airport_code)
            )
        )
        if airport is None:
            return {"airport_code": airport_code, "blockers": ["airport not found in development database"]}

        current = _current_db_rows_readonly(database, airport.id)

        candidate_codes = {c for c in (airport.faa_code, airport.iata_code, airport.icao_code) if c}
        source_runways = [row for row in runway_rows(zip_path, metadata_path) if row.values["ARPT_ID"] in candidate_codes]
        source_ends = [row for row in runway_end_rows(zip_path, metadata_path) if row.values["ARPT_ID"] in candidate_codes]

        plans = plan_airport_inventory(session, airport, source_runways, source_ends)
        # PhysicalInstallationIdentity.runway_end_id doesn't exist as a column
        # on the real, not-yet-migrated database - identities were already
        # read as plain tuples above (current["identities"]), so match
        # against those directly rather than through the ORM model.
        proposals = evaluate_identity_links_from_raw(current["identities"], plans)

        return {
            "airport_code": airport_code,
            "airport_id": airport.id,
            "airport_name": airport.name,
            "current_db_runway_ends_table_exists": current["runway_ends_table_exists"],
            "current_db_runways": [
                {"id": r[0], "designation": r[1], "length_m": r[2], "width_m": r[3], "surface": r[4]}
                for r in current["runways"]
            ],
            "current_db_runway_ends": [
                {"id": r[0], "runway_id": r[1], "designation": r[2]} for r in current["runway_ends"]
            ],
            "current_db_identities": [
                {"id": r[0], "airport_id": r[1], "runway_id": r[2], "runway_end": r[3]} for r in current["identities"]
            ],
            "source_runway_row_count": len(source_runways),
            "source_runway_end_row_count": len(source_ends),
            "runway_plans": [
                {
                    "normalized_designation": p.normalized_designation,
                    "raw_designation": p.raw_designation,
                    "length_m": p.length_m,
                    "width_m": p.width_m,
                    "surface": p.surface,
                    "action": (
                        "enrich" if p.existing_id is not None and p.would_enrich
                        else "unchanged" if p.existing_id is not None
                        else "create"
                    ),
                    "existing_runway_id": p.existing_id,
                    "ends": [
                        {
                            "designation": e.designation,
                            "action": "unchanged" if e.existing_id is not None else "create",
                            "existing_runway_end_id": e.existing_id,
                        }
                        for e in p.ends
                    ],
                }
                for p in plans
            ],
            "runways_would_create": sum(1 for p in plans if p.existing_id is None),
            "runways_would_enrich": sum(1 for p in plans if p.existing_id is not None and p.would_enrich),
            "runways_unchanged": sum(1 for p in plans if p.existing_id is not None and not p.would_enrich),
            "runway_ends_would_create": sum(1 for p in plans for e in p.ends if e.existing_id is None),
            "runway_ends_unchanged": sum(1 for p in plans for e in p.ends if e.existing_id is not None),
            "proposed_identity_links": [
                {
                    "identity_id": pr.identity_id,
                    "current_runway_end": pr.current_runway_end,
                    "matched_runway_designation": pr.matched_runway_designation,
                    "matched_runway_end_designation": pr.matched_runway_end_designation,
                    "target_runway_id": pr.target_runway_id,
                    "target_runway_end_id": pr.target_runway_end_id,
                }
                for pr in proposals
            ],
            "unresolved_identities": [
                {"id": r[0], "runway_end": r[3]}
                for r in current["identities"]
                if r[0] not in {pr.identity_id for pr in proposals}
            ],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--airport", required=True, help="FAA/IATA/ICAO code, e.g. MDW or CGF")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--zip", type=Path, default=DEFAULT_ZIP, dest="zip_path")
    args = parser.parse_args(argv)
    result = dry_run(airport_code=args.airport, database=args.database, zip_path=args.zip_path)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
