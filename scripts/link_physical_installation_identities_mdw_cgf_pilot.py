"""Narrow, dry-run-first pilot: link the already-reviewed MDW/CGF
PhysicalInstallationIdentity rows to their canonical RunwayEnd rows.

NOT general automatic reconciliation - scoped to exactly two airports
(TARGET_CODES) and writes only PhysicalInstallationIdentity.runway_end_id.
See docs/domain/canonical-runway-runway-end-design.md S7 and
app/services/physical_installation_identity_linking.py for the resolution
rules (fail-closed: unresolved/ambiguous/cross-airport/conflicting-link
never get linked, and abort the ENTIRE apply rather than partially write).
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from app.database import SessionLocal
from app.services.physical_installation_identity_linking import apply_identity_links, plan_identity_links

DEFAULT_DATABASE = Path("data/runway_safe.db")
BACKUP_DIRECTORY = Path("data/backups")
TARGET_CODES = ("MDW", "CGF")


def _backup_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"runway_safe-pre-physical-installation-identity-linking-mdw-cgf-pilot-{timestamp}.db"


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


def _as_dict(resolution) -> dict:
    return {
        "identity_id": resolution.identity_id,
        "airport_code": resolution.airport_code,
        "airport_id": resolution.airport_id,
        "stored_designation": resolution.stored_designation,
        "normalized_designation": resolution.normalized_designation,
        "target_runway_id": resolution.target_runway_id,
        "target_runway_designation": resolution.target_runway_designation,
        "target_runway_end_id": resolution.target_runway_end_id,
        "target_runway_end_designation": resolution.target_runway_end_designation,
        "current_runway_end_id": resolution.current_runway_end_id,
        "proposed_runway_end_id": resolution.proposed_runway_end_id,
        "status": resolution.status,
    }


def dry_run(*, database: Path = DEFAULT_DATABASE) -> dict:
    with SessionLocal() as session:
        resolutions = plan_identity_links(session, TARGET_CODES)
    return {
        "target_codes": list(TARGET_CODES),
        "identity_count": len(resolutions),
        "mappings": [_as_dict(r) for r in resolutions],
        "status_counts": {
            status: sum(1 for r in resolutions if r.status == status)
            for status in {r.status for r in resolutions}
        },
    }


def apply(*, database: Path = DEFAULT_DATABASE) -> dict:
    backup_path = backup_database(database)
    with SessionLocal() as session:
        resolutions = apply_identity_links(session, TARGET_CODES)
        session.commit()
    return {
        "backup_path": str(backup_path),
        "target_codes": list(TARGET_CODES),
        "identity_count": len(resolutions),
        "mappings": [_as_dict(r) for r in resolutions],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write runway_end_id (default is dry run only)")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args(argv)

    result = apply(database=args.database) if args.apply else dry_run(database=args.database)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
