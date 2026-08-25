"""UAC4 — governed human-resolution execution for UnknownAirportCandidate
(docs/architecture/rwi-uac4-unknown-airport-resolution-report.md, Slice 4
of docs/architecture/rwi-governed-new-airport-discovery-design.md).

    UnknownAirportCandidate (UAC1) + UnknownAirportCandidateReview
    (action=MATCH_EXISTING_AIRPORT or CREATE_NEW_AIRPORT, UAC1)
        -> resolve_candidate_to_existing_airport() / create_airport_from_approved_candidate()
        -> candidate.resolved_airport_id set
        -> every candidate-linked SourceAssertion moved to airport_id
           (unknown_airport_candidate_id cleared, UAC2B's mutual-exclusivity
           CHECK satisfied at every instant)
        -> STOP (no Runway, RunwayEnd, Installation, or Signal is ever
           created here; the human review CLI, correction/re-resolution
           workflows for an already-resolved candidate, and any
           candidate-selection/acquisition wiring are all separate,
           future, not-yet-built work)

RECORDING A REVIEW IS NOT EXECUTING ONE. `app.services.unknown_airport_candidate_persistence.record_unknown_airport_candidate_review()`
(UAC1, unmodified, imported not reimplemented) is the ONLY way a human
review decision is ever persisted - inserting a
`UnknownAirportCandidateReview` row never mutates
`candidate.resolved_airport_id` or any `SourceAssertion`, by that
module's own, unmodified design. The two functions in THIS module are
the ONLY code in the entire repository that may ever set
`UnknownAirportCandidate.resolved_airport_id` or reassign a
`SourceAssertion` between candidate-linked and airport-linked state -
this is the strict review/execution separation the design mandates,
enforced structurally by which module owns which write, not merely by
convention.

STALE-REVIEW PROTECTION (mirrors the existing R4C precedent in
`app.services.governed_signal_creation` exactly, simplified for this
module's own narrower need): both execution functions require the
caller to name the EXACT `review_id` they are executing, and refuse
(`StaleReviewError`) unless that review is genuinely the CURRENT latest
review for the candidate (via UAC1's own, unmodified
`get_latest_unknown_airport_candidate_review()`) AND its `action`
matches the execution being attempted. A review recorded after the one
the caller read - even a DEFER, even one that later reverses itself back
to the same action - makes the original `review_id` stale; the caller
must re-read and retry, exactly the same "no execution ever trusts
a possibly-outdated read" discipline `create_signal_from_approved_review()`
already established for its own `CONFIRM_DISTINCT_SIGNAL` path.

ALREADY-RESOLVED IS A HARD, PERMANENT REFUSAL, NOT AN IDEMPOTENT REPLAY.
Unlike `persist_discovery_fragment()`/`find_or_create_unknown_airport_candidate()`'s
own "same input twice, same safe result" idempotency, once
`candidate.resolved_airport_id` is set to ANY value, both execution
functions refuse unconditionally on every subsequent call for that
candidate, regardless of what the current review says - `UAC4RESOLUTION`
implements no re-resolution, correction, or reversal workflow of any
kind (a later, separately-designed, explicitly out-of-scope slice, per
this module's own governing mission). This is a deliberate asymmetry
from the rest of this pipeline's usual idempotent-replay convention,
documented here so a future reader does not "fix" it into a silent
no-op that would hide a genuine caller bug (attempting to re-execute an
already-resolved candidate is *always* a caller error worth surfacing,
never a safe retry).

CREATE_NEW_AIRPORT NEVER AUTO-COPIES CANDIDATE CLAIM FIELDS INTO THE
NEW AIRPORT. `create_airport_from_approved_candidate()` accepts only
explicit, named, human-selected keyword arguments (`name`, `country`,
and a handful of optional canonical fields) - never a raw
`UnknownAirportCandidate` instance, and never reads `candidate.raw_*`
for VALUE purposes anywhere in this module (only for gating: does a
CREATE_NEW_AIRPORT review exist, is the candidate unresolved). This
mirrors `create_signal_from_approved_review()`'s own identical,
already-reviewed discipline exactly, and closes a genuine schema
mismatch this slice's own review process found: `Airport.country` is
NOT NULL, but `UnknownAirportCandidate.raw_country` is nullable and, via
the current UAC3 discovery-integration pipeline, is *always* None today
(`CandidateFragment.locations` is undifferentiated by geographic
granularity - UAC3 deliberately never guesses it into `raw_country`).
Silently defaulting a required canonical field from an unreliable,
frequently-absent claimed field would be exactly the kind of
"invent data" this module's own governing mission explicitly forbids;
requiring an explicit human-supplied `country` kwarg instead is the
correct, precedented, smallest-possible-mismatch resolution - not an
unresolved architecture gap.

Never commits and never imports `app.database.SessionLocal` anywhere in
this module - mutates the caller-supplied `Session` and flushes only so
a constraint violation surfaces immediately; the caller owns the
transaction boundary entirely, matching every other persistence service
in this pipeline.

ERG4 CANONICAL-ADMISSION RELEVANCE GATE
(docs/architecture/rwi-erg4-canonical-airport-admission-gate-report.md):
`create_airport_from_approved_candidate()` is THE authoritative
enforcement point for the ERG4 rule - there is no other code path in the
repository that inserts an `Airport` row from an `UnknownAirportCandidate`.
Immediately after the pre-existing identity-review gate
(`_require_current_review()`, UNCHANGED, still the first check - ERG4
is an ADDITIONAL precondition, never a replacement for identity
governance), this function calls the pure, read-only
`evaluate_unknown_airport_candidate_admission_eligibility()`
(app.services.unknown_airport_candidate_admission_eligibility, ERG4) and
refuses with `RelevanceGateRefusedError` unless it reports
`eligible=True`. That function composes ERG2's current-assessment and
ERG3's current-human-review helpers UNMODIFIED - this module still
contains no relevance-classification or review-state logic of its own,
exactly the same "identity governance vs. relevance governance, never
blurred" separation ERG1-ERG3 already established. A direct Python
caller of this function - not just the UAC5 CLI - is bound by this gate;
there is no separate, weaker code path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Airport, SourceAssertion
from app.models.unknown_airport_candidate import UnknownAirportCandidate, UnknownAirportCandidateReview
from app.services.unknown_airport_candidate_admission_eligibility import (
    evaluate_unknown_airport_candidate_admission_eligibility,
)
from app.services.unknown_airport_candidate_persistence import get_latest_unknown_airport_candidate_review

__all__ = [
    "AlreadyResolvedError",
    "StaleReviewError",
    "InconsistentCandidateStateError",
    "RelevanceGateRefusedError",
    "MatchExistingAirportResult",
    "CreateNewAirportResult",
    "resolve_candidate_to_existing_airport",
    "create_airport_from_approved_candidate",
]

_MATCH_ACTION = "MATCH_EXISTING_AIRPORT"
_CREATE_ACTION = "CREATE_NEW_AIRPORT"


class AlreadyResolvedError(RuntimeError):
    """Raised by either execution function when
    `candidate.resolved_airport_id` is already set to ANY value -
    execution in this module is a one-time, permanent operation per
    candidate, never an idempotent replay and never a re-resolution.
    Fires unconditionally, regardless of what the current review says -
    UAC4 implements no correction/reversal workflow of any kind (module
    docstring)."""

    def __init__(self, candidate_id: int, resolved_airport_id: int) -> None:
        self.candidate_id = candidate_id
        self.resolved_airport_id = resolved_airport_id
        super().__init__(
            f"UnknownAirportCandidate {candidate_id} is already resolved to Airport "
            f"{resolved_airport_id} - this module implements no re-resolution or correction "
            "workflow; a new, separately-designed slice is required to change an already-resolved "
            "candidate's canonical linkage."
        )


class StaleReviewError(RuntimeError):
    """Raised when the caller-supplied `review_id` is not genuinely the
    CURRENT latest review for the candidate, or its `action` does not
    match the execution being attempted - mirrors
    `app.services.governed_signal_creation.StaleReconciliationConfirmationError`'s
    own reasoning: a review that was current when the caller read it may
    no longer be current by the time they act on it (a newer review -
    even a DEFER - may have been recorded in between). The caller must
    re-read `get_latest_unknown_airport_candidate_review()` and retry
    with the genuinely current review_id."""

    def __init__(self, candidate_id: int, review_id: int, *, reason: str) -> None:
        self.candidate_id = candidate_id
        self.review_id = review_id
        super().__init__(
            f"review_id={review_id} for UnknownAirportCandidate {candidate_id} is stale or invalid: "
            f"{reason}. Re-read get_latest_unknown_airport_candidate_review() and retry with the "
            "current review."
        )


class InconsistentCandidateStateError(RuntimeError):
    """Raised when the candidate or its linked SourceAssertions are found
    in a state that cannot honestly proceed - e.g. one linked
    SourceAssertion already carries a non-NULL airport_id (structurally
    impossible via this module's own two functions given UAC2B's own
    mutual-exclusivity CHECK, but not impossible via raw SQL/direct ORM
    bypass). This module never silently "repairs" such a state; it fails
    loud and refuses to act."""

    def __init__(self, candidate_id: int, *, reason: str) -> None:
        self.candidate_id = candidate_id
        super().__init__(
            f"UnknownAirportCandidate {candidate_id} is in an inconsistent state and cannot be "
            f"resolved: {reason}. This module never silently repairs malformed state."
        )


class RelevanceGateRefusedError(RuntimeError):
    """Raised by `create_airport_from_approved_candidate()` (ONLY - never
    `resolve_candidate_to_existing_airport()`, which links to an existing
    Airport rather than creating a new one and is out of ERG4's scope) when
    `evaluate_unknown_airport_candidate_admission_eligibility()` reports
    `eligible=False`. The candidate, its SourceAssertions, its relevance
    assessment history, and its relevance review history are all left
    completely untouched - this is a precondition refusal raised before
    any mutation, exactly like `AlreadyResolvedError`/`StaleReviewError`/
    `InconsistentCandidateStateError`."""

    def __init__(self, candidate_id: int, *, reason: str, detail: str) -> None:
        self.candidate_id = candidate_id
        self.reason = reason
        super().__init__(
            f"UnknownAirportCandidate {candidate_id} is not canonical-admission-eligible "
            f"(reason={reason}): {detail}"
        )


def _linked_assertions(session: Session, candidate_id: int) -> "list[SourceAssertion]":
    return (
        session.query(SourceAssertion)
        .filter(SourceAssertion.unknown_airport_candidate_id == candidate_id)
        .order_by(SourceAssertion.id.asc())
        .all()
    )


def _require_unresolved(candidate: UnknownAirportCandidate) -> None:
    if candidate.resolved_airport_id is not None:
        raise AlreadyResolvedError(candidate.id, candidate.resolved_airport_id)


def _require_current_review(
    session: Session, candidate: UnknownAirportCandidate, *, review_id: int, expected_action: str,
) -> UnknownAirportCandidateReview:
    # UAC-H1 no_autoflush hardening: wraps this ENTIRE read-only function,
    # starting from the very first `candidate.id` read - reading an
    # expired attribute (e.g. after an earlier caller commit) triggers an
    # internal refresh SELECT that runs through SQLAlchemy's default
    # autoflush path exactly like session.get()/session.query() do, and
    # would otherwise flush any unrelated pending object the caller
    # happens to be holding in the same session. Reproduced directly
    # before this fix. This function performs no writes of its own, so
    # wrapping its entire body is safe.
    with session.no_autoflush:
        latest = get_latest_unknown_airport_candidate_review(session, candidate.id)
        if latest is None:
            raise StaleReviewError(candidate.id, review_id, reason="no review has ever been recorded for this candidate")
        if latest.id != review_id:
            raise StaleReviewError(
                candidate.id, review_id,
                reason=f"the current latest review is id={latest.id} (action={latest.action!r}), not {review_id!r}",
            )
        if latest.action != expected_action:
            raise StaleReviewError(
                candidate.id, review_id,
                reason=f"the current review's action is {latest.action!r}, not the required {expected_action!r}",
            )
        return latest


def _require_no_linked_assertion_already_canonical(candidate_id: int, assertions: "list[SourceAssertion]") -> None:
    for assertion in assertions:
        if assertion.airport_id is not None:
            raise InconsistentCandidateStateError(
                candidate_id,
                reason=(
                    f"SourceAssertion {assertion.id} is linked to this candidate via "
                    "unknown_airport_candidate_id but already carries a non-NULL airport_id "
                    f"({assertion.airport_id}) - structurally impossible via this module's own "
                    "functions; only reachable via a direct schema bypass."
                ),
            )


_ADMISSION_REASON_DETAIL = {
    "NO_RELEVANCE_ASSESSMENT": "no ERG2 automatic relevance assessment has ever been recorded for this candidate",
    "AUTOMATIC_RELEVANCE_NOT_ADMISSION_ELIGIBLE": (
        "the current automatic relevance assessment shows is_inventory_relevant=False and "
        "is_watch_worthy=False - a human relevance review, even CONFIRM_EMAS_RELEVANT, cannot "
        "manufacture EMAS relevance the automatic evaluator did not find"
    ),
    "NO_CURRENT_HUMAN_REVIEW": "no human relevance review has ever been recorded for the current assessment",
    "HUMAN_REVIEW_STALE": (
        "the latest human relevance review was recorded against an earlier assessment, not the "
        "current one - a newer automatic assessment has been recorded since; a new, current review "
        "is required"
    ),
    "HUMAN_REVIEW_DEFERRED": "the current human relevance review action is DEFER_RELEVANCE_REVIEW, not CONFIRM_EMAS_RELEVANT",
    "HUMAN_REVIEW_MARKED_NOT_RELEVANT": (
        "the current human relevance review action is MARK_NOT_EMAS_RELEVANT, not CONFIRM_EMAS_RELEVANT"
    ),
}


def _require_admission_eligible(session: Session, candidate_id: int) -> None:
    result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate_id)
    if not result.eligible:
        detail = _ADMISSION_REASON_DETAIL.get(result.reason.value, "eligibility evaluation reported ineligible")
        raise RelevanceGateRefusedError(candidate_id, reason=result.reason.value, detail=detail)


@dataclass(frozen=True)
class MatchExistingAirportResult:
    """Deterministic, ORM-free summary of what
    resolve_candidate_to_existing_airport() did."""

    candidate_id: int
    review_id: int
    resolved_airport_id: int
    moved_source_assertion_ids: "tuple[int, ...]"


@dataclass(frozen=True)
class CreateNewAirportResult:
    """Deterministic, ORM-free summary of what
    create_airport_from_approved_candidate() did."""

    candidate_id: int
    review_id: int
    created_airport_id: int
    moved_source_assertion_ids: "tuple[int, ...]"


def resolve_candidate_to_existing_airport(
    session: Session, *, candidate_id: int, review_id: int,
) -> MatchExistingAirportResult:
    """Executes an already-recorded MATCH_EXISTING_AIRPORT review: sets
    `candidate.resolved_airport_id` to the review's own `matched_airport_id`,
    and moves every SourceAssertion currently linked to this candidate to
    that Airport (airport_id set, unknown_airport_candidate_id cleared -
    both in the SAME flush, so UAC2B's mutual-exclusivity CHECK is never
    even transiently violated). Creates no Airport, no Runway, no
    RunwayEnd, no Installation, no Signal. Never commits.

    Fails closed (before any mutation) if: the candidate does not exist;
    it is already resolved (AlreadyResolvedError); `review_id` is not
    genuinely the current latest MATCH_EXISTING_AIRPORT review
    (StaleReviewError); the matched Airport no longer exists; or any
    linked SourceAssertion is already inconsistently airport-linked
    (InconsistentCandidateStateError).
    """
    # UAC-H1 no_autoflush hardening: wraps the ENTIRE read-only
    # precondition-check phase below - starting from the very first
    # session.get() - in session.no_autoflush. Reproduced directly before
    # this fix: an unrelated, invalid, pending ORM object elsewhere in the
    # same session would have its own constraint violation raised HERE,
    # at this function's first read, instead of being left pending until
    # the caller's own intended commit. SCOPE, stated precisely (do not
    # over-claim): this guard protects only the PRECONDITION-CHECK phase -
    # the intentional write section below (candidate.resolved_airport_id
    # assignment + session.flush()) is deliberately OUTSIDE this block;
    # that flush is this function's own intentional write point and
    # flushes the whole session's pending state, by design, exactly like
    # every other persistence function in this codebase.
    with session.no_autoflush:
        candidate = session.get(UnknownAirportCandidate, candidate_id)
        if candidate is None:
            raise ValueError(f"UnknownAirportCandidate {candidate_id} does not exist")
        _require_unresolved(candidate)
        review = _require_current_review(session, candidate, review_id=review_id, expected_action=_MATCH_ACTION)

        matched_airport_id = review.matched_airport_id
        if session.get(Airport, matched_airport_id) is None:
            raise ValueError(
                f"review {review_id}'s matched_airport_id={matched_airport_id!r} does not reference an "
                "existing Airport"
            )

        assertions = _linked_assertions(session, candidate_id)
        _require_no_linked_assertion_already_canonical(candidate_id, assertions)

    candidate.resolved_airport_id = matched_airport_id
    for assertion in assertions:
        assertion.airport_id = matched_airport_id
        assertion.unknown_airport_candidate_id = None
    session.flush()

    return MatchExistingAirportResult(
        candidate_id=candidate_id, review_id=review_id, resolved_airport_id=matched_airport_id,
        moved_source_assertion_ids=tuple(a.id for a in assertions),
    )


def create_airport_from_approved_candidate(
    session: Session,
    *,
    candidate_id: int,
    review_id: int,
    name: str,
    country: str,
    city: Optional[str] = None,
    state_region: Optional[str] = None,
    iata_code: Optional[str] = None,
    icao_code: Optional[str] = None,
    faa_code: Optional[str] = None,
) -> CreateNewAirportResult:
    """Executes an already-recorded CREATE_NEW_AIRPORT review: creates
    EXACTLY ONE Airport row from the explicit, human-supplied keyword
    arguments (never from `candidate.raw_*` - see module docstring),
    sets `candidate.resolved_airport_id` to it, and moves every
    SourceAssertion currently linked to this candidate to that Airport
    (same atomic-flush discipline as `resolve_candidate_to_existing_airport()`).
    Creates no Runway, no RunwayEnd, no Installation, no Signal. Never
    commits.

    `name`/`country` are required (matching `Airport`'s own NOT NULL
    columns exactly); every other keyword is optional and left `None`
    if not supplied - no field is ever guessed or defaulted from the
    candidate's own claims.

    DUPLICATE-AIRPORT DEFENSE (deterministic only, never fuzzy): before
    creating anything, refuses if any EXISTING Airport already has a
    non-empty `iata_code`, `icao_code`, or `faa_code` that matches one
    supplied here under whitespace-stripped, case-insensitive comparison
    - the same `.strip().casefold()` normalization
    `app.services.evidence_attachment_guard._norm_text()` already
    establishes as this repository's convention for comparing a claimed
    identifier against a canonical one (`Airport.iata_code`/`icao_code`/
    `faa_code` carry no DB-level case constraint of their own - confirmed
    by direct model inspection - so a byte-exact comparison would miss a
    real duplicate differing only in case or incidental whitespace,
    e.g. `"kabc"` vs. an existing `"KABC"`). A real airport with that
    canonical code already exists, so the human should use
    `resolve_candidate_to_existing_airport()` (MATCH_EXISTING_AIRPORT)
    instead. Never compares `name`/`city`/`country` for duplication
    (those are exactly the free-text fields this project's own
    evidentiary discipline has repeatedly refused to treat as
    proof of sameness) - only exact (case/whitespace-insensitive),
    canonical, coded-identifier collisions are checked.

    Fails closed (before any mutation) for the same set of reasons
    `resolve_candidate_to_existing_airport()` does, replacing the
    "matched Airport must exist" check with the duplicate-code check
    above - PLUS one additional, ERG4-specific precondition
    (`RelevanceGateRefusedError`): the candidate's current automatic
    relevance assessment must show canonical-admission relevance
    (`is_inventory_relevant OR is_watch_worthy`) AND its current human
    relevance review must be CONFIRM_EMAS_RELEVANT against that exact
    assessment. See module docstring's own ERG4 section.
    """
    # UAC-H1 no_autoflush hardening: same rationale and SCOPE boundary as
    # resolve_candidate_to_existing_airport() above - wraps the entire
    # read-only precondition-check phase (including the duplicate-code
    # query loop below, a second, independently-discovered unwrapped
    # session.query() with the same latent risk), never the intentional
    # write section starting at `airport = Airport(...)`.
    with session.no_autoflush:
        candidate = session.get(UnknownAirportCandidate, candidate_id)
        if candidate is None:
            raise ValueError(f"UnknownAirportCandidate {candidate_id} does not exist")
        _require_unresolved(candidate)
        _require_current_review(session, candidate, review_id=review_id, expected_action=_CREATE_ACTION)
        _require_admission_eligible(session, candidate_id)

        if not name or not name.strip():
            raise ValueError("name is required")
        if not country or not country.strip():
            raise ValueError("country is required")

        normalized_codes: dict[str, Optional[str]] = {}
        for code_field, code_value in (("iata_code", iata_code), ("icao_code", icao_code), ("faa_code", faa_code)):
            stripped = code_value.strip() if code_value and code_value.strip() else None
            normalized_codes[code_field] = stripped
            if stripped is None:
                continue
            target = stripped.casefold()
            conflict = next(
                (
                    existing
                    for existing in session.query(Airport).filter(getattr(Airport, code_field).isnot(None)).all()
                    if getattr(existing, code_field).strip().casefold() == target
                ),
                None,
            )
            if conflict is not None:
                raise ValueError(
                    f"CREATE_NEW_AIRPORT refused: an existing Airport (id={conflict.id}) already has "
                    f"{code_field}={getattr(conflict, code_field)!r}, matching {stripped!r} under "
                    "case/whitespace-insensitive comparison - use resolve_candidate_to_existing_airport() "
                    "(MATCH_EXISTING_AIRPORT) instead of creating a duplicate."
                )

        assertions = _linked_assertions(session, candidate_id)
        _require_no_linked_assertion_already_canonical(candidate_id, assertions)

    airport = Airport(
        name=name.strip(), country=country.strip(),
        city=city, state_region=state_region,
        iata_code=normalized_codes["iata_code"], icao_code=normalized_codes["icao_code"],
        faa_code=normalized_codes["faa_code"],
    )
    session.add(airport)
    session.flush()  # obtain airport.id

    candidate.resolved_airport_id = airport.id
    for assertion in assertions:
        assertion.airport_id = airport.id
        assertion.unknown_airport_candidate_id = None
    session.flush()

    return CreateNewAirportResult(
        candidate_id=candidate_id, review_id=review_id, created_airport_id=airport.id,
        moved_source_assertion_ids=tuple(a.id for a in assertions),
    )
