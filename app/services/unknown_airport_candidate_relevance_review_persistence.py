"""ERG3 persistence bridge for HUMAN EMAS-relevance review recording
(docs/architecture/rwi-erg3-human-relevance-review-recording-report.md).

    UnknownAirportCandidate (already persisted, UAC1, unmodified)
        + UnknownAirportCandidateRelevanceAssessment.id (the SPECIFIC
          automatic assessment a human actually reviewed - ERG2, unmodified)
        + action / reviewer / reason (caller-supplied human judgment)
        -> record_unknown_airport_candidate_relevance_review()
        -> one immutable UnknownAirportCandidateRelevanceReview row appended
        -> STOP (no UAC4 gate, no CLI integration, no canonical Airport
           creation, no Signal - all future, separately-authorized slices)

STALE-BASIS GATE, THE CENTRAL DISCIPLINE (mission's own explicit
instruction, HIGHEST PRIORITY): `record_unknown_airport_candidate_relevance_review()`
refuses outright if `basis_assessment_id` is not the candidate's CURRENT
latest automatic assessment (per
app.services.unknown_airport_candidate_relevance_persistence.
get_latest_unknown_airport_candidate_relevance_assessment(), reused
verbatim, never a second "latest" definition invented here) at the moment
of recording. A human who reviewed assessment #10 cannot have that review
silently reinterpreted as authorizing the candidate once assessment #11
exists - the caller must fetch the CURRENT assessment fresh and record an
explicit new review against it. This mirrors
app.services.unknown_airport_candidate_resolution's own
`_require_current_review()` staleness discipline and R4B's own
"reconciliation fingerprint must match the CURRENT blocking state"
principle, applied to a new "current" concept (latest assessment, not
latest identity review).

REVIEW ELIGIBILITY - DELIBERATELY UNRESTRICTED BY AUTOMATIC OUTCOME
(derived, not assumed - see the persistence report's own "review
eligibility" section): all three actions
(CONFIRM_EMAS_RELEVANT/MARK_NOT_EMAS_RELEVANT/DEFER_RELEVANCE_REVIEW) are
recordable against ANY of the five automatic RelevanceOutcome values,
including RUNWAY_ONLY_NOT_EMAS_RELEVANT and INSUFFICIENT_EVIDENCE. This
module does NOT block CONFIRM_EMAS_RELEVANT merely because the automatic
assessment was negative - a human may legitimately have out-of-band
knowledge the automated evidence set does not capture (the design doc's
own governing "materiality is genuinely ambiguous, human judgment
required" principle), and this mission's own explicit instruction forbids
inventing an automatic-outcome-based restriction here ("it must NOT yet
gate UAC4 CREATE_NEW_AIRPORT - that is a later slice"; the analogous
question of which reviews should eventually UNLOCK CREATE_NEW_AIRPORT
belongs to a future ERG4 gate, never to this recording layer). "Evidence-
grounded" is satisfied structurally, not by outcome-filtering: every
review is bound to one specific, real, CURRENT automatic assessment via
`basis_assessment_id` - the human is always grounded in some real,
inspectable evidence snapshot, whatever conclusion they draw from it.

Composes the already-committed, unmodified ERG2 core
(app.services.unknown_airport_candidate_relevance_persistence) exactly the
way ERG2 itself composes ERG1 - this module contains no relevance-
classification logic of its own, never writes to
UnknownAirportCandidateRelevanceAssessment, and never reinterprets what
that table already says.

CROSS-CANDIDATE INTEGRITY - SERVICE-LEVEL ONLY, DELIBERATELY (see the
model's own docstring, CROSS-CANDIDATE INTEGRITY section, for the full
derivation of why a composite-FK schema-level guarantee was considered and
NOT attempted this time, learned directly from ERG2's own reverted
attempt): `basis_assessment.candidate_id != candidate.id` is checked
explicitly here, before any row is constructed.

Never commits and never imports app.database.SessionLocal - mutates the
caller-supplied Session and flushes only so a constraint violation
(including the immutability event listeners) surfaces immediately; the
caller owns the transaction boundary entirely, matching every other
persistence service in this pipeline.

NO-AUTOFLUSH (ERG2 lesson, carried forward verbatim - docs/architecture/
rwi-erg2-relevance-assessment-persistence-report.md S34 S13): the entire
read-only precondition-check phase, starting from the very first attribute
read on `candidate`, is wrapped in `session.no_autoflush` - a caller with
unrelated, invalid, pending ORM state in the SAME session must never see a
leaked autoflush error from a read-only check. The intentional write
section remains unwrapped, exactly like ERG2's own documented SCOPE
boundary: once preconditions pass, session.flush() there flushes the
whole session's pending state, by design, matching every other
persistence function in this codebase.

INFORMATION FIREWALL: this module never imports Airport's write path,
Installation, Signal, app.services.unknown_airport_candidate_resolution
(UAC4), app.services.unknown_airport_discovery_integration (UAC3), or any
promotion/publish code - it creates exactly one kind of row (a relevance
review) and nothing else. It never mutates
UnknownAirportCandidateRelevanceAssessment, UnknownAirportCandidate, or
UnknownAirportCandidateReview - identity review history, automatic
relevance-assessment history, and human relevance-review history remain
three entirely separate, non-interacting append-only logs.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from sqlalchemy.orm import Session

from app.models.unknown_airport_candidate import UnknownAirportCandidate
from app.models.unknown_airport_candidate_relevance_assessment import UnknownAirportCandidateRelevanceAssessment
from app.models.unknown_airport_candidate_relevance_review import (
    RELEVANCE_REVIEW_ACTIONS,
    UnknownAirportCandidateRelevanceReview,
)
from app.services.unknown_airport_candidate_relevance_persistence import (
    get_latest_unknown_airport_candidate_relevance_assessment,
)

__all__ = [
    "RelevanceReviewState",
    "EffectiveRelevanceReviewState",
    "record_unknown_airport_candidate_relevance_review",
    "get_latest_unknown_airport_candidate_relevance_review",
    "resolve_effective_unknown_airport_candidate_relevance_review_state",
]


class RelevanceReviewState(str, Enum):
    """The minimal, deterministic vocabulary a future ERG4/UAC4 gate needs
    (mission's own S13/S23, HIGH PRIORITY) - distinguishes "latest review
    chronologically" from "review valid for the CURRENT assessment," which
    are NOT always the same thing (mission's own S14 worked example: a
    review that was current when recorded becomes stale the moment a newer
    automatic assessment is recorded, with no action on the review's own
    part)."""

    # The candidate has no automatic relevance assessment at all yet
    # (edge case - identity discovery fired via UAC3 but ERG2 has not yet
    # evaluated any evidence for this candidate).
    NO_ASSESSMENT_YET = "NO_ASSESSMENT_YET"
    # An assessment exists, but no human review has ever been recorded for
    # this candidate.
    UNREVIEWED = "UNREVIEWED"
    # A review exists, but its own basis_assessment_id no longer matches
    # the candidate's current latest assessment - a newer automatic
    # assessment has been recorded since. The review remains fully visible
    # in history; it simply no longer speaks for the CURRENT state.
    STALE = "STALE"
    # The latest review's own basis_assessment_id matches the candidate's
    # current latest assessment exactly - the human's decision (`action`)
    # is authoritative for the CURRENT state.
    CURRENT = "CURRENT"


@dataclass(frozen=True)
class EffectiveRelevanceReviewState:
    """The exact, minimal result contract a future ERG4/UAC4 gate can query
    deterministically to answer "does the CURRENT automatic assessment
    have a CURRENT human relevance review, and what did the human decide?"
    (mission's own S23). Never itself a gate decision - this module does
    not implement or authorize CREATE_NEW_AIRPORT eligibility; it only
    exposes the facts a future gate needs (see the persistence report's
    own FUTURE UAC4 GATE SEAM section for the exact recommended query)."""

    candidate_id: int
    state: RelevanceReviewState
    latest_assessment_id: "Optional[int]" = None
    latest_review_id: "Optional[int]" = None
    latest_review_basis_assessment_id: "Optional[int]" = None
    latest_review_action: "Optional[str]" = None

    @property
    def is_current(self) -> bool:
        return self.state == RelevanceReviewState.CURRENT

    @property
    def review_required(self) -> bool:
        return self.state in (RelevanceReviewState.UNREVIEWED, RelevanceReviewState.STALE)


def record_unknown_airport_candidate_relevance_review(
    session: Session,
    candidate: UnknownAirportCandidate,
    *,
    basis_assessment_id: int,
    action: str,
    reviewer: str,
    reason: str,
    supersedes_review_id: Optional[int] = None,
) -> UnknownAirportCandidateRelevanceReview:
    """Validates and appends exactly one immutable
    UnknownAirportCandidateRelevanceReview row. Never commits; calls
    session.flush() only so a constraint violation (including the
    immutability event listeners) surfaces immediately. See module
    docstring for the full stale-basis/cross-candidate/no-autoflush
    rationale.

    Never mutates `candidate`, never mutates the referenced
    UnknownAirportCandidateRelevanceAssessment, never creates an Airport,
    Installation, or Signal. CONFIRM_EMAS_RELEVANT records only that a
    human authorized a later, separate, not-yet-built governed action
    (a future UAC4 gate, ERG4) to consider this candidate - it is not that
    action itself.
    """
    with session.no_autoflush:
        if candidate.id is None:
            raise ValueError("candidate must already be persisted (has no id)")

        if action not in RELEVANCE_REVIEW_ACTIONS:
            raise ValueError(f"action must be one of {RELEVANCE_REVIEW_ACTIONS!r}, got {action!r}")
        if not reviewer or not reviewer.strip():
            raise ValueError("reviewer is required for a relevance review")
        if not reason or not reason.strip():
            raise ValueError("reason is required for a relevance review")

        if session.get(UnknownAirportCandidate, candidate.id) is None:
            raise ValueError("referenced UnknownAirportCandidate does not exist")

        basis_assessment = session.get(UnknownAirportCandidateRelevanceAssessment, basis_assessment_id)
        if basis_assessment is None:
            raise ValueError(f"referenced basis assessment (id={basis_assessment_id}) does not exist")
        if basis_assessment.candidate_id != candidate.id:
            raise ValueError(
                f"basis assessment (id={basis_assessment_id}) belongs to a different candidate "
                f"(candidate_id={basis_assessment.candidate_id}), not the reviewed candidate "
                f"(id={candidate.id}) - cannot record a relevance review against evidence belonging "
                "to a different candidate."
            )

        current_assessment = get_latest_unknown_airport_candidate_relevance_assessment(session, candidate.id)
        if current_assessment is None or current_assessment.id != basis_assessment_id:
            current_id = current_assessment.id if current_assessment is not None else None
            raise ValueError(
                f"basis_assessment_id={basis_assessment_id} is not the current latest automatic "
                f"assessment for candidate (id={candidate.id}) - current latest is "
                f"{current_id!r}. A stale basis can never silently authorize the current candidate "
                "state; fetch the current assessment and record an explicit new review against it."
            )

        if supersedes_review_id is not None:
            previous = session.get(UnknownAirportCandidateRelevanceReview, supersedes_review_id)
            if previous is None or previous.candidate_id != candidate.id:
                raise ValueError("superseded review must exist and concern the same candidate")

    record = UnknownAirportCandidateRelevanceReview(
        candidate_id=candidate.id,
        basis_assessment_id=basis_assessment_id,
        action=action,
        reviewer=reviewer.strip(),
        reason=reason.strip(),
        supersedes_review_id=supersedes_review_id,
    )
    session.add(record)
    session.flush()
    return record


def get_latest_unknown_airport_candidate_relevance_review(
    session: Session, candidate_id: int
) -> Optional[UnknownAirportCandidateRelevanceReview]:
    """The most recently recorded UnknownAirportCandidateRelevanceReview
    for a candidate, ordered by created_at then id - the exact same
    tiebreak discipline every other "latest" helper in this pipeline uses
    (get_latest_unknown_airport_candidate_relevance_assessment(),
    get_latest_unknown_airport_candidate_review(),
    get_latest_reviewer_action()). "Latest" means "most recently
    recorded," never "the unsuperseded terminal node reached by walking
    supersedes_review_id." Returns None if no review has ever been
    recorded for this candidate. Pure read - never mutates. Wrapped in
    session.no_autoflush - see module docstring.

    NOTE: this is the latest review CHRONOLOGICALLY - it says nothing
    about whether that review's own basis_assessment_id still matches the
    candidate's CURRENT latest assessment. Use
    resolve_effective_unknown_airport_candidate_relevance_review_state()
    to answer "is there a CURRENT human relevance review," not this
    function alone.
    """
    with session.no_autoflush:
        rows = (
            session.query(UnknownAirportCandidateRelevanceReview)
            .filter(UnknownAirportCandidateRelevanceReview.candidate_id == candidate_id)
            .order_by(
                UnknownAirportCandidateRelevanceReview.created_at.desc(),
                UnknownAirportCandidateRelevanceReview.id.desc(),
            )
            .limit(1)
            .all()
        )
    return rows[0] if rows else None


def resolve_effective_unknown_airport_candidate_relevance_review_state(
    session: Session, candidate_id: int
) -> EffectiveRelevanceReviewState:
    """Composes get_latest_unknown_airport_candidate_relevance_assessment()
    (ERG2, unmodified) and get_latest_unknown_airport_candidate_relevance_review()
    (this module) to answer the one question a future ERG4/UAC4 gate needs
    deterministically: is there a CURRENT human relevance review, and what
    did it decide? Pure read - never mutates, never records anything,
    never re-evaluates. Wrapped in session.no_autoflush (same rationale as
    every other read helper in this module).

    The latest review CHRONOLOGICALLY is never re-interpreted as CURRENT
    unless its own basis_assessment_id equals the candidate's current
    latest assessment id exactly - see RelevanceReviewState's own
    docstring for why these are not always the same thing.
    """
    with session.no_autoflush:
        latest_assessment = get_latest_unknown_airport_candidate_relevance_assessment(session, candidate_id)
        latest_review = get_latest_unknown_airport_candidate_relevance_review(session, candidate_id)

    if latest_assessment is None:
        return EffectiveRelevanceReviewState(candidate_id=candidate_id, state=RelevanceReviewState.NO_ASSESSMENT_YET)

    if latest_review is None:
        return EffectiveRelevanceReviewState(
            candidate_id=candidate_id, state=RelevanceReviewState.UNREVIEWED,
            latest_assessment_id=latest_assessment.id,
        )

    if latest_review.basis_assessment_id != latest_assessment.id:
        return EffectiveRelevanceReviewState(
            candidate_id=candidate_id, state=RelevanceReviewState.STALE,
            latest_assessment_id=latest_assessment.id,
            latest_review_id=latest_review.id,
            latest_review_basis_assessment_id=latest_review.basis_assessment_id,
            latest_review_action=latest_review.action,
        )

    return EffectiveRelevanceReviewState(
        candidate_id=candidate_id, state=RelevanceReviewState.CURRENT,
        latest_assessment_id=latest_assessment.id,
        latest_review_id=latest_review.id,
        latest_review_basis_assessment_id=latest_review.basis_assessment_id,
        latest_review_action=latest_review.action,
    )
