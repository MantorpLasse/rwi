"""Human operator CLI for first-class governed manual claim evidence
("RWI - First-Class Manual Claim Evidence - Implementation" mission).

    python -m scripts.record_manual_claim_evidence --database data/runway_safe.db \\
        --source-assertion-id 235 --claim-category explicit_document_fact \\
        --subject "..." --statement "..." --evidence-excerpt "..."
        -> dry-run: shows the mandatory preview (effective identity,
           literal-containment result, identity-anchor result, duplicate
           check, predicted Claim object) and whether this claim would be
           accepted. Never writes.

    ... --analyst human:you --allow-database-write
        -> the only invocation shape that writes: calls
           app.services.manual_claim_evidence.record_manual_claim_evidence()
           (imported, never reimplemented) exactly once. Eligibility is
           RECOMPUTED transactionally inside that same call.

ONE SourceAssertion, ONE claim PER INVOCATION, NO BULK MODE (matching
every other governed CLI in this pipeline).

TYPED ARGUMENTS ONLY - NO GENERIC JSON BLOB: financial/temporal/
relationship fields are each their own explicit, typed CLI flag; there is
no --payload/--json escape hatch.

THIS TOOL NEVER TRANSLATES, INFERS, OR ESTIMATES ANYTHING - it only ever
persists exactly what the analyst supplies, mechanically verified against
the cited SourceAssertion's own preserved raw_relevant_text.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.services.manual_claim_evidence import (
    preview_manual_claim_evidence,
    record_manual_claim_evidence,
)

_KNOWN_ERRORS = (ValueError,)


@dataclass(frozen=True)
class RecordManualClaimEvidenceConfig:
    database: Path
    source_assertion_id: int
    claim_category: str
    subject: str
    statement: str
    evidence_excerpt: str
    analyst: Optional[str] = None
    financial_amount: Optional[str] = None
    financial_amount_evidence_token: Optional[str] = None
    financial_currency: Optional[str] = None
    financial_semantic_role: Optional[str] = None
    temporal_qualifier: Optional[str] = None
    temporal_year_tokens: "tuple[str, ...]" = ()
    relationship_party: Optional[str] = None
    relationship_role: Optional[str] = None
    relationship_scope: Optional[str] = None
    allow_database_write: bool = False


@dataclass
class RecordManualClaimEvidenceResult:
    source_assertion_id: int
    preview: "dict | None" = None
    decision_eligible: bool = False
    decision_refusal_reason: Optional[str] = None
    written: bool = False
    written_id: Optional[int] = None
    blockers: "list[str]" = field(default_factory=list)


def run_record(config: RecordManualClaimEvidenceConfig) -> RecordManualClaimEvidenceResult:
    result = RecordManualClaimEvidenceResult(source_assertion_id=config.source_assertion_id)
    engine = create_engine(f"sqlite:///{config.database}", connect_args={"check_same_thread": False}, future=True)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    session = Session(engine)
    try:
        amount = Decimal(config.financial_amount) if config.financial_amount is not None else None
        try:
            preview = preview_manual_claim_evidence(
                session, source_assertion_id=config.source_assertion_id, claim_category=config.claim_category,
                subject=config.subject, statement=config.statement, evidence_excerpt=config.evidence_excerpt,
                financial_amount=amount, financial_amount_evidence_token=config.financial_amount_evidence_token,
                financial_currency=config.financial_currency, financial_semantic_role=config.financial_semantic_role,
                temporal_qualifier=config.temporal_qualifier, temporal_year_tokens=config.temporal_year_tokens,
                relationship_party=config.relationship_party, relationship_role=config.relationship_role,
                relationship_scope=config.relationship_scope,
            )
        except _KNOWN_ERRORS as exc:
            result.blockers.append(f"preview failed: {exc}")
            return result

        result.preview = {
            "airport_id": preview.airport_id,
            "source_id": preview.source_id,
            "raw_identity_decision": preview.raw_identity_decision,
            "effective_identity_decision": preview.effective_identity_decision,
            "effective_identity_basis": preview.effective_identity_basis,
            "literal_excerpt_match": preview.literal_excerpt_match,
            "identity_anchor_present": preview.identity_anchor_present,
            "duplicate_of_id": preview.duplicate_of_id,
            "eligible": preview.eligible,
            "refusal_reason": preview.refusal_reason,
            "predicted_claim": repr(preview.predicted_claim) if preview.predicted_claim else None,
        }
        result.decision_eligible = preview.eligible
        result.decision_refusal_reason = preview.refusal_reason

        if config.analyst is None:
            return result
        if not config.analyst.strip():
            result.blockers.append("--analyst is required to record a claim")
            return result
        if not preview.eligible:
            return result
        if not config.allow_database_write:
            return result

        try:
            write_result = record_manual_claim_evidence(
                session, source_assertion_id=config.source_assertion_id, claim_category=config.claim_category,
                subject=config.subject, statement=config.statement, evidence_excerpt=config.evidence_excerpt,
                analyst=config.analyst, financial_amount=amount,
                financial_amount_evidence_token=config.financial_amount_evidence_token,
                financial_currency=config.financial_currency, financial_semantic_role=config.financial_semantic_role,
                temporal_qualifier=config.temporal_qualifier, temporal_year_tokens=config.temporal_year_tokens,
                relationship_party=config.relationship_party, relationship_role=config.relationship_role,
                relationship_scope=config.relationship_scope,
            )
        except _KNOWN_ERRORS as exc:
            result.decision_eligible = False
            result.decision_refusal_reason = str(exc)
            return result

        session.commit()
        result.written = True
        result.written_id = write_result.manual_claim_evidence_id
        return result
    finally:
        session.close()


def render_result(result: RecordManualClaimEvidenceResult) -> str:
    lines: "list[str]" = [f"SourceAssertion id: {result.source_assertion_id}"]
    if result.blockers:
        for blocker in result.blockers:
            lines.append(f"BLOCKED: {blocker}")
        return "\n".join(lines) + "\n"

    p = result.preview or {}
    lines.append("")
    lines.append("MANDATORY PREVIEW (never writes)")
    lines.append(f"  airport_id={p.get('airport_id')}  source_id={p.get('source_id')}")
    lines.append(f"  raw identity: {p.get('raw_identity_decision')}")
    lines.append(f"  effective identity: {p.get('effective_identity_decision')}  basis={p.get('effective_identity_basis')}")
    lines.append(f"  literal excerpt match: {p.get('literal_excerpt_match')}")
    lines.append(f"  identity anchor present: {p.get('identity_anchor_present')}")
    lines.append(f"  duplicate of: {p.get('duplicate_of_id')}")
    lines.append(f"  predicted claim: {p.get('predicted_claim')}")
    lines.append("")
    lines.append(f"  eligible: {result.decision_eligible}")
    if result.decision_refusal_reason:
        lines.append(f"  refused: {result.decision_refusal_reason}")
    if result.written:
        lines.append(f"  WRITTEN: ManualClaimEvidence #{result.written_id}")
    elif result.decision_eligible:
        lines.append("  DRY RUN - no write performed (pass --analyst and --allow-database-write to record this claim)")
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--source-assertion-id", type=int, required=True, dest="source_assertion_id")
    parser.add_argument("--claim-category", type=str, required=True, dest="claim_category")
    parser.add_argument("--subject", type=str, required=True)
    parser.add_argument("--statement", type=str, required=True)
    parser.add_argument("--evidence-excerpt", type=str, required=True, dest="evidence_excerpt")
    parser.add_argument("--analyst", type=str, default=None)
    parser.add_argument("--financial-amount", type=str, default=None, dest="financial_amount")
    parser.add_argument("--financial-amount-evidence-token", type=str, default=None, dest="financial_amount_evidence_token")
    parser.add_argument("--financial-currency", type=str, default=None, dest="financial_currency")
    parser.add_argument("--financial-semantic-role", type=str, default=None, dest="financial_semantic_role")
    parser.add_argument("--temporal-qualifier", type=str, default=None, dest="temporal_qualifier")
    parser.add_argument("--temporal-year-token", type=str, action="append", default=[], dest="temporal_year_tokens")
    parser.add_argument("--relationship-party", type=str, default=None, dest="relationship_party")
    parser.add_argument("--relationship-role", type=str, default=None, dest="relationship_role")
    parser.add_argument("--relationship-scope", type=str, default=None, dest="relationship_scope")
    parser.add_argument("--allow-database-write", action="store_true")
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = _parser().parse_args(argv)
    config = RecordManualClaimEvidenceConfig(
        database=args.database, source_assertion_id=args.source_assertion_id, claim_category=args.claim_category,
        subject=args.subject, statement=args.statement, evidence_excerpt=args.evidence_excerpt, analyst=args.analyst,
        financial_amount=args.financial_amount, financial_amount_evidence_token=args.financial_amount_evidence_token,
        financial_currency=args.financial_currency, financial_semantic_role=args.financial_semantic_role,
        temporal_qualifier=args.temporal_qualifier, temporal_year_tokens=tuple(args.temporal_year_tokens),
        relationship_party=args.relationship_party, relationship_role=args.relationship_role,
        relationship_scope=args.relationship_scope, allow_database_write=args.allow_database_write,
    )
    result = run_record(config)
    print(render_result(result))
    if result.blockers:
        return 1
    if not result.decision_eligible:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
