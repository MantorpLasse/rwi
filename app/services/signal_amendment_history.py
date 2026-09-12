"""Durable, append-only Signal amendment history persistence (RWI HQ
"Signal Amendment Audit Trail — Append-Only Governance" mission,
implementing docs/architecture/rwi-signal-amendment-audit-trail-design.md
§5-9, §14, §20).

    tuple[FieldChange, ...] (already-computed real diffs - see
        app.services.signal_amendment.FieldChange)
        + signal_id, reason, reviewer (+ optional source_assertion_id/
          reviewer_action_id)
        -> record_signal_amendment()
        -> one SignalAmendmentAction row + one SignalAmendmentFieldChange
           row per change, inserted atomically
        -> STOP (never mutates the Signal itself - that is
           app.services.signal_amendment.amend_signal()'s own
           responsibility, done in the same transaction immediately before
           this call, mirroring
           app.services.signal_publication.record_signal_publication_action()'s
           own "audit row is a separate, lower-level function" precedent
           exactly)

Also provides the read side: `list_signal_amendments()`.

VALIDATES ITS OWN INVARIANTS, independent of any caller (mirrors
`record_signal_group_disposition()`'s own discipline: never assumes a
caller already checked) - safely, independently testable and callable, not
merely trusted to be pre-validated by `amend_signal()`.

NO speculative broadening beyond the approved design: no `supersedes_id`
(see app.models.signal_amendment's own module docstring for why one is not
needed here), no new ReviewerAction/SignalPublicationAction vocabulary, no
JSON, no re-implementation of `app.services.existing_signal_reconciliation`'s
identity/change-decision logic - this module is a pure write-and-read seam
over an already-made decision, nothing else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.reviewer_action import ReviewerAction
from app.models.signal import Signal
from app.models.signal_amendment import (
    ACCEPTING_INITIAL_FIELD_CHANGES_ATTR,
    SignalAmendmentAction,
    SignalAmendmentFieldChange,
)
from app.models.source_assertion import SourceAssertion
from app.services.signal_amendment_serialization import (
    deserialize_amendment_value,
    expected_python_type_for_field,
    serialize_amendment_value,
)

if TYPE_CHECKING:  # pragma: no cover - type-checking only, avoids a runtime cycle
    from app.services.signal_amendment import FieldChange

__all__ = [
    "SignalAmendmentHistoryError",
    "DeserializedFieldChange",
    "record_signal_amendment",
    "list_signal_amendments",
]


class SignalAmendmentHistoryError(ValueError):
    """Raised for every refusal this module makes - a plain ValueError
    subclass, matching app.services.signal_amendment.SignalAmendmentError's
    own convention."""


def record_signal_amendment(
    session: Session,
    *,
    signal_id: int,
    changes: "tuple[FieldChange, ...]",
    reason: str,
    reviewer: str,
    source_assertion_id: Optional[int] = None,
    reviewer_action_id: Optional[int] = None,
) -> SignalAmendmentAction:
    """Validates and appends exactly one `SignalAmendmentAction` row plus
    one `SignalAmendmentFieldChange` row per entry in `changes`. Never
    commits; calls `session.flush()` only - the caller (normally
    `app.services.signal_amendment.amend_signal()`) controls the
    transaction boundary. Never mutates the `Signal` itself.

    Field values are serialized via
    `app.services.signal_amendment_serialization.serialize_amendment_value()`
    - an unsupported value type for a given field fails closed here, before
    any row is added, exactly like every other validation in this module.
    """
    if not reason.strip():
        raise SignalAmendmentHistoryError("reason is required for a signal amendment")
    if not reviewer.strip():
        raise SignalAmendmentHistoryError("reviewer is required for a signal amendment")
    if not changes:
        raise SignalAmendmentHistoryError("changes must be a non-empty sequence of field changes")

    if session.get(Signal, signal_id) is None:
        raise SignalAmendmentHistoryError(f"Signal {signal_id!r} does not exist")
    if source_assertion_id is not None and session.get(SourceAssertion, source_assertion_id) is None:
        raise SignalAmendmentHistoryError(f"supporting SourceAssertion {source_assertion_id!r} does not exist")
    if reviewer_action_id is not None and session.get(ReviewerAction, reviewer_action_id) is None:
        raise SignalAmendmentHistoryError(f"referenced ReviewerAction {reviewer_action_id!r} does not exist")

    # Serialize every value BEFORE any row is added - an unsupported type
    # for one field must refuse the whole call, not leave a partial batch
    # constructed.
    serialized: "list[tuple[str, Optional[str], Optional[str]]]" = [
        (change.field, serialize_amendment_value(change.old_value), serialize_amendment_value(change.new_value))
        for change in changes
    ]

    action = SignalAmendmentAction(
        signal_id=signal_id,
        reason=reason.strip(),
        reviewer=reviewer.strip(),
        source_assertion_id=source_assertion_id,
        reviewer_action_id=reviewer_action_id,
    )
    # Review-checkpoint-precedent addition (mirrors
    # app.services.signal_disposition_persistence.record_signal_group_disposition()
    # exactly): this transient, never-mapped attribute is the one signal
    # app.models.signal_amendment's own before_insert listener on
    # SignalAmendmentFieldChange accepts as proof a field-change row is
    # part of THIS action's initial, legitimate creation batch. Cleared in
    # the `finally` block no matter how child insertion finishes, so this
    # action object can never be reused to legitimize a later, separate
    # append.
    setattr(action, ACCEPTING_INITIAL_FIELD_CHANGES_ATTR, True)
    session.add(action)
    session.flush()  # obtain action.id

    try:
        for field_name, old_text, new_text in serialized:
            session.add(
                SignalAmendmentFieldChange(
                    action_id=action.id, field_name=field_name, old_value=old_text, new_value=new_text,
                )
            )
        session.flush()
    finally:
        setattr(action, ACCEPTING_INITIAL_FIELD_CHANGES_ATTR, False)

    return action


class DeserializedFieldChange:
    """Read-only, non-persisted view of one `SignalAmendmentFieldChange`
    with its `old_value`/`new_value` deserialized back to a typed Python
    value (design doc §20: "prefer typed deserialized values in the
    service result if the approved design supports that cleanly" - it
    does, via `field_name` alone, see
    app.services.signal_amendment_serialization)."""

    __slots__ = ("field_name", "old_value", "new_value")

    def __init__(self, field_name: str, old_value: Any, new_value: Any) -> None:
        self.field_name = field_name
        self.old_value = old_value
        self.new_value = new_value

    def __repr__(self) -> str:  # pragma: no cover - debugging convenience only
        return f"DeserializedFieldChange(field_name={self.field_name!r}, old_value={self.old_value!r}, new_value={self.new_value!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DeserializedFieldChange):
            return NotImplemented
        return (self.field_name, self.old_value, self.new_value) == (other.field_name, other.old_value, other.new_value)


def _deserialize_field_change(row: SignalAmendmentFieldChange) -> DeserializedFieldChange:
    expected_type = expected_python_type_for_field(row.field_name)
    return DeserializedFieldChange(
        field_name=row.field_name,
        old_value=deserialize_amendment_value(row.old_value, expected_type),
        new_value=deserialize_amendment_value(row.new_value, expected_type),
    )


class SignalAmendmentHistoryEntry:
    """Read-only, non-persisted projection of one `SignalAmendmentAction`
    plus its deserialized field changes - the shape
    `list_signal_amendments()` returns. Never mutated, never itself
    persisted."""

    __slots__ = (
        "action_id", "signal_id", "reviewer", "reason", "source_assertion_id",
        "reviewer_action_id", "created_at", "field_changes",
    )

    def __init__(self, action: SignalAmendmentAction) -> None:
        self.action_id = action.id
        self.signal_id = action.signal_id
        self.reviewer = action.reviewer
        self.reason = action.reason
        self.source_assertion_id = action.source_assertion_id
        self.reviewer_action_id = action.reviewer_action_id
        self.created_at = action.created_at
        self.field_changes = tuple(
            _deserialize_field_change(row)
            for row in sorted(action.field_changes, key=lambda r: r.id)
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging convenience only
        return (
            f"SignalAmendmentHistoryEntry(action_id={self.action_id!r}, signal_id={self.signal_id!r}, "
            f"reviewer={self.reviewer!r}, created_at={self.created_at!r}, field_changes={self.field_changes!r})"
        )


def list_signal_amendments(
    session: Session, signal_id: int, *, limit: Optional[int] = None,
) -> "tuple[SignalAmendmentHistoryEntry, ...]":
    """Read-only: `SELECT` only, never `add`/`flush`/`commit`. Ordered by
    `created_at` then `id` — the same deterministic tie-break convention
    used throughout this codebase (`get_latest_reviewer_action()`,
    `list_review_workflow_items()`). Eager-loads `field_changes` via
    `selectinload` (this codebase's own established preference, e.g.
    `app.services.human_review_queue`), never N+1 queries per action.
    `limit`, if given, bounds the number of actions returned (each with its
    own complete field-change set, never a truncated one)."""
    stmt = (
        select(SignalAmendmentAction)
        .where(SignalAmendmentAction.signal_id == signal_id)
        .options(selectinload(SignalAmendmentAction.field_changes))
        .order_by(SignalAmendmentAction.created_at, SignalAmendmentAction.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = session.execute(stmt).scalars().all()
    return tuple(SignalAmendmentHistoryEntry(row) for row in rows)
