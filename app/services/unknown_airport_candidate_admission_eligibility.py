"""ERG4 — pure, read-only canonical-admission eligibility evaluation for
UnknownAirportCandidate
(docs/architecture/rwi-erg4-canonical-airport-admission-gate-report.md).

    UnknownAirportCandidateRelevanceAssessment (ERG2, current/latest)
        + EffectiveRelevanceReviewState (ERG3, current/latest)
        -> evaluate_unknown_airport_candidate_admission_eligibility()
        -> AdmissionEligibilityResult (eligible: bool, reason: enum)
        -> STOP (this module never creates an Airport, never mutates
           anything, never raises on ineligibility - it only ANSWERS a
           question; app.services.unknown_airport_candidate_resolution's
           create_airport_from_approved_candidate() is the ONLY place
           this answer is actually enforced)

THE PRODUCT RULE (locked, mission's own Section 3): canonical Airport
admission from an UnknownAirportCandidate requires ALL of:

    1. a current/latest ERG2 automatic relevance assessment exists
    2. that assessment's automatic canonical-admission relevance is TRUE,
       derived (never persisted - ERG1.6's own locked rule) as
       `is_inventory_relevant OR is_watch_worthy`
    3. the ERG3 effective human relevance review state is CURRENT (its
       basis_assessment_id matches the assessment named in (1) exactly)
    4. that CURRENT review's action is CONFIRM_EMAS_RELEVANT

Human CONFIRM can never substitute for automatic relevance (case: G-class-
only evidence, automatic admission=false, human CONFIRMs anyway - still
ineligible, reason=AUTOMATIC_RELEVANCE_NOT_ADMISSION_ELIGIBLE). Automatic
relevance can never substitute for a CURRENT human CONFIRM (case: D-class
evidence, automatic admission=true, no review at all, or a DEFER, or a
MARK_NOT, or a CONFIRM that has gone stale because a newer assessment
arrived without a matching new review - all ineligible). Neither
direction is ever weakened.

DERIVED, NEVER PERSISTED: this module adds no new column and no new
table. `is_inventory_relevant OR is_watch_worthy` is computed fresh on
every call from the current ERG2 assessment row, exactly mirroring
`UnknownAirportCandidateRelevanceAssessmentResult.is_canonical_admission_relevant`'s
own existing convention in
app.services.unknown_airport_candidate_relevance_persistence - this
module does not invent a second definition of that derivation, it reuses
the booleans ERG2 already persists.

REUSES, NEVER REIMPLEMENTS, "CURRENT": both "current assessment" and
"current human review" are answered by calling ERG2's
`get_latest_unknown_airport_candidate_relevance_assessment()` and ERG3's
`resolve_effective_unknown_airport_candidate_relevance_review_state()`
directly, unmodified. This module contains no independent sorting,
tiebreak, or staleness logic of its own - if either upstream helper's own
semantics ever change, this module's own behavior changes with them
automatically, by construction, rather than risking a second, potentially
divergent definition of "current."

FAIL-CLOSED BY SINGLE DERIVATION: `eligible` is never computed
independently of `reason` - it is always exactly
`reason == AdmissionEligibilityReason.ELIGIBLE`, derived by one ordered
elif chain with an explicit, documented fail-closed default branch for
any unrecognized review action (only reachable via a malformed, in-memory-
only state that bypasses the DB's own CHECK constraint - never reachable
through the governed `record_unknown_airport_candidate_relevance_review()`
write path). There is no second code path that could compute `eligible`
and `reason` inconsistently with each other.

INFORMATION FIREWALL: this module imports only the two existing ERG2/ERG3
read helpers and their own result types. It never imports Airport,
Signal, Installation, app.services.unknown_airport_candidate_resolution
(UAC4 - the enforcement point that imports THIS module, never the
reverse), app.services.unknown_airport_discovery_integration (UAC3), or
any promotion/publish code. It never mutates anything - no `session.add`,
no `session.flush`, no `session.commit` appears anywhere in this module.

ELIGIBLE DOES NOT IMPLY SIGNAL-CREATABLE: a candidate can be
canonical-admission-eligible (this module says so) while the separate,
already-known UAC5B guard-replay gap still prevents candidate-origin
evidence from reaching Signal creation after admission. That gap is not
fixed here and is explicitly out of scope for ERG4 (a future, separate
mission).

Never commits, never flushes, never writes. Wrapped in
`session.no_autoflush` for the same reason every other read-only helper
in this pipeline is (ERG2/ERG3's own no-autoflush lessons): a purely
read-only evaluation must never trigger a premature flush of some
unrelated pending object the caller happens to be holding in the same
session.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from sqlalchemy.orm import Session

from app.services.unknown_airport_candidate_relevance_persistence import (
    get_latest_unknown_airport_candidate_relevance_assessment,
)
from app.services.unknown_airport_candidate_relevance_review_persistence import (
    RelevanceReviewState,
    resolve_effective_unknown_airport_candidate_relevance_review_state,
)

__all__ = [
    "AdmissionEligibilityReason",
    "AdmissionEligibilityResult",
    "evaluate_unknown_airport_candidate_admission_eligibility",
]

_CONFIRM_ACTION = "CONFIRM_EMAS_RELEVANT"
_MARK_NOT_ACTION = "MARK_NOT_EMAS_RELEVANT"
_DEFER_ACTION = "DEFER_RELEVANCE_REVIEW"


class AdmissionEligibilityReason(str, Enum):
    """Explainable, deterministic vocabulary for why a candidate is or is
    not canonical-admission-eligible. Exactly one member (`ELIGIBLE`)
    means "all four conditions of the locked product rule hold" - every
    other member names precisely which condition failed."""

    NO_RELEVANCE_ASSESSMENT = "NO_RELEVANCE_ASSESSMENT"
    AUTOMATIC_RELEVANCE_NOT_ADMISSION_ELIGIBLE = "AUTOMATIC_RELEVANCE_NOT_ADMISSION_ELIGIBLE"
    NO_CURRENT_HUMAN_REVIEW = "NO_CURRENT_HUMAN_REVIEW"
    HUMAN_REVIEW_STALE = "HUMAN_REVIEW_STALE"
    HUMAN_REVIEW_DEFERRED = "HUMAN_REVIEW_DEFERRED"
    HUMAN_REVIEW_MARKED_NOT_RELEVANT = "HUMAN_REVIEW_MARKED_NOT_RELEVANT"
    ELIGIBLE = "ELIGIBLE"


@dataclass(frozen=True)
class AdmissionEligibilityResult:
    """Deterministic, ORM-adjacent summary of
    evaluate_unknown_airport_candidate_admission_eligibility(). Exposes
    every fact the reason was derived from, so a caller/test/CLI can
    explain a BLOCK without a second read."""

    candidate_id: int
    eligible: bool
    reason: AdmissionEligibilityReason
    latest_assessment_id: "Optional[int]" = None
    is_automatic_admission_relevant: "Optional[bool]" = None
    review_state: "Optional[RelevanceReviewState]" = None
    latest_review_id: "Optional[int]" = None
    latest_review_action: "Optional[str]" = None


def evaluate_unknown_airport_candidate_admission_eligibility(
    session: Session, candidate_id: int
) -> AdmissionEligibilityResult:
    """Pure, read-only. Answers exactly one question: may this candidate
    proceed toward canonical Airport admission from an EMAS-relevance
    perspective? Never raises on ineligibility - callers that must enforce
    the answer (app.services.unknown_airport_candidate_resolution) are
    responsible for turning `eligible is False` into a refusal. Never
    touches identity review (UnknownAirportCandidateReview) at all - see
    module docstring."""
    # no_autoflush (ERG2/ERG3 lesson, applied from the start): wraps the
    # ENTIRE read-only phase, including the attribute reads on
    # `latest_assessment` below - not just the two upstream helper calls.
    # An expired ORM attribute read (e.g. after an earlier caller commit)
    # triggers an internal refresh SELECT that runs through SQLAlchemy's
    # default autoflush path exactly like session.get()/session.query()
    # do, and would otherwise flush any unrelated pending object the
    # caller happens to be holding in the same session. `review_state`'s
    # own attributes are plain dataclass fields (never lazy-loaded), so
    # only the `latest_assessment.*` reads below are actually at risk -
    # but they are included in this same block anyway, for the same
    # "wrap starting from the very first read" discipline ERG2's own
    # review established, not a narrower, easier-to-regress carve-out.
    with session.no_autoflush:
        latest_assessment = get_latest_unknown_airport_candidate_relevance_assessment(session, candidate_id)
        review_state = resolve_effective_unknown_airport_candidate_relevance_review_state(session, candidate_id)

        is_automatic_admission_relevant: "Optional[bool]" = None
        latest_assessment_id: "Optional[int]" = None
        if latest_assessment is not None:
            latest_assessment_id = latest_assessment.id
            is_automatic_admission_relevant = bool(
                latest_assessment.is_inventory_relevant or latest_assessment.is_watch_worthy
            )

    if latest_assessment is None:
        reason = AdmissionEligibilityReason.NO_RELEVANCE_ASSESSMENT
    elif not is_automatic_admission_relevant:
        reason = AdmissionEligibilityReason.AUTOMATIC_RELEVANCE_NOT_ADMISSION_ELIGIBLE
    elif review_state.state == RelevanceReviewState.UNREVIEWED:
        reason = AdmissionEligibilityReason.NO_CURRENT_HUMAN_REVIEW
    elif review_state.state == RelevanceReviewState.STALE:
        reason = AdmissionEligibilityReason.HUMAN_REVIEW_STALE
    elif review_state.state == RelevanceReviewState.NO_ASSESSMENT_YET:
        # Structurally unreachable here: latest_assessment is not None
        # (the branch above already excluded that), and both
        # latest_assessment and review_state.state derive "does an
        # assessment exist" from the exact same ERG2 helper - so
        # review_state.state cannot be NO_ASSESSMENT_YET at this point
        # without that invariant itself being violated. Kept as an
        # explicit, fail-closed branch rather than an assumption.
        reason = AdmissionEligibilityReason.NO_RELEVANCE_ASSESSMENT
    elif review_state.latest_review_action == _DEFER_ACTION:
        reason = AdmissionEligibilityReason.HUMAN_REVIEW_DEFERRED
    elif review_state.latest_review_action == _MARK_NOT_ACTION:
        reason = AdmissionEligibilityReason.HUMAN_REVIEW_MARKED_NOT_RELEVANT
    elif review_state.latest_review_action == _CONFIRM_ACTION:
        reason = AdmissionEligibilityReason.ELIGIBLE
    else:
        # Fail-closed default: review_state.state == CURRENT but
        # latest_review_action is none of the three known actions. Only
        # reachable via a malformed, in-memory-only ORM state that
        # bypasses the DB's own CHECK constraint on
        # UnknownAirportCandidateRelevanceReview.action - never reachable
        # through record_unknown_airport_candidate_relevance_review()'s
        # own governed, validated write path. Never ELIGIBLE.
        reason = AdmissionEligibilityReason.NO_CURRENT_HUMAN_REVIEW

    return AdmissionEligibilityResult(
        candidate_id=candidate_id,
        eligible=reason == AdmissionEligibilityReason.ELIGIBLE,
        reason=reason,
        latest_assessment_id=latest_assessment_id,
        is_automatic_admission_relevant=is_automatic_admission_relevant,
        review_state=review_state.state,
        latest_review_id=review_state.latest_review_id,
        latest_review_action=review_state.latest_review_action,
    )
