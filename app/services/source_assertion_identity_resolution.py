"""KAR2 — governed human resolution of unresolved, ambiguous-known-identity
SourceAssertion evidence to an existing canonical Airport
(docs/architecture/rwi-known-airport-ambiguity-resolution-design.md, locked
design contract for this capability).

    SourceAssertion (airport_id=NULL, unknown_airport_candidate_id=NULL,
    identity_guard_decision typically AMBIGUOUS_KNOWN_IDENTITY/
    REJECT_CROSS_AIRPORT/INSUFFICIENT_IDENTITY, already carries an
    immutable SourceAssertionEvidenceBag snapshot)
        + human action (ATTACH_TO_EXISTING_AIRPORT / REJECT_ATTACHMENT /
          DEFER_IDENTITY_REVIEW)
        -> record_source_assertion_identity_resolution()
        -> one append-only SourceAssertionIdentityResolution row +,
           for ATTACH_TO_EXISTING_AIRPORT only, SourceAssertion.airport_id
           set atomically in the SAME flush
        -> STOP (no Airport, UnknownAirportCandidate, Signal, or
           Installation is ever created here; downstream Signal
           eligibility remains governed entirely by the already-committed,
           unmodified EB4/EB5 - see design doc S11)

WHY THIS IS NOT A SPLIT REVIEW/EXECUTE PAIR (design doc S5, restated here
because it is the single largest structural difference from UAC4's own
review/execute shape this module is otherwise modeled on): UAC4 splits
recording a review from executing it because CREATE_NEW_AIRPORT needs
additional execute-time-only fields, and because ERG4's admission gate must
be independently re-checked at execution time. Neither reason applies here -
attaching to an *existing* Airport needs nothing beyond what is already in
the decision record, and there is no separate downstream eligibility gate
analogous to ERG4 to re-check at a later moment. Recording the decision and
(for ATTACH only) mutating SourceAssertion.airport_id happen atomically, in
one call, in the same flush.

HISTORICAL-FACT FIREWALL: this module never reads, writes, or depends on
SourceAssertion.identity_guard_decision/identity_guard_reason - the
permanent, once-only, discovery-time historical fact, exactly matching
every other persistence function in this pipeline. It never creates an
IdentityGuardEvaluation row (that remains EB4's own, separate, explicitly
human/operator-triggered re-evaluation step - design doc S19's own
documented "operator must remember to run EB4" open item, not silently
auto-triggered here).

ALREADY-RESOLVED (OR CANDIDATE-LINKED) IS A HARD, PERMANENT REFUSAL FOR
EVERY ACTION, NOT JUST ATTACH. Once SourceAssertion.airport_id is set to
ANY value (by this module or by UAC4), every one of this module's own
three actions refuses unconditionally for that assertion - there is no
reversal, correction, or re-resolution workflow of any kind (design doc
S12/S19, deliberately out of scope for KAR1-3). This holds even for
REJECT_ATTACHMENT/DEFER_IDENTITY_REVIEW, which never themselves mutate
`airport_id`: once resolved, an assertion has nothing left for THIS module
to defer or reject - it is UAC4/EB4 territory from that point on, not
this module's. A SourceAssertion still linked to an unresolved
UnknownAirportCandidate (unknown_airport_candidate_id IS NOT NULL) is
refused for the identical reason, routing the operator to UAC4 instead of
letting this module silently steal UAC's own evidence (design doc S8).

Never commits and never imports app.database.SessionLocal anywhere in this
module - mutates the caller-supplied Session and flushes only so a
constraint violation surfaces immediately; the caller owns the transaction
boundary entirely, matching every other persistence service in this
pipeline.

CALLER-SIDE EXPIRED-ATTRIBUTE RISK, STATED PRECISELY (do not over-claim):
this module's own `session.no_autoflush` block protects only ITS OWN
internal precondition reads. It cannot and does not protect a caller who
evaluates an expired ORM attribute (e.g. `assertion.id` on an object read
before an earlier `session.commit()`) as an ARGUMENT EXPRESSION to this
function - that read happens in the caller's own scope, before this
function is ever entered, exactly the same documented, accepted
limitation `get_latest_unknown_airport_candidate_review()` already states
for the identical class of risk. This repository's own CLI
(scripts/resolve_source_assertion_identity.py) is structurally immune:
it always passes a plain argparse int for `source_assertion_id`, never a
live ORM attribute.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Airport, SourceAssertion, SourceAssertionEvidenceBag
from app.models.source_assertion_identity_resolution import (
    SOURCE_ASSERTION_IDENTITY_RESOLUTION_ACTIONS,
    SourceAssertionIdentityResolution,
)
from app.services.resolved_candidate_evidence_reevaluation import SourceAssertionNotFoundError

__all__ = [
    "SourceAssertionNotFoundError",
    "SourceAssertionAlreadyResolvedError",
    "CandidateLinkedAssertionError",
    "MissingEvidenceBagSnapshotError",
    "TargetAirportNotFoundError",
    "SourceAssertionIdentityResolutionResult",
    "record_source_assertion_identity_resolution",
    "get_latest_source_assertion_identity_resolution",
]

_ATTACH_ACTION = "ATTACH_TO_EXISTING_AIRPORT"
_REJECT_ACTION = "REJECT_ATTACHMENT"
_DEFER_ACTION = "DEFER_IDENTITY_REVIEW"


class SourceAssertionAlreadyResolvedError(RuntimeError):
    """Raised for EVERY action (not just ATTACH_TO_EXISTING_AIRPORT) when
    `SourceAssertion.airport_id` is already set to ANY value - see module
    docstring. Fires unconditionally, regardless of which action was
    requested or what prior SourceAssertionIdentityResolution history
    exists; this module implements no re-resolution or correction
    workflow (design doc S12/S19)."""

    def __init__(self, source_assertion_id: int, airport_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        self.airport_id = airport_id
        super().__init__(
            f"SourceAssertion {source_assertion_id} is already resolved to Airport {airport_id} - "
            "this module implements no re-resolution or correction workflow; a new, "
            "separately-designed slice is required to change an already-resolved assertion's "
            "canonical linkage."
        )


class CandidateLinkedAssertionError(RuntimeError):
    """Raised for EVERY action when `SourceAssertion.unknown_airport_candidate_id`
    is set to ANY value - such an assertion is still-unresolved
    UnknownAirportCandidate evidence, governed exclusively by UAC4
    (app.services.unknown_airport_candidate_resolution), never by this
    module. Routes the operator to the correct existing workflow instead
    of silently reinterpreting UAC-linked evidence as a bare, ambiguous
    known-airport question."""

    def __init__(self, source_assertion_id: int, unknown_airport_candidate_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        self.unknown_airport_candidate_id = unknown_airport_candidate_id
        super().__init__(
            f"SourceAssertion {source_assertion_id} is linked to UnknownAirportCandidate "
            f"{unknown_airport_candidate_id} - this evidence is governed exclusively by UAC4 "
            "(app.services.unknown_airport_candidate_resolution), never by this module."
        )


class MissingEvidenceBagSnapshotError(RuntimeError):
    """Raised when the SourceAssertion has no SourceAssertionEvidenceBag
    snapshot - expected and permanent for legacy rows that predate EB3 (or
    rows from an EvidenceBag-free legacy importer). A human resolution
    decision must be recorded against a real, immutable, provable evidence
    basis (design doc S7); this module never reconstructs one from
    SourceAssertion's own lossy raw_* text columns and never records a
    resolution with no traceable evidentiary basis."""

    def __init__(self, source_assertion_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        super().__init__(
            f"SourceAssertion {source_assertion_id} has no SourceAssertionEvidenceBag snapshot - "
            "this is expected for legacy rows that predate EB3 (or rows from an EvidenceBag-free "
            "legacy importer) and is a permanent, not a transient, blocker. A human identity "
            "resolution requires a real, immutable evidence basis; this module never records one "
            "without it."
        )


class TargetAirportNotFoundError(RuntimeError):
    """Raised when ATTACH_TO_EXISTING_AIRPORT's own `matched_airport_id`
    does not reference an existing Airport - fails closed before any
    mutation, mirroring UAC4's own identical check verbatim."""

    def __init__(self, matched_airport_id: int) -> None:
        self.matched_airport_id = matched_airport_id
        super().__init__(f"referenced Airport (matched_airport_id={matched_airport_id!r}) does not exist")


