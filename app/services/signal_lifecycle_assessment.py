"""Durable, append-only Signal lifecycle assessment persistence and
effective-lifecycle resolution (RWI HQ "SLT2 - Governed Signal Lifecycle
Assessment" mission, implementing
docs/architecture/rwi-signal-temporal-relevance-opportunity-lifecycle-design.md
§8 "Option E", §9, §14, §16).

    signal_id, state, reason, reviewer
        -> record_signal_lifecycle_assessment()
        -> one SignalLifecycleAssessment row, appended
        -> STOP (never mutates the Signal itself - no Signal.status,
           Signal.completion_date, Signal.published, Installation, or
           SourceAssertion/ReviewerAction write of any kind)

    Signal + today
        -> resolve_effective_signal_lifecycle()
        -> always computes the SLT1 machine baseline
           (app.static_export.signal_lifecycle.derive_signal_lifecycle())
        -> reads the latest SLT2 assessment, if any
           (get_latest_signal_lifecycle_assessment())
        -> "latest row wins": the governed assessment, when present,
           becomes the effective state; the machine baseline is always
           still returned alongside it, never discarded
        -> STOP

VALIDATES ITS OWN INVARIANTS, independent of any caller (mirrors
`record_signal_amendment()`'s own discipline): never assumes a caller
already checked that the Signal exists, the state is a real lifecycle
value, or reason/reviewer are non-blank.

NO speculative broadening beyond the approved design: no supersedes_id (see
app.models.signal_lifecycle_assessment's own module docstring for why one is
not needed - a plain recency query already answers "what is current"), no
new lifecycle vocabulary, no Signal mutation of any kind, no Update & Report
wiring (out of this mission's scope).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy.orm import Session

from app.models.signal import Signal
from app.models.signal_lifecycle_assessment import SignalLifecycleAssessment

if TYPE_CHECKING:  # pragma: no cover - type-checking only, avoids a runtime cycle
    # app.static_export.build (presentation layer) imports THIS module to
    # call resolve_effective_signal_lifecycle() - a module-level import of
    # app.static_export.signal_lifecycle here would close a real import
    # cycle back through app.static_export's own package __init__ (which
    # eagerly imports build.py). See resolve_effective_signal_lifecycle()'s
    # own local import below for the runtime equivalent, deferred until
    # after both modules have finished loading.
    from app.static_export.signal_lifecycle import SignalLifecycleState

__all__ = [
    "SignalLifecycleAssessmentError",
    "EffectiveSignalLifecycle",
    "record_signal_lifecycle_assessment",
    "get_latest_signal_lifecycle_assessment",
    "resolve_effective_signal_lifecycle",
]

# Mirrors app.models.signal_lifecycle_assessment's own CHECK constraint
# (ck_signal_lifecycle_assessments_state) and
# app.static_export.signal_lifecycle.SignalLifecycleState's five real
# members exactly - a literal tuple here, not an import of that enum at
# module scope, specifically to avoid the import cycle noted above.
# tests/test_signal_lifecycle_assessment.py asserts this stays in sync with
# the real enum, so a future vocabulary change cannot silently drift.
_VALID_STATES = frozenset({
    "active_opportunity", "developing_watch", "stale_unresolved", "realized_historical", "other",
})

_MACHINE_SOURCE = "machine"
_GOVERNED_SOURCE = "governed_assessment"


class SignalLifecycleAssessmentError(ValueError):
    """Raised for every refusal this module makes - a plain ValueError
    subclass, matching SignalAmendmentHistoryError's own convention."""


def record_signal_lifecycle_assessment(
    session: Session,
    *,
    signal_id: int,
    state: str,
    reason: str,
    reviewer: str,
) -> SignalLifecycleAssessment:
    """Validates and appends exactly one `SignalLifecycleAssessment` row.
    Never commits; calls `session.flush()` only - the caller (normally
    scripts/record_signal_lifecycle_assessment.py) controls the transaction
    boundary. Never mutates the `Signal` itself, and never touches
    `Signal.status`/`completion_date`/`published` or any Installation/
    SourceAssertion/ReviewerAction row.

    Idempotent no-op: if the latest existing assessment for this signal
    already has the exact same `state`, `reason`, and `reviewer`, that
    existing row is returned unchanged and no duplicate row is inserted -
    re-running an identical, already-recorded judgment is a safe no-op, not
    a fresh audit event. A change to any one of those three fields (a
    different state, an updated/expanded reason, or a different reviewer)
    is always a new, independent, appended row - never an update to the
    prior one.
    """
    if session.get(Signal, signal_id) is None:
        raise SignalLifecycleAssessmentError(f"Signal {signal_id!r} does not exist")

    if state not in _VALID_STATES:
        raise SignalLifecycleAssessmentError(
            f"state must be one of {sorted(_VALID_STATES)!r}, got {state!r}"
        )
    if not reason.strip():
        raise SignalLifecycleAssessmentError("reason is required for a signal lifecycle assessment")
    if not reviewer.strip():
        raise SignalLifecycleAssessmentError("reviewer is required for a signal lifecycle assessment")

    reason = reason.strip()
    reviewer = reviewer.strip()

    latest = get_latest_signal_lifecycle_assessment(session, signal_id)
    if latest is not None and latest.state == state and latest.reason == reason and latest.reviewer == reviewer:
        return latest

    assessment = SignalLifecycleAssessment(
        signal_id=signal_id, state=state, reason=reason, reviewer=reviewer,
    )
    session.add(assessment)
    session.flush()
    return assessment


