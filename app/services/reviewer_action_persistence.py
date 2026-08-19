"""Validation and creation of immutable human reviewer-action records
(docs/architecture/reviewer-action-persistence-slice9b-report.md, Slice 9B of
docs/architecture/reviewer-action-human-signal-promotion-slice9-design.md).

    SourceAssertion (already governed: identity_guard_decision,
        intelligence_review_decision, promotion_policy_decision all set)
        -> record_reviewer_action()
        -> one immutable ReviewerAction row appended
        -> STOP (no Signal, no publication, no automatic promotion - all
           future, separately-authorized slices)

This module intentionally contains no Signal-creation, Signal-update, or
Signal-publication behavior whatsoever - it never imports the `Signal`
constructor's write path and never sets any attribute on a Signal instance.
`Signal` is imported only to validate that a `duplicate_of_signal_id` target
actually exists, exactly the same read-only existence check
app.services.physical_installation_reconciliation.record_reconciliation_decision
performs against `PhysicalInstallationIdentity`. APPROVE_SIGNAL records only
that a human authorized a later, separate, not-yet-built governed
Signal-creation operation (Slice 9C) to act - it is not that operation
itself, and this module never calls one.

Modeled directly on
app.services.physical_installation_reconciliation.record_reconciliation_decision,
the exact structural precedent for validating and inserting a human-reviewed,
append-only decision about a SourceAssertion. Never commits and never imports
app.database.SessionLocal - it mutates the caller-supplied Session and
flushes only so a constraint violation (including the immutability event
listeners on ReviewerAction) surfaces immediately; the caller owns the
transaction boundary entirely, mirroring every other persistence service in
this pipeline (intelligence_review_persistence, promotion_policy_persistence,
physical_installation_reconciliation).

APPROVAL GATE, fail-closed: APPROVE_SIGNAL may only be recorded where all
three governed decisions already agree:

    identity_guard_decision       == "ATTACH_CONFIRMED"
    intelligence_review_decision  == "REVIEW_REQUIRED"
    promotion_policy_decision     == "HUMAN_REVIEW_REQUIRED"

`intelligence_review_decision` is checked explicitly even though, given the
current pipeline's own construction, a row can never reach
promotion_policy_decision="HUMAN_REVIEW_REQUIRED" without already having
intelligence_review_decision="REVIEW_REQUIRED" - the same "checked
explicitly rather than assumed" defense-in-depth discipline
app.services.promotion_policy_persistence and
app.services.human_review_queue's own invariant warnings already apply to
this exact chain. AUTO_ELIGIBLE and DO_NOT_PROMOTE are both refused here:
AUTO_ELIGIBLE belongs to a future, separate automation/audit route (Slice
9's own design doc suggested it as also human-approvable, but this slice's
own governing instruction narrows that - see the Slice 9B report's "design
corrections discovered" section for why); DO_NOT_PROMOTE is an absolute
block no reviewer action can override at this boundary. HUMAN_REVIEW_REQUIRED
items do NOT need to first become AUTO_ELIGIBLE to be approved - that
distinction from the design doc is preserved.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models import Signal, SourceAssertion
from app.models.reviewer_action import REVIEWER_ACTIONS, ReviewerAction

__all__ = [
    "REQUIRED_IDENTITY_DECISION_FOR_APPROVAL",
    "REQUIRED_INTELLIGENCE_DECISION_FOR_APPROVAL",
    "REQUIRED_PROMOTION_DECISION_FOR_APPROVAL",
    "get_latest_reviewer_action",
    "record_reviewer_action",
]

REQUIRED_IDENTITY_DECISION_FOR_APPROVAL = "ATTACH_CONFIRMED"
REQUIRED_INTELLIGENCE_DECISION_FOR_APPROVAL = "REVIEW_REQUIRED"
REQUIRED_PROMOTION_DECISION_FOR_APPROVAL = "HUMAN_REVIEW_REQUIRED"


def record_reviewer_action(
    session: Session,
    source_assertion: SourceAssertion,
    *,
    action: str,
    reason: str,
    reviewer: str,
    supersedes_action_id: Optional[int] = None,
    duplicate_of_signal_id: Optional[int] = None,
) -> ReviewerAction:
    """Validates and appends exactly one ReviewerAction row. Never commits;
    calls session.flush() only so a constraint violation surfaces
    immediately. Never mutates source_assertion, never mutates or creates a
    Signal.
    """
    if action not in REVIEWER_ACTIONS:
        raise ValueError(f"action must be one of {REVIEWER_ACTIONS!r}, got {action!r}")
    if not reason.strip():
        raise ValueError("reason is required for a reviewer action")
    if not reviewer.strip():
        raise ValueError("reviewer is required for a reviewer action")

    if source_assertion.id is None:
        raise ValueError("source_assertion must already be persisted (has no id)")
    if session.get(SourceAssertion, source_assertion.id) is None:
        raise ValueError("referenced SourceAssertion does not exist")

    if action == "MARK_DUPLICATE":
        if duplicate_of_signal_id is None:
            raise ValueError("MARK_DUPLICATE requires duplicate_of_signal_id")
        if session.get(Signal, duplicate_of_signal_id) is None:
            raise ValueError("referenced Signal (duplicate_of_signal_id) does not exist")
    elif duplicate_of_signal_id is not None:
        raise ValueError("duplicate_of_signal_id is only valid when action == MARK_DUPLICATE")

    if action == "APPROVE_SIGNAL":
        if source_assertion.identity_guard_decision != REQUIRED_IDENTITY_DECISION_FOR_APPROVAL:
            raise ValueError(
                "APPROVE_SIGNAL requires identity_guard_decision == "
                f"{REQUIRED_IDENTITY_DECISION_FOR_APPROVAL!r}, got "
                f"{source_assertion.identity_guard_decision!r}"
            )
        if source_assertion.intelligence_review_decision != REQUIRED_INTELLIGENCE_DECISION_FOR_APPROVAL:
            raise ValueError(
                "APPROVE_SIGNAL requires intelligence_review_decision == "
                f"{REQUIRED_INTELLIGENCE_DECISION_FOR_APPROVAL!r}, got "
                f"{source_assertion.intelligence_review_decision!r}"
            )
        if source_assertion.promotion_policy_decision != REQUIRED_PROMOTION_DECISION_FOR_APPROVAL:
            raise ValueError(
                "APPROVE_SIGNAL requires promotion_policy_decision == "
                f"{REQUIRED_PROMOTION_DECISION_FOR_APPROVAL!r}, got "
                f"{source_assertion.promotion_policy_decision!r}"
            )

    if supersedes_action_id is not None:
        previous = session.get(ReviewerAction, supersedes_action_id)
        if previous is None or previous.source_assertion_id != source_assertion.id:
            raise ValueError("superseded action must exist and concern the same SourceAssertion")

    record = ReviewerAction(
        source_assertion_id=source_assertion.id,
        action=action,
        reason=reason.strip(),
        reviewer=reviewer.strip(),
        supersedes_action_id=supersedes_action_id,
        duplicate_of_signal_id=duplicate_of_signal_id,
    )
    session.add(record)
    session.flush()
    return record


def get_latest_reviewer_action(session: Session, source_assertion_id: int) -> Optional[ReviewerAction]:
    """The effective current reviewer action for a SourceAssertion: the most
    recently recorded ReviewerAction row, ordered by created_at then id (the
    same tiebreak discipline app.services.human_review_queue uses for its
    own ordering). "Latest" means "most recently recorded," not "the
    unsuperseded terminal node reached by walking supersedes_action_id" -
    with an append-only log, recency alone already identifies the current
    state regardless of whether a row explicitly named its predecessor, so
    no chain-walking is needed (matches the design doc's own §2.2
    reasoning). Returns None if no action has ever been recorded for this
    SourceAssertion.
    """
    actions = (
        session.query(ReviewerAction)
        .filter(ReviewerAction.source_assertion_id == source_assertion_id)
        .order_by(ReviewerAction.created_at.desc(), ReviewerAction.id.desc())
        .limit(1)
        .all()
    )
    return actions[0] if actions else None
