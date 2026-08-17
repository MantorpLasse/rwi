"""Narrow, dry-run-first canonical runway/runway-end inventory pilot - MDW and CGF only.

Populates canonical Runway/RunwayEnd rows from the already-preserved,
SHA-256-verified FAA NASR 2026-08-06 archive (APT_RWY.csv/APT_RWY_END.csv -
the runway INVENTORY files, never APT_ARS.csv, which is EMAS presence
evidence and stays conceptually separate - see
docs/domain/canonical-runway-runway-end-design.md S4).

Writes ONLY to runways/runway_ends, and only for MDW/CGF. Never sets
PhysicalInstallationIdentity.runway_end_id - identity_mapping_proposals()
below is report-only, exactly like
scripts/apply_mdw_current_presence_pilot.py's dry-run-then-separately-
approved-apply discipline. No network access anywhere in this module.
"""
from __future__ import annotations

import argparse
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.evidence.nasr_apt_rwy import runway_end_rows, runway_rows
from app.models import Airport, PhysicalInstallationIdentity
from app.services.runway_inventory import apply_plan, evaluate_identity_links_from_raw, plan_airport_inventory

DEFAULT_ZIP = Path("data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip")
DEFAULT_DATABASE = Path("data/runway_safe.db")
BACKUP_DIRECTORY = Path("data/backups")
TARGET_CODES = ("MDW", "CGF")


def _backup_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"runway_safe-pre-canonical-runway-runway-end-pilot-apply-{timestamp}.db"


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


def _resolve_airport(session: Session, code: str) -> Airport | None:
    return session.scalar(
        select(Airport).where(
            (Airport.faa_code == code) | (Airport.iata_code == code) | (Airport.icao_code == code)
        )
    )


def _source_rows_for_airport(airport: Airport, zip_path: Path, metadata_path: Path):
    candidate_codes = {c for c in (airport.faa_code, airport.iata_code, airport.icao_code) if c}
    rwy = [r for r in runway_rows(zip_path, metadata_path) if r.values["ARPT_ID"] in candidate_codes]
    ends = [r for r in runway_end_rows(zip_path, metadata_path) if r.values["ARPT_ID"] in candidate_codes]
    return rwy, ends


def _ends_by_designation(plans) -> dict[str, list]:
    lookup: dict[str, list] = {}
    for plan in plans:
        for end_plan in plan.ends:
            lookup.setdefault(end_plan.designation, []).append((plan, end_plan))
    return lookup


