"""EB1 - append-only future re-evaluation persistence shape
(docs/architecture/rwi-eb1-evidencebag-persistence-foundation-report.md,
Slice 1 of docs/architecture/rwi-full-evidencebag-persistence-design.md).

An `IdentityGuardEvaluation` represents ONE future, post-resolution
re-run of the identity guard (app.services.evidence_attachment_guard)
against an already-canonically-resolved SourceAssertion's own frozen
EvidenceBag snapshot. EB1 defines ONLY this persistence shape - no
service writes to this table yet (that is EB4's own, separately-scoped,
separately-reviewed work); this model exists now so EB4 has an already-
reviewed foundation to build on, exactly as this mission's own governing
design intends.

HISTORICAL-FACT FIREWALL (the single most important property of this
table): `SourceAssertion.identity_guard_decision`/`identity_guard_reason`
remain the permanent, once-only, discovery-time historical fact - NOTHING
in this module ever reads, writes, or depends on them, and nothing in
this module's own tests exercises any code path capable of mutating them.
A later re-evaluation is represented ONLY as a new row here, appended,
never as an edit of that original historical column pair. See
docs/architecture/rwi-full-evidencebag-persistence-design.md S8 for the
full "historical fact vs. re-evaluation" reasoning this table
implements.

NOT a second copy of the SourceAssertion.identity_guard_decision/reason
columns, NOT a duplicate of Airport's own fields, and NOT a confidence
score of any kind - `outcome`/`reason` are the guard's own, real,
deterministic AttachmentOutcome/reason produced by a real future
evaluate_attachment() call (EB4), reused verbatim from
app.services.evidence_attachment_guard.AttachmentOutcome rather than a
second, hand-typed vocabulary copy - see the CHECK constraint below.

Deliberately does NOT modify app/models/source_assertion.py, for the
identical reasoning app.models.source_assertion_evidence_bag.SourceAssertionEvidenceBag
already gives (no back_populates, no reciprocal collection, a plain
query is always the correct read path - mirroring
get_latest_unknown_airport_candidate_review()'s own established
"current state is derived by recency, not cached, not eagerly loaded"
convention).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.services.evidence_attachment_guard import AttachmentOutcome

# Reused verbatim from the real, already-governed AttachmentOutcome enum -
# never a second, hand-typed vocabulary copy (this module's own mission's
# explicit instruction). Built once, at import time, directly from the
# enum's own members, so this CHECK constraint can never silently drift
# out of sync with AttachmentOutcome if a future slice ever adds a sixth
# outcome value.
_ATTACHMENT_OUTCOME_VALUES = tuple(outcome.value for outcome in AttachmentOutcome)
_ATTACHMENT_OUTCOME_CHECK_SQL = "outcome IN ({})".format(
    ", ".join(f"'{value}'" for value in _ATTACHMENT_OUTCOME_VALUES)
)


class IdentityGuardEvaluation(Base):
    """One historical record of a real, deterministic
    evaluate_attachment() call against an already-resolved
    SourceAssertion's own frozen EvidenceBag snapshot and its resolved
    canonical Airport. Append-only: MULTIPLE rows may exist for the same
    source_assertion_id over time (no uniqueness constraint on that
    column, unlike SourceAssertionEvidenceBag's own 1:1 shape) - a future
    re-evaluation is never deduplicated against a prior identical one
    (docs/architecture/rwi-full-evidencebag-persistence-design.md S13's
    own "always append, never skip" idempotency policy)."""

    __tablename__ = "identity_guard_evaluations"
    __table_args__ = (
        CheckConstraint(_ATTACHMENT_OUTCOME_CHECK_SQL, name="ck_identity_guard_evaluations_outcome"),
        # CRITICAL CAUSAL-INTEGRITY CONSTRAINT (adversarial-review finding):
        # a plain single-column FK on evidence_bag_snapshot_id alone would
        # only prove "this id refers to SOME real snapshot row" - it would
        # NOT prove that snapshot actually belongs to THIS row's own
        # source_assertion_id. A composite FK against
        # SourceAssertionEvidenceBag's own (id, source_assertion_id)
        # UniqueConstraint (app/models/source_assertion_evidence_bag.py)
        # makes it structurally impossible - enforced by SQLite itself,
        # not by application code - to persist an evaluation claiming to
        # concern SourceAssertion A while its evidence_bag_snapshot_id
        # actually references a snapshot belonging to a different
        # SourceAssertion B. This is the single most important integrity
        # property this table has, since its entire purpose is proving
        # "this evaluation used the correct, exact evidence."
        ForeignKeyConstraint(
            ["evidence_bag_snapshot_id", "source_assertion_id"],
            ["source_assertion_evidence_bags.id", "source_assertion_evidence_bags.source_assertion_id"],
            name="fk_identity_guard_evaluations_snapshot_matches_assertion",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Not unique - append-only, many evaluations may exist per assertion.
    # No standalone ForeignKey() here - covered by the composite
    # ForeignKeyConstraint above, together with evidence_bag_snapshot_id.
    source_assertion_id: Mapped[int] = mapped_column(ForeignKey("source_assertions.id"), index=True)

    # Which exact, immutable snapshot this evaluation read - auditability:
    # proves this evaluation used the original, complete evidence, never
    # something reconstructed or approximated. Covered by the composite
    # ForeignKeyConstraint above (see __table_args__), not a standalone
    # ForeignKey() - the composite constraint already implies "this id
    # refers to a real snapshot row" as part of proving it belongs to the
    # correct source_assertion_id.
    evidence_bag_snapshot_id: Mapped[int] = mapped_column(index=True)

    # The canonical Airport this run evaluated against (the assertion's
    # own airport_id at evaluation time, copied here so the fact survives
    # even if a future, separately-designed correction workflow ever
    # changes that assertion's own airport_id).
    evaluated_against_airport_id: Mapped[int] = mapped_column(ForeignKey("airports.id"), index=True)

    # Which UAC4 resolution this evaluation followed from, when
    # applicable - nullable since a future evaluation trigger reason
    # other than "just resolved via this review" is conceivable, and this
    # module does not presume there is exactly one possible trigger.
    triggering_review_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("unknown_airport_candidate_reviews.id"), nullable=True, index=True
    )

    # Reused verbatim from AttachmentOutcome.value - never regenerated,
    # summarized, or reinterpreted.
    outcome: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    # All one-directional (no back_populates) - see module docstring.
    source_assertion: Mapped["SourceAssertion"] = relationship()
    # overlaps="source_assertion": the composite FK above legitimately
    # shares the source_assertion_id column with the `source_assertion`
    # relationship above - this is the intended, reviewed causal-integrity
    # design (see __table_args__), not an accidental ambiguity, so this
    # silences SQLAlchemy's own generic "overlapping relationship" warning
    # exactly as its own message recommends for this documented case.
    evidence_bag_snapshot: Mapped["SourceAssertionEvidenceBag"] = relationship(overlaps="source_assertion")
    evaluated_against_airport: Mapped["Airport"] = relationship()
    triggering_review: Mapped[Optional["UnknownAirportCandidateReview"]] = relationship()


@event.listens_for(IdentityGuardEvaluation, "before_update")
def _prevent_identity_guard_evaluation_update(_mapper, _connection, _target) -> None:
    raise ValueError(
        "IdentityGuardEvaluation rows are immutable; a changed re-evaluation outcome is recorded "
        "as a new, appended row, never an edit of an existing one."
    )


@event.listens_for(IdentityGuardEvaluation, "before_delete")
def _prevent_identity_guard_evaluation_delete(_mapper, _connection, _target) -> None:
    raise ValueError("IdentityGuardEvaluation rows are auditable and cannot be deleted.")
