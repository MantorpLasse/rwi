"""Commander Staged Funding Review CLI (RWI HQ "Commander Staged Funding
Review CLI", following the recon mission "Commander Lightweight Funding
Review CLI" and the hardening mission "Lightweight Funding Eligibility
Hardening").

    known-airport-staged, funding-provenance SourceAssertion
        (Source.external_id starts with "faa_aip:" or "usaspending:" -
         app.services.known_airport_funding_lightweight_path_guard)
        -> explicit human ReviewerAction
           (app.services.known_airport_funding_reviewer_action)
        -> optional, APPROVE_SIGNAL-gated Signal creation/reuse
           (app.services.known_airport_funding_signal_creation)
        -> STOP

This script implements NO governance logic of its own. It is a thin
orchestrator over the three already-committed, already-tested lightweight
funding services above - eligibility, ReviewerAction recording, and
Signal creation/reuse are all delegated entirely to them. This script
never widens the funding-provenance namespace, never accepts DEFER or
CONFIRM_DISTINCT_SIGNAL (those remain heavy-pipeline-only, via
app.services.reviewer_action_persistence), and never exposes a
runway_id/supplier/likely_supplier/estimated_total_value_usd/
estimated_emas_value_usd parameter of any kind - those are structurally
absent from create_signal_from_lightweight_funding_review()'s own
signature, not merely unfilled by this CLI.

Research Loop / Discovery evidence (Source.external_id starting
"discovery:", e.g. the real SourceAssertion 258) is refused by the SAME
hardened eligibility gate every other caller uses - this CLI cannot
promote it, by construction, not by a parallel check of its own.

SAFETY MODEL (mirrors scripts/persist_selected_fragments.py's own
default-closed convention):
  - No database write unless BOTH --apply AND --allow-database-write.
  - Without both, this script performs a read-only PREVIEW: it loads the
    one named SourceAssertion, its Airport and Source, runs the hardened
    eligibility check, and - for APPROVE_SIGNAL - predicts (via the same
    pure, read-only R1/R2 reconciliation core the real apply path uses,
    never by calling a mutating function and rolling back) whether a
    Signal would be created, an existing one reused, or the apply refused.
  - A real apply against a persistent file database backs it up first
    (scripts.migrate_discovery_governed_evidence_slice1.backup_database,
    reused directly) unless --skip-backup, marked "isolated/temp DBs only"
    exactly like persist_selected_fragments.py's own.
  - APPROVE_SIGNAL apply records the ReviewerAction and creates/reuses the
    Signal in ONE session, committing EXACTLY ONCE at the end - an
    exception at either step rolls back the whole operation, so this
    script can never durably leave an APPROVE_SIGNAL ReviewerAction
    without its Signal, or vice versa.

Invocation (matches this repository's existing script convention):

    # Preview only - no database write, regardless of flags below.
    python -m scripts.review_staged_funding_evidence \\
        --database data/runway_safe.db --source-assertion-id 255 \\
        --action NEEDS_MORE_EVIDENCE --reviewer human:you --reason "..."

    python -m scripts.review_staged_funding_evidence \\
        --database data/runway_safe.db --source-assertion-id 255 \\
        --action APPROVE_SIGNAL --reviewer human:you --reason "..." \\
        --title "..." --category replacement --confidence medium

    # Apply - explicit, both flags required.
    python -m scripts.review_staged_funding_evidence \\
        --database data/runway_safe.db --source-assertion-id 255 \\
        --action APPROVE_SIGNAL --reviewer human:you --reason "..." \\
        --title "..." --category replacement --confidence medium \\
        --apply --allow-database-write
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Airport, ReviewerAction, Signal, Source, SourceAssertion
from app.models.signal import DEFAULT_SCORE_BY_CONFIDENCE
from app.services.existing_signal_reconciliation import (
    ExistingSignalReconciliationOutcome,
    evaluate_existing_signal_reconciliation,
)
from app.services.existing_signal_reconciliation_candidates import (
    build_reconciliation_subject,
    find_reconciliation_candidates,
)
from app.services.governed_signal_creation import ExistingSignalPossibleMatchError
from app.services.known_airport_funding_lightweight_path_guard import (
    NotLightweightFundingAssertionError,
    check_lightweight_funding_path_eligibility,
    funding_namespace_for,
)
from app.services.known_airport_funding_reviewer_action import (
    LIGHTWEIGHT_FUNDING_REVIEWER_ACTIONS,
    get_latest_reviewer_action,
    record_lightweight_funding_reviewer_action,
)
from app.services.known_airport_funding_signal_creation import (
    create_signal_from_lightweight_funding_review,
)
from scripts.migrate_discovery_governed_evidence_slice1 import BACKUP_DIRECTORY, backup_database

DEFAULT_DATABASE = Path("data/runway_safe.db")

_APPROVE_SIGNAL = "APPROVE_SIGNAL"
_MARK_DUPLICATE = "MARK_DUPLICATE"

# Mirrors known_airport_funding_signal_creation._DISALLOWED_INITIAL_STATUSES
# exactly - deliberately reimplemented, not imported (that name is private
# to its own module, and this CLI must never import a private name across
# modules - same discipline that module's own docstring already
# establishes for its own reimplementation of the heavy path's equivalent).
_DISALLOWED_INITIAL_STATUSES = frozenset({"completed", "awarded", "executed", "contracted"})

_EXCERPT_MAX_LEN = 400

_DISCLAIMER = (
    "COMMANDER FUNDING REVIEW - lightweight known-Airport funding path only. "
    "PERSISTED CITATION != ACCEPTED EVIDENCE. This never runs IdentityGuard, "
    "Intelligence Review, or Promotion Policy, and never touches Research Loop / "
    "Discovery evidence (Source.external_id \"discovery:\") - only known-airport, "
    "funding-provenance evidence (Source.external_id \"faa_aip:\"/\"usaspending:\")."
)


class RefusedError(ValueError):
    """Raised for any refuse-to-proceed safety-gate failure this CLI itself
    checks before calling any service - distinct from the underlying
    services' own exceptions, which are caught and reported separately."""


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected an ISO date (YYYY-MM-DD), got {value!r}") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--source-assertion-id", required=True, type=int)
    parser.add_argument(
        "--action", required=True, choices=LIGHTWEIGHT_FUNDING_REVIEWER_ACTIONS,
        help="Exactly the lightweight funding subset - DEFER/CONFIRM_DISTINCT_SIGNAL remain "
        "available only via app.services.reviewer_action_persistence for heavy-pipeline rows.",
    )
    parser.add_argument("--reviewer", required=True, help="e.g. human:you. Required, non-empty.")
    parser.add_argument("--reason", required=True, help="Required, non-empty.")
    parser.add_argument(
        "--duplicate-of-signal-id", type=int, default=None,
        help="Required for --action MARK_DUPLICATE; invalid for every other action.",
    )
    parser.add_argument(
        "--supersedes-action-id", type=int, default=None,
        help="Optional: the ReviewerAction.id this new action supersedes (a human changing their mind).",
    )

    approve_group = parser.add_argument_group("APPROVE_SIGNAL fields (required only for --action APPROVE_SIGNAL)")
    approve_group.add_argument("--title", default=None)
    approve_group.add_argument("--category", default=None)
    approve_group.add_argument("--confidence", default=None, choices=sorted(DEFAULT_SCORE_BY_CONFIDENCE))
    approve_group.add_argument("--status", default=None)
    approve_group.add_argument("--notes", default=None)
    approve_group.add_argument("--source-notes", default=None)
    approve_group.add_argument("--target-year", type=int, default=None)
    approve_group.add_argument("--planning-year", type=int, default=None)
    approve_group.add_argument("--procurement-year", type=int, default=None)
    approve_group.add_argument("--construction-start", type=_iso_date, default=None)
    approve_group.add_argument("--completion-date", type=_iso_date, default=None)
    approve_group.add_argument("--manual-year-estimate", type=int, default=None)
    approve_group.add_argument("--last-verified-at", type=_iso_date, default=None)

    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-database-write", action="store_true")
    parser.add_argument("--skip-backup", action="store_true", help="isolated/temp DBs only")
    parser.add_argument("--backup-directory", type=Path, default=BACKUP_DIRECTORY)
    return parser




