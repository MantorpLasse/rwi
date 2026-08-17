"""Deterministic, narrowly-scoped apply of ONLY the U.S. canonical runway
inventory clean batch (docs/domain/canonical-runway-us-clean-batch-report.md,
docs/domain/canonical-runway-us-wide-dry-run-report.md).

Writes ONLY to runways/runway_ends, and only for airports that
app.services.runway_inventory.resolve_us_clean_batch() classifies as
ALREADY_COMPLETE/CLEAN_ENRICH/CLEAN_CREATE against the preserved FAA NASR
2026-08-06 archive (APT_RWY.csv/APT_RWY_END.csv - never APT_ARS.csv, which
stays separate EMAS-presence evidence). The clean-airport set is always
re-derived from the current database and the preserved NASR artifact -
never a hardcoded airport list. The 12 AMBIGUOUS airports (helipad/
special-use NASR rows mixed into APT_RWY.csv) and the 1 airport with no
FAA/IATA/ICAO identifier are excluded, exactly as classified - no new
filtering rule for those rows is introduced here.

Reuses plan_airport_inventory()/apply_plan() unmodified - this module adds
no second reconciliation path. Never touches
PhysicalInstallationIdentity or any table other than runways/runway_ends
(apply_plan()'s own guarantee, docs/domain/canonical-runway-runway-end-design.md).

Default is dry run. --apply requires --allow-database-write, aborts before
ever writing if the freshly-resolved plan doesn't match the already
-approved snapshot below, re-resolves the clean batch a second time
immediately before writing and aborts the ENTIRE batch (nothing is added
to the session before that point, so no partial write is possible) if
clean-set membership or the aggregate plan has drifted at all between the
two resolutions.
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
from app.evidence.nasr_apt_rwy import runway_end_rows, runway_rows
from app.models import Airport
from app.services.runway_inventory import (
    CLEAN_BATCH_CLASSIFICATIONS,
    apply_plan,
    clean_batch_aggregate,
    resolve_us_clean_batch,
)

DEFAULT_ZIP = Path("data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip")
DEFAULT_DATABASE = Path("data/runway_safe.db")
BACKUP_DIRECTORY = Path("data/backups")

# The approved snapshot from docs/domain/canonical-runway-us-wide-dry-run-report.md
# and docs/domain/canonical-runway-us-clean-batch-report.md. A real --apply
# aborts entirely if a fresh resolve doesn't match this exactly - protects
# against the NASR artifact, or the database, having changed underneath
# this script since the plan was reviewed and approved.
EXPECTED_CLEAN_AIRPORT_COUNT = 63
EXPECTED_RUNWAY_CREATES = 94
EXPECTED_RUNWAY_ENRICH = 39
EXPECTED_RUNWAY_END_CREATES = 284


def _backup_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"runway_safe-pre-canonical-runway-us-clean-batch-apply-{timestamp}.db"


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


def _resolve(session: Session, zip_path: Path):
    metadata_path = Path(str(zip_path) + ".metadata.json")
    all_rwy = list(runway_rows(zip_path, metadata_path))
    all_end = list(runway_end_rows(zip_path, metadata_path))
    classifications = resolve_us_clean_batch(session, all_rwy, all_end)
    return classifications, clean_batch_aggregate(classifications)


def dry_run(session: Session, *, zip_path: Path = DEFAULT_ZIP) -> dict[str, Any]:
    classifications, aggregate = _resolve(session, zip_path)
    clean = [c for c in classifications if c.classification in CLEAN_BATCH_CLASSIFICATIONS]
    excluded = [c for c in classifications if c.classification not in CLEAN_BATCH_CLASSIFICATIONS]
    return {
        "aggregate": aggregate,
        "clean_airport_ids": sorted(c.airport_id for c in clean),
        "excluded": [
            {"airport_id": c.airport_id, "classification": c.classification, "error": c.error} for c in excluded
        ],
    }


def run(
    session: Session,
    *,
    apply: bool = False,
    zip_path: Path = DEFAULT_ZIP,
    expected_clean_airport_count: int = EXPECTED_CLEAN_AIRPORT_COUNT,
    expected_runway_creates: int = EXPECTED_RUNWAY_CREATES,
    expected_runway_enrich: int = EXPECTED_RUNWAY_ENRICH,
    expected_runway_end_creates: int = EXPECTED_RUNWAY_END_CREATES,
) -> dict[str, Any]:
    """The expected_* parameters are the already-approved plan snapshot a
    real apply must match before it's allowed to write anything - they
    default to the values approved in
    docs/domain/canonical-runway-us-clean-batch-report.md. Tests pass their
    own fixture-shaped snapshot to exercise the same abort-on-drift
    behavior without being tied to the real database's numbers."""
    report = dry_run(session, zip_path=zip_path)
    if not apply:
        return report

    agg = report["aggregate"]
    expected = (
        expected_clean_airport_count,
        expected_runway_creates,
        expected_runway_enrich,
        expected_runway_end_creates,
    )
    actual = (
        agg["clean_airport_count"],
        agg["runways_would_create"],
        agg["runways_would_enrich"],
        agg["runway_ends_would_create"],
    )
    if actual != expected:
        raise ValueError(
            "Aborting: clean-batch plan does not match the approved snapshot. "
            f"expected(clean_count, creates, enrich, end_creates)={expected}, actual={actual}"
        )

    # Re-resolve immediately before writing. Nothing has been added to the
    # session yet, so if either check below fails, the whole batch aborts
    # with zero writes - no partial apply is possible.
    classifications, fresh_aggregate = _resolve(session, zip_path)
    fresh_clean = [c for c in classifications if c.classification in CLEAN_BATCH_CLASSIFICATIONS]
    fresh_clean_ids = sorted(c.airport_id for c in fresh_clean)
    if fresh_clean_ids != report["clean_airport_ids"]:
        raise ValueError("Aborting: clean-set membership changed between resolution and pre-write re-check")
    if fresh_aggregate != agg:
        raise ValueError("Aborting: aggregate plan changed between resolution and pre-write re-check")

    airports_by_id = {a.id: a for a in session.scalars(select(Airport).where(Airport.id.in_(fresh_clean_ids))).all()}
    for classification in fresh_clean:
        airport = airports_by_id[classification.airport_id]
        apply_plan(session, airport, list(classification.plans))
    session.commit()

    return dry_run(session, zip_path=zip_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the clean batch (default is dry run only)")
    parser.add_argument("--allow-database-write", action="store_true", help="required together with --apply")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--zip", type=Path, default=DEFAULT_ZIP, dest="zip_path")
    args = parser.parse_args(argv)

    if args.apply and not args.allow_database_write:
        parser.error("--apply requires --allow-database-write")

    if args.apply:
        backup_path = backup_database(args.database)
        print("Backup created:", backup_path)

    with SessionLocal() as session:
        result = run(session, apply=args.apply, zip_path=args.zip_path)

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