@dataclass(frozen=True)
class SourceAssertionIdentityResolutionResult:
    """Deterministic, ORM-adjacent summary of what
    record_source_assertion_identity_resolution() did. `resolution` is the
    persisted, already-flushed row (has a real `id`). `airport_id_set` is
    the exact value SourceAssertion.airport_id now holds - the same,
    already-non-NULL value it held before this call for REJECT_ATTACHMENT/
    DEFER_IDENTITY_REVIEW (always None, since both actions require the
    assertion to be unresolved), or the freshly-set `matched_airport_id`
    for ATTACH_TO_EXISTING_AIRPORT."""

    resolution: SourceAssertionIdentityResolution
    source_assertion_id: int
    action: str
    airport_id_set: Optional[int]


def record_source_assertion_identity_resolution(
    session: Session,
    *,
    source_assertion_id: int,
    action: str,
    reason: str,
    reviewer: str,
    matched_airport_id: Optional[int] = None,
    supersedes_resolution_id: Optional[int] = None,
) -> SourceAssertionIdentityResolutionResult:
    """Validates, appends exactly one SourceAssertionIdentityResolution
    row, and - for ATTACH_TO_EXISTING_AIRPORT only - atomically sets
    SourceAssertion.airport_id in the SAME flush. Never commits; the
    caller owns the transaction boundary.

    Fails closed, before any mutation and before any row is added, for
    every precondition below - checked in this order:

    1. ValueError - action is not one of SOURCE_ASSERTION_IDENTITY_RESOLUTION_ACTIONS.
    2. ValueError - reason/reviewer is empty or whitespace-only.
    3. ValueError - matched_airport_id supplied for a non-ATTACH action, or
       omitted for ATTACH_TO_EXISTING_AIRPORT (mirrors the schema's own
       CHECK constraints, re-checked here to fail with a clear typed error
       before ever reaching the database).
    4. SourceAssertionNotFoundError - source_assertion_id does not exist
       (reused verbatim from EB4/EB5 - the identical, context-free concept).
    5. SourceAssertionAlreadyResolvedError - airport_id is already set, for
       ANY action (module docstring).
    6. CandidateLinkedAssertionError - unknown_airport_candidate_id is
       already set, for ANY action (module docstring).
    7. MissingEvidenceBagSnapshotError - no SourceAssertionEvidenceBag
       snapshot exists for this assertion.
    8. TargetAirportNotFoundError - matched_airport_id (ATTACH only) does
       not reference an existing Airport.
    9. ValueError - a supplied supersedes_resolution_id does not reference
       a real SourceAssertionIdentityResolution for the SAME
       source_assertion_id (defense-in-depth audit-integrity check,
       mirroring EB4's own analogous triggering_review_id validation).
    """
    if action not in SOURCE_ASSERTION_IDENTITY_RESOLUTION_ACTIONS:
        raise ValueError(
            f"action must be one of {SOURCE_ASSERTION_IDENTITY_RESOLUTION_ACTIONS!r}, got {action!r}"
        )
    if not reason.strip():
        raise ValueError("reason is required for a source-assertion identity resolution")
    if not reviewer.strip():
        raise ValueError("reviewer is required for a source-assertion identity resolution")

    if action == _ATTACH_ACTION:
        if matched_airport_id is None:
            raise ValueError(f"{_ATTACH_ACTION} requires matched_airport_id")
    else:
        if matched_airport_id is not None:
            raise ValueError(f"{action} must not supply matched_airport_id (only {_ATTACH_ACTION} may)")

    # UAC-H1/ERG-family no_autoflush hardening: wraps the ENTIRE read-only
    # precondition-check phase below, starting from the very first
    # session.get() - session.get()/session.query() each autoflush pending
    # state by default just like session.execute() does, and would
    # otherwise flush ANY OTHER unrelated pending object the caller
    # happens to be holding in the SAME session, raising on ITS constraint
    # violation here, at precondition-check time, instead of leaving it
    # pending until the caller's own intended commit. The write section
    # below (session.add()+flush()) is deliberately OUTSIDE this block -
    # that flush is this function's own intentional write point and
    # flushes the whole session's pending state, by design, exactly like
    # every other persistence function in this pipeline.
    with session.no_autoflush:
        assertion = session.get(SourceAssertion, source_assertion_id)
        if assertion is None:
            raise SourceAssertionNotFoundError(source_assertion_id)

        if assertion.airport_id is not None:
            raise SourceAssertionAlreadyResolvedError(source_assertion_id, assertion.airport_id)

        if assertion.unknown_airport_candidate_id is not None:
            raise CandidateLinkedAssertionError(source_assertion_id, assertion.unknown_airport_candidate_id)

        # .scalars().one_or_none() rather than .scalar(): a well-formed
        # database structurally cannot have more than one snapshot per
        # source_assertion_id (EB1's own unique=True FK), but .scalar()
        # would silently return an arbitrary one of several rows if that
        # constraint were ever bypassed - exactly the "take the first
        # snapshot it finds" failure mode this service must never exhibit,
        # matching EB4's own identical defensive convention verbatim.
        snapshot = session.scalars(
            select(SourceAssertionEvidenceBag).where(
                SourceAssertionEvidenceBag.source_assertion_id == source_assertion_id
            )
        ).one_or_none()
        if snapshot is None:
            raise MissingEvidenceBagSnapshotError(source_assertion_id)

        if action == _ATTACH_ACTION:
            if session.get(Airport, matched_airport_id) is None:
                raise TargetAirportNotFoundError(matched_airport_id)

        if supersedes_resolution_id is not None:
            superseded = session.get(SourceAssertionIdentityResolution, supersedes_resolution_id)
            if superseded is None:
                raise ValueError(
                    f"supersedes_resolution_id={supersedes_resolution_id!r} does not reference an "
                    "existing SourceAssertionIdentityResolution"
                )
            if superseded.source_assertion_id != source_assertion_id:
                raise ValueError(
                    f"supersedes_resolution_id={supersedes_resolution_id!r} belongs to SourceAssertion "
                    f"{superseded.source_assertion_id!r}, not {source_assertion_id!r} - refusing to "
                    "record a false supersession link between unrelated assertions."
                )

    resolution = SourceAssertionIdentityResolution(
        source_assertion_id=source_assertion_id,
        evidence_bag_snapshot_id=snapshot.id,
        action=action,
        reason=reason,
        reviewer=reviewer,
        matched_airport_id=matched_airport_id,
        supersedes_resolution_id=supersedes_resolution_id,
    )
    session.add(resolution)

    if action == _ATTACH_ACTION:
        assertion.airport_id = matched_airport_id

    session.flush()

    return SourceAssertionIdentityResolutionResult(
        resolution=resolution,
        source_assertion_id=source_assertion_id,
        action=action,
        airport_id_set=assertion.airport_id,
    )


