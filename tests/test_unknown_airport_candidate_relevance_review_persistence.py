"""Tests for app/services/unknown_airport_candidate_relevance_review_persistence.py
and app/models/unknown_airport_candidate_relevance_review.py (ERG3,
docs/architecture/rwi-erg3-human-relevance-review-recording-report.md).

Every test uses an isolated in-memory SQLite database - never the real
data/runway_safe.db. Fixtures are entirely fictional. Modeled on the
already-proven pattern in
tests/test_unknown_airport_candidate_relevance_persistence.py (ERG2).

Attack matrix (mission's own S30, A-T) is covered by the test class names
below.
"""
from __future__ import annotations

import ast
import inspect as inspect_module

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Source, SourceAssertion
from app.models.unknown_airport_candidate import UnknownAirportCandidate
from app.models.unknown_airport_candidate_relevance_assessment import UnknownAirportCandidateRelevanceAssessment
from app.models.unknown_airport_candidate_relevance_review import (
    RELEVANCE_REVIEW_ACTIONS,
    UnknownAirportCandidateRelevanceReview,
)
from app.services.emas_relevance_evaluation import EmasEvidenceObservation, EvidenceClass
from app.services.evidence_claim_semantics import TemporalQualifier
from app.services.unknown_airport_candidate_persistence import find_or_create_unknown_airport_candidate
from app.services.unknown_airport_candidate_relevance_persistence import (
    persist_unknown_airport_candidate_relevance_assessment,
)
from app.services import unknown_airport_candidate_relevance_review_persistence as erg3_persistence
from app.services.unknown_airport_candidate_relevance_review_persistence import (
    EffectiveRelevanceReviewState,
    RelevanceReviewState,
    get_latest_unknown_airport_candidate_relevance_review,
    record_unknown_airport_candidate_relevance_review,
    resolve_effective_unknown_airport_candidate_relevance_review_state,
)

A = EvidenceClass.A_EXPLICIT_EMAS
B = EvidenceClass.B_RUNWAY_SAFETY_AREA_OR_ARRESTOR_NEED
D = EvidenceClass.D_FUNDING_OR_PROCUREMENT
E = EvidenceClass.E_EXISTING_INSTALLATION
G = EvidenceClass.G_GENERIC_RUNWAY_WORK

CONFIRM = "CONFIRM_EMAS_RELEVANT"
MARK_NOT = "MARK_NOT_EMAS_RELEVANT"
DEFER = "DEFER_RELEVANCE_REVIEW"


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def _source_without_docstrings(module) -> str:
    source = inspect_module.getsource(module)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                source = source.replace(doc, "")
    return source


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


# ---------------------------------------------------------------------------
# Review model / vocabulary
# ---------------------------------------------------------------------------


class TestReviewVocabulary:
    def test_vocabulary_is_exactly_the_three_narrow_actions(self):
        assert set(RELEVANCE_REVIEW_ACTIONS) == {CONFIRM, MARK_NOT, DEFER}

    def test_no_downstream_effect_actions_in_vocabulary(self):
        forbidden = {"WATCH", "CREATE_AIRPORT", "CREATE_SIGNAL", "PROMOTE", "CREATE_NEW_AIRPORT"}
        assert forbidden.isdisjoint(RELEVANCE_REVIEW_ACTIONS)

    def test_invalid_action_h(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(G),))
        with pytest.raises(ValueError, match="action must be one of"):
            record_unknown_airport_candidate_relevance_review(
                session, candidate, basis_assessment_id=assessment.id,
                action="NOT_A_REAL_ACTION", reviewer="human:x", reason="x",
            )


# ---------------------------------------------------------------------------
# Basis-assessment binding / same-candidate integrity (HIGH PRIORITY)
# ---------------------------------------------------------------------------


