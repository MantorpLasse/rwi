"""Governed existing-Signal amendment service (RWI HQ "Trust Preconditions
for Update & Report V1" mission, Part 3 — following the recon in
"RWI System Design Recon — Update & Report V1 + Trust/Operational
Readiness Checkpoint", §13: "existing Signal corrections are performed
through one-off scripts" was the concrete gap this closes).

    existing Signal (any provenance generation)
        + an explicit, finite set of allowed field changes
        + a human-supplied reason (+ optional supporting SourceAssertion/
          ReviewerAction references)
        -> amend_signal()
        -> SignalAmendmentResult (old/new values, reason, provenance
           references, timestamp)
        -> STOP (no publication change, no new Signal, no ReviewerAction
           write, no reconciliation re-evaluation — a separate, future,
           explicitly-authorized step for each)

WHY THIS EXISTS: every historical correction to an already-existing Signal
(`scripts/correct_bgm_signal6_target_year.py`, `scripts/confirm_phl_emas_completion.py`,
`scripts/update_fty_emas_details.py`, `scripts/update_lex_emas_details.py`,
`scripts/update_ase_runway_relocation_note.py`) is a hand-written, one-off,
single-Signal script, each independently re-implementing the same
"verify the exact expected before-state, fail closed on any mismatch,
apply, append a dated note, be safe to re-run" pattern —
`correct_bgm_signal6_target_year.py` says so explicitly in its own
docstring. This module generalizes that proven pattern into one reusable,
tested service, so a future correction is a function call with an explicit
reason, not a new bespoke script.

AUDIT TRAIL — RESOLVED (RWI HQ "Signal Amendment Audit Trail — Append-Only
Governance" mission, following the design recon
docs/architecture/rwi-signal-amendment-audit-trail-design.md): every real
`amend_signal()` call now creates one durable, immutable
`app.models.signal_amendment.SignalAmendmentAction` row plus one
`SignalAmendmentFieldChange` row per changed field (via
`app.services.signal_amendment_history.record_signal_amendment()`), in the
SAME transaction as the Signal mutation itself, atomically. This is a real,
persisted, queryable audit trail — "show me every amendment ever made to
Signal 6" is now `app.services.signal_amendment_history.list_signal_amendments()`.

Neither `ReviewerAction` (fixed, closed action vocabulary this mission is
explicitly forbidden from extending) nor `SignalPublicationAction` (hard
`CHECK`'d to `PUBLISH`/`UNPUBLISH` only, and its own docstring explicitly
says it "never touches any of its content fields") was reused or altered -
the design recon's own §3/§4 concluded reuse would corrupt either table's
documented meaning; two new, dedicated, additive tables were the
recommended (and now implemented) design instead.

`Signal.source_notes` NO LONGER receives an automatic audit-prose line from
this module (see design recon §12/§18): `source_notes` is public-facing
evidence/context text; audit history is internal governance history, and
conflating the two was always this module's own documented, temporary
workaround, never the intended end state. A caller who also wants to add a
public-facing research-finding sentence does so explicitly and separately
(exactly like `scripts/update_fty_emas_details.py`/
`scripts/update_ase_runway_relocation_note.py` already do), never as an
automatic byproduct of calling `amend_signal()`. No existing production
`source_notes` value was touched by this change - only future calls behave
differently.

SCOPE OF THIS WRITE SEAM: this is a write seam AFTER a human/governed
decision has already been made elsewhere — it is deliberately NOT a
reconciliation engine and does not duplicate
`app.services.existing_signal_reconciliation`'s identity/change-decision
logic. A caller who needs to decide WHETHER new evidence should change an
existing Signal must resolve that upstream (e.g. via that module, or by a
human's own direct judgment); this service only performs the WRITE once
that decision has already been made, and only within the narrow, explicit
constraints below.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.airport import Runway
from app.models.reviewer_action import ReviewerAction
from app.models.signal import Signal
from app.models.source_assertion import SourceAssertion
from app.services.signal_amendment_history import record_signal_amendment

__all__ = [
    "ALLOWED_AMENDMENT_FIELDS",
    "FieldChange",
    "SignalAmendmentResult",
    "SignalAmendmentError",
    "amend_signal",
]


# Deliberately small and explicit (mission's own "start small" instruction),
# derived from what the five historical correction scripts (BGM/PHL/FTY/LEX/
# ASE) actually needed, minus anything the mission's own "should probably
# NOT be generally mutable" list names:
#
#   EXCLUDED, with a specific reason each (never silently omitted):
#     published        -> app.services.signal_publication is the only
#                          governed write path for this (mission's own
#                          explicit instruction)
#     airport_id       -> an identity-changing field; the mission's own
#                          "should probably NOT be generally mutable
#                          without stronger design" list names this
#                          explicitly
#     category         -> changes the Signal's fundamental classification;
#                          same explicit exclusion
#     installation_id  -> owned by scripts/graduate_signal_to_installation.py's
#                          own one-Signal-at-a-time governed process
#     probability_score -> a derived scoring output
#                          (DEFAULT_SCORE_BY_CONFIDENCE-driven), not a fact
#                          to amend directly; same explicit exclusion
#     confidence       -> an evidence-strength judgment tied to how the
#                          Signal was created, not a post-hoc editable
#                          fact; same explicit exclusion
#     title            -> not in the mission's suggested candidate list;
#                          renaming changes the Signal's public identity -
#                          left out of V1 deliberately, can be reconsidered
#                          in a future, separately-scoped mission
#     notes            -> a private, personal, unverified annotation field
#                          (see Signal.notes' own docstring) - different
#                          semantics from an evidence-driven amendment
#     manual_year_estimate -> explicitly "a personal, unverified... hunch"
#                          per Signal's own docstring - not evidence-driven
#     likely_supplier, supplier_reason -> analytical judgment/guess fields,
#                          not in the mission's suggested candidate list
#     source_id        -> changing which Source backs a Signal is itself a
#                          significant provenance change, out of scope here
#     source_notes     -> managed automatically by this service itself
#                          (always appended, never caller-overwritten - see
#                          module docstring "AUDIT TRAIL") rather than
#                          exposed as a directly settable field
ALLOWED_AMENDMENT_FIELDS: "frozenset[str]" = frozenset(
    {
        "target_year",
        "planning_year",
        "procurement_year",
        "construction_start",
        "completion_date",
        "status",
        "runway_id",
        "supplier",
        "confirmed_vendor",
        "estimated_total_value_usd",
        "estimated_emas_value_usd",
        "last_verified_at",
    }
)

# Named explicitly so a caller gets a specific, helpful refusal reason
# instead of a generic "not allowed" for the fields most likely to be
# tried by mistake.
_EXPLICITLY_DISALLOWED_REASONS = {
    "published": "published cannot be amended here - use app.services.signal_publication.publish_signal()/unpublish_signal()",
    "airport_id": "airport_id is an identity-changing field, not generally mutable by this service",
    "category": "category is an identity-changing field, not generally mutable by this service",
    "installation_id": "installation_id is owned by scripts/graduate_signal_to_installation.py's own governed process",
    "probability_score": "probability_score is a derived scoring output, not a fact to amend directly",
    "confidence": "confidence is an evidence-strength judgment tied to Signal creation, not amendable post-hoc",
}


class SignalAmendmentError(ValueError):
    """Raised for every refusal this service makes - a plain ValueError
    subclass (matching this codebase's existing convention, e.g.
    app.services.governed_signal_creation.ExistingSignalPossibleMatchError)
    so existing `except ValueError` callers keep working unchanged."""


@dataclass(frozen=True)
class FieldChange:
    """One field's before/after value. `old_value`/`new_value` are the raw
    Python values already on/being set on the ORM object - never
    serialized or summarized."""

    field: str
    old_value: Any
    new_value: Any


@dataclass(frozen=True)
class SignalAmendmentResult:
    """Structured record of one `amend_signal()` call, returned to the
    immediate caller for convenience (e.g. printing a summary) - the
    durable, queryable record of this same call is now the
    `SignalAmendmentAction` row identified by `signal_amendment_action_id`
    (see `app.services.signal_amendment_history.list_signal_amendments()`).
    This object itself is never persisted and is not a second audit
    system - the database rows are the source of truth."""

    signal_id: int
    reason: str
    reviewer: str
    changes: "tuple[FieldChange, ...]"
    source_assertion_id: Optional[int]
    reviewer_action_id: Optional[int]
    timestamp: datetime
    signal_amendment_action_id: int


def _validate_runway_compatibility(session: Session, signal: Signal, new_runway_id: Optional[int]) -> None:
    """Structural, non-transitive compatibility check only: the runway must
    exist and belong to the SAME airport this Signal already belongs to.
    Never infers project/runway-end identity - that remains
    app.services.existing_signal_reconciliation's job, not duplicated
    here."""
    if new_runway_id is None:
        return
    runway = session.get(Runway, new_runway_id)
    if runway is None:
        raise SignalAmendmentError(f"runway_id {new_runway_id!r} does not exist")
    if runway.airport_id != signal.airport_id:
        raise SignalAmendmentError(
            f"runway_id {new_runway_id!r} belongs to airport {runway.airport_id!r}, not this "
            f"Signal's airport {signal.airport_id!r} - refusing (no transitive identity inference)"
        )


def _validate_source_assertion(session: Session, signal: Signal, source_assertion_id: Optional[int]) -> Optional[SourceAssertion]:
    """Verifies existence and same-airport compatibility only - never
    same-project identity (that would require reusing
    app.services.existing_signal_reconciliation, which this write seam
    deliberately does not duplicate). Never reassigns or otherwise mutates
    `SourceAssertion.signal_id` - this function only reads the row."""
    if source_assertion_id is None:
        return None
    assertion = session.get(SourceAssertion, source_assertion_id)
    if assertion is None:
        raise SignalAmendmentError(f"supporting SourceAssertion {source_assertion_id!r} does not exist")
    if assertion.airport_id is not None and signal.airport_id is not None and assertion.airport_id != signal.airport_id:
        raise SignalAmendmentError(
            f"supporting SourceAssertion {source_assertion_id!r} belongs to airport "
            f"{assertion.airport_id!r}, not this Signal's airport {signal.airport_id!r} - refusing "
            "(incompatible provenance; no transitive identity inference)"
        )
    return assertion


def _validate_reviewer_action(
    session: Session, reviewer_action_id: Optional[int], source_assertion_id: Optional[int]
) -> None:
    if reviewer_action_id is None:
        return
    action = session.get(ReviewerAction, reviewer_action_id)
    if action is None:
        raise SignalAmendmentError(f"referenced ReviewerAction {reviewer_action_id!r} does not exist")
    if source_assertion_id is not None and action.source_assertion_id != source_assertion_id:
        raise SignalAmendmentError(
            f"ReviewerAction {reviewer_action_id!r} belongs to SourceAssertion "
            f"{action.source_assertion_id!r}, not the supplied source_assertion_id {source_assertion_id!r}"
        )


def amend_signal(
    session: Session,
    *,
    signal_id: int,
    changes: "dict[str, Any]",
    reason: str,
    reviewer: str,
    source_assertion_id: Optional[int] = None,
    reviewer_action_id: Optional[int] = None,
) -> SignalAmendmentResult:
    """Load the existing Signal, validate every proposed change against
    `ALLOWED_AMENDMENT_FIELDS`, refuse a no-op call, apply the real diff,
    record it durably (one `SignalAmendmentAction` + one
    `SignalAmendmentFieldChange` per changed field, via
    `app.services.signal_amendment_history.record_signal_amendment()`),
    and return a `SignalAmendmentResult`. Never commits - calls
    `session.flush()` only, exactly like
    `create_signal_from_approved_review()`/`publish_signal()` do, so the
    caller controls the transaction boundary (and a validation failure,
    which always happens before any `setattr`, leaves nothing to roll
    back).

    Every validation runs BEFORE any mutation - a `SignalAmendmentError`
    never leaves the Signal or the session partially changed. The Signal
    mutation and its audit rows are applied in the same transaction and
    the same final `flush()` - a failure recording the audit rows never
    leaves an unaudited Signal mutation behind (see
    `record_signal_amendment()`'s own fail-closed validation, which runs
    before either it or `amend_signal()` has added a single row).

    `Signal.source_notes` is no longer touched by this function - see
    module docstring "AUDIT TRAIL".
    """
    if not reason.strip():
        raise SignalAmendmentError("reason is required")
    if not reviewer.strip():
        raise SignalAmendmentError("reviewer is required")
    if not changes:
        raise SignalAmendmentError("changes must be a non-empty mapping of field -> new value")

    signal = session.get(Signal, signal_id)
    if signal is None:
        raise SignalAmendmentError(f"Signal {signal_id!r} does not exist")

    for field in changes:
        if field in _EXPLICITLY_DISALLOWED_REASONS:
            raise SignalAmendmentError(_EXPLICITLY_DISALLOWED_REASONS[field])
        if field not in ALLOWED_AMENDMENT_FIELDS:
            raise SignalAmendmentError(
                f"{field!r} is not in the allowed amendment field set {sorted(ALLOWED_AMENDMENT_FIELDS)!r}"
            )

    if "runway_id" in changes:
        _validate_runway_compatibility(session, signal, changes["runway_id"])
    _validate_source_assertion(session, signal, source_assertion_id)
    _validate_reviewer_action(session, reviewer_action_id, source_assertion_id)

    real_changes = tuple(
        FieldChange(field=field, old_value=getattr(signal, field), new_value=new_value)
        for field, new_value in changes.items()
        if getattr(signal, field) != new_value
    )
    if not real_changes:
        raise SignalAmendmentError("no-op amendment: every proposed value already matches the current value")

    timestamp = datetime.now(UTC)

    # Durable audit history first (steps 3-4 of this mission's own approved
    # ordering), THEN the Signal mutation (step 5) - both land in the same
    # transaction and the same final flush() (step 6), so a caller's
    # rollback on any later failure undoes both together; there is no
    # return path between the two that could leave one without the other.
    action = record_signal_amendment(
        session, signal_id=signal.id, changes=real_changes, reason=reason, reviewer=reviewer,
        source_assertion_id=source_assertion_id, reviewer_action_id=reviewer_action_id,
    )

    for change in real_changes:
        setattr(signal, change.field, change.new_value)

    session.flush()

    return SignalAmendmentResult(
        signal_id=signal.id,
        reason=reason.strip(),
        reviewer=reviewer.strip(),
        changes=real_changes,
        source_assertion_id=source_assertion_id,
        reviewer_action_id=reviewer_action_id,
        timestamp=timestamp,
        signal_amendment_action_id=action.id,
    )