def _validate_approve_signal_fields(args: argparse.Namespace) -> None:
    if not args.title or not args.title.strip():
        raise RefusedError("--title is required and cannot be blank for --action APPROVE_SIGNAL")
    if not args.category or not args.category.strip():
        raise RefusedError("--category is required and cannot be blank for --action APPROVE_SIGNAL")
    if not args.confidence:
        raise RefusedError(f"--confidence is required for --action APPROVE_SIGNAL (one of {sorted(DEFAULT_SCORE_BY_CONFIDENCE)!r})")
    if args.status is not None and args.status.strip().lower() in _DISALLOWED_INITIAL_STATUSES:
        raise RefusedError(
            f"--status {args.status!r} is not a state human review approval alone can establish "
            f"(disallowed: {sorted(_DISALLOWED_INITIAL_STATUSES)!r})"
        )


def _resolve_reference_year_preview(args: argparse.Namespace) -> "Optional[int]":
    """Mirrors known_airport_funding_signal_creation._resolve_reference_year()
    byte-for-byte - reimplemented, not imported (that name is private to
    its own module), so this preview's reconciliation subject is built the
    identical way apply would build it."""
    for value in (args.target_year, args.planning_year, args.procurement_year):
        if value is not None:
            return value
    if args.construction_start is not None:
        return args.construction_start.year
    if args.completion_date is not None:
        return args.completion_date.year
    return None