class TestBasisAssessmentBinding:
    def test_nonexistent_basis_assessment_rejected(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        with pytest.raises(ValueError, match="does not exist"):
            record_unknown_airport_candidate_relevance_review(
                session, candidate, basis_assessment_id=999999,
                action=DEFER, reviewer="human:x", reason="x",
            )

    def test_cross_candidate_basis_rejected_g(self):
        """Attack G: review candidate A, basis assessment from candidate B - must fail closed."""
        _engine, session = make_session()
        candidate_a = _make_candidate(session, "Airport A")
        candidate_b = _make_candidate(session, "Airport B")
        assessment_b = _persist_assessment(session, candidate_b, (_obs(G),), "sa-b")
        with pytest.raises(ValueError, match="belongs to a different candidate"):
            record_unknown_airport_candidate_relevance_review(
                session, candidate_a, basis_assessment_id=assessment_b.id,
                action=DEFER, reviewer="human:x", reason="x",
            )

    def test_review_recorded_against_own_candidates_current_assessment_succeeds(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(G),))
        review = record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=assessment.id,
            action=MARK_NOT, reviewer="human:x", reason="x",
        )
        assert review.basis_assessment_id == assessment.id
        assert review.candidate_id == candidate.id


# ---------------------------------------------------------------------------
# Current-assessment gate / stale-basis refusal (HIGH PRIORITY)
# ---------------------------------------------------------------------------


class TestStaleBasisRefused:
    def test_stale_basis_refused_e(self):
        """Attack E: assessment #10 exists, human reviews #10, new evidence
        creates #11, attempt to record against #10 must refuse."""
        _engine, session = make_session()
        candidate = _make_candidate(session)
        old_assessment = _persist_assessment(session, candidate, (_obs(G),), "sa-1")
        new_assessment = _persist_assessment(session, candidate, (_obs(A),), "sa-2")
        assert new_assessment.id != old_assessment.id
        with pytest.raises(ValueError, match="not the current latest automatic assessment"):
            record_unknown_airport_candidate_relevance_review(
                session, candidate, basis_assessment_id=old_assessment.id,
                action=MARK_NOT, reviewer="human:x", reason="x",
            )

    def test_review_against_the_new_current_assessment_succeeds(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        _persist_assessment(session, candidate, (_obs(G),), "sa-1")
        new_assessment = _persist_assessment(session, candidate, (_obs(A),), "sa-2")
        review = record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=new_assessment.id,
            action=CONFIRM, reviewer="human:x", reason="x",
        )
        assert review.basis_assessment_id == new_assessment.id

    def test_no_silent_rebinding_to_new_assessment(self):
        """The service must never reinterpret a stale-basis attempt as
        applying to the new assessment instead - it must refuse outright,
        never silently substitute."""
        _engine, session = make_session()
        candidate = _make_candidate(session)
        old_assessment = _persist_assessment(session, candidate, (_obs(G),), "sa-1")
        _persist_assessment(session, candidate, (_obs(A),), "sa-2")
        with pytest.raises(ValueError):
            record_unknown_airport_candidate_relevance_review(
                session, candidate, basis_assessment_id=old_assessment.id,
                action=CONFIRM, reviewer="human:x", reason="x",
            )
        assert session.query(UnknownAirportCandidateRelevanceReview).count() == 0


# ---------------------------------------------------------------------------
# F. Same-timestamp latest-assessment tie
# ---------------------------------------------------------------------------


