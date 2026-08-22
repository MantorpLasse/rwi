"""Governed unknown-airport candidate persistence foundation (UAC1,
docs/architecture/rwi-uac1-unknown-airport-candidate-persistence-report.md,
Slice 1 of docs/architecture/rwi-governed-new-airport-discovery-design.md).

An UnknownAirportCandidate is NOT a shadow Airport. It represents:
"Evidence appears to refer to an airport that RWI cannot yet safely attach
to a canonical Airport." It has no relationship to Runway, RunwayEnd,
Installation, or Signal, and cannot participate in any canonical domain
logic - by construction, it carries only claimed (raw_*) fields, never
validated or normalized as if authoritative, exactly the same discipline
app.models.source_assertion.SourceAssertion already applies to its own
raw_* fields.

`resolved_airport_id` exists on the model now (matching the design
document's own §4 field list) but is never read or written by any UAC1
code - no service in this module constructs, queries by, or mutates it.
It is inert schema, reserved for the not-yet-built governed resolution
services (create_airport_from_approved_candidate() /
link_candidate_to_existing_airport()) named in the design document's §8
and explicitly out of scope for this slice. See
tests/test_unknown_airport_candidate_persistence.py::TestNoCanonicalSideEffects
for the tests proving this.

Deliberately no `review_state` column on UnknownAirportCandidate: RWI's
established convention (ReviewerAction/InstallationAssertionLink) is that
"current" review state is always derived by recency from the append-only
review history, never cached on the parent row - caching it here would
risk exactly the kind of stale-field drift this project's own D4D8 work
repeatedly guarded against. See get_latest_unknown_airport_candidate_review()
in app/services/unknown_airport_candidate_persistence.py.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, event, inspect
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# The approved review vocabulary (docs/architecture/rwi-governed-new-airport-discovery-design.md
# §7) - deliberately NOT app.models.reviewer_action.REVIEWER_ACTIONS: that
# vocabulary answers "is this evidence worth a Signal," a structurally
# different, separately governed question from "does this evidence
# identify a real airport, and if so which one." Reusing ReviewerAction's
# table/vocabulary here would blur two independently governed decisions
# the same way this pipeline has repeatedly refused to blur
# identity_guard_decision/intelligence_review_decision/promotion_policy_decision
# into one column.
UNKNOWN_AIRPORT_CANDIDATE_REVIEW_ACTIONS = (
    "MATCH_EXISTING_AIRPORT",
    "CREATE_NEW_AIRPORT",
    "REJECT_CANDIDATE",
    "DEFER",
)


class UnknownAirportCandidate(Base):
    """One claimed, still-unresolved airport identity, derived from
    discovery evidence. `candidate_fingerprint` is the deterministic exact-
    convergence key (see compute_candidate_fingerprint() in
    app/services/unknown_airport_candidate_persistence.py) - a UNIQUE
    constraint enforces exact-fingerprint identity at the DB layer,
    mirroring SourceAssertion's own DB-enforced fragment-identity
    UniqueConstraint, since the fingerprint IS this table's whole identity
    the same way (source_id, artifact_identity, source_locator,
    raw_fragment_hash) is SourceAssertion's. Near-duplicate convergence
    (different fingerprint, same real airport) is deliberately NOT
    resolved here - see the design document §9: it is advisory-only and
    resolved by a human's own review action, never by a background merge.
    """

    __tablename__ = "unknown_airport_candidates"
    __table_args__ = (
        UniqueConstraint("candidate_fingerprint", name="uq_unknown_airport_candidates_fingerprint"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_fingerprint: Mapped[str] = mapped_column(String(64), index=True)

    # --- CLAIMED (raw) identity fields - never validated/normalized as
    # canonical, matching SourceAssertion's own raw_* discipline. ---
    raw_name: Mapped[str] = mapped_column(String(200))
    raw_city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    raw_state_region: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    raw_country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    raw_iata_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    raw_icao_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    raw_faa_lid: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    raw_runway_designation: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # --- Provenance/traceability, audit-only - never read by any decision
    # in this module. Populated from the evidence fragment's own identity
    # fields (mirrors CandidateFragment.artifact_identity/source_locator's
    # vocabulary) so a reviewer can trace a candidate back to what
    # produced it, without this table taking on a live FK relationship to
    # SourceAssertion - that integration is UAC2's explicit scope (design
    # doc §5), not this slice's. ---
    evidence_source_locator: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evidence_artifact_identity: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Inert until a future resolution slice - see module docstring.
    resolved_airport_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("airports.id"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    reviews: Mapped[list["UnknownAirportCandidateReview"]] = relationship(back_populates="candidate")
    resolved_airport: Mapped[Optional["Airport"]] = relationship(foreign_keys=[resolved_airport_id])


class UnknownAirportCandidateReview(Base):
    """An append-only human decision about one UnknownAirportCandidate.

    Modeled directly on app.models.reviewer_action.ReviewerAction and
    app.models.physical_installation_identity.InstallationAssertionLink -
    the exact structural precedent for a human-reviewed, append-only
    decision (immutability enforced by ORM event listeners, not just
    convention; free-text `reviewer` matching ReviewerAction.reviewer/
    InstallationAssertionLink.actor - RWI has no auth infrastructure;
    `supersedes_review_id` a self-referential nullable FK so a reviewer
    changing their mind appends a new row rather than editing the old
    one).

    Recording CREATE_NEW_AIRPORT here authorizes a later, separate,
    not-yet-built governed Airport-creation operation to act - exactly
    the same "records only that a human authorized" boundary
    app.services.governed_signal_creation's own module docstring
    describes for ReviewerAction.APPROVE_SIGNAL. This row never creates,
    updates, or resolves anything itself: no Airport, Runway, RunwayEnd,
    Installation, or Signal is touched anywhere in this module, and
    UnknownAirportCandidate.resolved_airport_id is never set here.
    """

    __tablename__ = "unknown_airport_candidate_reviews"
    __table_args__ = (
        CheckConstraint(
            "action IN ('MATCH_EXISTING_AIRPORT','CREATE_NEW_AIRPORT','REJECT_CANDIDATE','DEFER')",
            name="ck_unknown_airport_candidate_reviews_action",
        ),
        CheckConstraint(
            "(action != 'MATCH_EXISTING_AIRPORT') OR matched_airport_id IS NOT NULL",
            name="ck_unknown_airport_candidate_reviews_match_target_required",
        ),
        CheckConstraint(
            "(action = 'MATCH_EXISTING_AIRPORT') OR matched_airport_id IS NULL",
            name="ck_unknown_airport_candidate_reviews_match_target_only_for_match",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("unknown_airport_candidates.id"), index=True)
    action: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)

    # Plain free-text identity - matches ReviewerAction.reviewer exactly.
    reviewer: Mapped[str] = mapped_column(String(100))

    # Populated only for action == "MATCH_EXISTING_AIRPORT" (enforced by
    # the CHECK constraints above and re-checked in Python): the existing
    # Airport this candidate is claimed to actually be. Never used to
    # create or modify that Airport - resolving the link is a separate,
    # future, not-yet-built operation (design doc §8/§10).
    matched_airport_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("airports.id"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    supersedes_review_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("unknown_airport_candidate_reviews.id"), nullable=True, index=True
    )

    candidate: Mapped["UnknownAirportCandidate"] = relationship(back_populates="reviews")
    matched_airport: Mapped[Optional["Airport"]] = relationship(foreign_keys=[matched_airport_id])
    supersedes: Mapped[Optional["UnknownAirportCandidateReview"]] = relationship(
        remote_side="UnknownAirportCandidateReview.id", foreign_keys=[supersedes_review_id]
    )


@event.listens_for(UnknownAirportCandidateReview, "before_update")
def _prevent_unknown_airport_candidate_review_update(_mapper, _connection, _target) -> None:
    raise ValueError("Unknown-airport candidate reviews are immutable; record a superseding review instead.")


@event.listens_for(UnknownAirportCandidateReview, "before_delete")
def _prevent_unknown_airport_candidate_review_delete(_mapper, _connection, _target) -> None:
    raise ValueError("Unknown-airport candidate reviews are auditable and cannot be deleted.")


# Field-level (not row-level) immutability, added during UAC1's own
# critical review: every column on UnknownAirportCandidate EXCEPT
# resolved_airport_id is a source-derived claim fixed at creation time -
# candidate_fingerprint, raw_name, and every other raw_*/evidence_* field
# must never be silently rewritten after a review may already have been
# recorded against the claim as it stood at creation. resolved_airport_id
# is deliberately excluded: it is inert in UAC1 (never read or written by
# any function in this module - see the class docstring) but must remain
# assignable for the future governed resolution service (design doc §8,
# not built in UAC1) that will eventually set it - a blanket row-level
# immutability guard (like UnknownAirportCandidateReview's own) would
# block that intended future write path entirely, so this listener
# checks column-level history instead of rejecting every UPDATE
# unconditionally.
_CANDIDATE_MUTABLE_AFTER_CREATION_COLUMNS = frozenset({"resolved_airport_id"})


@event.listens_for(UnknownAirportCandidate, "before_update")
def _prevent_unknown_airport_candidate_claim_mutation(_mapper, _connection, target) -> None:
    state = inspect(target)
    for attr in state.mapper.column_attrs:
        if attr.key in _CANDIDATE_MUTABLE_AFTER_CREATION_COLUMNS:
            continue
        if state.get_history(attr.key, True).has_changes():
            raise ValueError(
                f"UnknownAirportCandidate.{attr.key} is immutable after creation - source-derived "
                "identity claims must never be silently rewritten. Only resolved_airport_id may be "
                "set later, by a future governed resolution service."
            )