def _print_header(session: Session, assertion: SourceAssertion, args: argparse.Namespace) -> None:
    airport = session.get(Airport, assertion.airport_id) if assertion.airport_id is not None else None
    source = session.get(Source, assertion.source_id)
    namespace = funding_namespace_for(source.external_id if source else None)

    print(_DISCLAIMER)
    print()
    print(f"SourceAssertion: {assertion.id}")
    print(f"Airport: {airport.id if airport else None} - {airport.name if airport else '(none)'} "
          f"({airport.iata_code if airport else '?'}/{airport.icao_code if airport else '?'})" if airport else "Airport: (none)")
    print(f"Source: {source.id if source else None} - {source.title if source else '(none)'}")
    print(f"Source.external_id: {source.external_id if source else None}")
    print(f"Funding provenance: {namespace or '(not a recognized funding namespace)'}")
    print(f"assertion_type: {assertion.assertion_type}")
    print(f"evidence_quality: {assertion.evidence_quality}  review_state: {assertion.review_state}")
    excerpt = (assertion.raw_relevant_text or "")[:_EXCERPT_MAX_LEN]
    print(f"Evidence excerpt: {excerpt}{'...' if len(assertion.raw_relevant_text or '') > _EXCERPT_MAX_LEN else ''}")
    print(f"linked_signal_id: {assertion.signal_id}")
    print()
    print(f"Requested action: {args.action}")
    print(f"Reviewer: {args.reviewer}")
    print(f"Reason: {args.reason}")
    if args.duplicate_of_signal_id is not None:
        print(f"duplicate_of_signal_id: {args.duplicate_of_signal_id}")


def _print_approve_signal_intended_fields(args: argparse.Namespace, assertion: SourceAssertion) -> None:
    print("\nIntended Signal fields if created:")
    print(f"  airport_id={assertion.airport_id}")
    print("  runway_id=None (always - no parameter exists for this on the lightweight path)")
    print(f"  source_id={assertion.source_id}")
    print(f"  title={args.title!r}")
    print(f"  category={args.category!r}")
    print(f"  confidence={args.confidence!r}  ->  probability_score={DEFAULT_SCORE_BY_CONFIDENCE.get(args.confidence)} (evidence strength, NOT a win/project probability)")
    print(f"  status={args.status!r}")
    print(f"  notes={args.notes!r}")
    print(f"  source_notes={args.source_notes!r}")
    print(f"  target_year={args.target_year}  planning_year={args.planning_year}  procurement_year={args.procurement_year}")
    print(f"  construction_start={args.construction_start}  completion_date={args.completion_date}")
    print(f"  manual_year_estimate={args.manual_year_estimate}  last_verified_at={args.last_verified_at}")
    print("  published=False (always)")
    print("Fields intentionally remaining unknown (no parameter exists for any of these on this path):")
    print("  supplier, likely_supplier, supplier_reason, estimated_total_value_usd, estimated_emas_value_usd")
    print("Grant amount (if any, in the evidence text above) is NOT copied into any Signal value field - "
          "grant amount != project total, grant amount != EMAS contract value.")


def _preview_approve_signal(session: Session, assertion: SourceAssertion, args: argparse.Namespace) -> None:
    """Read-only prediction, using the SAME pure R1/R2 reconciliation core
    the real apply path uses (never a mutating call rolled back). Mirrors
    create_signal_from_lightweight_funding_review()'s own control flow
    exactly: reconciliation first (POSSIBLE_EXISTING_SIGNAL_MATCH refuses
    unconditionally), then the existing-signal_id signature comparison."""
    reference_year = _resolve_reference_year_preview(args)
    subject = build_reconciliation_subject(assertion, (), category=args.category, reference_year=reference_year)
    candidates = find_reconciliation_candidates(session, assertion)
    decision = evaluate_existing_signal_reconciliation(subject, candidates)

    if decision.outcome == ExistingSignalReconciliationOutcome.POSSIBLE_EXISTING_SIGNAL_MATCH:
        print("\nAPPLY WOULD BE REFUSED: existing-Signal reconciliation match.")
        print(f"  Blocking Signal(s): {', '.join(str(i) for i in decision.candidate_signal_ids)}")
        for reason in decision.reasons:
            print(f"  - {reason}")
        return

    if assertion.signal_id is not None:
        existing = session.get(Signal, assertion.signal_id)
        if existing is None:
            print(f"\nAPPLY WOULD BE REFUSED: signal_id={assertion.signal_id} points to a Signal that no longer exists.")
            return
        existing_signature = (existing.title, existing.category, existing.confidence)
        requested_signature = (args.title, args.category, args.confidence)
        if existing_signature == requested_signature:
            print(f"\nEXISTING SIGNAL WOULD BE REUSED: Signal {existing.id} (identical title/category/confidence).")
        else:
            print(
                f"\nAPPLY WOULD BE REFUSED: signal_id={assertion.signal_id} already points to Signal "
                f"{existing.id} with different core fields (existing={existing_signature!r}, "
                f"requested={requested_signature!r}) - refusing to overwrite or create a second Signal."
            )
        return

    print("\nSIGNAL WOULD BE CREATED (pending the ReviewerAction this apply would also record).")
    _print_approve_signal_intended_fields(args, assertion)


