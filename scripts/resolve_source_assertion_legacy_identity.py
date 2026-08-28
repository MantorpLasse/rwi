"""Human operator CLI for legacy-attached SourceAssertion identity
attestation (docs/architecture/rwi-legacy-attached-sourceassertion-identity-
governance-design.md, locked design contract for this capability).

    python -m scripts.resolve_source_assertion_legacy_identity --database data/runway_safe.db \\
        --source-assertion-id 81
        -> pure inspection: assertion identity, current airport attachment,
           raw preserved evidence, target Airport's own canonical
           identifiers, the CURRENT EB5 effective-identity result, existing
           legacy-attestation history, eligibility status, and a preview of
           the review-time snapshot/hash this tool would record. Never
           writes.

    python -m scripts.resolve_source_assertion_legacy_identity --database ... --source-assertion-id 81 \\
        --decision CONFIRM_EXISTING_ATTACHMENT --matched-airport-id 63 \\
        --reviewer human:you --reason "..."
        -> dry-run: shows whether this decision would be accepted (via a
           rolled-back SAVEPOINT, so eligibility is authoritative, never
           re-derived by this CLI). Never writes.

    python -m scripts.resolve_source_assertion_legacy_identity --database ... --source-assertion-id 81 \\
        --decision CONFIRM_EXISTING_ATTACHMENT --matched-airport-id 63 \\
        --reviewer human:you --reason "..." --allow-database-write
        -> the only invocation shape that writes: calls
           app.services.source_assertion_legacy_identity_attestation.record_legacy_identity_attestation()
           (imported, never reimplemented) exactly once.

ONE ASSERTION PER INVOCATION, NO BULK MODE (design doc S10's own explicit
no-go boundary, and this mission's own repeated instruction): there is no
"list all eligible rows and confirm" feature anywhere in this script, by
design - the real database's own blast radius for this legacy class is 216
rows (docs/architecture/rwi-legacy-attached-sourceassertion-identity-
governance-design.md S9), and a bulk-confirm shortcut here would be exactly
the "human can attach any assertion to any Airport" bypass that design
document's own security analysis (S7) warns against.

NO DUPLICATED BUSINESS RULES: every precondition (not legacy-attached,
already modernly governed, missing evidence, missing Airport, action/target
consistency, empty reviewer/reason, conflicting-reversal-without-explicit-
supersession) is enforced exactly once, inside
record_legacy_identity_attestation()/check_legacy_attestation_eligibility()
themselves - this script only ever calls them and prints whatever they
raise.

REMINDER, NOT A GATE: after a successful CONFIRM_EXISTING_ATTACHMENT write,
this CLI prints a plain reminder that Signal-track eligibility still
requires the assertion's own next gate (intelligence review) to clear
separately - which, for the real USAspending-grant class this mission's own
control case belongs to, currently has no committed claims extractor at
all (design doc S8's own explicit statement) - display-only, never implied
to be automatically resolved by this tool.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.models import Airport, SourceAssertion
from app.models.source_assertion_legacy_identity_attestation import (
    SOURCE_ASSERTION_LEGACY_IDENTITY_ATTESTATION_ACTIONS,
    SourceAssertionLegacyIdentityAttestation,
)
from app.services.effective_identity_guard_decision import resolve_effective_identity_guard_decision
from app.services.source_assertion_legacy_identity_attestation import (
    ConflictingAttestationRequiresSupersessionError,
    MissingReviewableEvidenceError,
    ModernEvidenceBagExistsError,
    ModernIdentityGuardAlreadyRanError,
    NotLegacyAttachedError,
    SignalAlreadyLinkedError,
    SourceAssertionNotFoundError,
    TargetAirportMismatchError,
    TargetAirportNotFoundError,
    build_review_snapshot_payload,
    check_legacy_attestation_eligibility,
    get_latest_legacy_identity_attestation,
    hash_review_snapshot,
    record_legacy_identity_attestation,
    serialize_review_snapshot,
)

_CONFIRM_ACTION = "CONFIRM_EXISTING_ATTACHMENT"

_KNOWN_ERRORS = (
    SourceAssertionNotFoundError,
    NotLegacyAttachedError,
    ModernIdentityGuardAlreadyRanError,
    ModernEvidenceBagExistsError,
    SignalAlreadyLinkedError,
    MissingReviewableEvidenceError,
    TargetAirportNotFoundError,
    TargetAirportMismatchError,
    ConflictingAttestationRequiresSupersessionError,
    ValueError,
)


@dataclass(frozen=True)
class ResolveLegacyIdentityConfig:
    database: Path
    source_assertion_id: int
    decision: Optional[str] = None
    reviewer: Optional[str] = None
    reason: Optional[str] = None
    matched_airport_id: Optional[int] = None
    supersedes_attestation_id: Optional[int] = None
    allow_database_write: bool = False


@dataclass
class ResolveLegacyIdentityResult:
    source_assertion_id: int
    assertion_found: bool = False
    airport_id: Optional[int] = None
    airport_name: Optional[str] = None
    airport_iata: Optional[str] = None
    airport_icao: Optional[str] = None
    source_id: Optional[int] = None
    source_type: Optional[str] = None
    parser_identifier: Optional[str] = None
    raw_relevant_text: Optional[str] = None
    raw_product_type: Optional[str] = None
    assertion_type: Optional[str] = None
    evidence_quality: Optional[str] = None
    identity_guard_decision: Optional[str] = None
    signal_id: Optional[int] = None
    eligible: bool = False
    eligibility_blocker: Optional[str] = None
    current_effective_decision: Optional[str] = None
    current_effective_basis: Optional[str] = None
    current_is_identity_confirmed: bool = False
    attestation_history: "list[dict]" = field(default_factory=list)
    preview_snapshot_hash: Optional[str] = None

    proposed_decision: Optional[str] = None
    proposed_matched_airport_id: Optional[int] = None
    proposed_matched_airport_name: Optional[str] = None
    proposed_reviewer: Optional[str] = None
    proposed_reason: Optional[str] = None
    proposed_supersedes_attestation_id: Optional[int] = None
    decision_eligible: bool = False
    decision_refusal_reason: Optional[str] = None
    is_reversal: bool = False
    written: bool = False
    written_attestation_id: Optional[int] = None

    blockers: "list[str]" = field(default_factory=list)


def run_resolve(config: ResolveLegacyIdentityConfig) -> ResolveLegacyIdentityResult:
    result = ResolveLegacyIdentityResult(source_assertion_id=config.source_assertion_id)
    engine = create_engine(f"sqlite:///{config.database}", connect_args={"check_same_thread": False}, future=True)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    session = Session(engine)
    try:
        assertion = session.get(SourceAssertion, config.source_assertion_id)
        if assertion is None:
            result.blockers.append(f"SourceAssertion {config.source_assertion_id} does not exist")
            return result

        result.assertion_found = True
        result.airport_id = assertion.airport_id
        result.source_id = assertion.source_id
        result.parser_identifier = assertion.parser_identifier
        result.raw_relevant_text = assertion.raw_relevant_text
        result.raw_product_type = assertion.raw_product_type
        result.assertion_type = assertion.assertion_type
        result.evidence_quality = assertion.evidence_quality
        result.identity_guard_decision = assertion.identity_guard_decision
        result.signal_id = assertion.signal_id

        if assertion.source is not None:
            result.source_type = assertion.source.source_type

        airport = session.get(Airport, assertion.airport_id) if assertion.airport_id is not None else None
        if airport is not None:
            result.airport_name = airport.name
            result.airport_iata = airport.iata_code
            result.airport_icao = airport.icao_code
            payload = build_review_snapshot_payload(assertion, airport)
            result.preview_snapshot_hash = hash_review_snapshot(serialize_review_snapshot(payload))

        try:
            check_legacy_attestation_eligibility(session, assertion)
            result.eligible = True
        except _KNOWN_ERRORS as exc:
            result.eligible = False
            result.eligibility_blocker = str(exc)

        effective = resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)
        result.current_effective_decision = effective.effective_decision.value
        result.current_effective_basis = effective.basis.value
        result.current_is_identity_confirmed = effective.is_identity_confirmed

        rows = (
            session.query(SourceAssertionLegacyIdentityAttestation)
            .filter(SourceAssertionLegacyIdentityAttestation.source_assertion_id == assertion.id)
            .order_by(
                SourceAssertionLegacyIdentityAttestation.created_at.asc(),
                SourceAssertionLegacyIdentityAttestation.id.asc(),
            )
            .all()
        )
        result.attestation_history = [
            {
                "id": row.id,
                "action": row.action,
                "matched_airport_id": row.matched_airport_id,
                "reviewer": row.reviewer,
                "reason": row.reason,
                "supersedes_attestation_id": row.supersedes_attestation_id,
                "created_at": row.created_at,
            }
            for row in rows
        ]

        if config.decision is None:
            return result

        if config.decision not in SOURCE_ASSERTION_LEGACY_IDENTITY_ATTESTATION_ACTIONS:
            result.blockers.append(
                f"--decision must be one of {SOURCE_ASSERTION_LEGACY_IDENTITY_ATTESTATION_ACTIONS!r}, "
                f"got {config.decision!r}"
            )
            return result

        result.proposed_decision = config.decision
        result.proposed_matched_airport_id = config.matched_airport_id
        result.proposed_reviewer = config.reviewer
        result.proposed_reason = config.reason
        result.proposed_supersedes_attestation_id = config.supersedes_attestation_id

        if config.matched_airport_id is not None:
            matched = session.get(Airport, config.matched_airport_id)
            result.proposed_matched_airport_name = matched.name if matched else None

        if not config.reviewer or not config.reviewer.strip():
            result.blockers.append("--reviewer is required with --decision")
            return result
        if not config.reason or not config.reason.strip():
            result.blockers.append("--reason is required with --decision")
            return result

        latest = get_latest_legacy_identity_attestation(session, assertion.id)
        result.is_reversal = (
            latest is not None
            and latest.action in ("CONFIRM_EXISTING_ATTACHMENT", "REJECT_EXISTING_ATTACHMENT")
            and config.decision in ("CONFIRM_EXISTING_ATTACHMENT", "REJECT_EXISTING_ATTACHMENT")
            and latest.action != config.decision
        )

        try:
            savepoint = session.begin_nested()
            try:
                record_legacy_identity_attestation(
                    session,
                    source_assertion_id=config.source_assertion_id,
                    action=config.decision,
                    reason=config.reason,
                    reviewer=config.reviewer,
                    matched_airport_id=config.matched_airport_id,
                    supersedes_attestation_id=config.supersedes_attestation_id,
                )
            finally:
                savepoint.rollback()
            result.decision_eligible = True
        except _KNOWN_ERRORS as exc:
            result.decision_eligible = False
            result.decision_refusal_reason = str(exc)
            return result

        if not config.allow_database_write:
            return result

        write_result = record_legacy_identity_attestation(
            session,
            source_assertion_id=config.source_assertion_id,
            action=config.decision,
            reason=config.reason,
            reviewer=config.reviewer,
            matched_airport_id=config.matched_airport_id,
            supersedes_attestation_id=config.supersedes_attestation_id,
        )
        session.commit()
        result.written = True
        result.written_attestation_id = write_result.attestation_id
        result.attestation_history = result.attestation_history + [
            {
                "id": write_result.attestation_id,
                "action": write_result.action,
                "matched_airport_id": write_result.matched_airport_id,
                "reviewer": config.reviewer,
                "reason": config.reason,
                "supersedes_attestation_id": write_result.superseded_attestation_id,
                "created_at": None,
            }
        ]
        effective_after = resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)
        result.current_effective_decision = effective_after.effective_decision.value
        result.current_effective_basis = effective_after.basis.value
        result.current_is_identity_confirmed = effective_after.is_identity_confirmed
        return result
    finally:
        session.close()


def render_result(result: ResolveLegacyIdentityResult) -> str:
    lines: "list[str]" = []
    lines.append(f"SourceAssertion id: {result.source_assertion_id}")

    if result.blockers:
        for blocker in result.blockers:
            lines.append(f"BLOCKED: {blocker}")
        return "\n".join(lines) + "\n"

    lines.append("")
    lines.append("SOURCE ASSERTION (permanent historical fact, never mutated by this tool)")
    lines.append(f"  airport_id: {result.airport_id}  ({result.airport_name!r}, IATA={result.airport_iata}, ICAO={result.airport_icao})")
    lines.append(f"  source_id: {result.source_id}  source_type={result.source_type!r}  parser_identifier={result.parser_identifier!r}")
    lines.append(f"  assertion_type: {result.assertion_type!r}  evidence_quality: {result.evidence_quality!r}")
    lines.append(f"  identity_guard_decision (modern, if any): {result.identity_guard_decision!r}")
    lines.append(f"  signal_id: {result.signal_id}")
    lines.append(f"  raw_product_type: {result.raw_product_type!r}")
    lines.append(f"  raw_relevant_text: {result.raw_relevant_text!r}")

    lines.append("")
    lines.append(f"ELIGIBILITY FOR THIS MECHANISM: {'YES' if result.eligible else 'NO'}")
    if result.eligibility_blocker:
        lines.append(f"  blocked: {result.eligibility_blocker}")
    lines.append(f"  review-time snapshot preview hash: {result.preview_snapshot_hash}")

    lines.append("")
    lines.append("CURRENT EB5 EFFECTIVE IDENTITY (before any write this invocation performs)")
    lines.append(f"  effective_decision: {result.current_effective_decision}")
    lines.append(f"  basis: {result.current_effective_basis}")
    lines.append(f"  is_identity_confirmed: {result.current_is_identity_confirmed}")

    lines.append("")
    lines.append(f"LEGACY ATTESTATION HISTORY ({len(result.attestation_history)} total)")
    if not result.attestation_history:
        lines.append("  (none)")
    for row in result.attestation_history:
        lines.append(
            f"  #{row['id']} action={row['action']} matched_airport_id={row['matched_airport_id']} "
            f"reviewer={row['reviewer']!r} reason={row['reason']!r} "
            f"supersedes={row['supersedes_attestation_id']} created_at={row['created_at']}"
        )

    if result.proposed_decision is not None:
        lines.append("")
        lines.append(f"Proposed attestation: {result.proposed_decision}")
        lines.append(f"  matched_airport_id: {result.proposed_matched_airport_id}")
        if result.proposed_matched_airport_id is not None:
            lines.append(f"  matched_airport_name: {result.proposed_matched_airport_name!r}")
        lines.append(f"  reviewer: {result.proposed_reviewer!r}")
        lines.append(f"  reason: {result.proposed_reason!r}")
        lines.append(f"  supersedes_attestation_id: {result.proposed_supersedes_attestation_id}")
        lines.append(f"  is_reversal_of_latest: {result.is_reversal}")
        lines.append(f"  eligible: {result.decision_eligible}")
        if result.decision_refusal_reason:
            lines.append(f"  refused: {result.decision_refusal_reason}")
        if result.written:
            lines.append(f"  WRITTEN: SourceAssertionLegacyIdentityAttestation #{result.written_attestation_id}")
            lines.append(f"  EB5 effective_decision is now: {result.current_effective_decision} (basis={result.current_effective_basis})")
            if result.proposed_decision == _CONFIRM_ACTION:
                lines.append(
                    "  REMINDER: identity confirmation alone does not clear intelligence review - that gate "
                    "still requires a source-family claims extractor this mechanism deliberately does not "
                    "provide (none exists yet for usaspending_grant-sourced rows)."
                )
        elif result.decision_eligible:
            lines.append("  DRY RUN - no write performed (pass --allow-database-write to record this attestation)")

    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--database", type=Path, required=True,
        help="Path to the SQLite database, read-only unless --decision is also given with "
        "--allow-database-write. No default - never the real production database by accident.",
    )
    parser.add_argument("--source-assertion-id", type=int, required=True, dest="source_assertion_id")
    parser.add_argument("--decision", choices=SOURCE_ASSERTION_LEGACY_IDENTITY_ATTESTATION_ACTIONS, default=None)
    parser.add_argument("--reviewer", type=str, default=None)
    parser.add_argument("--reason", type=str, default=None)
    parser.add_argument("--matched-airport-id", type=int, default=None, dest="matched_airport_id")
    parser.add_argument(
        "--supersedes-attestation-id", type=int, default=None, dest="supersedes_attestation_id",
        help="Required when --decision contradicts the latest existing attestation's own action "
        "(CONFIRM_EXISTING_ATTACHMENT <-> REJECT_EXISTING_ATTACHMENT) - must equal that latest "
        "attestation's own id, proving the reversal is deliberate.",
    )
    parser.add_argument("--allow-database-write", action="store_true")
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = _parser().parse_args(argv)
    config = ResolveLegacyIdentityConfig(
        database=args.database,
        source_assertion_id=args.source_assertion_id,
        decision=args.decision,
        reviewer=args.reviewer,
        reason=args.reason,
        matched_airport_id=args.matched_airport_id,
        supersedes_attestation_id=args.supersedes_attestation_id,
        allow_database_write=args.allow_database_write,
    )
    result = run_resolve(config)
    print(render_result(result))
    if result.blockers:
        return 1
    if result.proposed_decision is not None and not result.decision_eligible:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
