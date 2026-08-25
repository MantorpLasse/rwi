"""Tests for app/services/unknown_airport_candidate_admission_eligibility.py
(ERG4, docs/architecture/rwi-erg4-canonical-airport-admission-gate-report.md).

Pure, read-only evaluator tests only - the AUTHORITATIVE ENFORCEMENT tests
(does create_airport_from_approved_candidate() actually refuse; is a
direct-service bypass still blocked; does a blocked admission leave the
candidate/history intact) live in
tests/test_unknown_airport_candidate_resolution.py's own TestErg4Enforcement
class, alongside the rest of UAC4's test suite.

Every test uses an isolated in-memory SQLite database - never the real
data/runway_safe.db. Fixtures are entirely fictional.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Source, SourceAssertion
from app.models.unknown_airport_candidate import UnknownAirportCandidate
from app.models.unknown_airport_candidate_relevance_review import UnknownAirportCandidateRelevanceReview
from app.services.emas_relevance_evaluation import EmasEvidenceObservation, EvidenceClass
from app.services.unknown_airport_candidate_admission_eligibility import (
    AdmissionEligibilityReason,
    evaluate_unknown_airport_candidate_admission_eligibility,
)
from app.services.unknown_airport_candidate_persistence import find_or_create_unknown_airport_candidate
from app.services.unknown_airport_candidate_relevance_persistence import (
    persist_unknown_airport_candidate_relevance_assessment,
)
from app.services.unknown_airport_candidate_relevance_review_persistence import (
    record_unknown_airport_candidate_relevance_review,
)

A = EvidenceClass.A_EXPLICIT_EMAS  # opportunity-class -> watch-only by default
E = EvidenceClass.E_EXISTING_INSTALLATION  # confirmed-class -> inventory-only by default
G = EvidenceClass.G_GENERIC_RUNWAY_WORK  # never contributes -> automatic-false

CONFIRM = "CONFIRM_EMAS_RELEVANT"
MARK_NOT = "MARK_NOT_EMAS_RELEVANT"
DEFER = "DEFER_RELEVANCE_REVIEW"


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def _make_candidate(session, name="Foo Regional Airport"):
    result = find_or_create_unknown_airport_candidate(session, raw_name=name, raw_country="XX")
    session.commit()
    return result.candidate


def _make_source_assertion(session, candidate, identifier="sa-1"):
    source = Source(title="Test Source", source_type="official")
    session.add(source)
    session.commit()
    assertion = SourceAssertion(
        source_id=source.id, assertion_type="project_construction",
        source_record_identifier=identifier, unknown_airport_candidate_id=candidate.id,
    )
    session.add(assertion)
    session.commit()
    return assertion


def _persist_assessment(session, candidate, observations, assertion_identifier="sa"):
    assertion = _make_source_assertion(session, candidate, identifier=assertion_identifier) if observations else None
    result = persist_unknown_airport_candidate_relevance_assessment(
        session, candidate, observations=observations,
        source_assertion_ids=(assertion.id,) if assertion else (),
    )
    session.commit()
    return result.assessment


def _obs(cls, basis="x", **kwargs):
    return EmasEvidenceObservation(cls, basis=basis, **kwargs)


def _review(session, candidate, assessment, action, reviewer="human:x", reason="x"):
    review = record_unknown_airport_candidate_relevance_review(
        session, candidate, basis_assessment_id=assessment.id, action=action, reviewer=reviewer, reason=reason,
    )
    session.commit()
    return review


# ---------------------------------------------------------------------------
# S7/S9. No assessment at all
# ---------------------------------------------------------------------------


class TestNoAssessment:
    def test_no_assessment_ever_recorded_blocks(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate.id)
        assert result.eligible is False
        assert result.reason == AdmissionEligibilityReason.NO_RELEVANCE_ASSESSMENT
        assert result.latest_assessment_id is None
        assert result.is_automatic_admission_relevant is None

    def test_nonexistent_candidate_id_also_reports_no_assessment(self):
        """No candidate row at all is indistinguishable, at this pure
        read-only layer, from a real candidate with no assessment yet -
        both simply have zero rows in the assessments table for that id.
        Candidate-existence itself is UAC4's own, unmodified concern."""
        _engine, session = make_session()
        result = evaluate_unknown_airport_candidate_admission_eligibility(session, 999999)
        assert result.eligible is False
        assert result.reason == AdmissionEligibilityReason.NO_RELEVANCE_ASSESSMENT