def main(argv: "Sequence[str] | None" = None) -> int:
    args = _parser().parse_args(argv)

    if args.action == _MARK_DUPLICATE and args.duplicate_of_signal_id is None:
        print("Refused: --action MARK_DUPLICATE requires --duplicate-of-signal-id.", file=sys.stderr)
        return 2
    if args.action != _MARK_DUPLICATE and args.duplicate_of_signal_id is not None:
        print("Refused: --duplicate-of-signal-id is only valid with --action MARK_DUPLICATE.", file=sys.stderr)
        return 2
    if args.action == _APPROVE_SIGNAL:
        try:
            _validate_approve_signal_fields(args)
        except RefusedError as exc:
            print(f"Refused: {exc}", file=sys.stderr)
            return 2

    apply_requested = args.apply and args.allow_database_write
    if args.apply != args.allow_database_write:
        print("Refused: --apply and --allow-database-write are both required together for a write.", file=sys.stderr)
        return 2

    engine = create_engine(f"sqlite:///{args.database}")
    with Session(engine) as session:
        assertion = session.get(SourceAssertion, args.source_assertion_id)
        if assertion is None:
            print(f"Refused: no SourceAssertion with id={args.source_assertion_id!r} exists.", file=sys.stderr)
            return 2

        source = session.get(Source, assertion.source_id)
        source_external_id = source.external_id if source is not None else None

        _print_header(session, assertion, args)

        try:
            check_lightweight_funding_path_eligibility(assertion, source_external_id=source_external_id)
        except NotLightweightFundingAssertionError as exc:
            print(f"\nEligibility: FAIL - {exc}", file=sys.stderr)
            return 2
        print("\nEligibility: PASS")

        if args.action == _APPROVE_SIGNAL:
            _preview_approve_signal(session, assertion, args)
        else:
            print("\nIntended database changes:")
            print(f"  + ReviewerAction(action={args.action!r}, reviewer={args.reviewer!r}, reason={args.reason!r}"
                  + (f", duplicate_of_signal_id={args.duplicate_of_signal_id}" if args.duplicate_of_signal_id is not None else "") + ")")
            print("  Signal: none (only APPROVE_SIGNAL can create/reuse a Signal, and only in a separate, later apply)")

        if not apply_requested:
            print("\nMODE: PREVIEW - NO DATABASE WRITE")
            session.rollback()
            return 0

        # --- APPLY ---
        print("\nMODE: APPLY")
        if not args.skip_backup and args.database.exists():
            backup_path = backup_database(args.database, args.backup_directory)
            print(f"Backup written: {backup_path}")

        try:
            action_row = record_lightweight_funding_reviewer_action(
                session, assertion, action=args.action, reason=args.reason, reviewer=args.reviewer,
                supersedes_action_id=args.supersedes_action_id, duplicate_of_signal_id=args.duplicate_of_signal_id,
            )

            if args.action == _APPROVE_SIGNAL:
                try:
                    result = create_signal_from_lightweight_funding_review(
                        session, assertion,
                        title=args.title, category=args.category, confidence=args.confidence, status=args.status,
                        notes=args.notes, source_notes=args.source_notes, target_year=args.target_year,
                        planning_year=args.planning_year, procurement_year=args.procurement_year,
                        construction_start=args.construction_start, completion_date=args.completion_date,
                        manual_year_estimate=args.manual_year_estimate, last_verified_at=args.last_verified_at,
                    )
                except ExistingSignalPossibleMatchError as exc:
                    session.rollback()
                    print(f"\nAPPLY FAILED: existing-Signal reconciliation match - {exc}", file=sys.stderr)
                    return 1
                session.commit()
                print(f"\nReviewerAction recorded: id={action_row.id}")
                print(f"Signal {'created' if result.created else 'reused'}: id={result.signal.id}, published={result.signal.published}")
            else:
                session.commit()
                print(f"\nReviewerAction recorded: id={action_row.id}")
                print("No Signal created (not an APPROVE_SIGNAL action).")
        except (ValueError, NotLightweightFundingAssertionError) as exc:
            session.rollback()
            print(f"\nAPPLY FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        return 0


if __name__ == "__main__":
    raise SystemExit(main())
