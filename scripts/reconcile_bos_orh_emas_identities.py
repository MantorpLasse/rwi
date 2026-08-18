"""Guarded, narrowly-scoped reconciliation writer for the 4 BOS/ORH
REVIEW_REQUIRED NASR current-EMAS-presence `SourceAssertion` rows proven
reconcilable by docs/domain/bos-orh-emas-reconciliation-investigation.md.

Reuses the exact existing MDW/CGF reconciliation architecture
(app/services/physical_installation_reconciliation.py) - this module
introduces no new reconciliation concept. For each of the 4 target
assertions it creates exactly:

  - one PhysicalInstallationIdentity (PHYSICAL bed location only -
    airport_id, runway_id, runway_end, runway_end_id)
  - one InstallationAssertionLink (outcome="SAME_PHYSICAL_INSTALLATION")

Never writes SourceAssertion.runway_end (docs/domain/bos-orh-emas-
reconciliation-investigation.md S15: MDW/CGF's own linked assertions
still have runway_end IS NULL today - publication has only ever depended
on the reviewed identity existing, never on that column). Never touches
any other table, airport, or assertion - BGM (181/182), LEX (153/154),
and ELM (183) are structurally unreachable: TARGET_ROWS below is the only
population this writer ever considers.

Default is dry run. A real write requires BOTH --apply and
--allow-database-write, plus an --expected-fingerprint matching a fresh
plan(). Any precondition/snapshot/fingerprint mismatch aborts the entire
4-row batch before any write - never a partial apply.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Airport, PhysicalInstallationIdentity, Runway, RunwayEnd, SourceAssertion
from app.models.physical_installation_identity import InstallationAssertionLink
from app.services.physical_installation_reconciliation import (
    create_physical_installation_identity,
    record_reconciliation_decision,
)
from app.services.runway_identity import AmbiguousRunwayDesignationError, normalize_end

DEFAULT_DATABASE = Path("data/runway_safe.db")
BACKUP_DIRECTORY = Path("data/backups")
ACTOR = "human:rwi-owner"

# The approved, frozen 4-row target set - the ONLY assertions this writer
# will ever consider, by construction. Airport codes are the anchor (not
# raw ids) so a mismatched --database (e.g. a fixture where ids differ)
# is caught as a precondition failure rather than silently resolving to
# the wrong row. Values match
# docs/domain/bos-orh-emas-reconciliation-investigation.md S16 exactly.
TARGET_ROWS: tuple[dict[str, Any], ...] = (
    dict(assertion_id=161, airport_code="BOS", physical="04L", protected_expected="22R"),
    dict(assertion_id=162, airport_code="BOS", physical="15R", protected_expected="33L"),
    dict(assertion_id=164, airport_code="ORH", physical="11", protected_expected="29"),
    dict(assertion_id=165, airport_code="ORH", physical="29", protected_expected="11"),
)
TARGET_ASSERTION_IDS = frozenset(row["assertion_id"] for row in TARGET_ROWS)

# Approved snapshot of the planned reconciliation shape itself (not just a
# count) - the apply path aborts if a fresh plan doesn't match this
# exactly, protecting against the underlying data or the investigation's
# own conclusions having silently drifted since this batch was approved.
EXPECTED_SNAPSHOT = tuple(
    (row["assertion_id"], row["airport_code"], row["physical"], row["protected_expected"]) for row in TARGET_ROWS
)

WRITABLE = "WRITABLE"
ALREADY_RECONCILED = "ALREADY_RECONCILED"
CONFLICT = "CONFLICT"


class ReconciliationGuardError(ValueError):
    """Raised whenever a precondition/snapshot/fingerprint check fails -
    this writer never proceeds past a failed guard, partially or otherwise."""


@dataclass(frozen=True)
class PlannedRow:
    assertion_id: int
    airport_id: int
    airport_code: str
    runway_id: int
    runway_end_id: int
    physical: str
    protected_direction: str
    writer_state: str
    existing_identity_id: int | None = None


def _backup_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"runway_safe-pre-bos-orh-emas-reconciliation-{timestamp}.db"


def backup_database(database: Path = DEFAULT_DATABASE, backup_directory: Path = BACKUP_DIRECTORY) -> Path:
    database = database.resolve()
    if not database.is_file():
        raise FileNotFoundError(f"Database not found: {database}")
    backup_directory.mkdir(parents=True, exist_ok=True)
    destination = backup_directory / _backup_name()
    shutil.copy2(database, destination)
    if destination.stat().st_size != database.stat().st_size:
        raise RuntimeError("Database backup size does not match the source database.")
    conn = sqlite3.connect(f"file:{destination}?mode=ro", uri=True)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Database backup failed integrity_check.")
    finally:
        conn.close()
    return destination


def _resolve_canonical_end(airport: Airport, physical: str) -> RunwayEnd:
    try:
        target = normalize_end(physical)
    except AmbiguousRunwayDesignationError as exc:
        raise ReconciliationGuardError(f"physical value {physical!r} does not normalize to a single designation") from exc
    candidates = [
        end for runway in airport.runways for end in runway.runway_ends
        if _normalize_safe(end.designation) == target
    ]
    if len(candidates) != 1:
        raise ReconciliationGuardError(
            f"expected exactly one canonical RunwayEnd for {airport.iata_code} {physical!r}, found {len(candidates)}"
        )
    return candidates[0]


def _normalize_safe(designation: str) -> str | None:
    try:
        return normalize_end(designation)
    except AmbiguousRunwayDesignationError:
        return None


def _protected_direction(runway_end: RunwayEnd) -> str:
    """Pure topology - the OTHER RunwayEnd on the same canonical Runway.
    Never designation arithmetic. Fails closed if the parent Runway does
    not have exactly two governed RunwayEnd rows (structurally impossible
    today - docs/domain/emas-runway-end-semantics-and-nasr-promotion-
    analysis.md S4 - but checked explicitly rather than assumed)."""
    siblings = [e for e in runway_end.runway.runway_ends if e.id != runway_end.id]
    if len(siblings) != 1:
        raise ReconciliationGuardError(
            f"RunwayEnd {runway_end.id} ({runway_end.designation}) does not have exactly one reciprocal sibling"
        )
    return siblings[0].designation


def _plan_row(session: Session, target: dict[str, Any]) -> PlannedRow:
    assertion_id = target["assertion_id"]
    if assertion_id not in TARGET_ASSERTION_IDS:  # structurally unreachable, kept as an explicit guard
        raise ReconciliationGuardError(f"assertion {assertion_id} is not in the approved target set")

    airport = session.scalar(select(Airport).where(Airport.faa_code == target["airport_code"]))
    if airport is None:
        raise ReconciliationGuardError(f"airport {target['airport_code']!r} does not exist")

    assertion = session.get(SourceAssertion, assertion_id)
    if assertion is None:
        raise ReconciliationGuardError(f"assertion {assertion_id} does not exist")
    if assertion.airport_id != airport.id:
        raise ReconciliationGuardError(
            f"assertion {assertion_id} belongs to airport_id {assertion.airport_id}, expected {target['airport_code']} ({airport.id})"
        )
    if assertion.assertion_type != "runway_end":
        raise ReconciliationGuardError(f"assertion {assertion_id} has assertion_type {assertion.assertion_type!r}, expected 'runway_end'")
    if assertion.evidence_quality != "direct_strong":
        raise ReconciliationGuardError(
            f"assertion {assertion_id} has evidence_quality {assertion.evidence_quality!r}, expected 'direct_strong'"
        )
    if assertion.raw_runway_end_value != target["physical"]:
        raise ReconciliationGuardError(
            f"assertion {assertion_id} raw_runway_end_value {assertion.raw_runway_end_value!r} != expected {target['physical']!r}"
        )
    if assertion.runway_end is not None:
        raise ReconciliationGuardError(f"assertion {assertion_id}.runway_end is already {assertion.runway_end!r}, expected NULL")

    canonical_end = _resolve_canonical_end(airport, target["physical"])
    protected = _protected_direction(canonical_end)
    if protected != target["protected_expected"]:
        raise ReconciliationGuardError(
            f"assertion {assertion_id}: derived protected direction {protected!r} != investigation-approved {target['protected_expected']!r}"
        )

    existing_link = session.scalar(select(InstallationAssertionLink).where(InstallationAssertionLink.assertion_id == assertion_id))
    existing_identity = session.scalar(
        select(PhysicalInstallationIdentity).where(
            PhysicalInstallationIdentity.airport_id == airport.id,
            PhysicalInstallationIdentity.runway_end_id == canonical_end.id,
        )
    )

    if existing_link is None and existing_identity is None:
        state, existing_identity_id = WRITABLE, None
    elif (
        existing_link is not None and existing_identity is not None
        and existing_link.physical_installation_id == existing_identity.id
        and existing_link.outcome == "SAME_PHYSICAL_INSTALLATION"
        and existing_identity.runway_end == target["physical"]
    ):
        state, existing_identity_id = ALREADY_RECONCILED, existing_identity.id
    else:
        # Any other shape (a link with no matching identity, an identity
        # with no link, a link with a different outcome, a pre-existing
        # identity at this end not created by this reconciliation, etc.)
        # is a conflict this writer refuses to silently paper over.
        raise ReconciliationGuardError(
            f"assertion {assertion_id}: existing identity/link state does not match a clean WRITABLE or "
            f"ALREADY_RECONCILED shape (existing_link={existing_link}, existing_identity={existing_identity}) - "
            "refusing to proceed; this requires human review, not an automatic overwrite"
        )

    return PlannedRow(
        assertion_id=assertion_id, airport_id=airport.id, airport_code=target["airport_code"],
        runway_id=canonical_end.runway_id, runway_end_id=canonical_end.id,
        physical=target["physical"], protected_direction=protected,
        writer_state=state, existing_identity_id=existing_identity_id,
    )


def _fingerprint(rows: list[PlannedRow]) -> str:
    """Deterministic fingerprint of the exact planned WRITABLE set -
    protects against "4 rows" silently becoming "4 different rows"
    between approval and apply."""
    fingerprint_rows = sorted(
        (r.assertion_id, r.airport_id, r.physical, r.runway_end_id, r.protected_direction, "SAME_PHYSICAL_INSTALLATION")
        for r in rows
        if r.writer_state == WRITABLE
    )
    payload = json.dumps(fingerprint_rows, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _check_snapshot(rows: list[PlannedRow]) -> None:
    actual = tuple(
        (r.assertion_id, r.airport_code, r.physical, r.protected_direction)
        for r in sorted(rows, key=lambda r: r.assertion_id)
    )
    expected = tuple(sorted(EXPECTED_SNAPSHOT))
    if actual != expected:
        raise ReconciliationGuardError(f"Snapshot drift: expected {expected}, got {actual}. Refusing to proceed.")


def plan(session: Session, *, expected_fingerprint: str | None = None) -> dict[str, Any]:
    """Read-only. Never mutates the session (proven by dry_run()'s own
    assertion). Resolves and validates all 4 target rows, checks the
    approved snapshot, computes the fingerprint, and (if given) validates
    it too."""
    rows = [_plan_row(session, target) for target in TARGET_ROWS]
    _check_snapshot(rows)

    conflicts = [r for r in rows if r.writer_state == CONFLICT]
    if conflicts:  # structurally unreachable - _plan_row raises before returning a CONFLICT row - kept as a guard
        raise ReconciliationGuardError(f"Conflicting rows detected: {[r.assertion_id for r in conflicts]}")

    writable = [r for r in rows if r.writer_state == WRITABLE]
    already_reconciled = [r for r in rows if r.writer_state == ALREADY_RECONCILED]
    fingerprint = _fingerprint(rows)

    if expected_fingerprint is not None and fingerprint != expected_fingerprint:
        raise ReconciliationGuardError(
            f"Fingerprint drift: expected {expected_fingerprint}, got {fingerprint}. "
            "The writable row set has changed since this batch was approved - refusing to proceed."
        )

    return {
        "rows": rows,
        "fingerprint": fingerprint,
        "writable_count": len(writable),
        "already_reconciled_count": len(already_reconciled),
        "writable_rows": writable,
        "already_reconciled_rows": already_reconciled,
    }


def dry_run(session: Session, *, expected_fingerprint: str | None = None) -> dict[str, Any]:
    result = plan(session, expected_fingerprint=expected_fingerprint)
    assert len(session.new) == 0 and len(session.dirty) == 0 and len(session.deleted) == 0
    return result


def _reason(row: PlannedRow) -> str:
    return (
        f"FAA NASR 2026-08-06 explicitly reports EMAS at {row.airport_code} runway end {row.physical}; "
        "current-presence only, no historical continuity claim. Protected direction "
        f"({row.protected_direction}) confirmed by Massport/MPA primary-source evidence - see "
        "docs/domain/bos-orh-emas-reconciliation-investigation.md."
    )


def apply(session: Session, *, expected_fingerprint: str) -> dict[str, Any]:
    """Creates exactly one PhysicalInstallationIdentity + one
    InstallationAssertionLink per WRITABLE row. Re-plans immediately
    before writing (never trusts a stale caller-side plan). Verifies the
    exact created-row set afterward and raises (never partially commits)
    if anything unexpected happened."""
    result = plan(session, expected_fingerprint=expected_fingerprint)
    writable = result["writable_rows"]

    # Counted before/after, not via session.new: each row's identity must
    # be individually flushed to obtain its id before the link referencing
    # physical_installation_id=identity.id can be created, and a flush
    # commits ALL pending objects to the "persistent" state (removing them
    # from session.new) - not just the one object that triggered it. With
    # more than one row, checking session.new after the loop would only
    # ever see the last row's still-pending link, undercounting everything
    # flushed by an earlier iteration. A count-based check across the
    # whole operation is correct regardless of how many intermediate
    # flushes happen.
    identities_before = session.scalar(select(func.count()).select_from(PhysicalInstallationIdentity))
    links_before = session.scalar(select(func.count()).select_from(InstallationAssertionLink))

    created_identity_ids: list[int] = []
    with session.no_autoflush:
        for row in writable:
            identity = create_physical_installation_identity(
                session, airport_id=row.airport_id, runway_id=row.runway_id,
                runway_end=row.physical, runway_end_id=row.runway_end_id,
            )
            session.flush()
            created_identity_ids.append(identity.id)
            record_reconciliation_decision(
                session, assertion_id=row.assertion_id, physical_installation_id=identity.id,
                outcome="SAME_PHYSICAL_INSTALLATION", reason=_reason(row), actor=ACTOR,
            )
    session.flush()

    if len(session.deleted) != 0:
        raise ReconciliationGuardError("Unexpected deleted rows detected during apply - aborting")
    identities_after = session.scalar(select(func.count()).select_from(PhysicalInstallationIdentity))
    links_after = session.scalar(select(func.count()).select_from(InstallationAssertionLink))
    if identities_after - identities_before != len(writable):
        raise ReconciliationGuardError(
            f"Expected exactly {len(writable)} new PhysicalInstallationIdentity rows, "
            f"count changed by {identities_after - identities_before}"
        )
    if links_after - links_before != len(writable):
        raise ReconciliationGuardError(
            f"Expected exactly {len(writable)} new InstallationAssertionLink rows, "
            f"count changed by {links_after - links_before}"
        )

    for row in writable:
        link = session.scalar(select(InstallationAssertionLink).where(InstallationAssertionLink.assertion_id == row.assertion_id))
        identity = session.get(PhysicalInstallationIdentity, link.physical_installation_id) if link else None
        assertion = session.get(SourceAssertion, row.assertion_id)
        if (
            link is None or identity is None
            or link.outcome != "SAME_PHYSICAL_INSTALLATION"
            or identity.airport_id != row.airport_id
            or identity.runway_id != row.runway_id
            or identity.runway_end != row.physical
            or identity.runway_end_id != row.runway_end_id
            or assertion.runway_end is not None
        ):
            raise ReconciliationGuardError(f"Post-write verification failed for assertion {row.assertion_id}")

    session.commit()
    return {
        "rows_written": len(writable),
        "identities_created": len(writable),
        "links_created": len(writable),
        "fingerprint": result["fingerprint"],
        "writable_rows": writable,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the reconciliation (default is dry run only)")
    parser.add_argument("--allow-database-write", action="store_true", help="required together with --apply")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument(
        "--expected-fingerprint", type=str, default=None,
        help="required together with --apply; the approved fingerprint from a prior dry run",
    )
    args = parser.parse_args(argv)

    if args.apply and not args.allow_database_write:
        parser.error("--apply requires --allow-database-write")
    if args.apply and not args.expected_fingerprint:
        parser.error("--apply requires --expected-fingerprint")

    # Own engine/session bound to the exact resolved --database path -
    # NEVER app.database.SessionLocal, which is bound once at process
    # import time to settings.database_url and completely ignores this
    # script's own --database argument (the exact incident documented in
    # docs/domain/nasr-emas-auto-resolvable-promotion-dry-run.md).
    database_path = args.database.resolve()
    session_factory = sessionmaker(
        bind=create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False}),
        autoflush=False,
    )

    with session_factory() as session:
        if args.apply:
            # Validate BEFORE backing anything up - a stale fingerprint,
            # snapshot drift, or per-row precondition failure must never
            # trigger a backup at all, only an actual write should.
            plan(session, expected_fingerprint=args.expected_fingerprint)
            backup_path = backup_database(args.database)
            print("Backup created:", backup_path)
            result = apply(session, expected_fingerprint=args.expected_fingerprint)
        else:
            result = dry_run(session)

    def _default(value):
        if hasattr(value, "__dict__"):
            return vars(value)
        return str(value)

    print(json.dumps(result, indent=2, default=_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