# ---------------------------------------------------------------------------
# S8. Anoka core regression - automatic-false, all four human states BLOCK
# ---------------------------------------------------------------------------


class TestAnokaCoreRegression:
    """Anoka County-Blaine: RUNWAY_ONLY_NOT_EMAS_RELEVANT, inventory=false,
    watch=false. ALL FOUR human states must block - especially D: automatic
    false + human CONFIRM. Human confirmation cannot manufacture EMAS
    relevance."""

    def test_a_no_review_blocks(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(G),))
        assert assessment.is_inventory_relevant is False
        assert assessment.is_watch_worthy is False
        result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate.id)
        assert result.eligible is False
        assert result.reason == AdmissionEligibilityReason.AUTOMATIC_RELEVANCE_NOT_ADMISSION_ELIGIBLE

    def test_b_defer_blocks(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(G),))
        _review(session, candidate, assessment, DEFER)
        result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate.id)
        assert result.eligible is False
        assert result.reason == AdmissionEligibilityReason.AUTOMATIC_RELEVANCE_NOT_ADMISSION_ELIGIBLE

    def test_c_mark_not_blocks(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(G),))
        _review(session, candidate, assessment, MARK_NOT)
        result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate.id)
        assert result.eligible is False
        assert result.reason == AdmissionEligibilityReason.AUTOMATIC_RELEVANCE_NOT_ADMISSION_ELIGIBLE

    def test_d_confirm_still_blocks_the_central_product_rule(self):
        """THE central product rule: automatic false + human CONFIRM ==
        BLOCK, with the reason naming automatic relevance, not the human
        review, as the actual blocker."""
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(G),))
        _review(session, candidate, assessment, CONFIRM)
        result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate.id)
        assert result.eligible is False
        assert result.reason == AdmissionEligibilityReason.AUTOMATIC_RELEVANCE_NOT_ADMISSION_ELIGIBLE
        assert result.is_automatic_admission_relevant is False


# ---------------------------------------------------------------------------
# S9-S12. Automatic positive - every human review state
# ---------------------------------------------------------------------------


class TestAutomaticPositiveReviewStates:
    def test_no_review_blocks(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(A),))
        assert assessment.is_watch_worthy is True
        result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate.id)
        assert result.eligible is False
        assert result.reason == AdmissionEligibilityReason.NO_CURRENT_HUMAN_REVIEW

    def test_defer_blocks(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(A),))
        _review(session, candidate, assessment, DEFER)
        result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate.id)
        assert result.eligible is False
        assert result.reason == AdmissionEligibilityReason.HUMAN_REVIEW_DEFERRED

    def test_mark_not_blocks(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(A),))
        _review(session, candidate, assessment, MARK_NOT)
        result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate.id)
        assert result.eligible is False
        assert result.reason == AdmissionEligibilityReason.HUMAN_REVIEW_MARKED_NOT_RELEVANT

    def test_current_confirm_is_eligible(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(A),))
        review = _review(session, candidate, assessment, CONFIRM)
        result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate.id)
        assert result.eligible is True
        assert result.reason == AdmissionEligibilityReason.ELIGIBLE
        assert result.latest_assessment_id == assessment.id
        assert result.latest_review_id == review.id
        assert result.latest_review_action == CONFIRM


# ---------------------------------------------------------------------------
# S13. Stale CONFIRM
# ---------------------------------------------------------------------------