class TestLatestAssessmentTie:
    def test_same_timestamp_higher_id_wins_f(self):
        """Attack F: reuses ERG2's own get_latest semantics exactly -
        created_at DESC, id DESC - same-timestamp ties resolved by id.
        Assessment rows are immutable (before_update raises), so an
        identical created_at tie must be constructed at INSERT time, never
        via a post-hoc UPDATE - constructs both rows directly via the ORM
        (bypassing the persist service, legitimate here since this test
        exercises the READ helper's own tie-break logic, not the write
        path)."""
        from datetime import UTC, datetime
        _engine, session = make_session()
        candidate = _make_candidate(session)
        tied_timestamp = datetime.now(UTC)
        a1 = UnknownAirportCandidateRelevanceAssessment(
            candidate_id=candidate.id, outcome="RUNWAY_ONLY_NOT_EMAS_RELEVANT", reason="x",
            evidence_classes_matched_json="[]", contradicting_evidence_classes_json="[]",
            is_inventory_relevant=False, is_watch_worthy=False, evaluator_version="1",
            created_at=tied_timestamp,
        )
        session.add(a1)
        session.flush()
        a2 = UnknownAirportCandidateRelevanceAssessment(
            candidate_id=candidate.id, outcome="EMAS_STRONG_SIGNAL", reason="x",
            evidence_classes_matched_json="[]", contradicting_evidence_classes_json="[]",
            is_inventory_relevant=False, is_watch_worthy=True, evaluator_version="1",
            created_at=tied_timestamp,
        )
        session.add(a2)
        session.commit()
        assert a2.id > a1.id
        assert a1.created_at == a2.created_at

        review = record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=a2.id,  # the higher id
            action=CONFIRM, reviewer="human:x", reason="x",
        )
        assert review.basis_assessment_id == a2.id
        # The lower-id assessment must be correctly refused as stale.
        with pytest.raises(ValueError, match="not the current latest"):
            record_unknown_airport_candidate_relevance_review(
                session, candidate, basis_assessment_id=a1.id,
                action=CONFIRM, reviewer="human:x", reason="x",
            )


# ---------------------------------------------------------------------------
# Review eligibility (mission S9)
# ---------------------------------------------------------------------------


class TestReviewEligibility:
    """Deliberately unrestricted by automatic outcome - see the service's
    own module docstring for the full derivation."""

    def test_confirm_eligible_against_runway_only_outcome(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(G),))
        assert assessment.outcome == "RUNWAY_ONLY_NOT_EMAS_RELEVANT"
        review = record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=assessment.id,
            action=CONFIRM, reviewer="human:x", reason="out-of-band knowledge",
        )
        assert review.action == CONFIRM

    def test_mark_not_eligible_against_strong_emas_outcome(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(D),))
        assert assessment.outcome == "EMAS_STRONG_SIGNAL"
        review = record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=assessment.id,
            action=MARK_NOT, reviewer="human:x", reason="evidence misread by extraction",
        )
        assert review.action == MARK_NOT

    def test_defer_eligible_against_any_outcome(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, ())
        assert assessment.outcome == "INSUFFICIENT_EVIDENCE"
        review = record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=assessment.id,
            action=DEFER, reviewer="human:x", reason="need more information",
        )
        assert review.action == DEFER


# ---------------------------------------------------------------------------
# Human confirmation does not change automatic facts
# ---------------------------------------------------------------------------


class TestHumanReviewDoesNotMutateAssessment:
    def test_confirm_does_not_rewrite_assessment_fields(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(G),))
        before = (assessment.outcome, assessment.is_inventory_relevant, assessment.is_watch_worthy, assessment.evaluator_version)
        record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=assessment.id,
            action=CONFIRM, reviewer="human:x", reason="x",
        )
        session.commit()
        reloaded = session.get(UnknownAirportCandidateRelevanceAssessment, assessment.id)
        after = (reloaded.outcome, reloaded.is_inventory_relevant, reloaded.is_watch_worthy, reloaded.evaluator_version)
        assert before == after


# ---------------------------------------------------------------------------
# I. Multi-review history / append-only
# ---------------------------------------------------------------------------


class TestMultiReviewHistoryAppendOnly:
    def test_multi_review_history_i(self):
        """#1 DEFER on assessment 5, new evidence -> assessment 6, #2
        CONFIRM on assessment 6, new evidence -> assessment 7, #3 MARK_NOT
        on assessment 7. All review rows remain."""
        _engine, session = make_session()
        candidate = _make_candidate(session)
        a5 = _persist_assessment(session, candidate, (_obs(G),), "sa-5")
        r1 = record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=a5.id, action=DEFER, reviewer="human:x", reason="x",
        )
        session.commit()

        a6 = _persist_assessment(session, candidate, (_obs(A),), "sa-6")
        r2 = record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=a6.id, action=CONFIRM, reviewer="human:x", reason="x",
        )
        session.commit()

        a7 = _persist_assessment(session, candidate, (_obs(G),), "sa-7")
        r3 = record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=a7.id, action=MARK_NOT, reviewer="human:x", reason="x",
        )
        session.commit()

        all_reviews = session.query(UnknownAirportCandidateRelevanceReview).filter(
            UnknownAirportCandidateRelevanceReview.candidate_id == candidate.id
        ).all()
        assert len(all_reviews) == 3
        assert {r.id for r in all_reviews} == {r1.id, r2.id, r3.id}
        assert {r.action for r in all_reviews} == {DEFER, CONFIRM, MARK_NOT}


