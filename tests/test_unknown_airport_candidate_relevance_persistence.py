"""Tests for app/services/unknown_airport_candidate_relevance_persistence.py
and app/models/unknown_airport_candidate_relevance_assessment.py (ERG2,
docs/architecture/rwi-erg2-relevance-assessment-persistence-report.md).

Every test uses an isolated in-memory SQLite database - never the real
data/runway_safe.db. Fixtures are entirely fictional. Modeled on the
already-proven pattern in tests/test_unknown_airport_candidate_persistence.py
and tests/test_promotion_policy_persistence.py.
"""
from __future__ import annotations

import ast
import inspect as inspect_module
import json
import sqlite3

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Source, SourceAssertion
from app.models.unknown_airport_candidate import UnknownAirportCandidate
from app.models.unknown_airport_candidate_relevance_assessment import (
    RELEVANCE_ASSESSMENT_OUTCOMES,
    UnknownAirportCandidateRelevanceAssessment,
    UnknownAirportCandidateRelevanceAssessmentEvidenceLink,
)
from app.services.emas_relevance_evaluation import (
    EVALUATOR_VERSION,
    EmasEvidenceObservation,
    EvidenceClass,
    ObservationPolarity,
    RelevanceOutcome,
    evaluate_emas_relevance,
)
from app.services.evidence_claim_semantics import TemporalQualifier
from app.services.unknown_airport_candidate_persistence import find_or_create_unknown_airport_candidate
from app.services import unknown_airport_candidate_relevance_persistence as erg2_persistence
from app.services.unknown_airport_candidate_relevance_persistence import (
    UnknownAirportCandidateRelevanceAssessmentResult,
    deserialize_evidence_classes,
    get_latest_unknown_airport_candidate_relevance_assessment,
    persist_unknown_airport_candidate_relevance_assessment,
)

A = EvidenceClass.A_EXPLICIT_EMAS
B = EvidenceClass.B_RUNWAY_SAFETY_AREA_OR_ARRESTOR_NEED
D = EvidenceClass.D_FUNDING_OR_PROCUREMENT
E = EvidenceClass.E_EXISTING_INSTALLATION
G = EvidenceClass.G_GENERIC_RUNWAY_WORK


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def make_session_with_foreign_keys_enforced():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    Base.metadata.create_all(engine)
    return engine, Session(engine)


def _make_candidate(session, name="Foo Regional Airport"):
    result = find_or_create_unknown_airport_candidate(session, raw_name=name, raw_country="XX")
    session.commit()
    return result.candidate


def _make_source_assertion(session, candidate=None, identifier="sa-1"):
    source = Source(title="Test Source", source_type="official")
    session.add(source)
    session.commit()
    assertion = SourceAssertion(
        source_id=source.id, assertion_type="project_construction",
        source_record_identifier=identifier,
        unknown_airport_candidate_id=candidate.id if candidate is not None else None,
    )
    session.add(assertion)
    session.commit()
    return assertion


def _source_without_docstrings(module) -> str:
    """Explanatory docstrings legitimately name things like
    'session.commit()' as prose (explaining what this module deliberately
    does NOT do, or documenting a caller-side scenario) - source-content
    checks below must inspect executable code only, matching
    test_emas_relevance_evaluation.py's own identical helper."""
    source = inspect_module.getsource(module)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                source = source.replace(doc, "")
    return source


def _obs(cls, basis="x", **kwargs):
    return EmasEvidenceObservation(cls, basis=basis, **kwargs)


# ---------------------------------------------------------------------------
# Persisted model contract
# ---------------------------------------------------------------------------


class TestPersistedModelContract:
    def test_outcome_vocabulary_matches_evaluator_exactly(self):
        assert set(RELEVANCE_ASSESSMENT_OUTCOMES) == {o.value for o in RelevanceOutcome}

    def test_assessment_has_no_canonical_admission_column(self):
        columns = {c.name for c in UnknownAirportCandidateRelevanceAssessment.__table__.columns}
        assert "is_canonical_admission_relevant" not in columns
        assert "canonical_admission_relevant" not in columns