class TestStaleConfirm:
    def test_stale_confirm_blocks_then_fresh_confirm_eligible(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        a1 = _persist_assessment(session, candidate, (_obs(A),), "sa-1")
        _review(session, candidate, a1, CONFIRM)

        a2 = _persist_assessment(session, candidate, (_obs(A),), "sa-2")
        assert a2.is_watch_worthy is True

        result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate.id)
        assert result.eligible is False
        assert result.reason == AdmissionEligibilityReason.HUMAN_REVIEW_STALE
        assert result.latest_assessment_id == a2.id

        _review(session, candidate, a2, CONFIRM)
        result2 = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate.id)
        assert result2.eligible is True
        assert result2.reason == AdmissionEligibilityReason.ELIGIBLE


# ---------------------------------------------------------------------------
# S14. Positive -> negative rediscovery
# ---------------------------------------------------------------------------


class TestPositiveToNegativeRediscovery:
    def test_stale_before_new_review_and_blocked_even_with_new_confirm(self):
        """Mission's own locked rule ordering (Section 3): automatic
        relevance (step 2) is checked before review-currency (step 3) -
        so even in the "no new review yet" sub-case, where the stale old
        CONFIRM is technically ALSO independently disqualifying, the
        reported reason is AUTOMATIC_RELEVANCE_NOT_ADMISSION_ELIGIBLE,
        the same as the "human explicitly re-CONFIRMs against the new
        negative assessment" sub-case below - both correctly BLOCK, for
        the same reported reason, since is_automatic_admission_relevant
        is already false in both."""
        _engine, session = make_session()
        candidate = _make_candidate(session)
        a1 = _persist_assessment(session, candidate, (_obs(A),), "sa-1")
        _review(session, candidate, a1, CONFIRM)

        a2 = _persist_assessment(session, candidate, (_obs(G),), "sa-2")
        assert a2.is_inventory_relevant is False
        assert a2.is_watch_worthy is False

        stale_result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate.id)
        assert stale_result.eligible is False
        assert stale_result.reason == AdmissionEligibilityReason.AUTOMATIC_RELEVANCE_NOT_ADMISSION_ELIGIBLE

        _review(session, candidate, a2, CONFIRM)
        confirmed_result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate.id)
        assert confirmed_result.eligible is False
        assert confirmed_result.reason == AdmissionEligibilityReason.AUTOMATIC_RELEVANCE_NOT_ADMISSION_ELIGIBLE


# ---------------------------------------------------------------------------
# S15. Negative -> positive rediscovery
# ---------------------------------------------------------------------------


class TestNegativeToPositiveRediscovery:
    def test_old_mark_not_stale_then_blocked_then_eligible_after_new_confirm(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        a1 = _persist_assessment(session, candidate, (_obs(G),), "sa-1")
        _review(session, candidate, a1, MARK_NOT)

        a2 = _persist_assessment(session, candidate, (_obs(A),), "sa-2")
        assert a2.is_watch_worthy is True

        stale_result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate.id)
        assert stale_result.eligible is False
        assert stale_result.reason == AdmissionEligibilityReason.HUMAN_REVIEW_STALE

        _review(session, candidate, a2, CONFIRM)
        eligible_result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate.id)
        assert eligible_result.eligible is True
        assert eligible_result.reason == AdmissionEligibilityReason.ELIGIBLE

        # History preserved: the old MARK_NOT review row still exists,
        # untouched (append-only), even though it no longer governs.
        assert session.query(UnknownAirportCandidateRelevanceReview).count() == 2


# ---------------------------------------------------------------------------
# S16/S17/S18. Inventory-only / watch-only / both-true
# ---------------------------------------------------------------------------