def _airport_report(session: Session, airport: Airport, zip_path: Path, metadata_path: Path) -> dict[str, Any]:
    from app.services.runway_identity import AmbiguousRunwayDesignationError, normalize_end

    rwy_rows, end_rows = _source_rows_for_airport(airport, zip_path, metadata_path)
    plans = plan_airport_inventory(session, airport, rwy_rows, end_rows)
    ends_by_designation = _ends_by_designation(plans)

    identity_rows = session.execute(
        select(
            PhysicalInstallationIdentity.id,
            PhysicalInstallationIdentity.airport_id,
            PhysicalInstallationIdentity.runway_id,
            PhysicalInstallationIdentity.runway_end,
        ).where(PhysicalInstallationIdentity.airport_id == airport.id)
    ).all()
    identity_rows = [tuple(row) for row in identity_rows]

    proposals = evaluate_identity_links_from_raw(identity_rows, plans)
    proposed_ids = {p.identity_id for p in proposals}

    unresolved, ambiguous = [], []
    for identity_id, _airport_id, _runway_id, runway_end in identity_rows:
        if identity_id in proposed_ids or not runway_end:
            continue
        try:
            normalized = normalize_end(runway_end)
        except AmbiguousRunwayDesignationError:
            ambiguous.append(identity_id)
            continue
        matches = ends_by_designation.get(normalized, [])
        (ambiguous if len(matches) > 1 else unresolved).append(identity_id)

    target_counts: dict[tuple[str, str], int] = {}
    for p in proposals:
        key = (p.matched_runway_designation, p.matched_runway_end_designation)
        target_counts[key] = target_counts.get(key, 0) + 1
    duplicate_targets = {k: v for k, v in target_counts.items() if v > 1}

    return {
        "airport_id": airport.id,
        "airport_name": airport.name,
        "source_runway_row_count": len(rwy_rows),
        "source_runway_end_row_count": len(end_rows),
        "runways_would_create": sum(1 for p in plans if p.existing_id is None),
        "runways_would_enrich": sum(1 for p in plans if p.existing_id is not None and p.would_enrich),
        "runways_unchanged": sum(1 for p in plans if p.existing_id is not None and not p.would_enrich),
        "runway_ends_would_create": sum(1 for p in plans for e in p.ends if e.existing_id is None),
        "runway_ends_existing_matches": sum(1 for p in plans for e in p.ends if e.existing_id is not None),
        "unresolved_identity_ids": unresolved,
        "ambiguous_identity_ids": ambiguous,
        "duplicate_identity_targets": {f"{k[0]} end {k[1]}": v for k, v in duplicate_targets.items()},
        "runway_rows_to_create": [
            {
                "designation": p.normalized_designation,
                "raw_designation": p.raw_designation,
                "length_m": p.length_m,
                "width_m": p.width_m,
                "surface": p.surface,
            }
            for p in plans
            if p.existing_id is None
        ],
        "runway_rows_to_enrich": [
            {
                "existing_runway_id": p.existing_id,
                "designation": p.normalized_designation,
                "length_m": p.length_m,
                "width_m": p.width_m,
                "surface": p.surface,
            }
            for p in plans
            if p.existing_id is not None and p.would_enrich
        ],
        "runway_rows_unchanged": [
            {"existing_runway_id": p.existing_id, "designation": p.normalized_designation}
            for p in plans
            if p.existing_id is not None and not p.would_enrich
        ],
        "runway_end_rows_to_create": [
            {"runway_designation": p.normalized_designation, "designation": e.designation}
            for p in plans
            for e in p.ends
            if e.existing_id is None
        ],
        "identity_mapping_proposals": [
            {
                "identity_id": pr.identity_id,
                "airport": airport.name,
                "reviewed_end": pr.current_runway_end,
                "canonical_runway": pr.matched_runway_designation,
                "canonical_runway_end": pr.matched_runway_end_designation,
                "note": "REPORT ONLY - runway_end_id is not set by this script",
            }
            for pr in proposals
        ],
    }


def dry_run(session: Session, *, zip_path: Path = DEFAULT_ZIP) -> dict[str, Any]:
    metadata_path = Path(str(zip_path) + ".metadata.json")
    report: dict[str, Any] = {
        "source_artifact": str(zip_path),
        "target_codes": list(TARGET_CODES),
        "airports": {},
        "blockers": [],
    }
    for code in TARGET_CODES:
        airport = _resolve_airport(session, code)
        if airport is None:
            report["blockers"].append(f"{code}: airport not found in database")
            continue
        report["airports"][code] = _airport_report(session, airport, zip_path, metadata_path)
    return report


def run(session: Session, *, apply: bool = False, zip_path: Path = DEFAULT_ZIP) -> dict[str, Any]:
    report = dry_run(session, zip_path=zip_path)
    if not apply or report["blockers"]:
        return report
    metadata_path = Path(str(zip_path) + ".metadata.json")
    for code in TARGET_CODES:
        airport = _resolve_airport(session, code)
        rwy_rows, end_rows = _source_rows_for_airport(airport, zip_path, metadata_path)
        plans = plan_airport_inventory(session, airport, rwy_rows, end_rows)
        apply_plan(session, airport, plans)
    session.commit()
    return dry_run(session, zip_path=zip_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    with SessionLocal() as session:
        import json

        print(json.dumps(run(session, apply=args.apply), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
