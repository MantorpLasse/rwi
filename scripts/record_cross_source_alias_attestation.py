"""Human operator CLI for cross-source alias attestation
(docs/architecture, "RWI - Cross-Source Governed Airport Identity Binding -
Architecture Recon" mission's own Option C, the locked architecture this
capability implements).

    python -m scripts.record_cross_source_alias_attestation --database data/runway_safe.db \\
        --source-assertion-id 1 --matched-alias-id 1
        -> dry-run: shows the mandatory impact preview (matched alias
           provenance, literal-match result, current DB-local uniqueness
           result, source-independence result, current effective identity
           decision, predicted effective identity decision) and whether
           this attestation would be accepted. Never writes.

    ... --analyst human:you --reason "..." --allow-database-write
        -> the only invocation shape that writes: calls
           app.services.cross_source_alias_attestation.record_cross_source_alias_attestation()
           (imported, never reimplemented) exactly once. Eligibility is
           RECOMPUTED transactionally inside that same call, immediately
           before persistence - this CLI never trusts its own earlier,
           possibly-stale preview output as a substitute.

ONE SourceAssertion, ONE matched AirportAlias PER INVOCATION, NO BULK MODE
(matching every other governed CLI in this pipeline): there is no "attest
several assertions" or "scan for candidate matches" feature anywhere in
this script. The caller must explicitly name the exact AirportAlias id
being relied upon - this tool never silently auto-selects among aliases,
even when exactly one candidate exists for an Airport.

HUMAN APPROVAL IS MANDATORY (mission's own explicit Phase 17 instruction):
this tool never writes without an explicit --analyst and --reason, and
never without --allow-database-write. Eligibility alone (alias matches,
source differs, alias is currently unique) produces only a PROPOSAL - the
human is the one accepting the residual risk that RWI's own DB-local
uniqueness check cannot prove real-world global uniqueness, and that true
source independence is a judgment call this tool's mechanical check cannot
fully settle.

THIS TOOL NEVER MUTATES SourceAssertion.identity_guard_decision, never
creates an EvidenceBag or IdentityGuardEvaluation, and never touches
AirportAlias, AirportIdentifier, Airport, or Signal - only ever appends one
SourceAssertionCrossSourceAliasAttestation row.

NO DUPLICATED BUSINESS RULES: every precondition is enforced exactly once,
inside record_cross_source_alias_attestation()/
check_cross_source_alias_attestation_eligibility() themselves - this script
only ever calls them and prints whatever they report or raise.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.services.cross_source_alias_attestation import (
    AliasAirportMismatchError,
    AliasNotAdmittedError,
    AliasNotFoundError,
    AliasNotInAssertionEvidenceError,
    AmbiguousAliasAcrossAirportsError,
    DuplicateAttestationError,
    EmptyAnalystError,
    EmptyReasonError,
    NotCanonicallyAttachedError,
    NotIndependentSourceError,
    RawDecisionNotEligibleError,
    SignalAlreadyLinkedError,
    SourceAssertionNotFoundError,
    preview_cross_source_alias_attestation,
    record_cross_source_alias_attestation,
)

_KNOWN_ERRORS = (
    SourceAssertionNotFoundError,
    AliasNotFoundError,
    EmptyAnalystError,
    EmptyReasonError,
    NotCanonicallyAttachedError,
    SignalAlreadyLinkedError,
    AliasNotAdmittedError,
    AliasAirportMismatchError,
    RawDecisionNotEligibleError,
    AliasNotInAssertionEvidenceError,
    AmbiguousAliasAcrossAirportsError,
    NotIndependentSourceError,
    DuplicateAttestationError,
    ValueError,
)


@dataclass(frozen=True)
class RecordCrossSourceAliasAttestationConfig:
    database: Path
    source_assertion_id: int
    matched_alias_id: int
    analyst: Optional[str] = None
    reason: Optional[str] = None
    allow_database_write: bool = False


@dataclass
class RecordCrossSourceAliasAttestationResult:
    source_assertion_id: int
    matched_alias_id: int

    preview: "dict | None" = None

    proposed_analyst: Optional[str] = None
    proposed_reason: Optional[str] = None

    decision_eligible: bool = False
    decision_refusal_reason: Optional[str] = None
    written: bool = False
    written_attestation_id: Optional[int] = None

    blockers: "list[str]" = field(default_factory=list)


def run_record(config: RecordCrossSourceAliasAttestationConfig) -> RecordCrossSourceAliasAttestationResult:
    result = RecordCrossSourceAliasAttestationResult(
        source_assertion_id=config.source_assertion_id, matched_alias_id=config.matched_alias_id,
    )
    engine = create_engine(f"sqlite:///{config.database}", connect_args={"check_same_thread": False}, future=True)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    session = Session(engine)
    try:
        # Mandatory impact preview (shown BEFORE any eligibility/write
        # attempt), using the same pure computation the write path itself
        # recomputes transactionally.
        try:
            preview = preview_cross_source_alias_attestation(
                session, source_assertion_id=config.source_assertion_id, matched_alias_id=config.matched_alias_id,
            )
        except _KNOWN_ERRORS as exc:
            result.blockers.append(f"preview failed: {exc}")
            return result

        result.preview = {
            "matched_alias_text": preview.matched_alias_text,
            "matched_alias_source_id": preview.matched_alias_source_id,
            "matched_alias_status": preview.matched_alias_status,
            "target_airport_id": preview.target_airport_id,
            "being_attested_source_id": preview.being_attested_source_id,
            "raw_decision": preview.raw_decision,
            "current_effective_decision": preview.current_effective_decision,
            "current_effective_basis": preview.current_effective_basis,
            "literal_match": preview.literal_match,
            "alias_currently_unique": preview.alias_currently_unique,
            "current_alias_owner_airport_ids": preview.current_alias_owner_airport_ids,
            "source_independent": preview.source_independent,
            "already_has_attestation": preview.already_has_attestation,
            "eligible": preview.eligible,
            "refusal_reason": preview.refusal_reason,
            "predicted_effective_decision": preview.predicted_effective_decision,
            "predicted_effective_basis": preview.predicted_effective_basis,
            "would_change": preview.would_change,
        }
        result.decision_eligible = preview.eligible
        result.decision_refusal_reason = preview.refusal_reason

        if config.analyst is None and config.reason is None:
            return result

        result.proposed_analyst = config.analyst
        result.proposed_reason = config.reason

        for field_name, value in (("--analyst", config.analyst), ("--reason", config.reason)):
            if value is None or not value.strip():
                result.blockers.append(f"{field_name} is required to record an attestation")
        if result.blockers:
            return result

        if not preview.eligible:
            return result

        if not config.allow_database_write:
            return result

        try:
            write_result = record_cross_source_alias_attestation(
                session,
                source_assertion_id=config.source_assertion_id, matched_alias_id=config.matched_alias_id,
                analyst=config.analyst, reason=config.reason,
            )
        except _KNOWN_ERRORS as exc:
            result.decision_eligible = False
            result.decision_refusal_reason = str(exc)
            return result

        session.commit()
        result.written = True
        result.written_attestation_id = write_result.attestation_id
        return result
    finally:
        session.close()


def render_result(result: RecordCrossSourceAliasAttestationResult) -> str:
    lines: "list[str]" = []
    lines.append(f"SourceAssertion id: {result.source_assertion_id}   matched AirportAlias id: {result.matched_alias_id}")

    if result.blockers:
        for blocker in result.blockers:
            lines.append(f"BLOCKED: {blocker}")
        return "\n".join(lines) + "\n"

    p = result.preview or {}
    lines.append("")
    lines.append("MANDATORY IMPACT PREVIEW (never writes)")
    lines.append(f"  matched alias: {p.get('matched_alias_text')!r}  status={p.get('matched_alias_status')}  admitting source_id={p.get('matched_alias_source_id')}")
    lines.append(f"  target airport_id: {p.get('target_airport_id')}")
    lines.append(f"  being-attested source_id: {p.get('being_attested_source_id')}")
    lines.append(f"  raw identity_guard_decision: {p.get('raw_decision')}")
    lines.append(f"  current effective decision: {p.get('current_effective_decision')}  basis={p.get('current_effective_basis')}")
    lines.append(f"  literal alias match in preserved evidence: {p.get('literal_match')}")
    lines.append(f"  alias currently unique across Airports: {p.get('alias_currently_unique')}  (current owner airport_ids={p.get('current_alias_owner_airport_ids')})")
    lines.append(f"  source independent (mechanical check only - human judgment still required): {p.get('source_independent')}")
    lines.append(f"  already has an attestation: {p.get('already_has_attestation')}")
    lines.append(f"  predicted effective decision if recorded: {p.get('predicted_effective_decision')}  basis={p.get('predicted_effective_basis')}")
    lines.append(f"  would change current effective decision: {p.get('would_change')}")
    lines.append("")
    lines.append(f"  eligible: {result.decision_eligible}")
    if result.decision_refusal_reason:
        lines.append(f"  refused: {result.decision_refusal_reason}")

    if result.proposed_analyst is not None or result.proposed_reason is not None:
        lines.append("")
        lines.append("Proposed attestation (HUMAN GOVERNANCE ONLY - never re-derives identity evidence):")
        lines.append(f"  analyst: {result.proposed_analyst!r}")
        lines.append(f"  reason: {result.proposed_reason!r}")
        if result.written:
            lines.append(f"  WRITTEN: SourceAssertionCrossSourceAliasAttestation #{result.written_attestation_id}")
            lines.append(
                "  REMINDER: SourceAssertion.identity_guard_decision was NOT mutated - this is a separate, "
                "EB5-consumed fact; raw identity remains historical."
            )
        elif result.decision_eligible:
            lines.append("  DRY RUN - no write performed (pass --allow-database-write to record this attestation)")

    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--database", type=Path, required=True,
        help="Path to the SQLite database. No default - never the real production database by accident.",
    )
    parser.add_argument("--source-assertion-id", type=int, required=True, dest="source_assertion_id")
    parser.add_argument("--matched-alias-id", type=int, required=True, dest="matched_alias_id")
    parser.add_argument("--analyst", type=str, default=None)
    parser.add_argument("--reason", type=str, default=None)
    parser.add_argument("--allow-database-write", action="store_true")
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = _parser().parse_args(argv)
    config = RecordCrossSourceAliasAttestationConfig(
        database=args.database, source_assertion_id=args.source_assertion_id,
        matched_alias_id=args.matched_alias_id, analyst=args.analyst, reason=args.reason,
        allow_database_write=args.allow_database_write,
    )
    result = run_record(config)
    print(render_result(result))
    if result.blockers:
        return 1
    if result.proposed_analyst is not None and not result.decision_eligible:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