class TestInventoryWatchBothCases:
    def test_inventory_only_dormant_installation_eligible(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(E),))
        assert assessment.is_inventory_relevant is True
        assert assessment.is_watch_worthy is False
        _review(session, candidate, assessment, CONFIRM)
        result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate.id)
        assert result.eligible is True
        assert result.reason == AdmissionEligibilityReason.ELIGIBLE

    def test_watch_only_current_future_opportunity_eligible(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(A),))
        assert assessment.is_inventory_relevant is False
        assert assessment.is_watch_worthy is True
        _review(session, candidate, assessment, CONFIRM)
        result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate.id)
        assert result.eligible is True
        assert result.reason == AdmissionEligibilityReason.ELIGIBLE

    def test_both_true_no_special_privilege_still_just_eligible(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(E), _obs(A, basis="y")))
        assert assessment.is_inventory_relevant is True
        assert assessment.is_watch_worthy is True
        _review(session, candidate, assessment, CONFIRM)
        result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate.id)
        assert result.eligible is True
        assert result.reason == AdmissionEligibilityReason.ELIGIBLE


# ---------------------------------------------------------------------------
# S21. Current-assessment tie (reuse ERG2 semantics, no duplicated sort)
# ---------------------------------------------------------------------------


class TestCurrentAssessmentTie:
    def test_same_timestamp_higher_id_wins(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assertion1 = _make_source_assertion(session, candidate, "sa-1")
        assertion2 = _make_source_assertion(session, candidate, "sa-2")
        from app.models.unknown_airport_candidate_relevance_assessment import UnknownAirportCandidateRelevanceAssessment

        tied_at = candidate.created_at
        older = UnknownAirportCandidateRelevanceAssessment(
            candidate_id=candidate.id, outcome="RUNWAY_ONLY_NOT_EMAS_RELEVANT", reason="x",
            evidence_classes_matched_json="[]", contradicting_evidence_classes_json="[]",
            is_inventory_relevant=False, is_watch_worthy=False, evaluator_version="1", created_at=tied_at,
        )
        session.add(older)
        session.flush()
        newer = UnknownAirportCandidateRelevanceAssessment(
            candidate_id=candidate.id, outcome="EMAS_STRONG_SIGNAL", reason="x",
            evidence_classes_matched_json="[]", contradicting_evidence_classes_json="[]",
            is_inventory_relevant=False, is_watch_worthy=True, evaluator_version="1", created_at=tied_at,
        )
        session.add(newer)
        session.flush()
        session.commit()
        assert older.created_at == newer.created_at
        assert newer.id > older.id

        _review(session, candidate, newer, CONFIRM)
        result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate.id)
        assert result.latest_assessment_id == newer.id
        assert result.eligible is True


# ---------------------------------------------------------------------------
# S22. Current human review selection - same-basis multi-review
# ---------------------------------------------------------------------------


class TestCurrentReviewSameBasis:
    def test_defer_then_confirm_latest_governs(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(A),))
        _review(session, candidate, assessment, DEFER)
        _review(session, candidate, assessment, CONFIRM)
        result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate.id)
        assert result.eligible is True
        assert result.latest_review_action == CONFIRM

    def test_confirm_then_mark_not_latest_governs(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(A),))
        _review(session, candidate, assessment, CONFIRM)
        _review(session, candidate, assessment, MARK_NOT)
        result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate.id)
        assert result.eligible is False
        assert result.reason == AdmissionEligibilityReason.HUMAN_REVIEW_MARKED_NOT_RELEVANT
        assert result.latest_review_action == MARK_NOT


# ---------------------------------------------------------------------------
# S23. Cross-candidate attack
# ---------------------------------------------------------------------------


