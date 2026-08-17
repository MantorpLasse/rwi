"""Deterministic, narrowly-scoped apply of ONLY the U.S. airports that
became clean via the FAA/NASR special-record classification rule
(docs/domain/nasr-special-record-classification-investigation.md,
docs/domain/nasr-special-record-classification-implementation.md) -
i.e. classified CLEAN_CREATE/CLEAN_ENRICH but not already
ALREADY_COMPLETE. ALREADY_COMPLETE is the original 63-airport batch,
already applied separately by
scripts/apply_canonical_runway_inventory_us_clean_batch.py, which this
script never imports, calls, modifies, or broadens.

Writes ONLY to runways/runway_ends, and only for airports
resolve_us_clean_batch() classifies as CLEAN_CREATE/CLEAN_ENRICH with
real pending work - never a hardcoded airport list, never any
ALREADY_COMPLETE airport (the original 63), and never the one
no-identifier airport (Allegheny County Airport Authority, UNRESOLVED).

Default is dry run. --apply requires --allow-database-write, aborts
before ever writing if the freshly-resolved plan doesn't match the
already-approved snapshot below, re-resolves a second time immediately
before writing and aborts the ENTIRE batch (nothing is added to the
session before that point, so no partial write is possible) if
membership or the aggregate plan has drifted at all between the two
resolutions.
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
    CLEAN_CREATE,
    CLEAN_ENRICH,
    apply_plan,
    resolve_us_clean_batch,
)

DEFAULT_ZIP = Path("data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip")
DEFAULT_DATABASE = Path("data/runway_safe.db")
BACKUP_DIRECTORY = Path("data/backups")

# Deliberately excludes ALREADY_COMPLETE (the original 63-airport batch,
# already applied) - this is what makes this script's scope "the newly
# clean airports" without ever naming an airport ID.
NEWLY_CLEAN_CLASSIFICATIONS = frozenset({CLEAN_CREATE, CLEAN_ENRICH})

# The approved snapshot from docs/domain/nasr-special-record-classification-investigation.md
# and docs/domain/canonical-runway-us-newly-clean-12-batch-report.md. A
# real --apply aborts entirely if a fresh resolve doesn't match this
# exactly - protects against the NASR artifact, the database, or the
# classification rule having changed underneath this script since the
# plan was reviewed and approved.
EXPECTED_AIRPORT_COUNT = 12
EXPECTED_RUNWAY_CREATES = 22
EXPECTED_RUNWAY_ENRICH = 6
EXPECTED_RUNWAY_END_CREATES = 62


def _backup_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"runway_safe-pre-canonical-runway-us-newly-clean-12-batch-apply-{timestamp}.db"


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
    newly_clean = [c for c in classifications if c.classification in NEWLY_CLEAN_CLASSIFICATIONS]
    aggregate = {
        "airport_count": len(newly_clean),
        "runways_would_create": sum(c.runways_would_create for c in newly_clean),
        "runways_would_enrich": sum(c.runways_would_enrich for c in newly_clean),
        "runway_ends_would_create": sum(c.runway_ends_would_create for c in newly_clean),
    }
    return classifications, newly_clean, aggregate


def dry_run(session: Session, *, zip_path: Path = DEFAULT_ZIP) -> dict[str, Any]:
    classifications, newly_clean, aggregate = _resolve(session, zip_path)
    excluded = [c for c in classifications if c.classification not in CLEAN_BATCH_CLASSIFICATIONS]
    return {
        "aggregate": aggregate,
        "airport_ids": sorted(c.airport_id for c in newly_clean),
        "excluded": [
            {"airport_id": c.airport_id, "classification": c.classification, "error": c.error} for c in excluded
        ],
    }


def run(
    session: Session,
    *,
    apply: bool = False,
    zip_path: Path = DEFAULT_ZIP,
    expected_airport_count: int = EXPECTED_AIRPORT_COUNT,
    expected_runway_creates: int = EXPECTED_RUNWAY_CREATES,
    expected_runway_enrich: int = EXPECTED_RUNWAY_ENRICH,
    expected_runway_end_creates: int = EXPECTED_RUNWAY_END_CREATES,
) -> dict[str, Any]:
    """The expected_* parameters are the already-approved plan snapshot a
    real apply must match before it's allowed to write anything - they
    default to the values approved in
    docs/domain/canonical-runway-us-newly-clean-12-batch-report.md. Tests
    pass their own fixture-shaped snapshot to exercise the same
    abort-on-drift behavior without being tied to the real database's
    numbers."""
    report = dry_run(session, zip_path=zip_path)
    if not apply:
        return report

    agg = report["aggregate"]
    expected = (
        expected_airport_count,
        expected_runway_creates,
        expected_runway_enrich,
        expected_runway_end_creates,
    )
    actual = (
        agg["airport_count"],
        agg["runways_would_create"],
        agg["runways_would_enrich"],
        agg["runway_ends_would_create"],
    )
    if actual != expected:
        raise ValueError(
            "Aborting: newly-clean batch plan does not match the approved snapshot. "
            f"expected(airport_count, creates, enrich, end_creates)={expected}, actual={actual}"
        )

    # Re-resolve immediately before writing. Nothing has been added to the
    # session yet, so if either check below fails, the whole batch aborts
    # with zero writes - no partial apply is possible.
    classifications, fresh_newly_clean, fresh_aggregate = _resolve(session, zip_path)
    fresh_ids = sorted(c.airport_id for c in fresh_newly_clean)
    if fresh_ids != report["airport_ids"]:
        raise ValueError("Aborting: airport-set membership changed between resolution and pre-write re-check")
    if fresh_aggregate != agg:
        raise ValueError("Aborting: aggregate plan changed between resolution and pre-write re-check")

    airports_by_id = {a.id: a for a in session.scalars(select(Airport).where(Airport.id.in_(fresh_ids))).all()}
    for classification in fresh_newly_clean:
        airport = airports_by_id[classification.airport_id]
        apply_plan(session, airport, list(classification.plans))
    session.commit()

    return dry_run(session, zip_path=zip_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write the newly-clean batch (default is dry run only)"
    )
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
