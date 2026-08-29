"""Human operator CLI for first-class manual identity evidence
(docs/architecture, "RWI - New Source Family Manual Identity Evidence -
Architecture Design" mission, the locked design this capability
implements).

    python -m scripts.record_manual_identity_evidence --database data/runway_safe.db \\
        --source-assertion-id 232
        -> pure inspection: SourceAssertion identity, current canonical
           Airport (derived solely from SourceAssertion.airport_id, never
           caller-selectable), the preserved raw_relevant_text, baseline
           eligibility for this mechanism, and any existing
           ManualIdentityEvidence history. Never writes.

    python -m scripts.record_manual_identity_evidence --database ... --source-assertion-id 232 \\
        --source-id 82 --analyst human:you \\
        --evidence-excerpt "..." [--raw-airport-name ...] [--raw-country ...] \\
        [--raw-city ...] [--raw-identifier-code ...]
        -> dry-run: shows whether this transcription would be accepted (via
           a rolled-back SAVEPOINT, so eligibility/validation is
           authoritative, never re-derived by this CLI) and, if so, what
           IdentityGuard outcome it would ACTUALLY produce. Never writes.

    ... --allow-database-write
        -> the only invocation shape that writes: calls
           app.services.manual_identity_evidence.record_manual_identity_evidence()
           (imported, never reimplemented) exactly once.

THIS TOOL NEVER SHOWS OR ASKS FOR AN IDENTITY-DECISION CHOICE - there is no
`--decision`/`--outcome`/`--confirm` flag anywhere in this script. The
analyst supplies evidence only; the real, unmodified IdentityGuard
(app.services.evidence_attachment_guard.evaluate_attachment_for_candidates())
computes the outcome.

ONE ASSERTION PER INVOCATION, NO BULK MODE (mission's own repeated,
explicit instruction, matching scripts/resolve_source_assertion_legacy_identity.py's
own identical boundary): there is no "list all eligible rows" feature
anywhere in this script.

NO WEB FETCHING: this tool only ever reads what is already preserved on the
SourceAssertion row itself (`raw_relevant_text`) - it never fetches a URL,
by design.

CLI INPUT SAFETY (Phase 16): the submitted `--evidence-excerpt` must be
contained within the assertion's own preserved `raw_relevant_text` after
the same conservative normalization
app.services.manual_identity_evidence.excerpt_contains_value() uses
elsewhere - refused with a clear reason otherwise (e.g. `raw_relevant_text`
is empty/NULL, or the excerpt does not actually appear in it). This is a
CLI-level safety layer IN ADDITION TO, never a replacement for, the
service's own raw_*-field-within-excerpt validation.

NO DUPLICATED BUSINESS RULES: every precondition (Source binding, canonical
airport_id presence, not-already-governed, no existing EvidenceBag, no
existing ManualIdentityEvidence, no linked Signal, literal-transcription
containment) is enforced exactly once, inside
record_manual_identity_evidence()/check_manual_identity_evidence_eligibility()
themselves - this script only ever calls them and prints whatever they
raise.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.models import Airport, SourceAssertion
from app.models.manual_identity_evidence import (
    MANUAL_IDENTITY_EVIDENCE_EXTRACTION_MODES,
    ManualIdentityEvidence,
)
from app.services.effective_identity_guard_decision import resolve_effective_identity_guard_decision
from app.services.manual_identity_evidence import (
    EmptyEvidenceExcerptError,
    ExistingEvidenceBagError,
    ExistingManualIdentityEvidenceError,
    MissingCanonicalAirportError,
    ModernIdentityGuardAlreadyGovernedError,
    RawFieldNotInExcerptError,
    SignalAlreadyLinkedError,
    SourceAssertionNotFoundError,
    SourceMismatchError,
    SourceNotFoundError,
    TargetAirportNotFoundError,
    UnresolvedUnknownAirportCandidateError,
    check_manual_identity_evidence_eligibility,
    excerpt_contains_value,
    record_manual_identity_evidence,
)

_KNOWN_ERRORS = (
    SourceAssertionNotFoundError,
    SourceNotFoundError,
    SourceMismatchError,
    MissingCanonicalAirportError,
    UnresolvedUnknownAirportCandidateError,
    ModernIdentityGuardAlreadyGovernedError,
    ExistingEvidenceBagError,
    ExistingManualIdentityEvidenceError,
    SignalAlreadyLinkedError,
    TargetAirportNotFoundError,
    EmptyEvidenceExcerptError,
    RawFieldNotInExcerptError,
    ValueError,
)


@dataclass(frozen=True)
class RecordManualIdentityEvidenceConfig:
    database: Path
    source_assertion_id: int
    source_id: Optional[int] = None
    analyst: Optional[str] = None
    evidence_excerpt: Optional[str] = None
    raw_airport_name: Optional[str] = None
    raw_country: Optional[str] = None
    raw_city: Optional[str] = None
    raw_identifier_code: Optional[str] = None
    extraction_mode: str = "HUMAN_TRANSCRIPTION"
    allow_database_write: bool = False


@dataclass
class RecordManualIdentityEvidenceResult:
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
    baseline_eligible: bool = False
    baseline_eligibility_blocker: Optional[str] = None
    existing_manual_evidence_id: Optional[int] = None
    current_effective_decision: Optional[str] = None
    current_effective_basis: Optional[str] = None

    proposed_source_id: Optional[int] = None
    proposed_analyst: Optional[str] = None
    proposed_evidence_excerpt: Optional[str] = None
    proposed_raw_airport_name: Optional[str] = None
    proposed_raw_country: Optional[str] = None
    proposed_raw_city: Optional[str] = None
    proposed_raw_identifier_code: Optional[str] = None
    proposed_extraction_mode: Optional[str] = None
    excerpt_contained_in_raw_relevant_text: Optional[bool] = None
    decision_eligible: bool = False
    decision_refusal_reason: Optional[str] = None
    written: bool = False
    written_manual_identity_evidence_id: Optional[int] = None
    written_evidence_bag_snapshot_id: Optional[int] = None
    identity_guard_decision_after: Optional[str] = None
    identity_guard_reason_after: Optional[str] = None
    effective_decision_after: Optional[str] = None
    effective_basis_after: Optional[str] = None

    blockers: "list[str]" = field(default_factory=list)


def run_record(config: RecordManualIdentityEvidenceConfig) -> RecordManualIdentityEvidenceResult:
    result = RecordManualIdentityEvidenceResult(source_assertion_id=config.source_assertion_id)
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

        existing = session.scalar(
            select(ManualIdentityEvidence.id).where(ManualIdentityEvidence.source_assertion_id == assertion.id)
        )
        result.existing_manual_evidence_id = existing

        # Baseline eligibility (using the assertion's own real source_id -
        # never the caller's proposed one) - "would this row be eligible
        # for this mechanism at all", independent of any specific proposed
        # transcription.
        try:
            check_manual_identity_evidence_eligibility(session, assertion, source_id=assertion.source_id)
            result.baseline_eligible = True
        except _KNOWN_ERRORS as exc:
            result.baseline_eligible = False
            result.baseline_eligibility_blocker = str(exc)

        effective = resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)
        result.current_effective_decision = effective.effective_decision.value
        result.current_effective_basis = effective.basis.value

        if config.evidence_excerpt is None and config.analyst is None and config.source_id is None:
            return result

        result.proposed_source_id = config.source_id
        result.proposed_analyst = config.analyst
        result.proposed_evidence_excerpt = config.evidence_excerpt
        result.proposed_raw_airport_name = config.raw_airport_name
        result.proposed_raw_country = config.raw_country
        result.proposed_raw_city = config.raw_city
        result.proposed_raw_identifier_code = config.raw_identifier_code
        result.proposed_extraction_mode = config.extraction_mode

        if config.source_id is None:
            result.blockers.append("--source-id is required to propose a transcription")
            return result
        if not config.analyst or not config.analyst.strip():
            result.blockers.append("--analyst is required to propose a transcription")
            return result
        if not config.evidence_excerpt or not config.evidence_excerpt.strip():
            result.blockers.append("--evidence-excerpt is required to propose a transcription")
            return result
        if config.extraction_mode not in MANUAL_IDENTITY_EVIDENCE_EXTRACTION_MODES:
            result.blockers.append(
                f"--extraction-mode must be one of {MANUAL_IDENTITY_EVIDENCE_EXTRACTION_MODES!r}"
            )
            return result

        # Phase 16 CLI input safety: the excerpt must actually be
        # findable within the assertion's own preserved evidence - never
        # fetched from the web, never trusted merely because the analyst
        # typed it.
        if not assertion.raw_relevant_text or not assertion.raw_relevant_text.strip():
            result.excerpt_contained_in_raw_relevant_text = False
            result.blockers.append(
                "SourceAssertion has no preserved raw_relevant_text to verify the excerpt against - "
                "refusing to proceed."
            )
            return result
        result.excerpt_contained_in_raw_relevant_text = excerpt_contains_value(
            assertion.raw_relevant_text, config.evidence_excerpt,
        )
        if not result.excerpt_contained_in_raw_relevant_text:
            result.blockers.append(
                "--evidence-excerpt does not occur within SourceAssertion.raw_relevant_text - refusing "
                "to proceed with an excerpt that cannot be verified against the preserved source evidence."
            )
            return result

        try:
            savepoint = session.begin_nested()
            try:
                record_manual_identity_evidence(
                    session,
                    source_assertion_id=config.source_assertion_id,
                    source_id=config.source_id,
                    evidence_excerpt=config.evidence_excerpt,
                    analyst=config.analyst,
                    raw_airport_name=config.raw_airport_name,
                    raw_country=config.raw_country,
                    raw_city=config.raw_city,
                    raw_identifier_code=config.raw_identifier_code,
                    extraction_mode=config.extraction_mode,
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

        write_result = record_manual_identity_evidence(
            session,
            source_assertion_id=config.source_assertion_id,
            source_id=config.source_id,
            evidence_excerpt=config.evidence_excerpt,
            analyst=config.analyst,
            raw_airport_name=config.raw_airport_name,
            raw_country=config.raw_country,
            raw_city=config.raw_city,
            raw_identifier_code=config.raw_identifier_code,
            extraction_mode=config.extraction_mode,
        )
        session.commit()
        result.written = True
        result.written_manual_identity_evidence_id = write_result.manual_identity_evidence_id
        result.written_evidence_bag_snapshot_id = write_result.evidence_bag_snapshot_id
        result.identity_guard_decision_after = write_result.identity_guard_decision
        result.identity_guard_reason_after = write_result.identity_guard_reason

        effective_after = resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)
        result.effective_decision_after = effective_after.effective_decision.value
        result.effective_basis_after = effective_after.basis.value
        result.current_effective_decision = effective_after.effective_decision.value
        result.current_effective_basis = effective_after.basis.value
        return result
    finally:
        session.close()


def render_result(result: RecordManualIdentityEvidenceResult) -> str:
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
    lines.append(f"  identity_guard_decision (current, if any): {result.identity_guard_decision!r}")
    lines.append(f"  signal_id: {result.signal_id}")
    lines.append(f"  raw_product_type: {result.raw_product_type!r}")
    lines.append(f"  raw_relevant_text: {result.raw_relevant_text!r}")

    lines.append("")
    lines.append(f"BASELINE ELIGIBILITY FOR THIS MECHANISM: {'YES' if result.baseline_eligible else 'NO'}")
    if result.baseline_eligibility_blocker:
        lines.append(f"  blocked: {result.baseline_eligibility_blocker}")
    lines.append(f"  existing ManualIdentityEvidence id: {result.existing_manual_evidence_id}")

    lines.append("")
    lines.append("CURRENT EB5 EFFECTIVE IDENTITY (before any write this invocation performs)")
    lines.append(f"  effective_decision: {result.current_effective_decision}")
    lines.append(f"  basis: {result.current_effective_basis}")

    if result.proposed_evidence_excerpt is not None or result.proposed_analyst is not None or result.proposed_source_id is not None:
        lines.append("")
        lines.append("Proposed transcription (HUMAN EVIDENCE ONLY - no identity decision is ever accepted here):")
        lines.append(f"  source_id: {result.proposed_source_id}")
        lines.append(f"  analyst: {result.proposed_analyst!r}")
        lines.append(f"  extraction_mode: {result.proposed_extraction_mode!r}")
        lines.append(f"  evidence_excerpt: {result.proposed_evidence_excerpt!r}")
        lines.append(f"  raw_airport_name: {result.proposed_raw_airport_name!r}")
        lines.append(f"  raw_country: {result.proposed_raw_country!r}")
        lines.append(f"  raw_city: {result.proposed_raw_city!r}")
        lines.append(f"  raw_identifier_code: {result.proposed_raw_identifier_code!r}")
        lines.append(f"  excerpt_contained_in_raw_relevant_text: {result.excerpt_contained_in_raw_relevant_text}")
        lines.append(f"  eligible: {result.decision_eligible}")
        if result.decision_refusal_reason:
            lines.append(f"  refused: {result.decision_refusal_reason}")
        if result.written:
            lines.append(f"  WRITTEN: ManualIdentityEvidence #{result.written_manual_identity_evidence_id}")
            lines.append(f"  SourceAssertionEvidenceBag #{result.written_evidence_bag_snapshot_id} created")
            lines.append(
                f"  IdentityGuard result (computed by the real, unmodified guard - never chosen by this "
                f"tool or the analyst): {result.identity_guard_decision_after}"
            )
            lines.append(f"    reason: {result.identity_guard_reason_after!r}")
            lines.append(
                f"  EB5 effective_decision is now: {result.effective_decision_after} "
                f"(basis={result.effective_basis_after})"
            )
        elif result.decision_eligible:
            lines.append("  DRY RUN - no write performed (pass --allow-database-write to record this evidence)")

    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--database", type=Path, required=True,
        help="Path to the SQLite database. No default - never the real production database by accident.",
    )
    parser.add_argument("--source-assertion-id", type=int, required=True, dest="source_assertion_id")
    parser.add_argument("--source-id", type=int, default=None, dest="source_id")
    parser.add_argument("--analyst", type=str, default=None)
    parser.add_argument("--evidence-excerpt", type=str, default=None, dest="evidence_excerpt")
    parser.add_argument("--raw-airport-name", type=str, default=None, dest="raw_airport_name")
    parser.add_argument("--raw-country", type=str, default=None, dest="raw_country")
    parser.add_argument("--raw-city", type=str, default=None, dest="raw_city")
    parser.add_argument("--raw-identifier-code", type=str, default=None, dest="raw_identifier_code")
    parser.add_argument(
        "--extraction-mode", type=str, default="HUMAN_TRANSCRIPTION", dest="extraction_mode",
        choices=MANUAL_IDENTITY_EVIDENCE_EXTRACTION_MODES,
    )
    parser.add_argument("--allow-database-write", action="store_true")
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = _parser().parse_args(argv)
    config = RecordManualIdentityEvidenceConfig(
        database=args.database,
        source_assertion_id=args.source_assertion_id,
        source_id=args.source_id,
        analyst=args.analyst,
        evidence_excerpt=args.evidence_excerpt,
        raw_airport_name=args.raw_airport_name,
        raw_country=args.raw_country,
        raw_city=args.raw_city,
        raw_identifier_code=args.raw_identifier_code,
        extraction_mode=args.extraction_mode,
        allow_database_write=args.allow_database_write,
    )
    result = run_record(config)
    print(render_result(result))
    if result.blockers:
        return 1
    if result.proposed_evidence_excerpt is not None and not result.decision_eligible:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