class TestCrossCandidateAttack:
    def test_candidate_a_positive_assessment_candidate_b_confirm_never_combine(self):
        _engine, session = make_session()
        candidate_a = _make_candidate(session, "Airport A")
        candidate_b = _make_candidate(session, "Airport B")
        assessment_a = _persist_assessment(session, candidate_a, (_obs(A),), "sa-a")
        assessment_b = _persist_assessment(session, candidate_b, (_obs(G),), "sa-b")
        _review(session, candidate_b, assessment_b, CONFIRM)

        result_a = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate_a.id)
        assert result_a.eligible is False
        assert result_a.reason == AdmissionEligibilityReason.NO_CURRENT_HUMAN_REVIEW

        result_b = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate_b.id)
        assert result_b.eligible is False
        assert result_b.reason == AdmissionEligibilityReason.AUTOMATIC_RELEVANCE_NOT_ADMISSION_ELIGIBLE

    def test_direct_orm_cross_candidate_review_still_resolves_stale_never_eligible(self):
        """Attack: bypass record_unknown_airport_candidate_relevance_review()'s
        own service-level same-candidate check via direct ORM construction
        (ERG3's own already-documented, honest "service-level only"
        boundary), setting candidate_id=A but basis_assessment_id=B's own
        current assessment id. Deterministically safe regardless: ids are
        globally unique across the whole assessments table, so a foreign
        assessment id can never equal A's own current assessment id - the
        resolver's STALE branch catches this every time, not by luck."""
        _engine, session = make_session()
        candidate_a = _make_candidate(session, "Airport A")
        candidate_b = _make_candidate(session, "Airport B")
        assessment_a = _persist_assessment(session, candidate_a, (_obs(A),), "sa-a")
        assessment_b = _persist_assessment(session, candidate_b, (_obs(A),), "sa-b")
        assert assessment_a.id != assessment_b.id

        malformed_review = UnknownAirportCandidateRelevanceReview(
            candidate_id=candidate_a.id, basis_assessment_id=assessment_b.id,
            action=CONFIRM, reviewer="human:attacker", reason="cross-candidate attempt",
        )
        session.add(malformed_review)
        session.flush()
        session.commit()

        result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate_a.id)
        assert result.eligible is False
        assert result.reason == AdmissionEligibilityReason.HUMAN_REVIEW_STALE


# ---------------------------------------------------------------------------
# S24. Malformed / impossible state
# ---------------------------------------------------------------------------


class TestMalformedState:
    def test_unrecognized_action_in_memory_only_never_eligible(self):
        """Attack: a review's action is mutated to an unrecognized string
        directly on the in-memory ORM object, WITHOUT flushing or
        committing (a real commit would hit the model's own before_update
        immutability guard and raise) - simulating a state the resolver
        must still handle defensively even though it cannot be persisted
        through any governed path. The identity map makes this mutation
        visible to a same-session read without ever touching the DB."""
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(A),))
        review = _review(session, candidate, assessment, CONFIRM)
        candidate_id = candidate.id  # captured before the dirty/malformed mutation below

        review.action = "SOME_UNRECOGNIZED_ACTION"

        result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate_id)
        assert result.eligible is False
        assert result.reason == AdmissionEligibilityReason.NO_CURRENT_HUMAN_REVIEW

        session.expire(review)  # never actually persist the malformed value


# ---------------------------------------------------------------------------
# S25. no_autoflush
# ---------------------------------------------------------------------------


class TestNoAutoflush:
    def test_unrelated_invalid_pending_candidate_object(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(A),))
        _review(session, candidate, assessment, CONFIRM)
        candidate_id = candidate.id  # captured before unrelated pending state, realistic caller pattern
        bad = UnknownAirportCandidate(candidate_fingerprint="deadbeef")
        session.add(bad)
        result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate_id)
        assert result.eligible is True
        assert bad in session.new

    def test_unrelated_invalid_pending_review_object(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(A),))
        _review(session, candidate, assessment, CONFIRM)
        bad_review = UnknownAirportCandidateRelevanceReview(
            candidate_id=candidate.id, basis_assessment_id=assessment.id,
            action="BOGUS_ACTION", reviewer="human:x", reason="x",
        )
        session.add(bad_review)
        result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate.id)
        assert result.eligible is True
        assert bad_review in session.new

    def test_expired_candidate_attributes(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)  # _make_candidate commits, expiring attributes
        assessment = _persist_assessment(session, candidate, (_obs(A),))
        _review(session, candidate, assessment, CONFIRM)
        candidate_id = candidate.id  # first read after commit, on an expired instance
        result = evaluate_unknown_airport_candidate_admission_eligibility(session, candidate_id)
        assert result.eligible is True