# ---------------------------------------------------------------------------
# Evidence traceability
# ---------------------------------------------------------------------------


class TestEvidenceTraceability:
    def test_nonempty_observations_require_source_assertion_ids(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        with pytest.raises(ValueError, match="source_assertion_ids is required"):
            persist_unknown_airport_candidate_relevance_assessment(
                session, candidate, observations=(_obs(G),), source_assertion_ids=(),
            )

    def test_empty_observations_permit_empty_source_assertion_ids(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        result = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=(), source_assertion_ids=(),
        )
        assert result.outcome == RelevanceOutcome.INSUFFICIENT_EVIDENCE
        assert result.linked_source_assertion_ids == ()

    def test_nonexistent_source_assertion_id_rejected(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        with pytest.raises(ValueError, match="does not exist"):
            persist_unknown_airport_candidate_relevance_assessment(
                session, candidate, observations=(_obs(G),), source_assertion_ids=(999999,),
            )

    def test_source_assertion_linked_to_different_candidate_rejected(self):
        _engine, session = make_session()
        candidate_a = _make_candidate(session, "Airport A")
        candidate_b = _make_candidate(session, "Airport B")
        assertion = _make_source_assertion(session, candidate=candidate_a)
        with pytest.raises(ValueError, match="not linked to candidate"):
            persist_unknown_airport_candidate_relevance_assessment(
                session, candidate_b, observations=(_obs(G),), source_assertion_ids=(assertion.id,),
            )

    def test_source_assertion_not_linked_to_any_candidate_rejected(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assertion = _make_source_assertion(session, candidate=None)
        with pytest.raises(ValueError, match="not linked to candidate"):
            persist_unknown_airport_candidate_relevance_assessment(
                session, candidate, observations=(_obs(G),), source_assertion_ids=(assertion.id,),
            )

    def test_multiple_linked_source_assertions_all_recorded_as_child_rows(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        a1 = _make_source_assertion(session, candidate=candidate, identifier="sa-1")
        a2 = _make_source_assertion(session, candidate=candidate, identifier="sa-2")
        result = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=(_obs(G),), source_assertion_ids=(a1.id, a2.id),
        )
        session.commit()
        links = (
            session.query(UnknownAirportCandidateRelevanceAssessmentEvidenceLink)
            .filter(UnknownAirportCandidateRelevanceAssessmentEvidenceLink.assessment_id == result.assessment.id)
            .all()
        )
        assert {link.source_assertion_id for link in links} == {a1.id, a2.id}
        assert result.linked_source_assertion_ids == tuple(sorted((a1.id, a2.id)))

    def test_duplicate_source_assertion_ids_deduplicated_not_double_linked(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assertion = _make_source_assertion(session, candidate=candidate)
        result = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=(_obs(G),), source_assertion_ids=(assertion.id, assertion.id),
        )
        session.commit()
        links = (
            session.query(UnknownAirportCandidateRelevanceAssessmentEvidenceLink)
            .filter(UnknownAirportCandidateRelevanceAssessmentEvidenceLink.assessment_id == result.assessment.id)
            .all()
        )
        assert len(links) == 1


# ---------------------------------------------------------------------------
# Append-only immutability
# ---------------------------------------------------------------------------


class TestAppendOnlyImmutability:
    def test_assessment_update_blocked(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        result = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=(), source_assertion_ids=(),
        )
        session.commit()
        result.assessment.reason = "tampered"
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
        session.rollback()

    def test_assessment_delete_blocked(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        result = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=(), source_assertion_ids=(),
        )
        session.commit()
        session.delete(result.assessment)
        with pytest.raises(ValueError, match="auditable and cannot be deleted"):
            session.commit()
        session.rollback()

    def test_evidence_link_update_blocked(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assertion = _make_source_assertion(session, candidate=candidate)
        result = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=(_obs(G),), source_assertion_ids=(assertion.id,),
        )
        session.commit()
        link = (
            session.query(UnknownAirportCandidateRelevanceAssessmentEvidenceLink)
            .filter(UnknownAirportCandidateRelevanceAssessmentEvidenceLink.assessment_id == result.assessment.id)
            .one()
        )
        link.source_assertion_id = assertion.id  # trivial reassignment still triggers before_update
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
        session.rollback()

    def test_evidence_link_delete_blocked(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assertion = _make_source_assertion(session, candidate=candidate)
        result = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=(_obs(G),), source_assertion_ids=(assertion.id,),
        )
        session.commit()
        link = (
            session.query(UnknownAirportCandidateRelevanceAssessmentEvidenceLink)
            .filter(UnknownAirportCandidateRelevanceAssessmentEvidenceLink.assessment_id == result.assessment.id)
            .one()
        )
        session.delete(link)
        with pytest.raises(ValueError, match="auditable and cannot be deleted"):
            session.commit()
        session.rollback()


# ---------------------------------------------------------------------------
# Outcome/boolean consistency - no redundant canonical_admission_relevant
# ---------------------------------------------------------------------------


class TestOutcomeBooleanConsistency:
    @pytest.mark.parametrize("observations,expected_admission", [
        ((_obs(E, temporality=TemporalQualifier.HISTORICAL_FACT),), True),
        ((_obs(B),), True),
        ((_obs(G),), False),
        ((), False),
    ])
    def test_canonical_admission_relevance_rederived_correctly(self, observations, expected_admission):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        source_assertion_ids = ()
        if observations:
            assertion = _make_source_assertion(session, candidate=candidate)
            source_assertion_ids = (assertion.id,)
        result = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=observations, source_assertion_ids=source_assertion_ids,
        )
        assert result.is_canonical_admission_relevant == expected_admission
        assert result.is_canonical_admission_relevant == (
            result.assessment.is_inventory_relevant or result.assessment.is_watch_worthy
        )


# ---------------------------------------------------------------------------
# Evidence-class serialization
# ---------------------------------------------------------------------------


class TestEvidenceClassSerialization:
    def test_matched_and_contradicting_classes_round_trip_losslessly(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assertion = _make_source_assertion(session, candidate=candidate)
        result = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate,
            observations=(_obs(A, polarity=ObservationPolarity.POSITIVE), _obs(D, polarity=ObservationPolarity.CONTRADICTING)),
            source_assertion_ids=(assertion.id,),
        )
        session.commit()
        matched = deserialize_evidence_classes(result.assessment.evidence_classes_matched_json)
        contradicting = deserialize_evidence_classes(result.assessment.contradicting_evidence_classes_json)
        assert matched == frozenset({A})
        assert contradicting == frozenset({D})

    def test_empty_sets_serialize_to_empty_json_array(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        result = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=(), source_assertion_ids=(),
        )
        assert json.loads(result.assessment.evidence_classes_matched_json) == []
        assert json.loads(result.assessment.contradicting_evidence_classes_json) == []

    def test_serialized_json_is_sorted_deterministic(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assertion = _make_source_assertion(session, candidate=candidate)
        result = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate,
            observations=(_obs(B), _obs(A)),
            source_assertion_ids=(assertion.id,),
        )
        assert json.loads(result.assessment.evidence_classes_matched_json) == sorted(
            json.loads(result.assessment.evidence_classes_matched_json)
        )


# ---------------------------------------------------------------------------
# Evaluator version - fabrication-proof
# ---------------------------------------------------------------------------


class TestEvaluatorVersionHandling:
    def test_persisted_version_matches_real_evaluator_constant(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        result = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=(), source_assertion_ids=(),
        )
        assert result.assessment.evaluator_version == EVALUATOR_VERSION

    def test_no_evaluator_version_parameter_exists_on_the_persistence_api(self):
        """Structural, not behavioral: proves there is no way for a caller
        to pass a fabricated version - the parameter simply does not
        exist."""
        sig = inspect_module.signature(persist_unknown_airport_candidate_relevance_assessment)
        assert "evaluator_version" not in sig.parameters

    def test_no_outcome_or_boolean_parameters_exist_on_the_persistence_api(self):
        """Structural proof of the single authoritative path (mission's
        own explicit instruction): the only way to influence the persisted
        outcome/booleans is via `observations` - there is no `outcome`,
        `decision`, `is_inventory_relevant`, or `is_watch_worthy` parameter
        a caller could use to fabricate an inconsistent result."""
        sig = inspect_module.signature(persist_unknown_airport_candidate_relevance_assessment)
        forbidden = {"outcome", "decision", "is_inventory_relevant", "is_watch_worthy", "reason", "evaluator_version"}
        assert forbidden.isdisjoint(sig.parameters.keys())

    def test_persisted_decision_matches_a_fresh_independent_evaluate_call(self):
        """The persisted row's own outcome/booleans must agree exactly with
        calling evaluate_emas_relevance() directly on the same observations
        - the persistence service must never compute anything differently
        from the real evaluator."""
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assertion = _make_source_assertion(session, candidate=candidate)
        observations = (_obs(D),)
        result = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=observations, source_assertion_ids=(assertion.id,),
        )
        independent = evaluate_emas_relevance(observations)
        assert result.assessment.outcome == independent.outcome.value
        assert result.assessment.is_inventory_relevant == independent.is_inventory_relevant
        assert result.assessment.is_watch_worthy == independent.is_watch_worthy
        assert result.assessment.reason == independent.reason


# ---------------------------------------------------------------------------
# Latest-read semantics / multi-assessment history
# ---------------------------------------------------------------------------


class TestLatestReadAndHistory:
    def test_no_assessment_returns_none(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assert get_latest_unknown_airport_candidate_relevance_assessment(session, candidate.id) is None

    def test_multi_assessment_history_all_rows_preserved_latest_correct(self):
        """Mission's own S13 lifecycle: RUNWAY_ONLY -> watch-worthy ->
        inventory-relevant, all three rows remain, latest read returns #3,
        historical rows byte-identical."""
        _engine, session = make_session()
        candidate = _make_candidate(session)
        a1 = _make_source_assertion(session, candidate=candidate, identifier="sa-1")

        r1 = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=(_obs(G),), source_assertion_ids=(a1.id,),
        )
        session.commit()

        a2 = _make_source_assertion(session, candidate=candidate, identifier="sa-2")
        r2 = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=(_obs(G), _obs(A)), source_assertion_ids=(a1.id, a2.id),
        )
        session.commit()

        a3 = _make_source_assertion(session, candidate=candidate, identifier="sa-3")
        r3 = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate,
            observations=(
                _obs(E, temporality=TemporalQualifier.HISTORICAL_FACT),
            ),
            source_assertion_ids=(a3.id,),
        )
        session.commit()

        assert r1.outcome == RelevanceOutcome.RUNWAY_ONLY_NOT_EMAS_RELEVANT
        assert r2.outcome == RelevanceOutcome.EMAS_STRONG_SIGNAL
        assert r3.outcome == RelevanceOutcome.EMAS_CONFIRMED

        all_rows = (
            session.query(UnknownAirportCandidateRelevanceAssessment)
            .filter(UnknownAirportCandidateRelevanceAssessment.candidate_id == candidate.id)
            .all()
        )
        assert len(all_rows) == 3
        assert {row.outcome for row in all_rows} == {
            "RUNWAY_ONLY_NOT_EMAS_RELEVANT", "EMAS_STRONG_SIGNAL", "EMAS_CONFIRMED",
        }

        latest = get_latest_unknown_airport_candidate_relevance_assessment(session, candidate.id)
        assert latest.id == r3.assessment.id
        assert latest.outcome == "EMAS_CONFIRMED"

        # Historical rows byte-identical (not overwritten by later calls).
        reloaded_r1 = session.get(UnknownAirportCandidateRelevanceAssessment, r1.assessment.id)
        assert reloaded_r1.outcome == "RUNWAY_ONLY_NOT_EMAS_RELEVANT"
        assert reloaded_r1.reason == r1.decision.reason

        # HIGH-PRIORITY (mission's own S4 A/B/C): no dynamic "all assertions
        # currently on candidate" interpretation - each assessment's own
        # link membership stays exactly what it was at write time, even as
        # the candidate accumulates more assertions later, and even for #3
        # which deliberately links ONLY the newest assertion, not a1/a2 too.
        def _linked_ids(assessment_id):
            rows = (
                session.query(UnknownAirportCandidateRelevanceAssessmentEvidenceLink)
                .filter(UnknownAirportCandidateRelevanceAssessmentEvidenceLink.assessment_id == assessment_id)
                .all()
            )
            return {row.source_assertion_id for row in rows}

        assert _linked_ids(r1.assessment.id) == {a1.id}
        assert _linked_ids(r2.assessment.id) == {a1.id, a2.id}
        assert _linked_ids(r3.assessment.id) == {a3.id}


