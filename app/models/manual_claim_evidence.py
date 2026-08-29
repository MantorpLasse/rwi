"""First-class governed manual claim evidence ("RWI - First-Class Manual
Claim Evidence - Implementation" mission).

A `ManualClaimEvidence` row represents exactly one thing: an analyst's
audited, literal transcription of ONE independently-supportable claim from
an already-preserved `SourceAssertion.raw_relevant_text`, for a source
family with no automatic claims extractor
(app.services.human_review_claim_enrichment.enrich_claims() returns None
for its parser_identifier) - never a judgment call, never a Signal, never
free-form interpretation, never a translation, never a bypass of identity
governance. See app.services.manual_claim_evidence for the full mechanical
standard this table's own write path enforces, and
app.services.evidence_claim_semantics for the EXISTING, UNMODIFIED, pure
in-memory `Claim` taxonomy this table's rows are deterministically adapted
into - never a parallel claims universe.

ONE TABLE, TYPED NULLABLE PAYLOAD COLUMNS (this mission's own Phase 3
Option A, chosen over B/C/D): mirrors `ManualIdentityEvidence`/
`AirportAlias`/`AirportIdentifier`'s own identical, three-times-proven
shape in this pipeline - one row per governed fact, common
provenance/governance columns plus a set of nullable, CHECK-constrained,
GROUPED typed columns for the claim's optional financial/temporal/
relationship attachment (mirroring `Claim`'s own three optional,
independently-combinable attachments exactly). Rejected: a parent+child
table split (over-normalized for "one claim, a few optional attachments"),
one table per claim category (needless duplication of every governance
column four times), and a JSON payload (opaque, not mechanically
CHECK-constrained, inconsistent with every other governed-evidence table
in this pipeline).

CLAIM CATEGORY IS REQUIRED AND CLOSED (`claim_category`, CHECK-constrained
to the EXACT four members of
app.services.evidence_claim_semantics.ClaimCategory - never a fifth,
Sacheon-specific value): this table reuses the EXISTING claim taxonomy
verbatim, never invents a parallel one.

`provenance_kind` IS NEVER A COLUMN (deliberately absent, not merely
defaulted): every ManualClaimEvidence row is, by construction, a literal
transcription of something the source text directly states -
`app.services.evidence_claim_semantics.ProvenanceKind.EXPLICIT` always,
hardcoded in the adapter, never a caller-supplied choice. A DERIVED claim
(assembled by combining multiple explicit facts, or by inference) has NO
representation in this table at all - see the service module's own "FACT
VS INFERENCE" boundary.

FINANCIAL/TEMPORAL/RELATIONSHIP ARE OPTIONAL, GROUPED, ALL-OR-NOTHING
COLUMN SETS (mirrors `Claim`'s own "all three attachments optional and
independently combinable" shape exactly): a caller may supply NONE, ONE,
TWO, or all THREE groups on a single row - see the service module's own
group-completeness validation (each group's required member columns must
ALL be populated together, or none at all).

`financial_amount` IS STORED AS TEXT, NEVER A FLOAT/REAL COLUMN: SQLite's
REAL type is IEEE-754 double-precision binary floating point, which cannot
represent most decimal fractions exactly - the same reasoning
`app.services.evidence_claim_semantics.FinancialFact.amount: Decimal`
already enforces in memory. Storing the canonical `str(Decimal(...))`
representation and re-parsing via `Decimal()` at read time is the only way
to guarantee a value like "12500000" round-trips through SQLite without
silent floating-point drift.

`financial_amount_evidence_token` (mirrors
`app.models.airport_identifier.AirportIdentifier`'s own
`evidence_excerpt`/type-token mechanical-verification discipline exactly):
the EXACT literal numeral substring as it appears in `evidence_excerpt`
(e.g. "12,500,000", not a caller's own retyped "12500000") - mechanically
verified, at write time, to (a) occur literally in `evidence_excerpt` and
(b) parse (after stripping thousands separators/whitespace) to EXACTLY
`financial_amount`. Never inferred from proximity or shape.

`temporal_year_tokens` IS THE ONLY SOURCE OF `TemporalContext.detail`
(deliberately no separate free-text "detail" column - see the service
module's own "TEMPORAL SEMANTICS" section): a comma-separated list of
literal year/date tokens exactly as they appear in `evidence_excerpt`
(e.g. "2025년,2026년"), each mechanically verified at write time. This
preserves source granularity (e.g. "spans two specific fiscal years")
without EVER inferring a synthetic date range the existing
`TemporalQualifier`/`as_of_date` vocabulary cannot honestly represent - see
that module's own documented, disclosed limitation.

`TemporalContext.as_of_date` IS NEVER A COLUMN HERE EITHER: it is always
derived, at adapter time, from the being-transcribed SourceAssertion's own
`Source.published_date` (the CITED DOCUMENT's own date, per
`TemporalContext.as_of_date`'s own docstring) - never caller-supplied,
never guessed, never `date.today()`.

`relationship_party`/`relationship_role`/`relationship_scope` mirror
`RelationshipFact`'s own three fields exactly (`party`/`role` required
together, `scope` optional) - `relationship_party` is mechanically
verified literal in `evidence_excerpt` (an authority/party NAME is a
literal fact); `relationship_role`/`relationship_scope` are free-text
classification, exactly as free-text and non-literal-checked as
`FinancialFact.semantic_role` already is.

`source_assertion_id` IS REQUIRED (not nullable), for the identical
reason every other governed-evidence table's own required FK to
SourceAssertion is: `Source` alone carries no governed, immutable,
preserved-evidence text to mechanically verify a submitted excerpt
against. `airport_id`/`source_id` are DELIBERATELY NOT columns here -
both are inherited, read-only, through `source_assertion_id` (this
mission's own explicit Phase 8 "avoid redundant mutable bindings"
instruction) - a caller can never supply a mismatching one.

NO STATUS / SUPERSESSION VOCABULARY THIS SLICE (mission's own explicit
Phase 13 "prefer safe incompleteness" instruction, mirroring
`SourceAssertionCrossSourceAliasAttestation`'s own identical, already-
proven posture): every row is a permanent, immutable, positive fact. A
mistaken transcription cannot be corrected or withdrawn this slice - a
documented limitation, not an oversight.

Immutable (ORM before_update/before_delete event listeners), matching
every other append-only evidence/decision table in this pipeline.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# The EXACT four members of app.services.evidence_claim_semantics.ClaimCategory
# - reused verbatim (as plain strings, matching every other governed-evidence
# table's own plain-string-vocabulary convention in this pipeline), never a
# fifth value.
MANUAL_CLAIM_EVIDENCE_CATEGORIES = (
    "explicit_document_fact",
    "procedural_request",
    "temporal_statement",
    "relationship",
)

# The EXACT six members of app.services.evidence_claim_semantics.TemporalQualifier.
MANUAL_CLAIM_EVIDENCE_TEMPORAL_QUALIFIERS = (
    "historical_fact",
    "current_state_as_of_document_date",
    "planned_future_action",
    "requested_pending_approval",
    "completed",
    "unknown",
)

# Deliberately one member this slice - mirrors
# app.models.manual_identity_evidence.MANUAL_IDENTITY_EVIDENCE_EXTRACTION_MODES's
# own identical, already-proven posture exactly.
MANUAL_CLAIM_EVIDENCE_EXTRACTION_MODES = ("HUMAN_TRANSCRIPTION",)

MANUAL_CLAIM_EVIDENCE_NORMALIZATION_VERSION = 1


class ManualClaimEvidence(Base):
    """One append-only, immutable record of an analyst's literal
    transcription of one claim from an already-preserved SourceAssertion.
    See module docstring for the full design rationale."""

    __tablename__ = "manual_claim_evidences"
    __table_args__ = (
        CheckConstraint(
            "claim_category IN ('explicit_document_fact','procedural_request','temporal_statement','relationship')",
            name="ck_manual_claim_evidences_claim_category",
        ),
        CheckConstraint(
            "extraction_mode IN ('HUMAN_TRANSCRIPTION')",
            name="ck_manual_claim_evidences_extraction_mode",
        ),
        CheckConstraint(
            "temporal_qualifier IS NULL OR temporal_qualifier IN "
            "('historical_fact','current_state_as_of_document_date','planned_future_action',"
            "'requested_pending_approval','completed','unknown')",
            name="ck_manual_claim_evidences_temporal_qualifier",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    source_assertion_id: Mapped[int] = mapped_column(ForeignKey("source_assertions.id"), index=True)

    claim_category: Mapped[str] = mapped_column(String(30))
    subject: Mapped[str] = mapped_column(String(300))
    statement: Mapped[str] = mapped_column(Text)

    # The literal, preserved substring of source_assertion.raw_relevant_text
    # this claim is grounded in - never a paraphrase (see module docstring;
    # mechanically verified in the service, never here).
    evidence_excerpt: Mapped[str] = mapped_column(Text)

    analyst: Mapped[str] = mapped_column(String(100))
    extraction_mode: Mapped[str] = mapped_column(String(30), default="HUMAN_TRANSCRIPTION")
    normalization_version: Mapped[int] = mapped_column(Integer, default=MANUAL_CLAIM_EVIDENCE_NORMALIZATION_VERSION)

    # --- Optional FinancialFact attachment (all-or-nothing group; see
    # service module) ---
    financial_amount: Mapped[Optional[str]] = mapped_column(String(50))
    financial_amount_evidence_token: Mapped[Optional[str]] = mapped_column(String(50))
    financial_currency: Mapped[Optional[str]] = mapped_column(String(10))
    financial_semantic_role: Mapped[Optional[str]] = mapped_column(String(100))
    financial_not_established: Mapped[Optional[str]] = mapped_column(Text)

    # --- Optional TemporalContext attachment (qualifier alone is
    # sufficient; year_tokens optional within the group; as_of_date is
    # NEVER a column - see module docstring) ---
    temporal_qualifier: Mapped[Optional[str]] = mapped_column(String(40))
    temporal_year_tokens: Mapped[Optional[str]] = mapped_column(Text)

    # --- Optional RelationshipFact attachment (party+role required
    # together; scope optional within the group) ---
    relationship_party: Mapped[Optional[str]] = mapped_column(String(200))
    relationship_role: Mapped[Optional[str]] = mapped_column(String(100))
    relationship_scope: Mapped[Optional[str]] = mapped_column(String(200))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    source_assertion: Mapped["SourceAssertion"] = relationship()


@event.listens_for(ManualClaimEvidence, "before_update")
def _prevent_manual_claim_evidence_update(_mapper, _connection, _target) -> None:
    raise ValueError(
        "ManualClaimEvidence rows are immutable; this slice supports no correction/supersession "
        "mechanics (see module docstring) - a mistaken transcription cannot be edited."
    )


@event.listens_for(ManualClaimEvidence, "before_delete")
def _prevent_manual_claim_evidence_delete(_mapper, _connection, _target) -> None:
    raise ValueError("ManualClaimEvidence rows are auditable and cannot be deleted.")
