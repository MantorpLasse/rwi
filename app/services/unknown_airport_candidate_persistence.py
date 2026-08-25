"""Persistence and validation for governed unknown-airport candidates
(UAC1, docs/architecture/rwi-uac1-unknown-airport-candidate-persistence-report.md,
Slice 1 of docs/architecture/rwi-governed-new-airport-discovery-design.md).

    (evidence claims an airport RWI has no known candidate for)
        -> find_or_create_unknown_airport_candidate()
        -> one UnknownAirportCandidate row (found or created)
        -> record_unknown_airport_candidate_review()
        -> one immutable UnknownAirportCandidateReview row appended
        -> STOP (no Airport, Runway, RunwayEnd, Installation, or Signal is
           ever created, updated, or resolved by this module - creating a
           real Airport from an approved CREATE_NEW_AIRPORT review is a
           separate, future, not-yet-built governed operation, design doc
           §8)

Deliberately does NOT accept, import, or construct a CandidateFragment, an
EvidenceBag, or a SourceAssertion - integrating this module with the real
discovery pipeline's candidate-selection step (deciding WHEN evidence
qualifies as "no known candidate matched") and wiring SourceAssertion's
own additive unknown_airport_candidate_id FK are both explicitly out of
scope for this slice (design doc §5; UAC1 mission brief §3). This module
accepts only already-decided, plain keyword arguments - the caller is
responsible for having already determined that no known Airport candidate
matched.

Never commits and never imports app.database.SessionLocal - mutates the
caller-supplied Session and flushes only so a constraint violation
surfaces immediately; the caller owns the transaction boundary entirely,
matching every other persistence service in this pipeline
(persist_discovery_fragment, record_reviewer_action,
record_reconciliation_decision).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Airport
from app.models.unknown_airport_candidate import (
    UNKNOWN_AIRPORT_CANDIDATE_REVIEW_ACTIONS,
    UnknownAirportCandidate,
    UnknownAirportCandidateReview,
)

__all__ = [
    "UnknownAirportCandidateResult",
    "compute_candidate_fingerprint",
    "find_or_create_unknown_airport_candidate",
    "record_unknown_airport_candidate_review",
    "get_latest_unknown_airport_candidate_review",
]

# Actions requiring matched_airport_id (mirrors the CHECK constraint pair
# on UnknownAirportCandidateReview - re-validated in Python for the same
# fail-fast-before-flush reason app.services.reviewer_action_persistence
# re-validates ReviewerAction's own CHECK-constrained fields).
_MATCH_ACTION = "MATCH_EXISTING_AIRPORT"


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().casefold()


def compute_candidate_fingerprint(
    raw_name: str,
    raw_country: Optional[str] = None,
    raw_city: Optional[str] = None,
    raw_state_region: Optional[str] = None,
) -> str:
    """Deterministic exact-convergence key (design doc §9, revised during
    UAC1's own critical review - see the review report's "fingerprint
    safety" section): casefolded, whitespace-stripped
    raw_name + raw_city + raw_state_region + raw_country, sha256-hexdigest.

    CORRECTED FROM name+country ALONE: name+country alone is not a safe
    exact-identity key - it is only a candidate-grouping hint. Many real
    airports share a generic name ("Municipal Airport", "Regional
    Airport") within the same country; two fragments claiming the same
    such name in the same country but a DIFFERENT city would have
    silently converged onto one candidate row under the original
    two-field key, which is exactly the "false automatic convergence"
    the design's own §9 forbids ("prefer fail-closed grouping over
    automatic fuzzy merging"). Widening the key to also include
    raw_city/raw_state_region is still a byte-exact, deterministic
    function of already-claimed fields - not a similarity heuristic - and
    only ever makes convergence STRICTER (more separate candidate rows,
    never a merge of two rows that would previously have stayed
    separate). This trades a higher chance of avoidable near-duplicate
    candidate rows for the SAME real airport (whose evidence fragments
    differ only in whether city/state happened to be reported) for
    eliminating the false-merge risk entirely - exactly the "prefer false
    separation over false automatic convergence" instruction this
    correction was made under. Near-duplicate convergence across
    differing fingerprints remains advisory-only/human-resolved (design
    doc §9), unchanged by this correction.

    Deliberately does NOT include raw_iata_code/raw_icao_code/raw_faa_lid:
    codes are reported far more inconsistently per fragment (one fragment
    may carry only an ICAO code, another only an IATA code, for the
    exact same real airport - see the review report's "case M"
    discussion) than city/state, so including them would fragment
    genuinely-identical claims far more aggressively than the
    location-identity fields do; contradictory codes under an otherwise-
    matching location fingerprint are a near-duplicate/contradiction
    question for human review, not a convergence-key question. Pure - no
    I/O, no database access.
    """
    normalized = f"{_norm(raw_name)}|{_norm(raw_city)}|{_norm(raw_state_region)}|{_norm(raw_country)}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class UnknownAirportCandidateResult:
    """created=True only when this call itself inserted a new
    UnknownAirportCandidate row; False when an existing row with the same
    candidate_fingerprint was found and reused instead (exact
    convergence)."""

    candidate: UnknownAirportCandidate
    created: bool


def find_or_create_unknown_airport_candidate(
    session: Session,
    *,
    raw_name: str,
    raw_city: Optional[str] = None,
    raw_state_region: Optional[str] = None,
    raw_country: Optional[str] = None,
    raw_iata_code: Optional[str] = None,
    raw_icao_code: Optional[str] = None,
    raw_faa_lid: Optional[str] = None,
    raw_runway_designation: Optional[str] = None,
    evidence_source_locator: Optional[str] = None,
    evidence_artifact_identity: Optional[str] = None,
) -> UnknownAirportCandidateResult:
    """Finds an existing UnknownAirportCandidate by exact
    candidate_fingerprint, or creates exactly one new row. Never commits;
    calls session.flush() so a constraint violation (including the
    candidate_fingerprint UNIQUE constraint, as a defensive backstop -
    see the UAC1 implementation report's "fingerprint uniqueness layer"
    section for why this is not caught/retried, matching
    persist_discovery_fragment's own select-then-create convention)
    surfaces immediately.

    On an exact-fingerprint match, the EXISTING row is returned UNCHANGED
    - none of this call's raw_*/evidence_* arguments overwrite it, even
    if they differ in optional fields the first-seen row left blank. This
    mirrors persist_discovery_fragment's own "an existing row is reused
    UNCHANGED, never overwritten by a later... result" discipline for
    SourceAssertion. A future evidence-linking slice (UAC2) is
    responsible for deciding how additional observations against an
    already-existing candidate are recorded/traced; this function's own
    scope is exact-identity find-or-create only.
    """
    if not raw_name or not raw_name.strip():
        raise ValueError("raw_name is required")

    fingerprint = compute_candidate_fingerprint(raw_name, raw_country, raw_city, raw_state_region)

    existing = session.scalar(
        select(UnknownAirportCandidate).where(UnknownAirportCandidate.candidate_fingerprint == fingerprint)
    )
    if existing is not None:
        return UnknownAirportCandidateResult(candidate=existing, created=False)

    candidate = UnknownAirportCandidate(
        candidate_fingerprint=fingerprint,
        raw_name=raw_name.strip(),
        raw_city=raw_city,
        raw_state_region=raw_state_region,
        raw_country=raw_country,
        raw_iata_code=raw_iata_code,
        raw_icao_code=raw_icao_code,
        raw_faa_lid=raw_faa_lid,
        raw_runway_designation=raw_runway_designation,
        evidence_source_locator=evidence_source_locator,
        evidence_artifact_identity=evidence_artifact_identity,
    )
    session.add(candidate)
    session.flush()
    return UnknownAirportCandidateResult(candidate=candidate, created=True)


def record_unknown_airport_candidate_review(
    session: Session,
    candidate: UnknownAirportCandidate,
    *,
    action: str,
    reason: str,
    reviewer: str,
    matched_airport_id: Optional[int] = None,
    supersedes_review_id: Optional[int] = None,
) -> UnknownAirportCandidateReview:
    """Validates and appends exactly one UnknownAirportCandidateReview
    row. Never commits; calls session.flush() only so a constraint
    violation (including the immutability event listeners on
    UnknownAirportCandidateReview) surfaces immediately.

    Never mutates `candidate` - in particular, never sets
    candidate.resolved_airport_id, regardless of `action`.
    CREATE_NEW_AIRPORT records only that a human authorized a later,
    separate, not-yet-built governed Airport-creation operation to act -
    it is not that operation itself (module docstring). Never creates,
    updates, or queries any Runway, RunwayEnd, Installation, or Signal.
    """
    if action not in UNKNOWN_AIRPORT_CANDIDATE_REVIEW_ACTIONS:
        raise ValueError(
            f"action must be one of {UNKNOWN_AIRPORT_CANDIDATE_REVIEW_ACTIONS!r}, got {action!r}"
        )
    if not reason.strip():
        raise ValueError("reason is required for an unknown-airport candidate review")
    if not reviewer.strip():
        raise ValueError("reviewer is required for an unknown-airport candidate review")

    if candidate.id is None:
        raise ValueError("candidate must already be persisted (has no id)")
    if session.get(UnknownAirportCandidate, candidate.id) is None:
        raise ValueError("referenced UnknownAirportCandidate does not exist")

    if action == _MATCH_ACTION:
        if matched_airport_id is None:
            raise ValueError(f"{_MATCH_ACTION} requires matched_airport_id")
        if session.get(Airport, matched_airport_id) is None:
            raise ValueError("referenced Airport (matched_airport_id) does not exist")
    elif matched_airport_id is not None:
        raise ValueError(f"matched_airport_id is only valid when action == {_MATCH_ACTION!r}")

    if supersedes_review_id is not None:
        previous = session.get(UnknownAirportCandidateReview, supersedes_review_id)
        if previous is None or previous.candidate_id != candidate.id:
            raise ValueError("superseded review must exist and concern the same candidate")

    record = UnknownAirportCandidateReview(
        candidate_id=candidate.id,
        action=action,
        reason=reason.strip(),
        reviewer=reviewer.strip(),
        matched_airport_id=matched_airport_id,
        supersedes_review_id=supersedes_review_id,
    )
    session.add(record)
    session.flush()
    return record


def get_latest_unknown_airport_candidate_review(
    session: Session, candidate_id: int
) -> Optional[UnknownAirportCandidateReview]:
    """The most recently recorded UnknownAirportCandidateReview for a
    candidate, ordered by created_at then id (the same tiebreak
    discipline app.services.reviewer_action_persistence.get_latest_reviewer_action
    uses). "Latest" means "most recently recorded," not "the unsuperseded
    terminal node reached by walking supersedes_review_id" - with an
    append-only log, recency alone already identifies current state.
    Returns None if no review has ever been recorded for this candidate.

    This is a plain recency query, not a resolution decision: it never
    interprets the review's action, never touches
    candidate.resolved_airport_id, and is not the not-yet-built
    governed resolution service (design doc §8) that will eventually
    consume it.

    UAC-H1 no_autoflush hardening: wrapped in `session.no_autoflush`
    (the same pattern ERG2/ERG3/ERG4's own "latest" read helpers already
    use) - a purely read-only helper must never trigger a premature
    flush of some unrelated pending object the caller happens to be
    holding in the same session. Independently reproduced before this
    fix: a caller holding an unrelated, invalid, pending ORM object in
    the same session (e.g. a half-built row from an earlier step) would
    have that object's own constraint violation raised HERE, at this
    function's own read, instead of being left pending until the
    caller's actual intended flush/commit. SCOPE, stated precisely (do
    not over-claim): this guard protects only THIS function's own
    internal query - it does not and cannot protect a caller who reads
    an expired `candidate.id` attribute as the argument expression
    BEFORE calling this function (that read happens in the caller's own
    scope, not this function's); see
    app.services.unknown_airport_candidate_resolution._require_current_review()
    and scripts/review_unknown_airport_candidate.py's own
    `_read_candidate_state()` for the two call sites hardened against
    that separate, caller-side risk.
    """
    with session.no_autoflush:
        reviews = (
            session.query(UnknownAirportCandidateReview)
            .filter(UnknownAirportCandidateReview.candidate_id == candidate_id)
            .order_by(UnknownAirportCandidateReview.created_at.desc(), UnknownAirportCandidateReview.id.desc())
            .limit(1)
            .all()
        )
    return reviews[0] if reviews else None
