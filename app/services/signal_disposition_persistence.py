"""Validation and creation of immutable Signal-group disposition records
(D4D1 of docs/architecture/fh-d4-signal-disposition-design.md).

    tuple of already-existing Signal ids (an FH-D4 candidate group, or any
        other caller-supplied group of two or more Signals)
        -> record_signal_group_disposition()
        -> one immutable SignalDisposition row + one immutable
           SignalDispositionMember row per distinct Signal id
        -> STOP (no Signal write, no publication change, no provenance
           move, no canonical-Signal selection, no Fleet Health
           integration, no CLI - all future, separate, not-yet-authorized
           slices per the design doc's own §19 non-goals and this slice's
           own explicit boundary)

This module intentionally contains no Signal-creation, Signal-update, or
Signal-publication behavior whatsoever - it never imports the `Signal`
constructor's write path and never sets any attribute on a `Signal`
instance. `Signal` is imported only to validate that every referenced id
actually exists, exactly the same read-only existence check
`app.services.reviewer_action_persistence.record_reviewer_action()` already
performs for `duplicate_of_signal_id`.

Modeled directly on `app.services.reviewer_action_persistence`, the exact
structural precedent for validating and inserting a human-reviewed,
append-only decision in this pipeline. Never commits and never imports
`app.database.SessionLocal` - it mutates the caller-supplied `Session` and
flushes only so a constraint violation (including the immutability event
listeners on both new models) surfaces immediately; the caller owns the
transaction boundary entirely, mirroring every other persistence service in
this pipeline (`reviewer_action_persistence`, `intelligence_review_persistence`,
`promotion_policy_persistence`, `physical_installation_reconciliation`).

INFORMATION FIREWALL: this module reads and writes exactly five values -
Signal ids (existence-checked only, via `session.get(Signal, id)`, which
touches no other column), `decision`, `reviewer`, `reason`, and
`supersedes_id`. It never reads `Signal.title`, `.notes`, `.source_notes`,
`.estimated_total_value_usd`, `.estimated_emas_value_usd`, `.supplier`,
`.likely_supplier`, `.confirmed_vendor`, `.category`, `.confidence`,
`.status`, `.airport_id`, `.published`, or any other Signal column - a
disposition records only that a human compared a SET OF IDS, never anything
about what those ids' own rows contain. Verified structurally (AST) and
behaviorally in `tests/test_signal_disposition_persistence.py`.

GROUP IDENTITY (design doc §8): a disposition's identity is the exact,
deduplicated SET of its member Signal ids - no fingerprint, no hash. Two
Signal-id collections are the "same group" if and only if
`frozenset(a) == frozenset(b)`.

SUPERSESSION GATE (design doc §8, §12): `supersedes_id`, when supplied,
must reference an existing `SignalDisposition` whose own member-id set is
IDENTICAL to the new disposition's member-id set - not a subset, not a
superset, not merely overlapping. A disposition for a candidate group that
grew or shrank since a prior review is always a new, independent,
initially-unreviewed row; it can never "supersede" a disposition for a
differently-shaped group. This is the one validation this module needs
beyond `record_reviewer_action()`'s own precedent, because that module's
supersession is always implicitly scoped to one fixed
`source_assertion_id` and never needs a cross-row set comparison.
"""
from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.models import Signal
from app.models.signal_disposition import (
    ACCEPTING_INITIAL_MEMBERS_ATTR,
    SIGNAL_DISPOSITION_DECISIONS,
    SignalDisposition,
    SignalDispositionMember,
)

__all__ = [
    "MINIMUM_GROUP_CARDINALITY",
    "record_signal_group_disposition",
]

MINIMUM_GROUP_CARDINALITY = 2


def _member_signal_ids(session: Session, disposition_id: int) -> "frozenset[int]":
    rows = (
        session.query(SignalDispositionMember.signal_id)
        .filter(SignalDispositionMember.disposition_id == disposition_id)
        .all()
    )
    return frozenset(row[0] for row in rows)


def record_signal_group_disposition(
    session: Session,
    *,
    signal_ids: Sequence[int],
    decision: str,
    reviewer: str,
    reason: str,
    supersedes_id: Optional[int] = None,
) -> SignalDisposition:
    """Validates and appends exactly one SignalDisposition row plus one
    SignalDispositionMember row per distinct Signal id. Never commits; calls
    `session.flush()` only so a constraint violation surfaces immediately.
    Never mutates any Signal, never creates a Signal.

    `signal_ids` is deduplicated and sorted before validation/persistence -
    caller-supplied duplicates and ordering are normalized, never treated as
    an error (mirrors R1's own `_anchor_reasons()` dedup discipline for
    candidate ids). The stored member set is always this deduplicated,
    sorted set.
    """
    if decision not in SIGNAL_DISPOSITION_DECISIONS:
        raise ValueError(f"decision must be one of {SIGNAL_DISPOSITION_DECISIONS!r}, got {decision!r}")
    if not reason.strip():
        raise ValueError("reason is required for a signal disposition")
    if not reviewer.strip():
        raise ValueError("reviewer is required for a signal disposition")

    deduplicated_ids = tuple(sorted(set(signal_ids)))
    if len(deduplicated_ids) < MINIMUM_GROUP_CARDINALITY:
        raise ValueError(
            f"signal_ids must contain at least {MINIMUM_GROUP_CARDINALITY} distinct Signal ids, "
            f"got {deduplicated_ids!r}"
        )

    for signal_id in deduplicated_ids:
        if session.get(Signal, signal_id) is None:
            raise ValueError(f"referenced Signal does not exist: {signal_id!r}")

    if supersedes_id is not None:
        previous = session.get(SignalDisposition, supersedes_id)
        if previous is None:
            raise ValueError("supersedes_id must reference an existing SignalDisposition")
        previous_member_ids = _member_signal_ids(session, previous.id)
        if previous_member_ids != frozenset(deduplicated_ids):
            raise ValueError(
                "supersedes_id must reference a disposition with the exact same member Signal-id "
                f"set - superseded set={sorted(previous_member_ids)!r}, "
                f"new set={list(deduplicated_ids)!r}"
            )

    disposition = SignalDisposition(
        decision=decision,
        reason=reason.strip(),
        reviewer=reviewer.strip(),
        supersedes_id=supersedes_id,
    )
    # Review-checkpoint addition: this transient, never-mapped attribute is
    # the one signal app.models.signal_disposition's own before_insert
    # listener on SignalDispositionMember accepts as proof a member row is
    # part of THIS disposition's initial, legitimate creation batch - see
    # that listener's own docstring for the real, reproduced gap this
    # closes (a plain session.add() could otherwise silently append a
    # member to an already-committed disposition later, since inserting a
    # new row triggers neither before_update nor before_delete). Cleared in
    # the `finally` block below no matter how member insertion finishes,
    # so this disposition object can never be reused - by this call or a
    # caller holding the same reference - to legitimize a later, separate
    # append.
    setattr(disposition, ACCEPTING_INITIAL_MEMBERS_ATTR, True)
    session.add(disposition)
    session.flush()  # obtain disposition.id

    try:
        for signal_id in deduplicated_ids:
            session.add(SignalDispositionMember(disposition_id=disposition.id, signal_id=signal_id))
        session.flush()
    finally:
        setattr(disposition, ACCEPTING_INITIAL_MEMBERS_ATTR, False)

    return disposition
