"""ERG3 - append-only persistence for HUMAN EMAS-relevance review decisions
(docs/architecture/rwi-erg3-human-relevance-review-recording-report.md).

A row represents exactly: "human reviewer X reviewed automatic relevance
assessment Y (app.models.unknown_airport_candidate_relevance_assessment.
UnknownAirportCandidateRelevanceAssessment) and recorded decision Z with
reason R." It NEVER mutates the assessment it reviews (that table remains
ERG2's own exclusive write scope), NEVER mutates candidate identity truth
(UnknownAirportCandidate/UnknownAirportCandidateReview, UAC1-5's own
exclusive scope), and NEVER itself creates a canonical Airport, Installation,
or Signal - see app.services.unknown_airport_candidate_relevance_review_persistence,
the only writer, for the full governance chain this row sits in.

IDENTITY DISCOVERY != AUTOMATIC EMAS RELEVANCE ASSESSMENT != HUMAN EMAS
RELEVANCE REVIEW != CANONICAL AIRPORT ADMISSION != SIGNAL CREATION (this
mission's own locked principle, restated because this table implements
exactly the third term): `UnknownAirportCandidateReview` answers "is this
identity real, and should it become/match a canonical Airport" - an
identity/canonical-resolution question. This table answers a structurally
DIFFERENT question - "does this airport's evidence matter to RWI's EMAS
mission" - and is DELIBERATELY NOT merged into `UnknownAirportCandidateReview`'s
own action vocabulary, matching this codebase's own repeated, already-reviewed
refusal to blur identity_guard_decision/intelligence_review_decision/
promotion_policy_decision/ReviewerAction into one column (see
docs/architecture/rwi-emas-relevance-gate-design.md S12, and ERG1's own
"identity truth vs. business relevance, never blurred" principle, re-applied
here one governance layer further downstream).

BASIS-ASSESSMENT BINDING, THE CENTRAL DISCIPLINE: every review references
exactly one `basis_assessment_id` - the SPECIFIC automatic assessment the
human actually reviewed, never "whatever is current now." A stale basis
(a NEWER automatic assessment has since been recorded for the same
candidate) must never silently authorize the CURRENT candidate state - see
the persistence service's own `_require_current_basis_assessment()` for the
write-time gate, and `resolve_effective_relevance_review_state()` for the
read-time contract a future UAC4 gate will need. This mirrors
`app.services.reviewer_action_persistence`'s own R4B "reconciliation
fingerprint must match the CURRENT blocking state" discipline and UAC4's
own `_require_current_review()` staleness check, applied to a new kind of
"current" (latest assessment, not latest review of a different table).

CROSS-CANDIDATE INTEGRITY - SERVICE-LEVEL ONLY, DELIBERATELY, LEARNED
DIRECTLY FROM ERG2 (docs/architecture/rwi-erg2-relevance-assessment-persistence-report.md
S34 S5): a composite-FK schema-level guarantee that
`basis_assessment.candidate_id == candidate_id` was considered and NOT
attempted - the technique would require adding a `UNIQUE(id, candidate_id)`
constraint to `UnknownAirportCandidateRelevanceAssessment`, an
ALREADY-COMMITTED model this project's own precedent (and ERG2's own
review) treats as off-limits to retroactively alter without overwhelming
justification, exactly the same reasoning that made ERG2's own analogous
attempt against `source_assertions` unsafe - regardless of whether that
table happens to hold zero real rows today, a later migration cannot
safely assume it will still be empty at the time it actually runs for
real. Same-candidate integrity for `basis_assessment_id` is therefore
enforced at the SERVICE layer only
(app.services.unknown_airport_candidate_relevance_review_persistence's own
explicit `basis_assessment.candidate_id != candidate.id` check, before any
row is constructed) - a raw-SQL insert that bypassed that service entirely
could still create a mismatched review; this is a documented, accepted
boundary, not an oversight, proven by a permanent regression test.

Immutable (ORM before_update/before_delete event listeners), matching
ReviewerAction/UnknownAirportCandidateReview/
UnknownAirportCandidateRelevanceAssessment's own identical precedent - a
human changing their mind always appends a NEW review row (optionally
naming the superseded one via `supersedes_review_id`, audit-only metadata,
never load-bearing for "what is current" - see the persistence service's
own docstring), never edits an existing one.

Deliberately does NOT add a `relevance_reviews` back-populated collection
to UnknownAirportCandidate or a reciprocal collection to
UnknownAirportCandidateRelevanceAssessment - mirrors
UnknownAirportCandidateRelevanceAssessment's own explicit, already-reviewed
reasoning verbatim: not required for correctness, and would touch two
already-migrated, already-adversarially-reviewed model files for a
capability that does not need it. This module is therefore fully
additive.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Narrow, deliberately NOT including downstream-effect actions
# (WATCH/CREATE_AIRPORT/CREATE_SIGNAL/PROMOTE/...) - those are separate,
# later, human-gated governance questions (UAC4/Signal creation), never
# relevance-review actions themselves. See the persistence report's own
# "review vocabulary sufficiency" section for the full derivation.
RELEVANCE_REVIEW_ACTIONS = (
    "CONFIRM_EMAS_RELEVANT",
    "MARK_NOT_EMAS_RELEVANT",
    "DEFER_RELEVANCE_REVIEW",
)


class UnknownAirportCandidateRelevanceReview(Base):
    """One append-only, immutable record of a human's EMAS-relevance
    judgment against one specific automatic
    UnknownAirportCandidateRelevanceAssessment. See module docstring for
    the full design rationale.

    No outcome/reason/evidence-class/boolean columns are duplicated from
    the basis assessment here - all of that remains reachable, exactly
    once, via `basis_assessment_id` (mission's own explicit "no redundant
    storage" instruction); this row stores only the HUMAN's own judgment
    and its provenance."""

    __tablename__ = "unknown_airport_candidate_relevance_reviews"
    __table_args__ = (
        CheckConstraint(
            "action IN ('CONFIRM_EMAS_RELEVANT','MARK_NOT_EMAS_RELEVANT','DEFER_RELEVANCE_REVIEW')",
            name="ck_unknown_airport_candidate_relevance_reviews_action",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("unknown_airport_candidates.id"), index=True
    )

    # Ordinary (non-composite) FK: guarantees the referenced assessment ROW
    # exists; does NOT and cannot guarantee it belongs to `candidate_id` -
    # see module docstring's own CROSS-CANDIDATE INTEGRITY section for why
    # that stronger guarantee is service-level only in this slice.
    basis_assessment_id: Mapped[int] = mapped_column(
        ForeignKey("unknown_airport_candidate_relevance_assessments.id"), index=True
    )

    action: Mapped[str] = mapped_column(String(30))
    reviewer: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    # Self-referential: a later reviewer decision explicitly supersedes an
    # earlier one without deleting or editing it - audit-only metadata,
    # exactly mirroring ReviewerAction.supersedes_action_id/
    # UnknownAirportCandidateReview.supersedes_review_id's own identical
    # discipline. "Current" state is still always derived by recency (see
    # get_latest_unknown_airport_candidate_relevance_review() in the
    # persistence service), never by walking this chain.
    supersedes_review_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("unknown_airport_candidate_relevance_reviews.id"), nullable=True, index=True
    )

    # One-directional (no back_populates) - see module docstring.
    candidate: Mapped["UnknownAirportCandidate"] = relationship()
    basis_assessment: Mapped["UnknownAirportCandidateRelevanceAssessment"] = relationship()
    supersedes: Mapped[Optional["UnknownAirportCandidateRelevanceReview"]] = relationship(
        remote_side="UnknownAirportCandidateRelevanceReview.id", foreign_keys=[supersedes_review_id]
    )


@event.listens_for(UnknownAirportCandidateRelevanceReview, "before_update")
def _prevent_relevance_review_update(_mapper, _connection, _target) -> None:
    raise ValueError(
        "UnknownAirportCandidateRelevanceReview rows are immutable; record a new review "
        "(optionally naming this one as supersedes_review_id) instead of editing an existing one."
    )


@event.listens_for(UnknownAirportCandidateRelevanceReview, "before_delete")
def _prevent_relevance_review_delete(_mapper, _connection, _target) -> None:
    raise ValueError("UnknownAirportCandidateRelevanceReview rows are auditable and cannot be deleted.")
