from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# RWI HQ "SLT2 - Governed Signal Lifecycle Assessment" mission, implementing
# the SLT2 slice already specified in
# docs/architecture/rwi-signal-temporal-relevance-opportunity-lifecycle-design.md
# (§8 "Option E", §9, §14, §16).
#
#     app.static_export.signal_lifecycle.derive_signal_lifecycle() (SLT1)
#         -> a cheap, always-available, presentation-only MACHINE BASELINE,
#            computed fresh every time from Signal's own structured fields.
#            Unchanged by this mission.
#         +
#     SignalLifecycleAssessment (SLT2, this table)
#         -> an append-only, human-authored GOVERNED OVERRIDE, recorded only
#            when a human has reviewed the fuller evidence context and
#            concluded the machine baseline undersells or oversells the
#            Signal's current intelligence relevance.
#         -> "latest row wins" (app.services.signal_lifecycle_assessment
#            .resolve_effective_signal_lifecycle()), the exact same pattern
#            already proven for SourceAssertionIdentityResolution/
#            IdentityGuardEvaluation - never a mutable "current belief"
#            column on Signal itself (design doc §8's own explicit top risk:
#            a mutable column is precisely what silently rewrites history if
#            a later job reruns with different logic).
#
# Modeled directly on app.models.signal_amendment.SignalAmendmentAction - the
# closest existing structural precedent for "one append-only, human-
# authored, reason-carrying row about an existing Signal, immutable once
# written, no supersedes_id needed because a plain recency query already
# answers 'what is current'" (see that model's own module docstring for the
# identical reasoning, reused verbatim here).
#
# STRUCTURALLY SEPARATE FROM PROJECT STATUS: this table records lifecycle
# RELEVANCE only ("how should this intelligence item be treated now") -
# never Signal.status, Signal.completion_date, Signal.published, or any
# Installation/SourceAssertion/ReviewerAction state. Recording
# state="realized_historical" here never implies, and must never be
# confused with, Signal.status="completed" - that remains reserved
# exclusively for scripts/graduate_signal_to_installation.py's own governed,
# one-at-a-time, human-judgment path. See docs/architecture/rwi-signal-
# temporal-relevance-opportunity-lifecycle-design.md's own design principle
# section for the full rationale.
#
# NO new lifecycle vocabulary: `state` is constrained (via CHECK, matching
# ReviewerAction.action's own precedent for a small, fixed, unlikely-to-grow
# vocabulary) to exactly app.static_export.signal_lifecycle.SignalLifecycleState's
# five existing values - never re-declared here as a second source of truth,
# see app.services.signal_lifecycle_assessment's own import of that enum for
# the single place membership is actually validated before insert.


class SignalLifecycleAssessment(Base):
    """An append-only, human-authored lifecycle-relevance judgment about one
    existing Signal (SLT2). Immutable once written - a later, different
    judgment is simply another, independent row; the prior one remains,
    unedited, as part of the honest historical record (mirrors
    SignalAmendmentAction exactly - see this module's own docstring above).

    Never mutates the Signal itself, never touches SourceAssertion,
    ReviewerAction, or Installation state - a pure, additional, separately
    governed read layered on top of the Signal it describes.
    """

    __tablename__ = "signal_lifecycle_assessments"
    __table_args__ = (
        CheckConstraint(
            "state IN ('active_opportunity','developing_watch','stale_unresolved',"
            "'realized_historical','other')",
            name="ck_signal_lifecycle_assessments_state",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)

    # Plain string, not the SignalLifecycleState Python enum directly - this
    # table is a persisted human-decision record (like ReviewerAction.action),
    # not a pure evaluation-core output; membership in the fixed vocabulary
    # is enforced by the CHECK constraint above plus a Python-level check in
    # app.services.signal_lifecycle_assessment.record_signal_lifecycle_assessment()
    # before insert, exactly like that module's own precedent.
    state: Mapped[str] = mapped_column(String(30))

    # Mandatory: must explain why the human assessment differs from, or
    # confirms, the SLT1 machine state - enforced non-blank at the service
    # layer (matching SignalAmendmentAction.reason's own Text/non-blank
    # convention), never a DB-level length/emptiness constraint.
    reason: Mapped[str] = mapped_column(Text)

    # Plain free-text identity - matches ReviewerAction.reviewer/
    # SignalAmendmentAction.reviewer exactly (RWI has no authentication or
    # user-management infrastructure to FK to instead).
    reviewer: Mapped[str] = mapped_column(String(100))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    signal: Mapped["Signal"] = relationship()


@event.listens_for(SignalLifecycleAssessment, "before_update")
def _prevent_signal_lifecycle_assessment_update(_mapper, _connection, _target) -> None:
    raise ValueError("Signal lifecycle assessments are immutable; record a new assessment instead.")


@event.listens_for(SignalLifecycleAssessment, "before_delete")
def _prevent_signal_lifecycle_assessment_delete(_mapper, _connection, _target) -> None:
    raise ValueError("Signal lifecycle assessments are auditable and cannot be deleted.")
