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

AUDIT TRAIL — THE ONE REAL GAP THIS MISSION FOUND, REPORTED HONESTLY RATHER
THAN PAPERED OVER: this repository has exactly two existing append-only,
human-attributed decision logs — `ReviewerAction` (required, NOT NULL
`source_assertion_id`; a fixed, closed action vocabulary this mission is
explicitly forbidden from extending) and `SignalPublicationAction` (a hard
`CHECK (action IN ('PUBLISH','UNPUBLISH'))` constraint, and its own
docstring explicitly says it "never touches any of its content fields").
NEITHER can hold a general "field X changed from A to B, because Y" record
without either a schema change (a new table, or new columns/CHECK values
on an existing one) or reusing a fixed vocabulary in a way it was never
designed for. Per this mission's own explicit instruction ("do not hide
audit weakness behind source_notes free text" / "STOP before inventing
[a new persisted audit table]"), this module does NOT invent one, and
does NOT pretend `Signal.source_notes` is a substitute for one.

What this module actually provides instead, honestly scoped:

  1. A complete, structured `SignalAmendmentResult` — field/old value/new
     value/reason/reviewer/provenance references/timestamp — returned to
     the immediate caller (satisfies this mission's own explicit
     "preserve old/new values in a returned result object" requirement).
     A caller (e.g. a future Commander CLI) can log or print this; nothing
     here forces it to be thrown away.
  2. One dated, human-readable line appended (never overwritten) to
     `Signal.source_notes`, in the exact style every historical
     correction script already used — a public-facing transparency aid,
     NOT a queryable audit record, and NOT claimed to be one anywhere in
     this module.

**The durable, structured, queryable "show me every amendment ever made to
Signal 6" capability does not exist after this mission and would require a
real schema decision (a new append-only table) in a future, separate,
explicitly-authorized mission.** This module's own tests
(`test_signal_amendment.py::test_no_new_audit_semantics_invented_silently`)
assert directly that calling `amend_signal()` creates no new
`ReviewerAction` or `SignalPublicationAction` row — this module invents no
audit mechanism of its own, silently or otherwise.

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
    """Pure, structured record of one `amend_signal()` call - see module
    docstring "AUDIT TRAIL" for exactly what this is (a returned result
    object) and is not (a persisted, queryable audit trail)."""

    signal_id: int
    reason: str
    reviewer: str
    changes: "tuple[FieldChange, ...]"
    source_assertion_id: Optional[int]
    reviewer_action_id: Optional[int]
    timestamp: datetime
    source_notes_appended: bool


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
    `ALLOWED_AMENDMENT_FIELDS`, refuse a no-op call, apply the real diff in
    one transaction, append one dated `source_notes` line, and return a
    `SignalAmendmentResult`. Never commits - calls `session.flush()` only,
    exactly like `create_signal_from_approved_review()`/`publish_signal()`
    do, so the caller controls the transaction boundary (and a validation
    failure, which always happens before any `setattr`, leaves nothing to
    roll back).

    Every validation runs BEFORE any mutation - a `SignalAmendmentError`
    never leaves the Signal or the session partially changed.
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
    for change in real_changes:
        setattr(signal, change.field, change.new_value)

    note_lines = ", ".join(f"{c.field}: {c.old_value!r} -> {c.new_value!r}" for c in real_changes)
    provenance_suffix = f" (SourceAssertion #{source_assertion_id})" if source_assertion_id is not None else ""
    new_note = f"[{timestamp.date()}] Amended by {reviewer.strip()}: {reason.strip()} ({note_lines}){provenance_suffix}"
    signal.source_notes = f"{signal.source_notes}\n{new_note}" if signal.source_notes else new_note

    session.flush()

    return SignalAmendmentResult(
        signal_id=signal.id,
        reason=reason.strip(),
        reviewer=reviewer.strip(),
        changes=real_changes,
        source_assertion_id=source_assertion_id,
        reviewer_action_id=reviewer_action_id,
        timestamp=timestamp,
        source_notes_appended=True,
    )
