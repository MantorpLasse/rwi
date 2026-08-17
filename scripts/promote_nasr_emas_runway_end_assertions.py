"""Guarded, narrowly-scoped promotion writer for NASR current-EMAS-presence
`SourceAssertion` rows classified `AUTO_RESOLVABLE` by
scripts/analyze_nasr_emas_runway_end_resolution.py.

docs/domain/nasr-emas-auto-resolvable-promotion-dry-run.md /
docs/domain/emas-runway-end-semantics-and-nasr-promotion-analysis.md.

Reuses the existing read-only classifier for all classification logic -
this module never reimplements it. Writes exactly ONE field on exactly the
rows the classifier calls AUTO_RESOLVABLE:

    SourceAssertion.runway_end = <normalized PHYSICAL RunwayEnd designation>

Never writes the reciprocal/protected-direction designation. Never creates
or touches `PhysicalInstallationIdentity`, `InstallationAssertionLink`, or
any other table - this is not a reconciliation writer.

Default is dry run. A real write requires BOTH --apply and
--allow-database-write. The apply path is guarded by three independent
checks, all of which must pass before a single row is touched:
  1. the classifier's aggregate counts must exactly match the approved
     snapshot (EXPECTED_SNAPSHOT below);
  2. a deterministic fingerprint of the writable row set must exactly
     match the approved fingerprint computed at analysis time
     (EXPECTED_FINGERPRINT below);
  3. every row's own preconditions are re-checked immediately before
     writing (never trust a stale plan).
Any violation aborts the entire batch - never a partial write.
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

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import SourceAssertion
from scripts.analyze_nasr_emas_runway_end_resolution import (
    ALREADY_LINKED,
    AMBIGUOUS,
    AUTO_RESOLVABLE,
    CONFLICT,
    INSUFFICIENT_EVIDENCE,
    REVIEW_REQUIRED,
    AssertionClassification,
    classify_all,
    summarize,
)

DEFAULT_DATABASE = Path("data/runway_safe.db")
BACKUP_DIRECTORY = Path("data/backups")

# Approved snapshot, frozen at analysis time
# (docs/domain/emas-runway-end-semantics-and-nasr-promotion-analysis.md S11).
# The apply path aborts entirely if a fresh classification doesn't match
# this exactly - protects against the underlying data, the classifier, or
# the semantic rules having changed since this batch was reviewed and
# approved.
EXPECTED_SNAPSHOT = {
    "assertions_total": 115,
    AUTO_RESOLVABLE: 97,
    ALREADY_LINKED: 9,
    REVIEW_REQUIRED: 9,
    AMBIGUOUS: 0,
    CONFLICT: 0,
    INSUFFICIENT_EVIDENCE: 0,
}

# Writer-level states - refinements of the classifier's own AUTO_RESOLVABLE
# class based on the *current* value of SourceAssertion.runway_end, which
# the classifier itself deliberately does not consider (it answers "is the
# physical mapping deterministic", not "has this already been written").
WRITABLE = "WRITABLE"
ALREADY_PROMOTED = "ALREADY_PROMOTED"
DRIFTED = "DRIFTED"


class PromotionGuardError(ValueError):
    """Raised whenever a snapshot/fingerprint/precondition check fails -
    this writer never proceeds past a failed guard, partially or otherwise."""


@dataclass(frozen=True)
class WriterRow:
    assertion_id: int
    airport_id: int
    airport_code: str | None
    raw_runway_end_value: str | None
    proposed_runway_end: str
    writer_state: str


def _backup_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"runway_safe-pre-nasr-emas-runway-end-promotion-{timestamp}.db"


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


def _writer_row(session: Session, result: AssertionClassification) -> WriterRow | None:
    """None for anything the classifier didn't call AUTO_RESOLVABLE - those
    are reported separately (§ REVIEW_REQUIRED/ALREADY_LINKED must remain
    untouched) but never enter the writable set at all."""
    if result.classification != AUTO_RESOLVABLE:
        return None
    assertion = session.get(SourceAssertion, result.assertion_id)
    if result.candidate_designation is None or result.raw_runway_end_value is None:
        raise PromotionGuardError(
            f"assertion {result.assertion_id} classified AUTO_RESOLVABLE but has no candidate/raw value - "
            "this should be structurally impossible; refusing to proceed"
        )
    if assertion.runway_end is None:
        state = WRITABLE
    elif assertion.runway_end == result.candidate_designation:
        state = ALREADY_PROMOTED
    else:
        state = DRIFTED
    return WriterRow(
        assertion_id=result.assertion_id, airport_id=result.airport_id, airport_code=result.airport_code,
        raw_runway_end_value=result.raw_runway_end_value, proposed_runway_end=result.candidate_designation,
        writer_state=state,
    )


def _fingerprint(rows: list[WriterRow]) -> str:
    """Deterministic fingerprint of the intended writable (WRITABLE-state)
    row set - protects against "97 old rows" silently becoming "97
    different rows" between approval and apply, even when the count alone
    would look unchanged."""
    fingerprint_rows = sorted(
        (r.assertion_id, r.airport_id, r.raw_runway_end_value, r.proposed_runway_end)
        for r in rows
        if r.writer_state == WRITABLE
    )
    payload = json.dumps(fingerprint_rows, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _check_snapshot(summary: dict) -> None:
    actual = {
        "assertions_total": summary["assertions_total"],
        AUTO_RESOLVABLE: summary["by_classification"].get(AUTO_RESOLVABLE, 0),
        ALREADY_LINKED: summary["by_classification"].get(ALREADY_LINKED, 0),
        REVIEW_REQUIRED: summary["by_classification"].get(REVIEW_REQUIRED, 0),
        AMBIGUOUS: summary["by_classification"].get(AMBIGUOUS, 0),
        CONFLICT: summary["by_classification"].get(CONFLICT, 0),
        INSUFFICIENT_EVIDENCE: summary["by_classification"].get(INSUFFICIENT_EVIDENCE, 0),
    }
    if actual != EXPECTED_SNAPSHOT:
        raise PromotionGuardError(
            f"Classifier snapshot drift: expected {EXPECTED_SNAPSHOT}, got {actual}. Refusing to proceed."
        )


def plan(session: Session, *, expected_fingerprint: str | None = None) -> dict[str, Any]:
    """Read-only. Classifies the full population, validates the approved
    snapshot, computes the current fingerprint, and (if expected_fingerprint
    is given) validates it too. Never mutates the session."""
    results = classify_all(session)
    summary = summarize(results)
    _check_snapshot(summary)

    writer_rows = [row for row in (_writer_row(session, r) for r in results) if row is not None]
    drifted = [row for row in writer_rows if row.writer_state == DRIFTED]
    if drifted:
        raise PromotionGuardError(
            "Drift detected: assertion(s) classified AUTO_RESOLVABLE already have a runway_end value that "
            f"does not match the proposed physical designation: {[(r.assertion_id, r.raw_runway_end_value) for r in drifted]}"
        )

    writable = [row for row in writer_rows if row.writer_state == WRITABLE]
    already_promoted = [row for row in writer_rows if row.writer_state == ALREADY_PROMOTED]
    fingerprint = _fingerprint(writer_rows)

    if expected_fingerprint is not None and fingerprint != expected_fingerprint:
        raise PromotionGuardError(
            f"Fingerprint drift: expected {expected_fingerprint}, got {fingerprint}. "
            "The writable row set has changed since this batch was approved - refusing to proceed."
        )

    review_required_airports = sorted({r.airport_code or r.airport_id for r in results if r.classification == REVIEW_REQUIRED})
    already_linked_airports = sorted({r.airport_code or r.airport_id for r in results if r.classification == ALREADY_LINKED})

    return {
        "summary": summary,
        "fingerprint": fingerprint,
        "writable_count": len(writable),
        "already_promoted_count": len(already_promoted),
        "writable_rows": writable,
        "already_promoted_rows": already_promoted,
        "review_required_airports": review_required_airports,
        "already_linked_airports": already_linked_airports,
        "affected_airport_count": len({r.airport_id for r in writable}),
    }


def dry_run(session: Session, *, expected_fingerprint: str | None = None) -> dict[str, Any]:
    result = plan(session, expected_fingerprint=expected_fingerprint)
    assert len(session.new) == 0 and len(session.dirty) == 0 and len(session.deleted) == 0
    return result


def apply(session: Session, *, expected_fingerprint: str) -> dict[str, Any]:
    """Writes SourceAssertion.runway_end for exactly the WRITABLE rows.
    Re-plans immediately before writing (never trusts a stale caller-side
    plan). Verifies the exact changed-row set afterward and raises (never
    partially commits) if anything unexpected changed."""
    result = plan(session, expected_fingerprint=expected_fingerprint)
    writable = result["writable_rows"]

    touched_ids: set[int] = set()
    # no_autoflush is required here, not optional: session.get() on a
    # DIFFERENT primary key partway through this loop would otherwise
    # silently autoflush every EARLIER row's pending change first (true
    # regardless of whether the caller's own session has autoflush=True,
    # e.g. every plain Session(engine) in this project's tests, or
    # autoflush=False, e.g. app.database.SessionLocal) - which starves the
    # dirty-set verification below down to only the last unflushed row.
    # Caught empirically during this task's own disposable-copy rehearsal
    # against a 97-row batch (never observable with this repo's usual
    # single-row correction-script tests) - see
    # docs/domain/nasr-emas-auto-resolvable-promotion-dry-run.md.
    with session.no_autoflush:
        for row in writable:
            assertion = session.get(SourceAssertion, row.assertion_id)
            if assertion.runway_end is not None:
                raise PromotionGuardError(
                    f"assertion {row.assertion_id}.runway_end became non-NULL between planning and writing - aborting"
                )
            assertion.runway_end = row.proposed_runway_end
            touched_ids.add(row.assertion_id)

    if len(session.new) != 0 or len(session.deleted) != 0:
        raise PromotionGuardError("Unexpected new/deleted rows detected during apply - aborting")
    dirty_ids = {obj.id for obj in session.dirty if isinstance(obj, SourceAssertion)}
    if dirty_ids != touched_ids:
        raise PromotionGuardError(
            f"Pending-write verification failed: expected dirty SourceAssertion ids {touched_ids}, got {dirty_ids}"
        )

    session.flush()

    changed = {
        row.assertion_id: session.get(SourceAssertion, row.assertion_id).runway_end for row in writable
    }
    expected_changed = {row.assertion_id: row.proposed_runway_end for row in writable}
    if changed != expected_changed:
        raise PromotionGuardError(
            f"Post-write verification failed: expected exactly {expected_changed}, database shows {changed}"
        )

    session.commit()
    return {"rows_written": len(writable), "fingerprint": result["fingerprint"], "writable_rows": writable}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the promotion (default is dry run only)")
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

    # Deliberately build our own engine/session bound to the exact resolved
    # --database path, rather than importing the shared app.database
    # SessionLocal - that global is bound once, at process import time, to
    # settings.database_url, and completely ignores this script's own
    # --database argument. Discovered during this task's own real-DB vs.
    # disposable-copy simulation (docs/domain/nasr-emas-auto-resolvable-
    # promotion-dry-run.md): every prior correction script in this
    # repository (e.g. scripts/correct_morristown_airport_identity.py)
    # shares this same latent flaw - --database only ever labelled the
    # backup file, never the actual read/write target - which was never
    # exposed before because no earlier task tried to redirect an --apply
    # write away from the real database. Always building our own session
    # from --database here (never falling back to the shared SessionLocal)
    # closes that gap unconditionally, for both dry-run and apply.
    database_path = args.database.resolve()
    # autoflush=False matches app.database.SessionLocal's own convention -
    # without it, session.get() calls partway through apply()'s write loop
    # silently autoflush earlier pending changes before the loop finishes,
    # which starves the post-write session.dirty check below down to only
    # the last unflushed row (caught by that same check during this task's
    # own disposable-copy rehearsal - see the design doc's incident note).
    session_factory = sessionmaker(
        bind=create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False}),
        autoflush=False,
    )

    with session_factory() as session:
        if args.apply:
            # Validate BEFORE backing anything up - a stale/wrong
            # fingerprint, classifier drift, or per-row precondition
            # failure must never trigger a backup at all, only an actual
            # write should. plan() here is read-only (proven by dry_run()'s
            # own assertion elsewhere) and raises PromotionGuardError on
            # any drift, so a failed validation exits before backup_database()
            # is ever called.
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
