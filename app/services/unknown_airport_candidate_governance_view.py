"""ERG5 — read-only governance view for UnknownAirportCandidate
(docs/architecture/rwi-erg5-operator-governance-flow-report.md).

    UnknownAirportCandidateRelevanceAssessment (ERG2, current/latest)
        + UnknownAirportCandidateRelevanceAssessmentEvidenceLink (ERG2,
          structural membership only)
        + EffectiveRelevanceReviewState (ERG3, current/latest)
        + UnknownAirportCandidateRelevanceReview (ERG3, latest recorded)
        + AdmissionEligibilityResult (ERG4)
        -> get_unknown_airport_candidate_governance_view()
        -> UnknownAirportCandidateGovernanceView (plain, frozen, no ORM
           objects leaked)
        -> STOP (this module never creates or mutates anything; the UAC5
           CLI is the only consumer, and only ever DISPLAYS this view -
           see scripts/review_unknown_airport_candidate.py)

Composes ERG2's latest-assessment helper, ERG3's effective-review-state
resolver and latest-review helper, and ERG4's admission eligibility
evaluator into ONE queryable, plain-data view - so an operator-facing
caller never needs to recompute or duplicate any of the "latest,"
"current," or "eligible" business rules those three modules already own.

NO BUSINESS-RULE DUPLICATION: this module never re-derives
is_inventory_relevant/is_watch_worthy/is_canonical_admission_relevant,
never re-implements the STALE/CURRENT/UNREVIEWED/NO_ASSESSMENT_YET state
machine, and never re-implements the ERG4 admission rule (inventory OR
watch, AND current CONFIRM). Every derived fact in this module's own
output is read DIRECTLY off an already-governed helper's own result. The
only "new" work here is (a) reading a few additional raw columns those
helpers' own narrower result contracts don't expose (the full assessment
row's outcome/evidence classes/evaluator_version; the latest review's own
reviewer/reason/created_at) and (b) one purely structural evidence-
membership read (the ERG2 evidence-link table) that involves no
relevance judgment at all - a plain list of which SourceAssertion ids
contributed to the current assessment.

INFORMATION FIREWALL: read-only. No `session.add`/`session.flush`/
`session.commit` anywhere in this module. Never imports Airport's write
path, Signal, Installation, app.services.unknown_airport_discovery_integration
(UAC3), identity guard, or any promotion/publish code. Never mutates
UnknownAirportCandidate and never touches identity review
(UnknownAirportCandidateReview) at all - that remains UAC1/UAC4's own,
entirely separate concern; a caller that also needs identity-review state
reads it directly, exactly as it already did before this module existed.

"ELIGIBLE" NEVER IMPLIES SIGNAL-CREATABLE OR ALREADY-ADMITTED: this
module reports exactly what ERG4's own evaluator reports - a relevance-
gate verdict, not "has this candidate been made a canonical Airport" (see
`resolved_airport_id` on UnknownAirportCandidate for that, read
separately by the caller) and not "can this evidence reach Signal
creation" (a structurally separate, already-known, still-open UAC5B
question this module says nothing about).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models.unknown_airport_candidate_relevance_assessment import (
    UnknownAirportCandidateRelevanceAssessmentEvidenceLink,
)
from app.services.unknown_airport_candidate_admission_eligibility import (
    evaluate_unknown_airport_candidate_admission_eligibility,
)
from app.services.unknown_airport_candidate_relevance_persistence import (
    deserialize_evidence_classes,
    get_latest_unknown_airport_candidate_relevance_assessment,
)
from app.services.unknown_airport_candidate_relevance_review_persistence import (
    get_latest_unknown_airport_candidate_relevance_review,
    resolve_effective_unknown_airport_candidate_relevance_review_state,
)

__all__ = [
    "AutomaticRelevanceView",
    "HumanRelevanceReviewView",
    "CanonicalAdmissionView",
    "UnknownAirportCandidateGovernanceView",
    "get_unknown_airport_candidate_governance_view",
]


@dataclass(frozen=True)
class AutomaticRelevanceView:
    """The current/latest ERG2 assessment, in full - never re-evaluated,
    read directly off the persisted row via ERG2's own unmodified "latest"
    helper. `evidence_classes_matched`/`contradicting_evidence_classes`
    are sorted tuples of EvidenceClass string values (ERG2's own
    deserialize_evidence_classes(), never hand-parsed here)."""

    assessment_id: int
    outcome: str
    reason: str
    evidence_classes_matched: "tuple[str, ...]"
    contradicting_evidence_classes: "tuple[str, ...]"
    is_inventory_relevant: bool
    is_watch_worthy: bool
    is_canonical_admission_relevant: bool
    evaluator_version: str
    created_at: str
    linked_source_assertion_ids: "tuple[int, ...]"


@dataclass(frozen=True)
class HumanRelevanceReviewView:
    """The LATEST recorded relevance review (chronologically), plus
    ERG3's own CURRENT/STALE/UNREVIEWED/NO_ASSESSMENT_YET verdict for it
    - deliberately both, never collapsed into one field: a STALE review
    still shows its own real action/reviewer/reason (mission's own
    "historical CONFIRM exists but is not current authority" requirement)
    while `is_current` tells the reader whether it still speaks for the
    CURRENT automatic assessment. All fields None only when `state` is
    NO_ASSESSMENT_YET or UNREVIEWED (no review has ever been recorded)."""

    state: str
    is_current: bool
    latest_review_id: "Optional[int]" = None
    basis_assessment_id: "Optional[int]" = None
    action: "Optional[str]" = None
    reviewer: "Optional[str]" = None
    reason: "Optional[str]" = None
    created_at: "Optional[str]" = None


@dataclass(frozen=True)
class CanonicalAdmissionView:
    """Exactly ERG4's own verdict, unmodified. `eligible=True` means the
    ERG4 relevance gate allows canonical-admission CONSIDERATION - it
    does NOT mean an Airport has already been created (see the
    candidate's own `resolved_airport_id`, read separately) and does NOT
    mean the pre-existing UAC4 identity-review gate has also passed (a
    structurally separate precondition - see this module's own docstring
    and the ERG5 report's "identity gate vs relevance gate" section)."""

    eligible: bool
    reason: str


@dataclass(frozen=True)
class UnknownAirportCandidateGovernanceView:
    candidate_id: int
    automatic_relevance: "Optional[AutomaticRelevanceView]"
    human_relevance_review: HumanRelevanceReviewView
    canonical_admission: CanonicalAdmissionView


def get_unknown_airport_candidate_governance_view(
    session: Session, candidate_id: int
) -> UnknownAirportCandidateGovernanceView:
    """Pure, read-only. Never raises for a candidate with no assessment/
    review history - every section degrades to its own "nothing yet"
    shape (`automatic_relevance=None`, `human_relevance_review.state=
    "NO_ASSESSMENT_YET"`, `canonical_admission.eligible=False`). Does not
    itself verify the candidate exists - callers (the UAC5 CLI) already
    do that before reaching this point, exactly like every other helper
    in this pipeline.

    Wrapped in `session.no_autoflush` (ERG2/ERG3/ERG4/UAC-H1's own
    established lesson, applied from the start): a purely read-only view
    must never trigger a premature flush of some unrelated pending object
    the caller happens to be holding in the same session.
    """
    with session.no_autoflush:
        # ERG4's evaluator is called FIRST (not last) specifically so its
        # own already-computed is_automatic_admission_relevant can be
        # reused verbatim below - never re-derived a second time from
        # is_inventory_relevant/is_watch_worthy. Single source of truth
        # for "inventory OR watch," not a second, merely-consistent copy.
        admission = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate_id)
        canonical_admission = CanonicalAdmissionView(eligible=admission.eligible, reason=admission.reason.value)

        assessment = get_latest_unknown_airport_candidate_relevance_assessment(session, candidate_id)
        automatic_relevance: "Optional[AutomaticRelevanceView]" = None
        if assessment is not None:
            links = (
                session.query(UnknownAirportCandidateRelevanceAssessmentEvidenceLink)
                .filter(UnknownAirportCandidateRelevanceAssessmentEvidenceLink.assessment_id == assessment.id)
                .order_by(UnknownAirportCandidateRelevanceAssessmentEvidenceLink.source_assertion_id.asc())
                .all()
            )
            automatic_relevance = AutomaticRelevanceView(
                assessment_id=assessment.id,
                outcome=assessment.outcome,
                reason=assessment.reason,
                evidence_classes_matched=tuple(sorted(
                    c.value for c in deserialize_evidence_classes(assessment.evidence_classes_matched_json)
                )),
                contradicting_evidence_classes=tuple(sorted(
                    c.value for c in deserialize_evidence_classes(assessment.contradicting_evidence_classes_json)
                )),
                is_inventory_relevant=assessment.is_inventory_relevant,
                is_watch_worthy=assessment.is_watch_worthy,
                is_canonical_admission_relevant=bool(admission.is_automatic_admission_relevant),
                evaluator_version=assessment.evaluator_version,
                created_at=assessment.created_at.isoformat(),
                linked_source_assertion_ids=tuple(link.source_assertion_id for link in links),
            )

        review_state = resolve_effective_unknown_airport_candidate_relevance_review_state(session, candidate_id)
        latest_review = get_latest_unknown_airport_candidate_relevance_review(session, candidate_id)
        human_relevance_review = HumanRelevanceReviewView(
            state=review_state.state.value,
            is_current=review_state.is_current,
            latest_review_id=latest_review.id if latest_review is not None else None,
            basis_assessment_id=latest_review.basis_assessment_id if latest_review is not None else None,
            action=latest_review.action if latest_review is not None else None,
            reviewer=latest_review.reviewer if latest_review is not None else None,
            reason=latest_review.reason if latest_review is not None else None,
            created_at=latest_review.created_at.isoformat() if latest_review is not None else None,
        )

    return UnknownAirportCandidateGovernanceView(
        candidate_id=candidate_id,
        automatic_relevance=automatic_relevance,
        human_relevance_review=human_relevance_review,
        canonical_admission=canonical_admission,
    )
