"""Lightweight human-review decisions for known-Airport-staged funding
evidence (RWI HQ "Funding Human Review Gate - Slice B").

    SourceAssertion (known-Airport-staged, assertion_type="project_construction",
        eligible per app.services.known_airport_funding_lightweight_path_guard)
        -> record_lightweight_funding_reviewer_action()
        -> one immutable ReviewerAction row appended (the SAME model/table
           app.services.reviewer_action_persistence writes)
        -> STOP (no Signal, no publication - see
           app.services.known_airport_funding_signal_creation for the
           separate, APPROVE_SIGNAL-gated creation step)

This module is the lightweight-path SIBLING to
app.services.reviewer_action_persistence.record_reviewer_action() - it never
imports, calls, or modifies that function, and it writes exactly the same
`ReviewerAction` ORM class from the exact same table. `REVIEWER_ACTIONS` (the
DB-CHECK-constrained vocabulary on the model itself) is NOT extended or
duplicated - this module only narrows which of the SIX existing values it
itself will accept, to the four HQ specified: APPROVE_SIGNAL, MARK_DUPLICATE,
NEEDS_MORE_EVIDENCE, REJECT_SIGNAL. DEFER and CONFIRM_DISTINCT_SIGNAL remain
valid ReviewerAction vocabulary in general (unaffected, untouched) but are
simply not offered through this narrower entry point - a caller wanting
either of those for a heavy-pipeline row still uses
app.services.reviewer_action_persistence.record_reviewer_action() unchanged.

GATE, replacing the heavy path's EB5/intelligence-review/promotion-policy
triple gate (see docs/architecture, RWI HQ "Funding Human Review & Promotion
Contract Recon" mission, Section 2): every action recorded through this
module first requires
app.services.known_airport_funding_lightweight_path_guard.check_lightweight_funding_path_eligibility()
to pass - applied uniformly to all four supported actions (not merely
APPROVE_SIGNAL/MARK_DUPLICATE), so a caller can never get WEAKER validation
by mistakenly routing a non-lightweight SourceAssertion through this seam
than app.services.reviewer_action_persistence would apply. The EB5 effective-
identity check itself is never invoked here - the lightweight guard's own
field-shape check (airport_id already resolved by the known-Airport staging
seam's own governed construction, per that module's docstring) IS this
path's identity-sufficiency proxy, exactly as the recon mission concluded.

MARK_DUPLICATE keeps every existing requirement from the DB CHECK constraints
on `reviewer_actions` unchanged: `duplicate_of_signal_id` is required and
must reference a real, existing Signal. This module additionally re-checks
both in Python before insert (mirroring record_reviewer_action()'s own
defense-in-depth discipline), so a constraint violation is never the first
sign of a caller error.

ReviewerAction rows written here remain append-only (the model's own
before_update/before_delete event listeners apply identically - this module
performs no update or delete of any kind) and are read by the SAME
`get_latest_reviewer_action()` app.services.reviewer_action_persistence
already exports - reused directly here, never reimplemented, so "current"
never has two competing definitions across the heavy and lightweight paths.

A ReviewerAction row this module writes is, by construction, indistinguishable
in the `reviewer_actions` table from one `record_reviewer_action()` would
have written for the same action/reason/reviewer - this is deliberate: the
downstream reader (`get_latest_reviewer_action()`,
`link_source_assertion_to_duplicate_signal()`) needs no awareness of which
path recorded it. Which write PATH was and was not eligible to record it is
what differs, not the row's own shape.

Never commits and never imports app.database.SessionLocal - mutates the
caller-supplied Session and flushes only so a constraint violation surfaces
immediately; the caller owns the transaction boundary entirely, matching
every other persistence service in this pipeline.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models import Signal, SourceAssertion
from app.models.reviewer_action import ReviewerAction
from app.services.known_airport_funding_lightweight_path_guard import (
    check_lightweight_funding_path_eligibility,
)
from app.services.reviewer_action_persistence import get_latest_reviewer_action

__all__ = [
    "LIGHTWEIGHT_FUNDING_REVIEWER_ACTIONS",
    "record_lightweight_funding_reviewer_action",
    "get_latest_reviewer_action",
]

# The narrower, four-value subset of app.models.reviewer_action.REVIEWER_ACTIONS
# this module accepts - HQ's own explicit list, not the full six-value
# vocabulary. Not a schema change: REVIEWER_ACTIONS itself is untouched.
LIGHTWEIGHT_FUNDING_REVIEWER_ACTIONS = (
    "APPROVE_SIGNAL",
    "MARK_DUPLICATE",
    "NEEDS_MORE_EVIDENCE",
    "REJECT_SIGNAL",
)


def record_lightweight_funding_reviewer_action(
    session: Session,
    source_assertion: SourceAssertion,
    *,
    action: str,
    reason: str,
    reviewer: str,
    supersedes_action_id: Optional[int] = None,
    duplicate_of_signal_id: Optional[int] = None,
) -> ReviewerAction:
    """Validates and appends exactly one ReviewerAction row for a
    lightweight known-Airport-staged funding SourceAssertion. Never commits;
    calls session.flush() only so a constraint violation surfaces
    immediately. Never mutates source_assertion, never mutates or creates a
    Signal.
    """
    if action not in LIGHTWEIGHT_FUNDING_REVIEWER_ACTIONS:
        raise ValueError(
            f"action must be one of {LIGHTWEIGHT_FUNDING_REVIEWER_ACTIONS!r} through this lightweight funding "
            f"entry point, got {action!r} - DEFER/CONFIRM_DISTINCT_SIGNAL remain available only via "
            "app.services.reviewer_action_persistence.record_reviewer_action() for heavy-pipeline rows."
        )
    if not reason.strip():
        raise ValueError("reason is required for a reviewer action")
    if not reviewer.strip():
        raise ValueError("reviewer is required for a reviewer action")

    if source_assertion.id is None:
        raise ValueError("source_assertion must already be persisted (has no id)")
    if session.get(SourceAssertion, source_assertion.id) is None:
        raise ValueError("referenced SourceAssertion does not exist")

    # The lightweight-path gate - see module docstring "GATE". Applied
    # uniformly to all four supported actions.
    check_lightweight_funding_path_eligibility(source_assertion)

    if action == "MARK_DUPLICATE":
        if duplicate_of_signal_id is None:
            raise ValueError("MARK_DUPLICATE requires duplicate_of_signal_id")
        if session.get(Signal, duplicate_of_signal_id) is None:
            raise ValueError("referenced Signal (duplicate_of_signal_id) does not exist")
    elif duplicate_of_signal_id is not None:
        raise ValueError("duplicate_of_signal_id is only valid when action == MARK_DUPLICATE")

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
        reconciliation_fingerprint=None,
    )
    session.add(record)
    session.flush()
    return record
