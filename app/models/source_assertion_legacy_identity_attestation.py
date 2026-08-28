"""Append-only persistence for a human's identity decision about a LEGACY
SourceAssertion - one that already has `airport_id` set but predates the
modern identity-guard pipeline entirely (docs/architecture/rwi-legacy-
attached-sourceassertion-identity-governance-design.md, the locked design
this module implements).

A row represents exactly: "human reviewer X reviewed SourceAssertion Y's own
preserved raw evidence (and Y's own already-set airport_id) at a specific
point in time, and recorded decision Z (optionally confirming the existing
Airport) with reason R." It NEVER mutates
SourceAssertion.identity_guard_decision/identity_guard_reason (the permanent
historical fact "this legacy importer attached the row before modern
identity governance existed") and NEVER creates a
SourceAssertionEvidenceBag, Airport, UnknownAirportCandidate, Signal, or
Installation - see app.services.source_assertion_legacy_identity_attestation,
the only writer, for the full governed precondition chain.

WHY THIS IS A DISTINCT ENTITY FROM SourceAssertionIdentityResolution (KAR1),
NOT AN EXTENSION OF IT (design doc S3, option A rejected): KAR1's own
`evidence_bag_snapshot_id` is a NOT NULL column enforced by a composite
ForeignKeyConstraint against SourceAssertionEvidenceBag's own
(id, source_assertion_id) UniqueConstraint - relaxing that to support a
snapshot-less legacy row would weaken KAR1's own causal-integrity guarantee
for every OTHER, unrelated row that legitimately does have a real
discovery-time EvidenceBag. KAR1's own action vocabulary
(ATTACH_TO_EXISTING_AIRPORT/REJECT_ATTACHMENT/DEFER_IDENTITY_REVIEW) also
presumes an UNRESOLVED assertion (airport_id IS NULL); this table's own
rows are about an ALREADY-attached assertion, a structurally different
question with its own, deliberately non-overlapping vocabulary below.

REVIEW-TIME SNAPSHOT, NEVER A FABRICATED EvidenceBag (design doc S5, the
single most important property of this table): `reviewed_snapshot_json`/
`reviewed_snapshot_hash` capture what a human reviewer actually looked at,
and when - never inserted into source_assertion_evidence_bags, never
presented anywhere as "the evidence the identity guard saw at discovery
time" (that concept, EB1's SourceAssertionEvidenceBag, does not and cannot
exist for a row in this class). See
app.services.source_assertion_legacy_identity_attestation for the exact
snapshot contents and the deterministic serialize/hash functions - this
model never builds either itself.

Immutable (ORM before_update/before_delete event listeners), matching every
other append-only decision table in this pipeline (ReviewerAction,
SourceAssertionIdentityResolution, IdentityGuardEvaluation,
UnknownAirportCandidateReview) - a human changing their mind always appends
a NEW row, never edits an existing one. `supersedes_attestation_id` is
REQUIRED (not merely audit-only metadata, unlike its KAR1/UAC precedents)
when, and only when, the new row's own `action` contradicts the immediately
-latest existing attestation's own `action` for the same assertion
(CONFIRM_EXISTING_ATTACHMENT <-> REJECT_EXISTING_ATTACHMENT in either
direction) - see the service module's own docstring for the full reversal-
safety reasoning (design doc S6 / this mission's own explicit Commander
requirement: a second human decision must never silently erase the
governance meaning of the first).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Deliberately three members, mirroring KAR1's own three-member shape, but
# semantically distinct: these describe a human reviewing an ALREADY-
# attached assertion, never attaching an unresolved one. No
# CREATE_NEW_AIRPORT-equivalent action - out of scope, matching KAR1's own
# explicit exclusion for the identical reason (a UAC/candidate-formation
# question, not this table's own).
SOURCE_ASSERTION_LEGACY_IDENTITY_ATTESTATION_ACTIONS = (
    "CONFIRM_EXISTING_ATTACHMENT",
    "REJECT_EXISTING_ATTACHMENT",
    "DEFER_IDENTITY_REVIEW",
)


class SourceAssertionLegacyIdentityAttestation(Base):
    """One append-only, immutable record of a human's identity judgment
    about one already-airport-attached, pre-modern-governance
    SourceAssertion. See module docstring for the full design rationale."""

    __tablename__ = "source_assertion_legacy_identity_attestations"
    __table_args__ = (
        CheckConstraint(
            "action IN ('CONFIRM_EXISTING_ATTACHMENT','REJECT_EXISTING_ATTACHMENT','DEFER_IDENTITY_REVIEW')",
            name="ck_source_assertion_legacy_identity_attestations_action",
        ),
        # Action/target consistency, mirroring KAR1's own dual-constraint
        # pattern exactly: matched_airport_id is required for, and only
        # for, the confirm action.
        CheckConstraint(
            "(action != 'CONFIRM_EXISTING_ATTACHMENT') OR matched_airport_id IS NOT NULL",
            name="ck_source_assertion_legacy_identity_attestations_confirm_target_required",
        ),
        CheckConstraint(
            "(action = 'CONFIRM_EXISTING_ATTACHMENT') OR matched_airport_id IS NULL",
            name="ck_source_assertion_legacy_identity_attestations_target_only_for_confirm",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Not unique - append-only, many attestation rows may exist per
    # assertion over time (multiple DEFER/REJECT/CONFIRM entries are
    # legitimate; see design doc S6/S11 - reversal is explicit, never
    # forbidden).
    source_assertion_id: Mapped[int] = mapped_column(ForeignKey("source_assertions.id"), index=True)

    action: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)

    # Plain free-text identity - matches ReviewerAction.reviewer/
    # SourceAssertionIdentityResolution.reviewer exactly (RWI has no auth
    # infrastructure).
    reviewer: Mapped[str] = mapped_column(String(100))

    # Populated only for action == "CONFIRM_EXISTING_ATTACHMENT" (enforced
    # by the CHECK constraints above and re-checked in Python): must equal
    # the assertion's OWN airport_id at write time (this mechanism never
    # attaches to, or proposes, a DIFFERENT Airport - see module docstring
    # and the service's own precondition). Never used to create or modify
    # that Airport.
    matched_airport_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("airports.id"), nullable=True, index=True
    )

    # See module docstring "REVIEW-TIME SNAPSHOT" - produced/consumed only
    # by app.services.source_assertion_legacy_identity_attestation's own
    # serialize/hash functions, never hand-built elsewhere.
    reviewed_snapshot_json: Mapped[str] = mapped_column(Text)
    reviewed_snapshot_hash: Mapped[str] = mapped_column(String(64), index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Self-referential, REQUIRED for a reversal (see module docstring),
    # audit-only metadata otherwise - mirrors
    # SourceAssertionIdentityResolution.supersedes_resolution_id /
    # ReviewerAction.supersedes_action_id exactly in shape. "Current" state
    # is still always derived by recency (created_at, id), never by walking
    # this chain - matching every other append-only table in this pipeline.
    supersedes_attestation_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("source_assertion_legacy_identity_attestations.id"), nullable=True, index=True
    )

    # All one-directional (no back_populates) - matches
    # SourceAssertionIdentityResolution's own explicit reasoning: not
    # required for correctness, would touch an already-migrated,
    # already-reviewed model file for a capability that does not need it.
    source_assertion: Mapped["SourceAssertion"] = relationship()
    matched_airport: Mapped[Optional["Airport"]] = relationship(foreign_keys=[matched_airport_id])
    supersedes: Mapped[Optional["SourceAssertionLegacyIdentityAttestation"]] = relationship(
        remote_side="SourceAssertionLegacyIdentityAttestation.id", foreign_keys=[supersedes_attestation_id]
    )


@event.listens_for(SourceAssertionLegacyIdentityAttestation, "before_update")
def _prevent_legacy_identity_attestation_update(_mapper, _connection, _target) -> None:
    raise ValueError(
        "SourceAssertionLegacyIdentityAttestation rows are immutable; record a new attestation "
        "(optionally naming this one as supersedes_attestation_id) instead of editing an existing one."
    )


@event.listens_for(SourceAssertionLegacyIdentityAttestation, "before_delete")
def _prevent_legacy_identity_attestation_delete(_mapper, _connection, _target) -> None:
    raise ValueError("SourceAssertionLegacyIdentityAttestation rows are auditable and cannot be deleted.")
