"""ERG2 persistence bridge for app.services.emas_relevance_evaluation
(docs/architecture/rwi-erg2-relevance-assessment-persistence-report.md).

    tuple[EmasEvidenceObservation, ...] (caller-supplied, already-classified
        evidence facts - exactly ERG1's own input contract, unmodified)
        + UnknownAirportCandidate (already persisted, UAC1, unmodified)
        + source_assertion_ids (caller-supplied evidence-linkage set)
        -> persist_unknown_airport_candidate_relevance_assessment()
        -> evaluate_emas_relevance() called internally (never a
           caller-supplied result - see AUTHORITATIVE PATH below)
        -> one immutable UnknownAirportCandidateRelevanceAssessment row +
           its UnknownAirportCandidateRelevanceAssessmentEvidenceLink
           child rows appended
        -> STOP (no UAC4 gate, no CLI integration, no canonical Airport
           creation, no Signal - all future, separately-authorized slices)

Composes the already-committed, unmodified ERG1 core
(app.services.emas_relevance_evaluation.evaluate_emas_relevance()) exactly
the way app.services.promotion_policy_persistence composes
app.services.promotion_policy_evaluation.evaluate_promotion_policy() - this
module contains no relevance-classification logic of its own and never
reinterprets, re-derives, or overrides anything evaluate_emas_relevance()
returns. Persistence is a RECORDER, not a second evaluator (this mission's
own explicit instruction): `contradicting_evidence_classes` is persisted
exactly as the evaluator returns it, `is_inventory_relevant`/
`is_watch_worthy` are persisted exactly as computed, and
`is_canonical_admission_relevant` is never persisted at all (always
re-derived as `is_inventory_relevant OR is_watch_worthy` - ERG1.6's own
locked rule; see docs/architecture/rwi-erg1-5-inventory-vs-opportunity-design.md
S12/S18 for why this specific field must never become redundant persisted
truth).

AUTHORITATIVE PATH, fabrication-proof by construction (mission's own
explicit instruction: "ensure caller cannot fabricate evaluator_version or
inconsistent booleans... prefer one authoritative path"): this module
exposes exactly ONE way to produce a persisted assessment -
`persist_unknown_airport_candidate_relevance_assessment()` ALWAYS calls
`evaluate_emas_relevance()` itself, from the caller-supplied `observations`
tuple. There is no second code path that accepts a pre-computed
`EmasRelevanceDecision`, `outcome` string, boolean pair, or
`evaluator_version` string as a direct write-time argument - the ONLY thing
a caller can influence is which evidence goes IN, never what comes out.
`evaluator_version` is read directly from
`app.services.emas_relevance_evaluation.EVALUATOR_VERSION` at the moment of
the real evaluate_emas_relevance() call, never accepted as a parameter -
so a persisted row's `evaluator_version` can never disagree with the
evaluator that actually produced it, and `is_inventory_relevant`/
`is_watch_worthy`/`outcome` can never be internally inconsistent with each
other, since all three always originate from the exact same
EmasRelevanceDecision object.

EVIDENCE TRACEABILITY / NO FREE-FLOATING ASSESSMENT (mission's own explicit
instruction): if `observations` is non-empty (there IS evidence to
evaluate), `source_assertion_ids` MUST also be non-empty - an assessment
with real evidentiary content but no linked SourceAssertion(s) would be
exactly the "free-floating relevance approval" this slice's own design
review forbids. An assessment produced from a genuinely EMPTY observation
tuple (INSUFFICIENT_EVIDENCE with no evidence at all - a legitimate
rehearsal/placeholder shape) may also have zero linked SourceAssertions,
since there is nothing to link. Every supplied source_assertion_id must
already exist AND already be linked to THIS candidate
(SourceAssertion.unknown_airport_candidate_id == candidate.id) - a caller
cannot link an assessment to an unrelated SourceAssertion belonging to a
different candidate or to an already-canonical Airport. This check is
SERVICE-LEVEL ONLY (adversarial-review finding, considered and deliberately
not schema-enforced - see
app.models.unknown_airport_candidate_relevance_assessment.
UnknownAirportCandidateRelevanceAssessmentEvidenceLink's own docstring,
CROSS-CANDIDATE INTEGRITY section, for the full derivation of why a
composite-FK schema-level version was prototyped, proven to break the
migration against the real, already-existing production `source_assertions`
table, and reverted): a raw-SQL insert that bypassed this governed function
entirely could still create a mismatched link. This is a documented,
accepted boundary for this slice, not an oversight.

Never commits and never imports app.database.SessionLocal - mutates the
caller-supplied Session and flushes only so a constraint violation
(including the immutability event listeners on both new tables) surfaces
immediately; the caller owns the transaction boundary entirely, matching
every other persistence service in this pipeline (promotion_policy_persistence,
intelligence_review_persistence, reviewer_action_persistence,
unknown_airport_candidate_persistence).

INFORMATION FIREWALL: this module never imports Airport's write path,
Signal, app.services.unknown_airport_candidate_resolution (UAC4),
app.services.unknown_airport_discovery_integration (UAC3), or any
promotion/publish code - it creates exactly two kinds of row (one
assessment, its evidence links) and nothing else. It never mutates
UnknownAirportCandidate itself (never touches resolved_airport_id) and
never reads or writes UnknownAirportCandidateReview - identity review
history and relevance-assessment history remain two entirely separate,
non-interacting append-only logs, matching the design doc's own repeated
"identity truth vs. business relevance, never blurred" principle.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models import SourceAssertion
from app.models.unknown_airport_candidate import UnknownAirportCandidate
from app.models.unknown_airport_candidate_relevance_assessment import (
    RELEVANCE_ASSESSMENT_OUTCOMES,
    UnknownAirportCandidateRelevanceAssessment,
    UnknownAirportCandidateRelevanceAssessmentEvidenceLink,
)
from app.services.emas_relevance_evaluation import (
    EVALUATOR_VERSION,
    EmasEvidenceObservation,
    EmasRelevanceContext,
    EmasRelevanceDecision,
    EvidenceClass,
    evaluate_emas_relevance,
)

__all__ = [
    "UnknownAirportCandidateRelevanceAssessmentResult",
    "persist_unknown_airport_candidate_relevance_assessment",
    "get_latest_unknown_airport_candidate_relevance_assessment",
    "deserialize_evidence_classes",
]


def _serialize_evidence_classes(classes: "frozenset[EvidenceClass]") -> str:
    """Deterministic, sorted JSON array of EvidenceClass string values -
    see the model module's own docstring for why JSON was chosen over a
    comma-joined string."""
    return json.dumps(sorted(c.value for c in classes))


def deserialize_evidence_classes(serialized: str) -> "frozenset[EvidenceClass]":
    """The inverse of `_serialize_evidence_classes()` - the only supported
    way to read `evidence_classes_matched_json`/
    `contradicting_evidence_classes_json` back into typed EvidenceClass
    members. Never hand-parsed elsewhere."""
    return frozenset(EvidenceClass(value) for value in json.loads(serialized))


@dataclass(frozen=True)
class UnknownAirportCandidateRelevanceAssessmentResult:
    """Deterministic, ORM-adjacent summary of what
    persist_unknown_airport_candidate_relevance_assessment() did.
    `decision` is the exact, freshly-computed EmasRelevanceDecision the
    persisted row was derived from (never itself persisted, but the exact
    origin of every value that was) - exposed so a caller/test can inspect
    the full reasoning chain without a second read. `assessment` is the
    persisted, already-flushed row (has a real `id`). `linked_source_assertion_ids`
    is the exact, sorted tuple of ids this call linked."""

    assessment: UnknownAirportCandidateRelevanceAssessment
    decision: EmasRelevanceDecision
    linked_source_assertion_ids: "tuple[int, ...]" = ()

    @property
    def outcome(self):
        return self.decision.outcome

    @property
    def is_canonical_admission_relevant(self) -> bool:
        """Always derived, never persisted - ERG1.6's own locked rule,
        re-derived identically here for caller convenience."""
        return self.decision.is_inventory_relevant or self.decision.is_watch_worthy


def persist_unknown_airport_candidate_relevance_assessment(
    session: Session,
    candidate: UnknownAirportCandidate,
    *,
    observations: "tuple[EmasEvidenceObservation, ...]",
    source_assertion_ids: "tuple[int, ...]" = (),
    context: EmasRelevanceContext = EmasRelevanceContext(),
) -> UnknownAirportCandidateRelevanceAssessmentResult:
    """Evaluates `observations` via the real, unmodified
    evaluate_emas_relevance() and appends exactly one immutable
    UnknownAirportCandidateRelevanceAssessment row plus one
    UnknownAirportCandidateRelevanceAssessmentEvidenceLink row per unique,
    validated `source_assertion_ids` entry. Never commits; calls
    session.flush() after each insert so a constraint violation surfaces
    immediately. See module docstring for the full fabrication-proof/
    traceability rationale.
    """
    # no_autoflush (adversarial-review finding - "this bug class has
    # appeared elsewhere in RWI"): wraps EVERY read-only precondition check
    # below, starting with the very first one - including plain attribute
    # access on `candidate` itself. SQLAlchemy expires an object's
    # attributes by default after the caller's own earlier commit; merely reading
    # `candidate.id` on an expired instance triggers an internal refresh
    # SELECT, which - exactly like session.get()/session.query() - runs
    # through SQLAlchemy's default autoflush path and would otherwise flush
    # ANY OTHER unrelated pending object the caller happens to be holding
    # in the SAME session (e.g. a half-built object from an unrelated
    # earlier step), raising on ITS constraint violation here, at
    # precondition-check time, instead of leaving it pending until the
    # caller's own intended commit. Reproduced directly before this fix -
    # first with the bare `candidate.id` read on an expired instance, a
    # narrower fix that only wrapped the session.get() calls further down
    # was NOT sufficient on its own. The write section below
    # (session.add()+flush()) is deliberately OUTSIDE this block - that
    # flush is this function's own intentional write point.
    #
    # SCOPE, stated precisely (do not over-claim): this guard protects only
    # the PRECONDITION-CHECK phase - if a check fails, the caller sees this
    # function's own ValueError, never a leaked autoflush error from
    # unrelated state. It does NOT and cannot protect the INTENTIONAL write
    # below once preconditions pass: session.flush() there flushes the
    # WHOLE session's pending state, by design, exactly like every other
    # persistence function in this codebase (record_reviewer_action(),
    # record_unknown_airport_candidate_review(), persist_promotion_policy(),
    # ...) - a caller is always responsible for ensuring the rest of their
    # session is valid (or in a separate session entirely) before any
    # function that performs a real write is called, not just this one.
    with session.no_autoflush:
        if candidate.id is None:
            raise ValueError("candidate must already be persisted (has no id)")

        unique_source_assertion_ids = tuple(sorted(set(source_assertion_ids)))

        if observations and not unique_source_assertion_ids:
            raise ValueError(
                "source_assertion_ids is required when observations is non-empty - an assessment with "
                "real evidentiary content must be traceable to the SourceAssertion(s) it came from, never "
                "a free-floating relevance judgment."
            )

        if session.get(UnknownAirportCandidate, candidate.id) is None:
            raise ValueError("referenced UnknownAirportCandidate does not exist")

        for source_assertion_id in unique_source_assertion_ids:
            source_assertion = session.get(SourceAssertion, source_assertion_id)
            if source_assertion is None:
                raise ValueError(f"referenced SourceAssertion (id={source_assertion_id}) does not exist")
            if source_assertion.unknown_airport_candidate_id != candidate.id:
                raise ValueError(
                    f"SourceAssertion (id={source_assertion_id}) is not linked to candidate "
                    f"(id={candidate.id}) - cannot attach an assessment to evidence belonging to a "
                    "different candidate."
                )

    decision = evaluate_emas_relevance(observations, context)
    if decision.outcome.value not in RELEVANCE_ASSESSMENT_OUTCOMES:  # pragma: no cover - defense in depth
        raise ValueError(f"unexpected evaluator outcome {decision.outcome.value!r}")

    assessment = UnknownAirportCandidateRelevanceAssessment(
        candidate_id=candidate.id,
        outcome=decision.outcome.value,
        reason=decision.reason,
        evidence_classes_matched_json=_serialize_evidence_classes(decision.evidence_classes_matched),
        contradicting_evidence_classes_json=_serialize_evidence_classes(decision.contradicting_evidence_classes),
        is_inventory_relevant=decision.is_inventory_relevant,
        is_watch_worthy=decision.is_watch_worthy,
        evaluator_version=EVALUATOR_VERSION,
    )
    session.add(assessment)
    session.flush()

    for source_assertion_id in unique_source_assertion_ids:
        session.add(
            UnknownAirportCandidateRelevanceAssessmentEvidenceLink(
                assessment_id=assessment.id, source_assertion_id=source_assertion_id,
            )
        )
    session.flush()

    return UnknownAirportCandidateRelevanceAssessmentResult(
        assessment=assessment, decision=decision, linked_source_assertion_ids=unique_source_assertion_ids,
    )


def get_latest_unknown_airport_candidate_relevance_assessment(
    session: Session, candidate_id: int
) -> Optional[UnknownAirportCandidateRelevanceAssessment]:
    """The most recently recorded UnknownAirportCandidateRelevanceAssessment
    for a candidate, ordered by created_at then id - the same tiebreak
    discipline get_latest_unknown_airport_candidate_review()/
    get_latest_reviewer_action() already use. "Latest" means "most recently
    recorded"; with an append-only log, recency alone already identifies
    current state. Returns None if no assessment has ever been recorded for
    this candidate. Pure read - never mutates, never re-evaluates. Wrapped
    in session.no_autoflush (adversarial-review finding, same rationale as
    persist_unknown_airport_candidate_relevance_assessment()'s own
    precondition checks) - a purely read-only helper must never trigger a
    premature flush of some unrelated pending object the caller happens to
    be holding in the same session."""
    with session.no_autoflush:
        rows = (
            session.query(UnknownAirportCandidateRelevanceAssessment)
            .filter(UnknownAirportCandidateRelevanceAssessment.candidate_id == candidate_id)
            .order_by(
                UnknownAirportCandidateRelevanceAssessment.created_at.desc(),
                UnknownAirportCandidateRelevanceAssessment.id.desc(),
            )
            .limit(1)
            .all()
        )
    return rows[0] if rows else None