# ---------------------------------------------------------------------------
# M/N. Immutability
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_assessment_mutation_impossible_m(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(G),))
        assessment.reason = "tampered"
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
        session.rollback()

    def test_review_update_blocked_n(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(G),))
        review = record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=assessment.id, action=DEFER, reviewer="human:x", reason="x",
        )
        session.commit()
        review.reason = "changed"
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
        session.rollback()

    @pytest.mark.parametrize(
        "field,make_new_value",
        [
            ("action", lambda review, other: MARK_NOT if review.action != MARK_NOT else DEFER),
            ("reviewer", lambda review, other: "human:someone-else"),
            ("basis_assessment_id", lambda review, other: other["assessment2"].id),
            ("candidate_id", lambda review, other: other["candidate2"].id),
        ],
    )
    def test_review_field_mutation_blocked(self, field, make_new_value):
        """Review mission's own S15 checklist: the pre-existing
        test_review_update_blocked_n above only attacks `.reason`. Attack
        the other 4 mutable fields individually too - the before_update
        listener is column-agnostic, so all must be rejected identically,
        but this coverage gap was not previously closed."""
        _engine, session = make_session()
        candidate = _make_candidate(session, "Foo Airport")
        assessment = _persist_assessment(session, candidate, (_obs(G),))
        review = record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=assessment.id, action=DEFER, reviewer="human:x", reason="x",
        )
        session.commit()

        candidate2 = _make_candidate(session, "Bar Airport")
        assessment2 = _persist_assessment(session, candidate2, (_obs(G),), "sa-other")
        other = {"assessment2": assessment2, "candidate2": candidate2}

        setattr(review, field, make_new_value(review, other))
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
        session.rollback()

    def test_review_delete_blocked(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(G),))
        review = record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=assessment.id, action=DEFER, reviewer="human:x", reason="x",
        )
        session.commit()
        session.delete(review)
        with pytest.raises(ValueError, match="auditable and cannot be deleted"):
            session.commit()
        session.rollback()


# ---------------------------------------------------------------------------
# Effective review state (HIGH PRIORITY)
# ---------------------------------------------------------------------------


class TestEffectiveReviewState:
    def test_no_assessment_yet(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        state = resolve_effective_unknown_airport_candidate_relevance_review_state(session, candidate.id)
        assert state.state == RelevanceReviewState.NO_ASSESSMENT_YET
        assert state.is_current is False
        assert state.review_required is False  # nothing to review yet

    def test_unreviewed(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(G),))
        state = resolve_effective_unknown_airport_candidate_relevance_review_state(session, candidate.id)
        assert state.state == RelevanceReviewState.UNREVIEWED
        assert state.latest_assessment_id == assessment.id
        assert state.review_required is True
        assert state.is_current is False

    def test_current(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(D),))
        review = record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=assessment.id, action=CONFIRM, reviewer="human:x", reason="x",
        )
        session.commit()
        state = resolve_effective_unknown_airport_candidate_relevance_review_state(session, candidate.id)
        assert state.state == RelevanceReviewState.CURRENT
        assert state.is_current is True
        assert state.review_required is False
        assert state.latest_review_action == CONFIRM
        assert state.latest_review_id == review.id

    def test_stale_after_new_assessment_j(self):
        """Attack J: old CONFIRM becomes stale after new assessment."""
        _engine, session = make_session()
        candidate = _make_candidate(session)
        a1 = _persist_assessment(session, candidate, (_obs(D),), "sa-1")
        record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=a1.id, action=CONFIRM, reviewer="human:x", reason="x",
        )
        session.commit()

        a2 = _persist_assessment(session, candidate, (_obs(G),), "sa-2")
        state = resolve_effective_unknown_airport_candidate_relevance_review_state(session, candidate.id)
        assert state.state == RelevanceReviewState.STALE
        assert state.latest_assessment_id == a2.id
        assert state.latest_review_basis_assessment_id == a1.id
        assert state.review_required is True
        assert state.is_current is False
        # The action must NOT carry forward as if still authoritative -
        # exposed for informational purposes only via latest_review_action,
        # but state itself is STALE, never CURRENT/CONFIRMED.

    def test_stale_after_new_assessment_mark_not_k(self):
        """Attack K: old MARK_NOT becomes stale after new assessment."""
        _engine, session = make_session()
        candidate = _make_candidate(session)
        a1 = _persist_assessment(session, candidate, (_obs(G),), "sa-1")
        record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=a1.id, action=MARK_NOT, reviewer="human:x", reason="x",
        )
        session.commit()

        _persist_assessment(session, candidate, (_obs(A),), "sa-2")
        state = resolve_effective_unknown_airport_candidate_relevance_review_state(session, candidate.id)
        assert state.state == RelevanceReviewState.STALE


