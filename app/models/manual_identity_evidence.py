"""First-class manual identity evidence (docs/architecture, "RWI - New
Source Family Manual Identity Evidence - Architecture Design" mission, the
locked design this module implements).

A `ManualIdentityEvidence` row represents exactly one thing: a human analyst
transcribed, LITERALLY and without inference, a piece of identity evidence
(an airport name/country/city/code) that already appears in one preserved
SourceAssertion's own raw evidence, so that evidence can feed the EXISTING,
UNMODIFIED EvidenceBag -> IdentityGuard -> EB5 pipeline exactly as if it had
been extracted by a machine parser.

    preserved Source -> existing SourceAssertion -> human literal
        transcription -> ManualIdentityEvidence (this table, append-only)
        -> app.services.manual_identity_evidence.manual_identity_evidence_to_candidate_fragment()
        -> app.services.discovery_candidate_fragment.candidate_fragment_to_evidence_bag()
           (existing, unmodified)
        -> app.services.evidence_attachment_guard.evaluate_attachment_for_candidates()
           (existing, unmodified)
        -> SourceAssertion.identity_guard_decision/identity_guard_reason set
           once (NULL -> value), exactly like discovery-time persistence
        -> app.services.effective_identity_guard_decision.resolve_effective_identity_guard_decision()
           (existing, unmodified EB5) reads it back as ORIGINAL_DECISION,
           indistinguishable from an automated modern identity-governed row.

THE HUMAN SUPPLIES EVIDENCE, NEVER A DECISION: there is no column here for
an identity outcome, an "attach" choice, a confidence score, or an override
of any kind. `app.services.evidence_attachment_guard.evaluate_attachment_for_candidates()`
(unmodified) is the ONLY thing that ever computes an AttachmentOutcome from
this row's contents - see app.services.manual_identity_evidence, the sole
writer, for the full governed precondition chain and literal-transcription
enforcement.

ONE MODE THIS SLICE: `extraction_mode` is always "HUMAN_TRANSCRIPTION" -
see EXTRACTION_MODES below. No elaborate taxonomy; a future mode is a
future, separately-reviewed addition to that tuple, not a reason to widen
this column's semantics today.

GENERIC, NOT SOURCE-FAMILY-SPECIFIC: nothing in this model or its writer
mentions Korea, Sacheon, "international," or "news" - it is the same
generic capability regardless of which new source family motivated its
existence (see app.services.manual_identity_evidence module docstring for
why a Korean-news control case is a USE of this mechanism, never part of
its architecture).

WHY NOT A NEW FIELD ON SourceAssertion, AND NOT SourceAssertionLegacyIdentityAttestation
(design doc): a legacy attestation means "this row predates modern identity
governance entirely, and a human is directly confirming/rejecting an
ALREADY-set airport_id." This table means the opposite: the assertion has
NEVER been identity-governed at all (identity_guard_decision IS NULL), and
the human supplies only EVIDENCE - IdentityGuard, unmodified, still makes
the actual identity decision, exactly as it would for a machine-extracted
fragment. Reusing the legacy mechanism here would misrepresent history AND
let a human directly supply the identity outcome, violating this module's
own central constraint.

WHY NO evidence_bag_snapshot_id / identity_guard_evaluation_id COLUMN
(audit-chain design choice - see app.services.manual_identity_evidence
module docstring "AUDIT-CHAIN RECONSTRUCTION"): this row is persisted
BEFORE the SourceAssertionEvidenceBag it feeds even exists (the adapter
builds the CandidateFragment FROM this already-persisted row - see
app.services.manual_identity_evidence.record_manual_identity_evidence()).
Adding a column here that gets filled in AFTER the fact would require
either mutating this row post-persistence (forbidden - see immutability
below) or a nullable-then-backfilled column, the exact "generalized event
sourcing" this mission explicitly rejects. `source_assertion_id` alone is
sufficient: SourceAssertionEvidenceBag.source_assertion_id already carries
a UNIQUE constraint (at most one snapshot per assertion, mirroring
Snapshot.first_acquisition_run_id's own precedent), and
app.services.manual_identity_evidence's own eligibility check refuses a
second ManualIdentityEvidence row for an assertion that already has one -
so "the ManualIdentityEvidence for assertion X" and "the
SourceAssertionEvidenceBag for assertion X" are both already unambiguous,
reconstructible by a plain query on the shared source_assertion_id, exactly
matching SourceAssertionEvidenceBag/IdentityGuardEvaluation's own
established "no back_populates, no reciprocal collection, a plain query is
always the correct read path" convention.

IMMUTABLE (ORM before_update/before_delete event listeners), matching every
other append-only decision/evidence table in this pipeline (ReviewerAction,
SourceAssertionIdentityResolution, IdentityGuardEvaluation,
SourceAssertionLegacyIdentityAttestation, SourceAssertionEvidenceBag): a
mistaken transcription is never edited in place. This slice deliberately
implements NO supersession/correction mechanism (design mission's own
explicit instruction: omit rather than ship incomplete correction
semantics) - a second attempt for an assertion that already has a
ManualIdentityEvidence row, or an EvidenceBag, is simply refused by
app.services.manual_identity_evidence's own eligibility check.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Deliberately one member this slice (module docstring "ONE MODE THIS
# SLICE") - a future mode is a future, separately-reviewed addition, never
# a reason to widen this column's semantics today.
MANUAL_IDENTITY_EVIDENCE_EXTRACTION_MODES = ("HUMAN_TRANSCRIPTION",)

# Version of the literal-evidence validation rules
# (app.services.manual_identity_evidence's own containment-check
# normalization) applied to THIS row at write time - lets a future reader
# know unambiguously which rule set governed this row, so a future
# validation-rule evolution can never be silently misinterpreted under a
# different rule set's assumptions. Mirrors
# SourceAssertionEvidenceBag.schema_version's own identical convention.
MANUAL_IDENTITY_EVIDENCE_NORMALIZATION_VERSION = 1


class ManualIdentityEvidence(Base):
    """One append-only, immutable record of a human analyst's literal
    transcription of identity evidence from one preserved SourceAssertion's
    own raw evidence. See module docstring for the full design rationale."""

    __tablename__ = "manual_identity_evidences"
    __table_args__ = (
        CheckConstraint(
            "extraction_mode IN ('HUMAN_TRANSCRIPTION')",
            name="ck_manual_identity_evidences_extraction_mode",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Not unique at the DB layer - "at most one per assertion" is a
    # business rule enforced by
    # app.services.manual_identity_evidence.check_manual_identity_evidence_eligibility()
    # (refuses a second row once one exists OR once an EvidenceBag exists),
    # the identical convention already established by
    # SourceAssertionLegacyIdentityAttestation's own append-only table for
    # its analogous "current state is derived by recency/existence, never
    # a DB uniqueness constraint" posture.
    source_assertion_id: Mapped[int] = mapped_column(ForeignKey("source_assertions.id"), index=True)

    # Redundant with source_assertion.source_id (the FK above already
    # implies it), but copied here deliberately - the same "copy a fact
    # that must survive independently of a live join" reasoning
    # IdentityGuardEvaluation.evaluated_against_airport_id already
    # establishes. Also lets
    # app.services.manual_identity_evidence.check_manual_identity_evidence_eligibility()
    # verify the analyst transcribed from the Source they believed they
    # were looking at (source-binding validation - see that module's own
    # docstring) directly against a persisted, snapshotted fact, never
    # only against a live, later-joinable relationship.
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)

    # The exact, literal excerpt of raw_relevant_text the analyst is
    # transcribing evidence FROM - never a paraphrase or summary. Required;
    # app.services.manual_identity_evidence enforces non-empty and (in
    # dry-run CLI mode) containment within the assertion's own
    # raw_relevant_text.
    evidence_excerpt: Mapped[str] = mapped_column(Text)

    # Raw transcribed values - all nullable (a transcription need not
    # supply every field, matching every raw_* field elsewhere in this
    # pipeline, e.g. SourceAssertion.raw_airport_name/raw_airport_identifier).
    # Each one, if supplied, must occur literally within evidence_excerpt
    # (app.services.manual_identity_evidence's own mechanical containment
    # check) - never independently verified against outside/background
    # knowledge, and never inferred when absent from the excerpt.
    raw_airport_name: Mapped[Optional[str]] = mapped_column(String(300))
    raw_country: Mapped[Optional[str]] = mapped_column(String(200))
    raw_city: Mapped[Optional[str]] = mapped_column(String(200))

    # Singular by design this slice (module docstring / design mission's
    # own "minimum conceptual fields" list: "raw IATA/ICAO/FAA code") -
    # exactly one literal code string, whichever type the source text
    # actually states; app.services.manual_identity_evidence's own adapter
    # places it into EvidenceBag.identifiers as a single-element set. A
    # future need for more than one code per transcription is a future,
    # separately-reviewed widening, never guessed at here.
    raw_identifier_code: Mapped[Optional[str]] = mapped_column(String(20))

    # Plain free-text identity, matching ReviewerAction.reviewer /
    # SourceAssertionLegacyIdentityAttestation.reviewer exactly (RWI has no
    # auth infrastructure).
    analyst: Mapped[str] = mapped_column(String(100))

    extraction_mode: Mapped[str] = mapped_column(String(30), default="HUMAN_TRANSCRIPTION")
    normalization_version: Mapped[int] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # One-directional (no back_populates) - matches every other append-only
    # evidence/decision table in this pipeline; "many ManualIdentityEvidence
    # rows may exist per SourceAssertion/Source" (structurally true even
    # though the service layer enforces at most one live row per
    # assertion) is provable by a plain query, never an eagerly-loaded ORM
    # collection.
    source_assertion: Mapped["SourceAssertion"] = relationship()
    source: Mapped["Source"] = relationship()


@event.listens_for(ManualIdentityEvidence, "before_update")
def _prevent_manual_identity_evidence_update(_mapper, _connection, _target) -> None:
    raise ValueError(
        "ManualIdentityEvidence rows are immutable; a mistaken transcription is never edited in "
        "place - this slice deliberately implements no correction/supersession mechanism (see "
        "module docstring); refuse and record nothing rather than editing an existing row."
    )


@event.listens_for(ManualIdentityEvidence, "before_delete")
def _prevent_manual_identity_evidence_delete(_mapper, _connection, _target) -> None:
    raise ValueError("ManualIdentityEvidence rows are auditable and cannot be deleted.")
