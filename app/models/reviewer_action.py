from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# Matches the ALL_CAPS_WITH_UNDERSCORES vocabulary convention used throughout
# this pipeline's persisted decision columns (identity_guard_decision,
# intelligence_review_decision, promotion_policy_decision,
# PhysicalInstallationIdentity's own RECONCILIATION_OUTCOMES). Deliberately a
# plain tuple + DB CHECK constraint, not a Python str Enum on the model: this
# table is structurally a persisted human-decision record, exactly like
# InstallationAssertionLink (whose own `outcome` column is a plain
# String + CHECK, not an Enum) - not a pure evaluation-core output like
# SignalCandidateOutcome/PromotionPolicyOutcome/AttachmentOutcome, which ARE
# typed Enums because they are produced by deterministic evaluation
# functions, not recorded by a human. See
# app/services/reviewer_action_persistence.py for the Python-level
# validation that also enforces this vocabulary before insert.
REVIEWER_ACTIONS = (
    "APPROVE_SIGNAL",
    "REJECT_SIGNAL",
    "DEFER",
    "NEEDS_MORE_EVIDENCE",
    "MARK_DUPLICATE",
)


class ReviewerAction(Base):
    """An append-only human governance decision about one SourceAssertion.

    Records ONLY the reviewer's decision - it never rewrites evidence,
    Claims, identity_guard_decision, intelligence_review_decision, or
    promotion_policy_decision, and it never creates, updates, or publishes a
    Signal itself (see app/services/reviewer_action_persistence.py and
    docs/architecture/reviewer-action-persistence-slice9b-report.md).
    APPROVE_SIGNAL records only that a human authorized a later, separate,
    not-yet-built governed Signal-creation operation (Slice 9C) to act - it
    is not that operation itself.

    Modeled directly on
    app.models.physical_installation_identity.InstallationAssertionLink,
    the exact structural precedent for a human-reviewed, append-only
    decision about a SourceAssertion: immutability enforced the same way
    (ORM before_update/before_delete event listeners, not just convention),
    `reviewer` a plain free-text identity field (no auth/user-management FK,
    matching that table's own `actor` field - RWI has no such
    infrastructure), and `supersedes_action_id` a self-referential nullable
    FK so a reviewer changing their mind appends a new row rather than
    editing the old one, exactly like `supersedes_link_id`."""

    __tablename__ = "reviewer_actions"
    __table_args__ = (
        CheckConstraint(
            "action IN ('APPROVE_SIGNAL','REJECT_SIGNAL','DEFER','NEEDS_MORE_EVIDENCE','MARK_DUPLICATE')",
            name="ck_reviewer_actions_action",
        ),
        CheckConstraint(
            "(action != 'MARK_DUPLICATE') OR duplicate_of_signal_id IS NOT NULL",
            name="ck_reviewer_actions_duplicate_target_required",
        ),
        CheckConstraint(
            "(action = 'MARK_DUPLICATE') OR duplicate_of_signal_id IS NULL",
            name="ck_reviewer_actions_duplicate_target_only_for_duplicate",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_assertion_id: Mapped[int] = mapped_column(ForeignKey("source_assertions.id"), index=True)
    action: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)

    # Plain free-text identity (e.g. a name or email) - matches
    # InstallationAssertionLink.actor exactly. RWI has no authentication or
    # user-management infrastructure; inventing one here would be scope
    # creep this slice's own design doc explicitly warns against. If auth is
    # added later, this field can gain a foreign key without changing its
    # meaning.
    reviewer: Mapped[str] = mapped_column(String(100))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Self-referential: a later reviewer decision supersedes an earlier one
    # without deleting or editing it. Optional metadata for explicit
    # traceability - "current" state is still always derived by recency
    # (see get_latest_reviewer_action() in
    # app/services/reviewer_action_persistence.py), not by walking this
    # chain, matching the design doc's own §2.2 reasoning.
    supersedes_action_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("reviewer_actions.id"), nullable=True, index=True
    )

    # Populated only for action == "MARK_DUPLICATE" (enforced by the CHECK
    # constraints above and re-checked in Python): the existing Signal this
    # evidence corroborates. Never used to create or modify that Signal.
    duplicate_of_signal_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("signals.id"), nullable=True, index=True
    )

    source_assertion: Mapped["SourceAssertion"] = relationship(back_populates="reviewer_actions")
    supersedes: Mapped[Optional["ReviewerAction"]] = relationship(
        remote_side="ReviewerAction.id", foreign_keys=[supersedes_action_id]
    )
    duplicate_of_signal: Mapped[Optional["Signal"]] = relationship()


@event.listens_for(ReviewerAction, "before_update")
def _prevent_reviewer_action_update(_mapper, _connection, _target) -> None:
    raise ValueError("Reviewer actions are immutable; record a superseding action instead.")


@event.listens_for(ReviewerAction, "before_delete")
def _prevent_reviewer_action_delete(_mapper, _connection, _target) -> None:
    raise ValueError("Reviewer actions are auditable and cannot be deleted.")