# ---------------------------------------------------------------------------
# Anoka regression (A/B) and rediscovery lifecycle (L)
# ---------------------------------------------------------------------------


class TestAnokaRegression:
    def test_anoka_negative_review_a(self):
        _engine, session = make_session()
        candidate = _make_candidate(session, "Anoka County-Blaine Airport")
        assessment = _persist_assessment(
            session, candidate,
            (_obs(G, basis="Runway 18-36 Pavement Reconstruction"), _obs(G, basis="Electrical Vault Improvements")),
        )
        assert assessment.outcome == "RUNWAY_ONLY_NOT_EMAS_RELEVANT"
        review = record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=assessment.id,
            action=MARK_NOT, reviewer="human:reviewer1", reason="confirmed generic runway work only",
        )
        session.commit()
        state = resolve_effective_unknown_airport_candidate_relevance_review_state(session, candidate.id)
        assert state.state == RelevanceReviewState.CURRENT
        assert state.latest_review_action == MARK_NOT
        assert review.candidate_id == candidate.id

    def test_anoka_defer_b(self):
        _engine, session = make_session()
        candidate = _make_candidate(session, "Anoka County-Blaine Airport")
        assessment = _persist_assessment(session, candidate, (_obs(G),))
        review = record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=assessment.id,
            action=DEFER, reviewer="human:x", reason="want to check with regional office",
        )
        assert review.action == DEFER

    def test_no_airport_signal_or_candidate_resolution_created(self):
        from app.models import Airport, Signal
        _engine, session = make_session()
        candidate = _make_candidate(session, "Anoka County-Blaine Airport")
        assessment = _persist_assessment(session, candidate, (_obs(G),))
        record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=assessment.id, action=MARK_NOT, reviewer="human:x", reason="x",
        )
        session.commit()
        assert session.query(Airport).count() == 0
        assert session.query(Signal).count() == 0
        assert candidate.resolved_airport_id is None


class TestRediscoveryLifecycle:
    def test_rediscovery_then_new_confirm_l(self):
        """Attack L: old MARK_NOT becomes stale, human must review again,
        then CONFIRM against the new assessment - both review rows
        preserved, no duplicate candidate."""
        _engine, session = make_session()
        candidate = _make_candidate(session, "Anoka County-Blaine Airport")
        a1 = _persist_assessment(session, candidate, (_obs(G),), "sa-1")
        old_review = record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=a1.id, action=MARK_NOT, reviewer="human:x", reason="x",
        )
        session.commit()

        # "new SourceAssertion: Anoka Runway 18-36 EMAS Feasibility Study"
        a2 = _persist_assessment(session, candidate, (_obs(A, basis="EMAS Feasibility Study"), _obs(B)), "sa-2")
        assert a2.is_watch_worthy is True

        stale_state = resolve_effective_unknown_airport_candidate_relevance_review_state(session, candidate.id)
        assert stale_state.state == RelevanceReviewState.STALE

        new_review = record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=a2.id, action=CONFIRM, reviewer="human:x",
            reason="EMAS feasibility study now confirmed relevant", supersedes_review_id=old_review.id,
        )
        session.commit()

        current_state = resolve_effective_unknown_airport_candidate_relevance_review_state(session, candidate.id)
        assert current_state.state == RelevanceReviewState.CURRENT
        assert current_state.latest_review_action == CONFIRM

        all_reviews = session.query(UnknownAirportCandidateRelevanceReview).filter(
            UnknownAirportCandidateRelevanceReview.candidate_id == candidate.id
        ).all()
        assert len(all_reviews) == 2
        assert {r.id for r in all_reviews} == {old_review.id, new_review.id}
        # No duplicate candidate - still exactly one candidate row for this identity.
        assert session.query(UnknownAirportCandidate).filter(
            UnknownAirportCandidate.raw_name == "Anoka County-Blaine Airport"
        ).count() == 1


