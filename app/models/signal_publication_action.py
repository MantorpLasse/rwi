from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# Matches the ALL_CAPS_WITH_UNDERSCORES vocabulary convention this pipeline
# already uses for every other persisted human-decision column
# (ReviewerAction.action, identity_guard_decision, intelligence_review_decision,
# promotion_policy_decision). A plain tuple + DB CHECK constraint, not a
# Python str Enum on the model - this table is a persisted human-decision
# record, exactly like ReviewerAction (whose own `action` column is a plain
# String + CHECK, not an Enum), not a pure evaluation-core output.
PUBLICATION_ACTIONS = ("PUBLISH", "UNPUBLISH")


class SignalPublicationAction(Base):
    """An append-only human governance decision about one Signal's public
    visibility ("RWI - Signal Publication Governance - Design + Implementation"
    mission).

    Records ONLY the reviewer's publish/unpublish decision - it never
    creates, edits, or deletes a Signal, never touches any of its content
    fields (title, category, confidence, status, financial fields, ...),
    and never governs SourceAssertion/ReviewerAction/promotion-policy state.
    `app.services.signal_publication.publish_signal()`/`unpublish_signal()`
    are the only writers of this table; they compose an eligibility check
    with appending exactly one row here and atomically flipping the
    already-existing `Signal.published` denormalized flag (see that column's
    own docstring in app/models/signal.py, and
    docs/architecture/signal-publication-separation-slice9a-report.md) - this
    table is the audit trail for what that flag has ever been set to, and by
    whom, and why; it is never itself read by the static exporter.

    Modeled directly on `app.models.reviewer_action.ReviewerAction`, the
    exact structural precedent in this repository for an append-only,
    human-attributed, immutable decision log: immutability enforced the same
    way (ORM before_update/before_delete event listeners, not just
    convention), `reviewer` a plain free-text identity field (no auth/user-
    management FK - RWI has none, matching ReviewerAction.reviewer and
    InstallationAssertionLink.actor exactly), and `supersedes_action_id` a
    self-referential nullable FK so a reviewer changing their mind (e.g.
    publishing, then later unpublishing) appends a new row rather than
    editing or deleting the old one. "Current" publication state is always
    derived by recency (see `get_latest_signal_publication_action()` in
    app.services.signal_publication) - not by walking this chain - matching
    ReviewerAction's own §2.2-derived discipline exactly.

    `signal` is a deliberately ONE-WAY relationship (no back-populated
    collection added to `Signal`) - mirrors `ReviewerAction.duplicate_of_signal`
    exactly, and keeps this mission's own "NO SIGNAL CONTENT CHANGES"
    boundary literal: `app/models/signal.py` is never edited by this slice.
    """

    __tablename__ = "signal_publication_actions"
    __table_args__ = (
        CheckConstraint(
            "action IN ('PUBLISH','UNPUBLISH')",
            name="ck_signal_publication_actions_action",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    action: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)

    # Plain free-text identity (e.g. a name or email) - matches
    # ReviewerAction.reviewer exactly. See that column's own docstring for
    # why this repository has no authentication/user-management FK to point
    # to instead.
    reviewer: Mapped[str] = mapped_column(String(100))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Self-referential: a later publication decision supersedes an earlier
    # one without deleting or editing it. Optional metadata for explicit
    # traceability - "current" state is still always derived by recency (see
    # get_latest_signal_publication_action() in
    # app.services.signal_publication), not by walking this chain, matching
    # ReviewerAction.supersedes_action_id's own precedent exactly.
    supersedes_action_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("signal_publication_actions.id"), nullable=True, index=True
    )

    signal: Mapped["Signal"] = relationship()
    supersedes: Mapped[Optional["SignalPublicationAction"]] = relationship(
        remote_side="SignalPublicationAction.id", foreign_keys=[supersedes_action_id]
    )


@event.listens_for(SignalPublicationAction, "before_update")
def _prevent_signal_publication_action_update(_mapper, _connection, _target) -> None:
    raise ValueError("Signal publication actions are immutable; record a superseding action instead.")


@event.listens_for(SignalPublicationAction, "before_delete")
def _prevent_signal_publication_action_delete(_mapper, _connection, _target) -> None:
    raise ValueError("Signal publication actions are auditable and cannot be deleted.")
