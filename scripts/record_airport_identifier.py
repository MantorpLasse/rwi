"""Human operator CLI for first-class governed Airport identifiers
(docs/architecture, "RWI - Governed Canonical Airport Identifiers -
Architecture Design" mission, the locked design this capability
implements).

    python -m scripts.record_airport_identifier --database data/runway_safe.db \\
        --airport-id 88
        -> pure inspection: Airport identity (canonical name, existing
           codes), currently-admitted identifiers, identifier history.
           Never writes.

    python -m scripts.record_airport_identifier --database ... --airport-id 1 \\
        --source-id 1 --source-assertion-id 1 --identifier-type IATA \\
        --identifier-value XYZ --type-evidence-token "XYZ(IATA)" \\
        --evidence-excerpt "..." --analyst human:you
        -> dry-run: shows the mandatory impact/conflict preview and
           whether this admission would be accepted (via a rolled-back
           SAVEPOINT, so eligibility/anti-circularity is authoritative,
           never re-derived by this CLI). Never writes.

    ... --allow-database-write
        -> the only invocation shape that writes: calls
           app.services.airport_identifier.record_airport_identifier()
           (imported, never reimplemented) exactly once, atomically
           persisting the governance row AND the typed Airport-column
           write together.

THIS TOOL NEVER SHOWS OR ASKS FOR AN IDENTITY-DECISION CHOICE - there is
no `--decision`/`--outcome`/`--confirm` flag anywhere in this script. It
also never triggers EB4 re-evaluation or IdentityGuardEvaluation creation
for any SourceAssertion - admitting a governed identifier and any later
re-evaluation of existing evidence remain two separate, explicit
operations.

ONE AIRPORT, ONE IDENTIFIER TYPE, ONE VALUE PER INVOCATION, NO BULK MODE,
NO AUTO-CONFIRM (mission's own repeated, explicit instruction, matching
every other governed CLI in this pipeline).

NO WEB FETCHING, NO TRANSLATION, NO TRANSLITERATION, NO TYPE INFERENCE
FROM CODE LENGTH: this tool only ever reads what is already preserved on
the cited SourceAssertion row itself; `--identifier-type` and
`--type-evidence-token` must both be supplied explicitly by the analyst,
never guessed.

NO DUPLICATED BUSINESS RULES: every precondition is enforced exactly
once, inside record_airport_identifier()/
check_airport_identifier_admission_eligibility() themselves - this script
only ever calls them and prints whatever they raise.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.models import Airport, Source, SourceAssertion
from app.models.airport_identifier import (
    AIRPORT_IDENTIFIER_EVIDENCE_CLASSES,
    AIRPORT_IDENTIFIER_STATUSES,
    AIRPORT_IDENTIFIER_TYPES,
    AirportIdentifier,
)
from app.services.airport_identifier import (
    AirportNotFoundError,
    CircularIdentifierEvidenceError,
    ConflictingIdentifierStatusRequiresSupersessionError,
    CrossAirportTypedCollisionError,
    CurrentColumnPopulatedError,
    EmptyAnalystError,
    EmptyEvidenceExcerptError,
    EmptyIdentifierValueError,
    ExcerptNotInPreservedEvidenceError,
    IdentifierNotInExcerptError,
    InsufficientSourceReliabilityError,
    InvalidIdentifierTypeError,
    NoIdentityAnchorError,
    SourceAssertionAirportMismatchError,
    SourceAssertionNotFoundError,
    SourceAssertionSourceMismatchError,
    SourceNotFoundError,
    TargetColumnChangedDuringWriteError,
    TypeEvidenceIncompleteError,
    TypeEvidenceNotInExcerptError,
    preview_airport_identifier_admission_impact,
    record_airport_identifier,
)

_KNOWN_ERRORS = (
    AirportNotFoundError,
    SourceNotFoundError,
    SourceAssertionNotFoundError,
    SourceAssertionSourceMismatchError,
    SourceAssertionAirportMismatchError,
    InvalidIdentifierTypeError,
    EmptyIdentifierValueError,
    EmptyEvidenceExcerptError,
    EmptyAnalystError,
    IdentifierNotInExcerptError,
    TypeEvidenceNotInExcerptError,
    TypeEvidenceIncompleteError,
    ExcerptNotInPreservedEvidenceError,
    NoIdentityAnchorError,
    InsufficientSourceReliabilityError,
    CurrentColumnPopulatedError,
    CrossAirportTypedCollisionError,
    CircularIdentifierEvidenceError,
    ConflictingIdentifierStatusRequiresSupersessionError,
    TargetColumnChangedDuringWriteError,
    ValueError,
)


@dataclass(frozen=True)
class RecordAirportIdentifierConfig:
    database: Path
    airport_id: int
    source_id: Optional[int] = None
    source_assertion_id: Optional[int] = None
    identifier_type: Optional[str] = None
    identifier_value: Optional[str] = None
    type_evidence_token: Optional[str] = None
    evidence_excerpt: Optional[str] = None
    analyst: Optional[str] = None
    evidence_class: str = "AUTHORITATIVE_DIRECT"
    status: str = "ADMITTED"
    supersedes_identifier_id: Optional[int] = None
    allow_database_write: bool = False


@dataclass
class RecordAirportIdentifierResult:
    airport_id: int
    airport_found: bool = False
    airport_name: Optional[str] = None
    airport_iata: Optional[str] = None
    airport_icao: Optional[str] = None
    airport_faa: Optional[str] = None
    identifier_history: "list[dict]" = field(default_factory=list)

    proposed_source_id: Optional[int] = None
    proposed_source_assertion_id: Optional[int] = None
    proposed_identifier_type: Optional[str] = None
    proposed_identifier_value: Optional[str] = None
    proposed_type_evidence_token: Optional[str] = None
    proposed_evidence_excerpt: Optional[str] = None
    proposed_analyst: Optional[str] = None
    proposed_evidence_class: Optional[str] = None
    proposed_status: Optional[str] = None
    proposed_supersedes_identifier_id: Optional[int] = None

    impact_preview: "list[dict]" = field(default_factory=list)
    impact_changed_count: int = 0

    decision_eligible: bool = False
    decision_refusal_reason: Optional[str] = None
    written: bool = False
    written_identifier_id: Optional[int] = None
    column_written: bool = False

    blockers: "list[str]" = field(default_factory=list)


def run_record(config: RecordAirportIdentifierConfig) -> RecordAirportIdentifierResult:
    result = RecordAirportIdentifierResult(airport_id=config.airport_id)
    engine = create_engine(f"sqlite:///{config.database}", connect_args={"check_same_thread": False}, future=True)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    session = Session(engine)
    try:
        airport = session.get(Airport, config.airport_id)
        if airport is None:
            result.blockers.append(f"Airport {config.airport_id} does not exist")
            return result

        result.airport_found = True
        result.airport_name = airport.name
        result.airport_iata = airport.iata_code
        result.airport_icao = airport.icao_code
        result.airport_faa = airport.faa_code

        rows = session.scalars(
            select(AirportIdentifier)
            .where(AirportIdentifier.airport_id == airport.id)
            .order_by(AirportIdentifier.created_at.asc(), AirportIdentifier.id.asc())
        ).all()
        result.identifier_history = [
            {
                "id": row.id, "identifier_type": row.identifier_type, "identifier_value": row.identifier_value,
                "status": row.status, "evidence_class": row.evidence_class, "source_id": row.source_id,
                "source_assertion_id": row.source_assertion_id, "analyst": row.analyst,
                "supersedes_identifier_id": row.supersedes_identifier_id, "created_at": row.created_at,
            }
            for row in rows
        ]

        if (
            config.identifier_type is None and config.identifier_value is None and config.analyst is None
            and config.source_id is None and config.source_assertion_id is None
        ):
            return result

        result.proposed_source_id = config.source_id
        result.proposed_source_assertion_id = config.source_assertion_id
        result.proposed_identifier_type = config.identifier_type
        result.proposed_identifier_value = config.identifier_value
        result.proposed_type_evidence_token = config.type_evidence_token
        result.proposed_evidence_excerpt = config.evidence_excerpt
        result.proposed_analyst = config.analyst
        result.proposed_evidence_class = config.evidence_class
        result.proposed_status = config.status
        result.proposed_supersedes_identifier_id = config.supersedes_identifier_id

        for field_name, value in (
            ("--source-id", config.source_id), ("--source-assertion-id", config.source_assertion_id),
            ("--identifier-type", config.identifier_type), ("--identifier-value", config.identifier_value),
            ("--type-evidence-token", config.type_evidence_token),
            ("--evidence-excerpt", config.evidence_excerpt), ("--analyst", config.analyst),
        ):
            if value is None or (isinstance(value, str) and not value.strip()):
                result.blockers.append(f"{field_name} is required to propose an identifier admission")
        if result.blockers:
            return result

        if config.status not in AIRPORT_IDENTIFIER_STATUSES:
            result.blockers.append(f"--status must be one of {AIRPORT_IDENTIFIER_STATUSES!r}")
            return result
        if config.identifier_type not in AIRPORT_IDENTIFIER_TYPES:
            result.blockers.append(f"--identifier-type must be one of {AIRPORT_IDENTIFIER_TYPES!r}")
            return result
        if config.evidence_class not in AIRPORT_IDENTIFIER_EVIDENCE_CLASSES:
            result.blockers.append(f"--evidence-class must be one of {AIRPORT_IDENTIFIER_EVIDENCE_CLASSES!r}")
            return result

        # Mandatory impact/conflict preview - shown BEFORE any eligibility/
        # write attempt, using the same pure simulation the write path
        # itself recomputes transactionally.
        try:
            preview = preview_airport_identifier_admission_impact(
                session, airport_id=config.airport_id, identifier_type=config.identifier_type,
                proposed_value=config.identifier_value,
            )
            result.impact_preview = [
                {
                    "source_assertion_id": r.source_assertion_id, "source_id": r.source_id,
                    "has_snapshot": r.has_snapshot, "current_outcome": r.current_outcome,
                    "hypothetical_outcome": r.hypothetical_outcome, "changed": r.changed,
                }
                for r in preview.rows
            ]
            result.impact_changed_count = len(preview.changed_source_assertion_ids)
        except _KNOWN_ERRORS as exc:
            result.blockers.append(f"impact preview failed: {exc}")
            return result

        try:
            savepoint = session.begin_nested()
            try:
                record_airport_identifier(
                    session,
                    airport_id=config.airport_id, source_id=config.source_id,
                    source_assertion_id=config.source_assertion_id, identifier_type=config.identifier_type,
                    identifier_value=config.identifier_value, evidence_excerpt=config.evidence_excerpt,
                    analyst=config.analyst, type_evidence_token=config.type_evidence_token,
                    evidence_class=config.evidence_class, status=config.status,
                    supersedes_identifier_id=config.supersedes_identifier_id,
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

        write_result = record_airport_identifier(
            session,
            airport_id=config.airport_id, source_id=config.source_id,
            source_assertion_id=config.source_assertion_id, identifier_type=config.identifier_type,
            identifier_value=config.identifier_value, evidence_excerpt=config.evidence_excerpt,
            analyst=config.analyst, type_evidence_token=config.type_evidence_token,
            evidence_class=config.evidence_class, status=config.status,
            supersedes_identifier_id=config.supersedes_identifier_id,
        )
        session.commit()
        result.written = True
        result.written_identifier_id = write_result.identifier_id
        result.column_written = write_result.column_written
        session.refresh(airport)
        result.airport_iata = airport.iata_code
        result.airport_icao = airport.icao_code
        result.airport_faa = airport.faa_code
        return result
    finally:
        session.close()


def render_result(result: RecordAirportIdentifierResult) -> str:
    lines: "list[str]" = []
    lines.append(f"Airport id: {result.airport_id}")

    if result.blockers:
        for blocker in result.blockers:
            lines.append(f"BLOCKED: {blocker}")
        return "\n".join(lines) + "\n"

    lines.append("")
    lines.append("AIRPORT (canonical identity)")
    lines.append(f"  name: {result.airport_name!r}")
    lines.append(f"  iata_code: {result.airport_iata!r}  icao_code: {result.airport_icao!r}  faa_code: {result.airport_faa!r}")

    lines.append("")
    lines.append(f"IDENTIFIER HISTORY ({len(result.identifier_history)} total)")
    if not result.identifier_history:
        lines.append("  (none)")
    for row in result.identifier_history:
        lines.append(
            f"  #{row['id']} {row['identifier_type']}={row['identifier_value']!r} status={row['status']} "
            f"evidence_class={row['evidence_class']!r} source_id={row['source_id']} "
            f"source_assertion_id={row['source_assertion_id']} analyst={row['analyst']!r} "
            f"supersedes={row['supersedes_identifier_id']} created_at={row['created_at']}"
        )

    if result.proposed_identifier_type is not None or result.proposed_source_id is not None:
        lines.append("")
        lines.append("Proposed identifier admission (HUMAN EVIDENCE ONLY - no identity decision is ever accepted here):")
        lines.append(f"  source_id: {result.proposed_source_id}")
        lines.append(f"  source_assertion_id: {result.proposed_source_assertion_id}")
        lines.append(f"  identifier_type: {result.proposed_identifier_type!r}")
        lines.append(f"  identifier_value: {result.proposed_identifier_value!r}")
        lines.append(f"  type_evidence_token: {result.proposed_type_evidence_token!r}")
        lines.append(f"  evidence_excerpt: {result.proposed_evidence_excerpt!r}")
        lines.append(f"  analyst: {result.proposed_analyst!r}")
        lines.append(f"  evidence_class: {result.proposed_evidence_class!r}  status: {result.proposed_status!r}")
        lines.append(f"  supersedes_identifier_id: {result.proposed_supersedes_identifier_id}")

        lines.append("")
        lines.append(f"MANDATORY IMPACT/CONFLICT PREVIEW ({result.impact_changed_count} SourceAssertion(s) would change outcome)")
        for row in result.impact_preview:
            lines.append(
                f"  SA{row['source_assertion_id']} (source_id={row['source_id']}): "
                f"has_snapshot={row['has_snapshot']} current={row['current_outcome']} "
                f"-> hypothetical={row['hypothetical_outcome']}  changed={row['changed']}"
            )

        lines.append("")
        lines.append(f"  eligible: {result.decision_eligible}")
        if result.decision_refusal_reason:
            lines.append(f"  refused: {result.decision_refusal_reason}")
        if result.written:
            lines.append(f"  WRITTEN: AirportIdentifier #{result.written_identifier_id}  column_written={result.column_written}")
            lines.append(f"  Airport codes now: iata={result.airport_iata!r} icao={result.airport_icao!r} faa={result.airport_faa!r}")
            lines.append(
                "  REMINDER: no SourceAssertion was re-evaluated by this write - identifier admission and "
                "EB4 re-evaluation remain two separate, explicit operations."
            )
        elif result.decision_eligible:
            lines.append("  DRY RUN - no write performed (pass --allow-database-write to record this identifier)")

    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--database", type=Path, required=True,
        help="Path to the SQLite database. No default - never the real production database by accident.",
    )
    parser.add_argument("--airport-id", type=int, required=True, dest="airport_id")
    parser.add_argument("--source-id", type=int, default=None, dest="source_id")
    parser.add_argument("--source-assertion-id", type=int, default=None, dest="source_assertion_id")
    parser.add_argument("--identifier-type", type=str, default=None, dest="identifier_type", choices=AIRPORT_IDENTIFIER_TYPES)
    parser.add_argument("--identifier-value", type=str, default=None, dest="identifier_value")
    parser.add_argument("--type-evidence-token", type=str, default=None, dest="type_evidence_token")
    parser.add_argument("--evidence-excerpt", type=str, default=None, dest="evidence_excerpt")
    parser.add_argument("--analyst", type=str, default=None)
    parser.add_argument(
        "--evidence-class", type=str, default="AUTHORITATIVE_DIRECT", dest="evidence_class",
        choices=AIRPORT_IDENTIFIER_EVIDENCE_CLASSES,
    )
    parser.add_argument("--status", type=str, default="ADMITTED", choices=AIRPORT_IDENTIFIER_STATUSES)
    parser.add_argument(
        "--supersedes-identifier-id", type=int, default=None, dest="supersedes_identifier_id",
        help="Required when --status contradicts the latest existing row's own status for this exact "
        "(airport, identifier_type) pair - must equal that latest row's own id.",
    )
    parser.add_argument("--allow-database-write", action="store_true")
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = _parser().parse_args(argv)
    config = RecordAirportIdentifierConfig(
        database=args.database, airport_id=args.airport_id, source_id=args.source_id,
        source_assertion_id=args.source_assertion_id, identifier_type=args.identifier_type,
        identifier_value=args.identifier_value, type_evidence_token=args.type_evidence_token,
        evidence_excerpt=args.evidence_excerpt, analyst=args.analyst, evidence_class=args.evidence_class,
        status=args.status, supersedes_identifier_id=args.supersedes_identifier_id,
        allow_database_write=args.allow_database_write,
    )
    result = run_record(config)
    print(render_result(result))
    if result.blockers:
        return 1
    if result.proposed_identifier_type is not None and not result.decision_eligible:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