# ---------------------------------------------------------------------------
# Dormant installation case
# ---------------------------------------------------------------------------


class TestDormantInstallationReview:
    def test_confirm_valid_current_review_no_watch_no_signal(self):
        from app.models import Signal
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(
            session, candidate, (_obs(E, basis="EMAS installed 2011", temporality=TemporalQualifier.HISTORICAL_FACT),),
        )
        assert assessment.is_inventory_relevant is True
        assert assessment.is_watch_worthy is False

        review = record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=assessment.id,
            action=CONFIRM, reviewer="human:x", reason="confirmed existing installation, add to inventory",
        )
        session.commit()

        state = resolve_effective_unknown_airport_candidate_relevance_review_state(session, candidate.id)
        assert state.state == RelevanceReviewState.CURRENT
        assert state.latest_review_action == CONFIRM
        # no active watch is invented, no Signal created
        assert session.query(Signal).count() == 0


# ---------------------------------------------------------------------------
# Contradiction case
# ---------------------------------------------------------------------------


class TestContradictionReview:
    def test_review_actions_recordable_against_contradicted_assessment(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        from app.services.emas_relevance_evaluation import ObservationPolarity
        assessment = _persist_assessment(
            session, candidate,
            (_obs(A, basis="EMAS procurement announced", polarity=ObservationPolarity.POSITIVE),
             _obs(A, basis="project later cancelled", polarity=ObservationPolarity.CONTRADICTING)),
        )
        assert assessment.contradicting_evidence_classes_json != "[]"

        for action in (CONFIRM, MARK_NOT, DEFER):
            _engine2, session2 = make_session()
            candidate2 = _make_candidate(session2)
            assessment2 = _persist_assessment(
                session2, candidate2,
                (_obs(A, basis="EMAS procurement announced", polarity=ObservationPolarity.POSITIVE),
                 _obs(A, basis="project later cancelled", polarity=ObservationPolarity.CONTRADICTING)),
            )
            review = record_unknown_airport_candidate_relevance_review(
                session2, candidate2, basis_assessment_id=assessment2.id,
                action=action, reviewer="human:x", reason=f"judgment: {action}",
            )
            assert review.action == action
        # Contradiction evidence itself is never erased or reinterpreted.
        assert assessment.contradicting_evidence_classes_json != "[]"


# ---------------------------------------------------------------------------
# R. No-autoflush
# ---------------------------------------------------------------------------


class TestErg4SeamDataContractPreservation:
    """HIGH-PRIORITY adversarial-review addition (review mission's own S28/
    S29/S30): explicit, permanently-locked proof that ERG3's own data
    contract preserves exactly what a future ERG4 gate needs to correctly
    BLOCK three dangerous cases - none of which ERG3 itself blocks (that
    is deliberately not this slice's job), but all of which must remain
    trivially detectable by a future reader of the persisted state."""

    def test_auto_negative_plus_human_confirm_s28(self):
        """automatic: inventory=false, watch=false. human: CONFIRM. ERG3
        correctly records a CURRENT CONFIRM - but the underlying assessment
        itself must still show admission=false, so a future ERG4 gate
        requiring BOTH signals still blocks. A human cannot manufacture
        EMAS evidence merely by confirming."""
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(G),))
        assert assessment.is_inventory_relevant is False
        assert assessment.is_watch_worthy is False

        record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=assessment.id, action=CONFIRM, reviewer="human:x", reason="x",
        )
        session.commit()

        state = resolve_effective_unknown_airport_candidate_relevance_review_state(session, candidate.id)
        assert state.state == RelevanceReviewState.CURRENT
        assert state.latest_review_action == CONFIRM
        # The automatic facts remain untouched and still say "not admission-relevant."
        reloaded = session.get(UnknownAirportCandidateRelevanceAssessment, state.latest_assessment_id)
        auto_admission_relevant = reloaded.is_inventory_relevant or reloaded.is_watch_worthy
        assert auto_admission_relevant is False
        # A future gate requiring (state.is_current AND action==CONFIRM AND
        # auto_admission_relevant) would correctly compute False here.
        future_gate_would_admit = state.is_current and state.latest_review_action == CONFIRM and auto_admission_relevant
        assert future_gate_would_admit is False

    def test_auto_positive_plus_human_mark_not_s29(self):
        """automatic: watch=true (admission-relevant). human: MARK_NOT.
        A future ERG4 gate requiring action==CONFIRM must block."""
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(D),))
        assert assessment.is_watch_worthy is True

        record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=assessment.id, action=MARK_NOT, reviewer="human:x", reason="x",
        )
        session.commit()

        state = resolve_effective_unknown_airport_candidate_relevance_review_state(session, candidate.id)
        assert state.state == RelevanceReviewState.CURRENT
        assert state.latest_review_action == MARK_NOT
        future_gate_would_admit = state.is_current and state.latest_review_action == CONFIRM
        assert future_gate_would_admit is False

    def test_auto_positive_plus_stale_confirm_s30(self):
        """assessment #1 positive, human CONFIRMs. assessment #2 positive
        arrives, no new review. A future ERG4 gate requiring
        state.is_current must block - the resolver makes this trivial
        (state flips to STALE automatically, no gate-side bookkeeping
        needed)."""
        _engine, session = make_session()
        candidate = _make_candidate(session)
        a1 = _persist_assessment(session, candidate, (_obs(D),), "sa-1")
        record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=a1.id, action=CONFIRM, reviewer="human:x", reason="x",
        )
        session.commit()

        a2 = _persist_assessment(session, candidate, (_obs(A),), "sa-2")
        assert a2.is_watch_worthy is True  # still auto-positive

        state = resolve_effective_unknown_airport_candidate_relevance_review_state(session, candidate.id)
        assert state.state == RelevanceReviewState.STALE
        future_gate_would_admit = state.is_current  # already False, no further check needed
        assert future_gate_would_admit is False


