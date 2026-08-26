"""KAR1 - append-only persistence for HUMAN known-airport identity
resolution decisions (docs/architecture/rwi-known-airport-ambiguity-
resolution-design.md, locked design contract for this capability).

A row represents exactly: "human reviewer X reviewed SourceAssertion Y's
own ambiguous/unresolved identity_guard_decision and recorded decision Z
(optionally naming an existing canonical Airport) with reason R." It NEVER
mutates SourceAssertion.identity_guard_decision/identity_guard_reason (the
permanent, once-only historical machine fact) and NEVER itself creates an
Airport, UnknownAirportCandidate, Signal, or Installation - see
app.services.source_assertion_identity_resolution, the only writer, for the
full governance chain this row sits in.

IDENTITY DISCOVERY != AUTOMATIC EMAS RELEVANCE ASSESSMENT != HUMAN EMAS
RELEVANCE REVIEW != CANONICAL AIRPORT ADMISSION != SIGNAL CREATION (this
pipeline's own locked principle). This table answers yet another,
structurally distinct question from every one of those: "which existing,
already-canonical Airport (if any) does this UNRESOLVED evidence actually
belong to" - deliberately separate from `UnknownAirportCandidateReview`
(design doc S6: that vocabulary answers "is this a new airport," this one
answers "which known airport is this"). Reusing that table/enum here would
blur two independently governed questions the same way this codebase has
repeatedly refused to blur identity_guard_decision/
intelligence_review_decision/promotion_policy_decision/ReviewerAction/
relevance-review actions into one column.

HISTORICAL-FACT FIREWALL (the single most important property of this
table, mirroring app.models.identity_guard_evaluation.IdentityGuardEvaluation's
own identical discipline): SourceAssertion.identity_guard_decision/
identity_guard_reason remain the permanent, once-only, discovery-time
historical fact - nothing in this module ever reads, writes, or depends on
them changing. A human resolution is represented ONLY as a new row here,
appended, never as an edit of that original historical column pair, and
never as a rewrite implying "the guard originally knew the answer."

CAUSAL-INTEGRITY: `evidence_bag_snapshot_id` uses the identical composite
ForeignKeyConstraint technique app.models.identity_guard_evaluation
established - a plain single-column FK would only prove "this id refers to
SOME real snapshot row," not that it belongs to THIS row's own
source_assertion_id. The composite FK against SourceAssertionEvidenceBag's
own (id, source_assertion_id) UniqueConstraint makes a mismatched pairing
structurally impossible, enforced by SQLite itself. Required (NOT NULL),
not optional: design doc S8's own precondition list requires a real,
immutable EvidenceBag snapshot to exist before any human resolution can be
recorded - matching EB4's own MissingEvidenceBagSnapshotError precondition
exactly, applied here at write time instead of at EB4's later read time.

Deliberately does NOT add a `identity_resolutions` back-populated collection
to SourceAssertion - mirrors IdentityGuardEvaluation/SourceAssertionEvidenceBag's
own explicit, already-reviewed reasoning verbatim: not required for
correctness, and would touch an already-migrated, already-adversarially-
reviewed model file for a capability that does not need it. This module is
therefore fully additive - no existing model file is changed.

Immutable (ORM before_update/before_delete event listeners), matching every
other append-only decision table in this pipeline (ReviewerAction,
UnknownAirportCandidateReview, UnknownAirportCandidateRelevanceReview,
IdentityGuardEvaluation) - a human changing their mind always appends a NEW
resolution row (optionally naming the superseded one via
`supersedes_resolution_id`, audit-only metadata, never load-bearing for
"what is current"), never edits an existing one.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Narrow, three-member vocabulary derived from the actual gap (design doc
# S6) - deliberately excludes any CREATE_NEW_AIRPORT-equivalent action (that
# remains a UAC/candidate-formation question, explicitly out of scope - see
# the design doc's own S6/S19 reasoning) and deliberately does not overlap
# UnknownAirportCandidateReview's own vocabulary (S6's own "no overlapping
# semantics" instruction).
SOURCE_ASSERTION_IDENTITY_RESOLUTION_ACTIONS = (
    "ATTACH_TO_EXISTING_AIRPORT",
    "REJECT_ATTACHMENT",
    "DEFER_IDENTITY_REVIEW",
)


class SourceAssertionIdentityResolution(Base):
    """One append-only, immutable record of a human's identity-resolution
    judgment against one specific, still-unresolved SourceAssertion. See
    module docstring for the full design rationale.

    No identity_guard_decision/identity_guard_reason/evidence fields are
    duplicated from the SourceAssertion here - all of that remains reachable
    exactly once via `source_assertion_id`; this row stores only the
    HUMAN's own judgment and its provenance, matching
    UnknownAirportCandidateRelevanceReview's own "no redundant storage"
    discipline verbatim.
    """

    __tablename__ = "source_assertion_identity_resolutions"
    __table_args__ = (
        CheckConstraint(
            "action IN ('ATTACH_TO_EXISTING_AIRPORT','REJECT_ATTACHMENT','DEFER_IDENTITY_REVIEW')",
            name="ck_source_assertion_identity_resolutions_action",
        ),
        # Action/target consistency, mirroring UnknownAirportCandidateReview's
        # own dual-constraint MATCH_EXISTING_AIRPORT pattern exactly: a
        # matched_airport_id is required for, and only for, the attach action.
        CheckConstraint(
            "(action != 'ATTACH_TO_EXISTING_AIRPORT') OR matched_airport_id IS NOT NULL",
            name="ck_source_assertion_identity_resolutions_attach_target_required",
        ),
        CheckConstraint(
            "(action = 'ATTACH_TO_EXISTING_AIRPORT') OR matched_airport_id IS NULL",
            name="ck_source_assertion_identity_resolutions_target_only_for_attach",
        ),
        # See module docstring, CAUSAL-INTEGRITY: proves the recorded
        # snapshot genuinely belongs to this row's own source_assertion_id,
        # not merely that it references some real snapshot row.
        ForeignKeyConstraint(
            ["evidence_bag_snapshot_id", "source_assertion_id"],
            ["source_assertion_evidence_bags.id", "source_assertion_evidence_bags.source_assertion_id"],
            name="fk_source_assertion_identity_resolutions_snapshot_matches_assertion",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Not unique - append-only, many resolution rows may exist per assertion
    # over time (multiple DEFER/REJECT_ATTACHMENT entries are legitimate;
    # see design doc S12). No standalone ForeignKey() here - covered by the
    # composite ForeignKeyConstraint above, together with
    # evidence_bag_snapshot_id.
    source_assertion_id: Mapped[int] = mapped_column(ForeignKey("source_assertions.id"), index=True)

    # Which exact, immutable snapshot this decision was made against -
    # auditability: proves the human reviewed the original, complete
    # evidence, never something reconstructed or approximated. Covered by
    # the composite ForeignKeyConstraint above (see __table_args__), not a
    # standalone ForeignKey().
    evidence_bag_snapshot_id: Mapped[int] = mapped_column(index=True)

    action: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)

    # Plain free-text identity - matches ReviewerAction.reviewer/
    # UnknownAirportCandidateReview.reviewer exactly (RWI has no auth
    # infrastructure).
    reviewer: Mapped[str] = mapped_column(String(100))

    # Populated only for action == "ATTACH_TO_EXISTING_AIRPORT" (enforced by
    # the CHECK constraints above and re-checked in Python): the existing,
    # already-canonical Airport this SourceAssertion is claimed to actually
    # belong to. Never used to create or modify that Airport.
    matched_airport_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("airports.id"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Self-referential, audit-only metadata - mirrors
    # UnknownAirportCandidateReview.supersedes_review_id/
    # UnknownAirportCandidateRelevanceReview.supersedes_review_id exactly.
    # "Current" state is still always derived by recency, never by walking
    # this chain.
    supersedes_resolution_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("source_assertion_identity_resolutions.id"), nullable=True, index=True
    )

    # All one-directional (no back_populates) - see module docstring.
    source_assertion: Mapped["SourceAssertion"] = relationship(foreign_keys=[source_assertion_id])
    # overlaps="source_assertion": the composite FK above legitimately
    # shares the source_assertion_id column with the `source_assertion`
    # relationship above - this is the intended, reviewed causal-integrity
    # design (see __table_args__), not an accidental ambiguity, silencing
    # SQLAlchemy's own generic "overlapping relationship" warning exactly
    # as IdentityGuardEvaluation's own identical case does.
    evidence_bag_snapshot: Mapped["SourceAssertionEvidenceBag"] = relationship(overlaps="source_assertion")
    matched_airport: Mapped[Optional["Airport"]] = relationship(foreign_keys=[matched_airport_id])
    supersedes: Mapped[Optional["SourceAssertionIdentityResolution"]] = relationship(
        remote_side="SourceAssertionIdentityResolution.id", foreign_keys=[supersedes_resolution_id]
    )


@event.listens_for(SourceAssertionIdentityResolution, "before_update")
def _prevent_source_assertion_identity_resolution_update(_mapper, _connection, _target) -> None:
    raise ValueError(
        "SourceAssertionIdentityResolution rows are immutable; record a new resolution "
        "(optionally naming this one as supersedes_resolution_id) instead of editing an existing one."
    )


@event.listens_for(SourceAssertionIdentityResolution, "before_delete")
def _prevent_source_assertion_identity_resolution_delete(_mapper, _connection, _target) -> None:
    raise ValueError("SourceAssertionIdentityResolution rows are auditable and cannot be deleted.")
