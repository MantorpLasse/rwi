"""Tests for app/services/unknown_airport_candidate_governance_view.py
(ERG5, docs/architecture/rwi-erg5-operator-governance-flow-report.md).

CLI-level coverage (via run_review()) lives in
tests/test_review_unknown_airport_candidate.py's own TestErg5* classes -
these tests exercise the service directly, in isolation, for cases that
would be awkward to set up through the CLI (evidence-link isolation
between assessment generations, cross-candidate view isolation).

Every test uses an isolated in-memory SQLite database - never the real
data/runway_safe.db. Fixtures are entirely fictional.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Source, SourceAssertion
from app.models.unknown_airport_candidate import UnknownAirportCandidate
from app.models.unknown_airport_candidate_relevance_review import UnknownAirportCandidateRelevanceReview
from app.services.emas_relevance_evaluation import EmasEvidenceObservation, EvidenceClass
from app.services.unknown_airport_candidate_governance_view import (
    get_unknown_airport_candidate_governance_view,
)
from app.services.unknown_airport_candidate_persistence import find_or_create_unknown_airport_candidate
from app.services.unknown_airport_candidate_relevance_persistence import (
    persist_unknown_airport_candidate_relevance_assessment,
)
from app.services.unknown_airport_candidate_relevance_review_persistence import (
    record_unknown_airport_candidate_relevance_review,
)

A = EvidenceClass.A_EXPLICIT_EMAS
G = EvidenceClass.G_GENERIC_RUNWAY_WORK
CONFIRM = "CONFIRM_EMAS_RELEVANT"


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def _make_candidate(session, name="Foo Regional Airport"):
    result = find_or_create_unknown_airport_candidate(session, raw_name=name, raw_country="XX")
    session.commit()
    return result.candidate


def _make_assertion(session, candidate, identifier):
    source = Source(title="t", source_type="official")
    session.add(source)
    session.commit()
    assertion = SourceAssertion(
        source_id=source.id, assertion_type="project_construction",
        source_record_identifier=identifier, unknown_airport_candidate_id=candidate.id,
    )
    session.add(assertion)
    session.commit()
    return assertion


class TestEvidenceLinkIsolationAcrossAssessmentGenerations:
    def test_new_assessment_does_not_inherit_old_assessments_evidence_links(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        a1_assertion = _make_assertion(session, candidate, "sa-1")
        result1 = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=(EmasEvidenceObservation(A, basis="x"),),
            source_assertion_ids=(a1_assertion.id,),
        )
        session.commit()

        a2_assertion = _make_assertion(session, candidate, "sa-2")
        result2 = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=(EmasEvidenceObservation(A, basis="y"),),
            source_assertion_ids=(a2_assertion.id,),
        )
        session.commit()

        view = get_unknown_airport_candidate_governance_view(session, candidate.id)
        assert view.automatic_relevance.assessment_id == result2.assessment.id
        assert view.automatic_relevance.linked_source_assertion_ids == (a2_assertion.id,)
        assert a1_assertion.id not in view.automatic_relevance.linked_source_assertion_ids


class TestCrossCandidateViewIsolation:
    def test_two_candidates_own_independent_views(self):
        _engine, session = make_session()
        candidate_a = _make_candidate(session, "Airport A")
        candidate_b = _make_candidate(session, "Airport B")
        assertion_a = _make_assertion(session, candidate_a, "sa-a")
        assertion_b = _make_assertion(session, candidate_b, "sa-b")

        result_a = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate_a, observations=(EmasEvidenceObservation(A, basis="x"),),
            source_assertion_ids=(assertion_a.id,),
        )
        session.commit()
        persist_unknown_airport_candidate_relevance_assessment(
            session, candidate_b, observations=(EmasEvidenceObservation(G, basis="x"),),
            source_assertion_ids=(assertion_b.id,),
        )
        session.commit()
        record_unknown_airport_candidate_relevance_review(
            session, candidate_a, basis_assessment_id=result_a.assessment.id, action=CONFIRM,
            reviewer="human:x", reason="x",
        )
        session.commit()

        view_a = get_unknown_airport_candidate_governance_view(session, candidate_a.id)
        view_b = get_unknown_airport_candidate_governance_view(session, candidate_b.id)
        assert view_a.canonical_admission.eligible is True
        assert view_b.canonical_admission.eligible is False
        assert view_b.human_relevance_review.state == "UNREVIEWED"
        assert assertion_b.id not in view_a.automatic_relevance.linked_source_assertion_ids


class TestNoAutoflush:
    def test_unrelated_invalid_pending_candidate_object(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assertion = _make_assertion(session, candidate, "sa-1")
        result = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=(EmasEvidenceObservation(A, basis="x"),),
            source_assertion_ids=(assertion.id,),
        )
        session.commit()
        record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=result.assessment.id, action=CONFIRM,
            reviewer="human:x", reason="x",
        )
        session.commit()
        candidate_id = candidate.id  # captured before unrelated pending state
        bad = UnknownAirportCandidate(candidate_fingerprint="deadbeef")
        session.add(bad)
        view = get_unknown_airport_candidate_governance_view(session, candidate_id)
        assert view.canonical_admission.eligible is True
        assert bad in session.new

    def test_unrelated_invalid_pending_review_object(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assertion = _make_assertion(session, candidate, "sa-1")
        result = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=(EmasEvidenceObservation(A, basis="x"),),
            source_assertion_ids=(assertion.id,),
        )
        session.commit()
        record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=result.assessment.id, action=CONFIRM,
            reviewer="human:x", reason="x",
        )
        session.commit()
        bad_review = UnknownAirportCandidateRelevanceReview(
            candidate_id=candidate.id, basis_assessment_id=result.assessment.id,
            action="BOGUS", reviewer="human:x", reason="x",
        )
        session.add(bad_review)
        view = get_unknown_airport_candidate_governance_view(session, candidate.id)
        assert view.canonical_admission.eligible is True
        assert bad_review in session.new

    def test_expired_candidate_attributes(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)  # commits internally, expiring attrs
        assertion = _make_assertion(session, candidate, "sa-1")
        result = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=(EmasEvidenceObservation(A, basis="x"),),
            source_assertion_ids=(assertion.id,),
        )
        session.commit()
        record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=result.assessment.id, action=CONFIRM,
            reviewer="human:x", reason="x",
        )
        session.commit()
        candidate_id = candidate.id  # first read after commit
        view = get_unknown_airport_candidate_governance_view(session, candidate_id)
        assert view.canonical_admission.eligible is True


class TestNeverMutates:
    def test_view_construction_performs_zero_writes(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        get_unknown_airport_candidate_governance_view(session, candidate.id)
        assert len(session.new) == 0
        assert len(session.dirty) == 0