def get_latest_source_assertion_identity_resolution(
    session: Session, source_assertion_id: int
) -> "Optional[SourceAssertionIdentityResolution]":
    """The most recently recorded SourceAssertionIdentityResolution for an
    assertion, ordered by created_at then id (the same tiebreak
    discipline get_latest_unknown_airport_candidate_review() uses).
    "Latest" means "most recently recorded," not "the unsuperseded
    terminal node reached by walking supersedes_resolution_id" - with an
    append-only log, recency alone already identifies current state.
    Returns None if no resolution has ever been recorded for this
    assertion.

    Plain recency query only: never interprets the resolution's action,
    never touches SourceAssertion.airport_id, and is not itself a
    resolution decision.

    Wrapped in session.no_autoflush (matches
    get_latest_unknown_airport_candidate_review()'s own identical
    precedent) - a purely read-only helper must never trigger a premature
    flush of some unrelated pending object the caller happens to be
    holding in the same session.
    """
    with session.no_autoflush:
        resolutions = (
            session.query(SourceAssertionIdentityResolution)
            .filter(SourceAssertionIdentityResolution.source_assertion_id == source_assertion_id)
            .order_by(
                SourceAssertionIdentityResolution.created_at.asc(),
                SourceAssertionIdentityResolution.id.asc(),
            )
            .all()
        )
        return resolutions[-1] if resolutions else None