class TestNoAutoflushLeak:
    def test_record_precondition_check_does_not_autoflush_unrelated_pending_state(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(G),))
        bad = UnknownAirportCandidate(candidate_fingerprint="deadbeef")
        session.add(bad)
        with pytest.raises(ValueError, match="does not exist"):
            record_unknown_airport_candidate_relevance_review(
                session, candidate, basis_assessment_id=999999,
                action=DEFER, reviewer="human:x", reason="x",
            )
        assert bad in session.new

    def test_get_latest_review_does_not_autoflush_unrelated_pending_state(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        candidate_id = candidate.id  # captured before unrelated pending state, realistic caller pattern
        bad = UnknownAirportCandidate(candidate_fingerprint="deadbeef")
        session.add(bad)
        result = get_latest_unknown_airport_candidate_relevance_review(session, candidate_id)
        assert result is None
        assert bad in session.new

    def test_resolve_effective_state_does_not_autoflush_unrelated_pending_state(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        candidate_id = candidate.id
        bad = UnknownAirportCandidate(candidate_fingerprint="deadbeef")
        session.add(bad)
        state = resolve_effective_unknown_airport_candidate_relevance_review_state(session, candidate_id)
        assert state.state == RelevanceReviewState.NO_ASSESSMENT_YET
        assert bad in session.new

    def test_get_latest_review_does_not_autoflush_unrelated_invalid_review_row(self):
        """Review mission's own S20 checklist: the three existing tests
        above all use an unrelated *candidate* row as the pending invalid
        object. Attack with a pending invalid
        UnknownAirportCandidateRelevanceReview instead (violates the CHECK
        constraint on action) to prove the no_autoflush guard is not
        accidentally scoped to only one table's own pending writes."""
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(G),))
        candidate_id = candidate.id
        bad_review = UnknownAirportCandidateRelevanceReview(
            candidate_id=candidate.id, basis_assessment_id=assessment.id,
            action="BOGUS_ACTION", reviewer="human:x", reason="x",
        )
        session.add(bad_review)
        result = get_latest_unknown_airport_candidate_relevance_review(session, candidate_id)
        assert result is None
        assert bad_review in session.new

    def test_record_precondition_check_isolated_expired_candidate_attribute_alone(self):
        """Narrower isolation of the very first sub-case named in S20: an
        expired candidate attribute (candidate.id, after an intervening
        commit) is read as the FIRST statement inside the no_autoflush
        block, with NO unrelated pending invalid object present at all.
        This is a strict subset of test_record_precondition_check_does_not_
        autoflush_unrelated_pending_state above (which combines this with a
        bad pending candidate row); isolating it documents that the expired-
        attribute refresh itself is unproblematic, and that the combined
        test's pass is not accidentally hiding a narrower failure."""
        _engine, session = make_session()
        candidate = _make_candidate(session)  # _make_candidate commits, expiring candidate's attributes
        assessment = _persist_assessment(session, candidate, (_obs(G),))
        review = record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=assessment.id,
            action=DEFER, reviewer="human:x", reason="x",
        )
        assert review.action == DEFER