# ---------------------------------------------------------------------------
# Locked regressions (mission's own worked examples)
# ---------------------------------------------------------------------------


class TestAnokaRegression:
    def test_anoka_evidence_persists_as_runway_only_never_inventory_or_watch(self):
        _engine, session = make_session()
        candidate = _make_candidate(session, "Anoka County-Blaine Airport")
        assertion = _make_source_assertion(session, candidate=candidate)
        result = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate,
            observations=(
                _obs(G, basis="Runway 18-36 Pavement Reconstruction"),
                _obs(G, basis="Electrical Vault Improvements"),
            ),
            source_assertion_ids=(assertion.id,),
        )
        session.commit()
        assert result.assessment.outcome == "RUNWAY_ONLY_NOT_EMAS_RELEVANT"
        assert result.assessment.is_inventory_relevant is False
        assert result.assessment.is_watch_worthy is False
        assert result.is_canonical_admission_relevant is False


class TestDormantInstallationRegression:
    def test_historical_installation_persists_as_inventory_relevant_not_watch(self):
        from app.services.evidence_claim_semantics import TemporalQualifier
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assertion = _make_source_assertion(session, candidate=candidate)
        result = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate,
            observations=(_obs(E, basis="EMAS installed 2011", temporality=TemporalQualifier.HISTORICAL_FACT),),
            source_assertion_ids=(assertion.id,),
        )
        session.commit()
        assert result.assessment.outcome == "EMAS_CONFIRMED"
        assert result.assessment.is_inventory_relevant is True
        assert result.assessment.is_watch_worthy is False
        assert result.is_canonical_admission_relevant is True