def get_latest_signal_lifecycle_assessment(
    session: Session, signal_id: int
) -> "Optional[SignalLifecycleAssessment]":
    """The most recently recorded SignalLifecycleAssessment for a signal,
    ordered by created_at then id (the same tiebreak discipline
    get_latest_source_assertion_identity_resolution() uses). "Latest" means
    "most recently recorded" - with an append-only log, recency alone
    already identifies the current governed judgment. Returns None if no
    assessment has ever been recorded for this signal.

    Wrapped in session.no_autoflush (matches
    get_latest_source_assertion_identity_resolution()'s own identical
    precedent) - a purely read-only helper must never trigger a premature
    flush of some unrelated pending object the caller happens to be
    holding in the same session.
    """
    with session.no_autoflush:
        assessments = (
            session.query(SignalLifecycleAssessment)
            .filter(SignalLifecycleAssessment.signal_id == signal_id)
            .order_by(SignalLifecycleAssessment.created_at.asc(), SignalLifecycleAssessment.id.asc())
            .all()
        )
        return assessments[-1] if assessments else None


@dataclass(frozen=True)
class EffectiveSignalLifecycle:
    """Read-only, non-persisted result of resolving one Signal's effective
    lifecycle - always carries the SLT1 machine baseline alongside whatever
    the effective (possibly governed-overridden) state is, so the machine
    explanation is never discarded even when a human assessment exists.

    `effective_source` is exactly `"machine"` or `"governed_assessment"`.
    `reviewer`/`assessed_at` are populated only when `effective_source ==
    "governed_assessment"` - both `None` for a machine-only result.
    """

    machine_state: SignalLifecycleState
    machine_reason: str
    effective_state: SignalLifecycleState
    effective_reason: str
    effective_source: str
    reviewer: Optional[str] = None
    assessed_at: Optional[datetime] = None


def resolve_effective_signal_lifecycle(
    session: Session, signal: Signal, *, today: date,
) -> EffectiveSignalLifecycle:
    """Always derives the SLT1 machine baseline first
    (`derive_signal_lifecycle()`, unchanged, still consulted every time),
    then checks for a latest SLT2 assessment
    (`get_latest_signal_lifecycle_assessment()`). "Latest row wins": when a
    governed assessment exists, it becomes the effective state/reason and
    the machine read is preserved alongside it as separate provenance;
    otherwise the machine read is itself the effective state. Never
    discards, recomputes, or reinterprets the machine baseline - purely an
    additional read layered on top of it.
    """
    # Local import: see this module's own top-of-file comment for why a
    # module-level import of app.static_export.signal_lifecycle would close
    # a real import cycle with app.static_export.build (which imports this
    # module). By the time this function is actually called, both modules
    # have always finished loading.
    from app.static_export.signal_lifecycle import SignalLifecycleState, derive_signal_lifecycle

    machine = derive_signal_lifecycle(signal, today=today)
    latest = get_latest_signal_lifecycle_assessment(session, signal.id)

    if latest is None:
        return EffectiveSignalLifecycle(
            machine_state=machine.state,
            machine_reason=machine.reason,
            effective_state=machine.state,
            effective_reason=machine.reason,
            effective_source=_MACHINE_SOURCE,
        )

    return EffectiveSignalLifecycle(
        machine_state=machine.state,
        machine_reason=machine.reason,
        effective_state=SignalLifecycleState(latest.state),
        effective_reason=latest.reason,
        effective_source=_GOVERNED_SOURCE,
        reviewer=latest.reviewer,
        assessed_at=latest.created_at,
    )