# ---------------------------------------------------------------------------
# Failure atomicity
# ---------------------------------------------------------------------------


class TestFailureAtomicity:
    def test_service_never_commits(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(G),))
        record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=assessment.id, action=DEFER, reviewer="human:x", reason="x",
        )
        session.rollback()
        assert session.query(UnknownAirportCandidateRelevanceReview).count() == 0

    def test_caller_rollback_after_failed_precondition_leaves_zero_partial_history(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        with pytest.raises(ValueError):
            record_unknown_airport_candidate_relevance_review(
                session, candidate, basis_assessment_id=999999, action=DEFER, reviewer="human:x", reason="x",
            )
        session.rollback()
        assert session.query(UnknownAirportCandidateRelevanceReview).count() == 0


# ---------------------------------------------------------------------------
# Information firewall
# ---------------------------------------------------------------------------


class TestInformationFirewall:
    def test_no_forbidden_imports(self):
        source = inspect_module.getsource(erg3_persistence)
        tree = ast.parse(source)
        forbidden_modules = {
            "app.services.unknown_airport_candidate_resolution",
            "app.services.unknown_airport_discovery_integration",
            "app.services.governed_signal_creation",
            "app.services.emas_relevance_evaluation",
            "httpx", "requests",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not any(module == m or module.startswith(m + ".") for m in forbidden_modules)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(alias.name == m or alias.name.startswith(m + ".") for m in forbidden_modules)

    def test_no_signal_airport_installation_write_path_names_imported(self):
        source = inspect_module.getsource(erg3_persistence)
        tree = ast.parse(source)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
        assert imported_names.isdisjoint({"Signal", "Airport", "Installation"})

    def test_no_commit_call_anywhere(self):
        source = _source_without_docstrings(erg3_persistence)
        assert ".commit(" not in source

    def test_never_mutates_candidate_or_assessment(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(G),))
        record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=assessment.id, action=CONFIRM, reviewer="human:x", reason="x",
        )
        assert candidate.resolved_airport_id is None

    def test_no_unknown_airport_candidate_review_created(self):
        from app.models.unknown_airport_candidate import UnknownAirportCandidateReview
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assessment = _persist_assessment(session, candidate, (_obs(G),))
        record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=assessment.id, action=CONFIRM, reviewer="human:x", reason="x",
        )
        session.commit()
        assert session.query(UnknownAirportCandidateReview).count() == 0