class TestActiveOpportunityRegression:
    def test_planned_replacement_persists_as_watch_worthy(self):
        from app.services.evidence_claim_semantics import TemporalQualifier
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assertion = _make_source_assertion(session, candidate=candidate)
        result = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate,
            observations=(_obs(D, basis="replacement planned", temporality=TemporalQualifier.PLANNED_FUTURE_ACTION),),
            source_assertion_ids=(assertion.id,),
        )
        session.commit()
        assert result.assessment.is_watch_worthy is True
        assert result.assessment.outcome == "EMAS_STRONG_SIGNAL"

    def test_no_signal_row_created_by_persistence(self):
        from app.models import Signal
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assertion = _make_source_assertion(session, candidate=candidate)
        persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=(_obs(D),), source_assertion_ids=(assertion.id,),
        )
        session.commit()
        assert session.query(Signal).count() == 0


# ---------------------------------------------------------------------------
# Contradiction persistence - recorder, not a second evaluator
# ---------------------------------------------------------------------------


class TestContradictionPersistence:
    def test_contradicting_classes_persisted_exactly_as_evaluator_returns(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        assertion = _make_source_assertion(session, candidate=candidate)
        observations = (
            _obs(A, basis="EMAS procurement announced", polarity=ObservationPolarity.POSITIVE),
            _obs(A, basis="project cancelled", polarity=ObservationPolarity.CONTRADICTING),
        )
        result = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=observations, source_assertion_ids=(assertion.id,),
        )
        independent = evaluate_emas_relevance(observations)
        assert deserialize_evidence_classes(result.assessment.contradicting_evidence_classes_json) == independent.contradicting_evidence_classes
        assert result.assessment.outcome == independent.outcome.value  # never reinterpreted


# ---------------------------------------------------------------------------
# Transaction / rollback atomicity (no commit ownership, failure injection)
# ---------------------------------------------------------------------------


class TestNoAutoflushLeak:
    """HIGH-PRIORITY adversarial-review addition (mission's own S14): 'this
    bug class has appeared elsewhere in RWI.' Reproduced directly against
    the pre-fix code: an unrelated, invalid, PENDING (never flushed)
    UnknownAirportCandidate object in the same session caused both
    get_latest_...() and persist_...()'s own precondition-check phase to
    raise a leaked autoflush IntegrityError instead of behaving correctly."""

    def test_latest_read_does_not_autoflush_unrelated_invalid_pending_state(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        candidate_id = candidate.id  # captured before the unrelated pending state - get_latest_...()
        # only ever receives a plain int, never the ORM object, so this is
        # the realistic call shape; evaluating `candidate.id` fresh AFTER
        # `bad` is added would itself trigger the expired-attribute refresh
        # in the TEST's own calling code, before get_latest_...() is even
        # entered - a real, but separate, caller-side concern this
        # function's own signature (candidate_id: int) cannot control.
        bad = UnknownAirportCandidate(candidate_fingerprint="deadbeef")  # raw_name NOT NULL, unset
        session.add(bad)
        # Must not raise - a read-only helper must never flush unrelated pending state.
        result = get_latest_unknown_airport_candidate_relevance_assessment(session, candidate_id)
        assert result is None
        assert bad in session.new  # still pending, not silently flushed or discarded

    def test_persist_precondition_check_does_not_autoflush_unrelated_invalid_pending_state(self):
        """A failing precondition check (invalid source_assertion_id) must
        surface as THIS function's own ValueError - never a leaked
        autoflush IntegrityError from the caller's unrelated pending
        object."""
        _engine, session = make_session()
        candidate = _make_candidate(session)
        bad = UnknownAirportCandidate(candidate_fingerprint="deadbeef")
        session.add(bad)
        with pytest.raises(ValueError, match="does not exist"):
            persist_unknown_airport_candidate_relevance_assessment(
                session, candidate, observations=(_obs(G),), source_assertion_ids=(999999,),
            )
        assert bad in session.new  # still pending, not flushed by the failed precondition check

    def test_expired_candidate_attribute_access_alone_does_not_autoflush(self):
        """The narrower bug: session.commit() expires `candidate`'s own
        attributes - merely reading `candidate.id` inside this function
        (even before any session.get()/query() call) must not itself
        trigger a refresh that autoflushes unrelated pending state during
        the PRECONDITION-CHECK phase. Uses a failing precondition (bad
        source_assertion_id) so the real write is never reached, isolating
        this narrower claim from the documented SCOPE boundary the next
        test exercises."""
        _engine, session = make_session()
        candidate = _make_candidate(session)  # _make_candidate() already commits once, expiring candidate's attributes
        bad = UnknownAirportCandidate(candidate_fingerprint="deadbeef")
        session.add(bad)
        with pytest.raises(ValueError, match="does not exist"):
            persist_unknown_airport_candidate_relevance_assessment(
                session, candidate, observations=(_obs(G),), source_assertion_ids=(999999,),
            )
        assert bad in session.new

    def test_real_write_still_flushes_the_whole_session_by_design_not_a_bug(self):
        """Documents the boundary explicitly, per the module's own SCOPE
        note: once preconditions pass and this function reaches its OWN
        intentional write, session.flush() flushes the whole session's
        pending state - exactly like every other persistence function in
        this codebase. An unrelated invalid pending object at THAT point
        correctly still causes a failure; this is standard SQLAlchemy
        unit-of-work behavior, not something this or any sibling
        persistence function attempts to avoid."""
        _engine, session = make_session()
        candidate = _make_candidate(session)
        bad = UnknownAirportCandidate(candidate_fingerprint="deadbeef")
        session.add(bad)
        with pytest.raises(Exception):  # sqlalchemy.exc.IntegrityError, the unrelated object's own violation
            persist_unknown_airport_candidate_relevance_assessment(
                session, candidate, observations=(), source_assertion_ids=(),
            )


class TestTransactionAtomicity:
    def test_service_never_commits(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=(), source_assertion_ids=(),
        )
        session.rollback()  # if the service had committed, this would be a no-op and the row would survive
        assert session.query(UnknownAirportCandidateRelevanceAssessment).count() == 0

    def test_caller_rollback_after_failed_link_leaves_zero_partial_history(self):
        """Injects a failure between the assessment insert and the
        evidence-link insert (an invalid source_assertion_id caught by the
        service's own pre-flight check) - confirms no assessment row
        survives a caller rollback even though the assessment insert alone
        would have succeeded."""
        _engine, session = make_session()
        candidate = _make_candidate(session)
        with pytest.raises(ValueError, match="does not exist"):
            persist_unknown_airport_candidate_relevance_assessment(
                session, candidate, observations=(_obs(G),), source_assertion_ids=(999999,),
            )
        session.rollback()
        assert session.query(UnknownAirportCandidateRelevanceAssessment).count() == 0

    def test_no_autoflush_side_effect_before_validation_completes(self):
        """A malformed call must not leave a half-flushed assessment row
        visible even before the caller explicitly rolls back."""
        _engine, session = make_session()
        candidate = _make_candidate(session)
        try:
            persist_unknown_airport_candidate_relevance_assessment(
                session, candidate, observations=(_obs(G),), source_assertion_ids=(999999,),
            )
        except ValueError:
            pass
        # Query forces a flush if anything pending exists; nothing should.
        assert session.query(UnknownAirportCandidateRelevanceAssessment).count() == 0
        session.rollback()


# ---------------------------------------------------------------------------
# Information firewall
# ---------------------------------------------------------------------------


class TestInformationFirewall:
    def test_no_forbidden_imports(self):
        source = inspect_module.getsource(erg2_persistence)
        tree = ast.parse(source)
        forbidden_modules = {
            "app.services.unknown_airport_candidate_resolution",
            "app.services.unknown_airport_discovery_integration",
            "app.services.governed_signal_creation",
            "app.services.promotion_policy_evaluation",
            "app.services.promotion_policy_persistence",
            "httpx", "requests",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not any(module == m or module.startswith(m + ".") for m in forbidden_modules)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(alias.name == m or alias.name.startswith(m + ".") for m in forbidden_modules)

    def test_no_signal_or_airport_write_path_names_imported(self):
        source = inspect_module.getsource(erg2_persistence)
        tree = ast.parse(source)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
        assert imported_names.isdisjoint({"Signal", "Airport"})

    def test_no_sessionlocal_or_create_engine_reference(self):
        tree = ast.parse(inspect_module.getsource(erg2_persistence))
        code_identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert "SessionLocal" not in code_identifiers
        assert "create_engine" not in code_identifiers

    def test_no_commit_call_anywhere(self):
        source = _source_without_docstrings(erg2_persistence)
        assert ".commit(" not in source

    def test_never_mutates_candidate_resolved_airport_id(self):
        _engine, session = make_session()
        candidate = _make_candidate(session)
        persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=(), source_assertion_ids=(),
        )
        assert candidate.resolved_airport_id is None

    def test_no_unknown_airport_candidate_review_created(self):
        from app.models.unknown_airport_candidate import UnknownAirportCandidateReview
        _engine, session = make_session()
        candidate = _make_candidate(session)
        persist_unknown_airport_candidate_relevance_assessment(
            session, candidate, observations=(), source_assertion_ids=(),
        )
        session.commit()
        assert session.query(UnknownAirportCandidateReview).count() == 0
