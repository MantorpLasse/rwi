"""Tests for the "RWI - Raw-vs-Effective APPROVE_SIGNAL Gate - Narrow Fix"
mission: APPROVE_SIGNAL/CONFIRM_DISTINCT_SIGNAL's identity component in
app/services/reviewer_action_persistence.py now consumes EB5's EFFECTIVE
decision (resolve_effective_identity_guard_decision()), never the raw
SourceAssertion.identity_guard_decision column directly - mirroring the
MARK_DUPLICATE gate's own, already-proven EB5 reuse.

A deliberately separate file from tests/test_reviewer_action_persistence.py/
tests/test_reviewer_action_confirm_distinct_signal.py/
tests/test_reviewer_action_mark_duplicate_identity_gate.py, all of which
must keep passing (mostly unmodified - one test in the MARK_DUPLICATE file
was updated to reflect this mission's own intended behavior change, not a
regression) as evidence this mission changed nothing else.

Every test uses an isolated in-memory SQLite database - data/runway_safe.db
is never opened here.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, ReviewerAction, Signal, Source, SourceAssertion
from app.models.airport_alias import AirportAlias
from app.models.identity_guard_evaluation import IdentityGuardEvaluation
from app.models.manual_claim_evidence import ManualClaimEvidence
from app.models.source_assertion_cross_source_alias_attestation import (
    SourceAssertionCrossSourceAliasAttestation,
)
from app.services.cross_source_alias_attestation import record_cross_source_alias_attestation
from app.services.reviewer_action_persistence import record_reviewer_action
from app.services.source_assertion_legacy_identity_attestation import record_legacy_identity_attestation

VALID_FINGERPRINT = "a" * 64


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def _airport(session, **overrides) -> Airport:
    kwargs = dict(name="Test Airport", country="Testland")
    kwargs.update(overrides)
    airport = Airport(**kwargs)
    session.add(airport); session.flush()
    return airport


def _source(session, **overrides) -> Source:
    kwargs = dict(title="Test source", source_type="Authority", reliability_level="official")
    kwargs.update(overrides)
    source = Source(**kwargs)
    session.add(source); session.flush()
    return source


def _governed_assertion(
    session, airport, source, *,
    identity_guard_decision="ATTACH_CONFIRMED",
    intelligence_review_decision="REVIEW_REQUIRED",
    promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
    raw_relevant_text=None,
    **kwargs,
) -> SourceAssertion:
    assertion = SourceAssertion(
        source=source, airport=airport, assertion_type="project_construction",
        source_record_identifier=f"rec-{airport.id}-{source.id}",
        identity_guard_decision=identity_guard_decision,
        intelligence_review_decision=intelligence_review_decision,
        promotion_policy_decision=promotion_policy_decision,
        raw_relevant_text=raw_relevant_text, evidence_quality="direct_strong",
        **kwargs,
    )
    session.add(assertion); session.commit()
    return assertion


def _sa235_shaped(session):
    """The exact real Sacheon/SA235 shape: raw ATTACH_PROVISIONAL, effective
    ATTACH_CONFIRMED via CrossSourceAliasAttestation, with intelligence
    review and promotion policy already governed to the point of requiring
    human approval - the central regression fixture."""
    airport = _airport(session)
    alias_source = _source(session, title="Alias registry")
    alias_assertion = SourceAssertion(
        source=alias_source, airport=airport, assertion_type="airport_inventory",
        raw_relevant_text="테스트공항(Test Airport) official.", source_record_identifier="rec-alias",
        evidence_quality="direct_strong",
    )
    session.add(alias_assertion); session.commit()
    alias = AirportAlias(
        airport_id=airport.id, alias="테스트공항", source_id=alias_source.id,
        source_assertion_id=alias_assertion.id, evidence_excerpt="테스트공항(Test Airport) official.",
        analyst="human:tester", evidence_class="AUTHORITATIVE_DIRECT", status="ADMITTED",
    )
    session.add(alias); session.commit()

    council_source = _source(session, title="Independent council")
    assertion = _governed_assertion(
        session, airport, council_source, identity_guard_decision="ATTACH_PROVISIONAL",
        raw_relevant_text="테스트공항 EMAS project underway.",
    )
    record_cross_source_alias_attestation(
        session, source_assertion_id=assertion.id, matched_alias_id=alias.id,
        analyst="human:tester", reason="test",
    )
    session.commit()
    return assertion


def _legacy_attested_shaped(session):
    """Raw NULL, effective ATTACH_CONFIRMED via LEGACY_HUMAN_ATTESTATION."""
    airport = _airport(session)
    source = _source(session, source_type="usaspending_grant")
    assertion = SourceAssertion(
        source=source, airport=airport, assertion_type="project_construction",
        raw_relevant_text="ENGINEERED MATERIAL ARRESTING SYSTEM project text",
        source_record_identifier="rec-legacy", evidence_quality="direct_strong",
        intelligence_review_decision="REVIEW_REQUIRED", promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
    )
    session.add(assertion); session.commit()
    record_legacy_identity_attestation(
        session, source_assertion_id=assertion.id, action="CONFIRM_EXISTING_ATTACHMENT",
        reason="Evidence matches this airport.", reviewer="human:tester", matched_airport_id=airport.id,
    )
    session.commit()
    return assertion


def _eb4_reevaluated_shaped(session, *, snapshot_id=1):
    """Raw INSUFFICIENT_IDENTITY, effective ATTACH_CONFIRMED via
    LATEST_REEVALUATION - mirrors tests/test_effective_identity_guard_decision.py's
    own _add_evaluation() helper/pattern exactly (no FK enforcement on this
    plain in-memory engine, so a synthetic snapshot_id is safe here)."""
    airport = _airport(session)
    source = _source(session)
    assertion = _governed_assertion(
        session, airport, source, identity_guard_decision="INSUFFICIENT_IDENTITY",
        raw_relevant_text="Test Airport memo.",
    )
    evaluation = IdentityGuardEvaluation(
        source_assertion_id=assertion.id, evidence_bag_snapshot_id=snapshot_id,
        evaluated_against_airport_id=airport.id, outcome="ATTACH_CONFIRMED",
        reason="synthetic test evaluation",
    )
    session.add(evaluation); session.commit()
    return assertion


# ---------------------------------------------------------------------------
# Central SA235-shaped regression test
# ---------------------------------------------------------------------------

class TestSA235ShapedRegression:
    def test_approve_signal_passes_raw_provisional_effective_confirmed(self):
        engine, session = make_session()
        assertion = _sa235_shaped(session)
        assert assertion.identity_guard_decision == "ATTACH_PROVISIONAL"

        action = record_reviewer_action(
            session, assertion, action="APPROVE_SIGNAL", reason="Effectively confirmed via cross-source attestation.",
            reviewer="human:tester",
        )
        session.commit()
        assert action.action == "APPROVE_SIGNAL"
        assert assertion.identity_guard_decision == "ATTACH_PROVISIONAL"  # raw untouched
        session.close(); engine.dispose()

    def test_confirm_distinct_signal_passes_raw_provisional_effective_confirmed(self):
        engine, session = make_session()
        assertion = _sa235_shaped(session)
        approve = record_reviewer_action(
            session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:tester",
        )
        session.commit()

        action = record_reviewer_action(
            session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="Resolves reconciliation block.",
            reviewer="human:tester", reconciliation_fingerprint=VALID_FINGERPRINT,
            supersedes_action_id=approve.id,
        )
        session.commit()
        assert action.action == "CONFIRM_DISTINCT_SIGNAL"
        assert assertion.identity_guard_decision == "ATTACH_PROVISIONAL"  # raw untouched
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Negative tests
# ---------------------------------------------------------------------------

class TestNegativeCases:
    @pytest.mark.parametrize("action", ["APPROVE_SIGNAL", "CONFIRM_DISTINCT_SIGNAL"])
    def test_raw_and_effective_both_provisional_refused(self, action):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        assertion = _governed_assertion(session, airport, source, identity_guard_decision="ATTACH_PROVISIONAL")
        with pytest.raises(ValueError, match="identity_guard_decision"):
            record_reviewer_action(session, assertion, action=action, reason="x", reviewer="human:tester")
        assert session.query(ReviewerAction).count() == 0
        session.close(); engine.dispose()

    @pytest.mark.parametrize("action", ["APPROVE_SIGNAL", "CONFIRM_DISTINCT_SIGNAL"])
    def test_raw_and_effective_both_insufficient_refused(self, action):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        assertion = _governed_assertion(session, airport, source, identity_guard_decision="INSUFFICIENT_IDENTITY")
        with pytest.raises(ValueError, match="identity_guard_decision"):
            record_reviewer_action(session, assertion, action=action, reason="x", reviewer="human:tester")
        session.close(); engine.dispose()

    @pytest.mark.parametrize("action", ["APPROVE_SIGNAL", "CONFIRM_DISTINCT_SIGNAL"])
    def test_raw_and_effective_both_reject_refused(self, action):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        assertion = _governed_assertion(session, airport, source, identity_guard_decision="REJECT_CROSS_AIRPORT")
        with pytest.raises(ValueError, match="identity_guard_decision"):
            record_reviewer_action(session, assertion, action=action, reason="x", reviewer="human:tester")
        session.close(); engine.dispose()

    def test_raw_null_no_effective_confirmation_refused(self):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        assertion = _governed_assertion(session, airport, source, identity_guard_decision=None)
        with pytest.raises(ValueError, match="identity_guard_decision"):
            record_reviewer_action(session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:tester")
        session.close(); engine.dispose()

    def test_inconsistent_reevaluation_refused(self):
        """evaluated_against_airport_id mismatch - EB5's own
        INCONSISTENT_REEVALUATION basis - must fail closed, never trusted."""
        engine, session = make_session()
        airport = _airport(session)
        other_airport = _airport(session, name="Other Airport")
        source = _source(session)
        assertion = _governed_assertion(session, airport, source, identity_guard_decision="ATTACH_PROVISIONAL")
        evaluation = IdentityGuardEvaluation(
            source_assertion_id=assertion.id, evidence_bag_snapshot_id=1,
            evaluated_against_airport_id=other_airport.id,  # mismatch
            outcome="ATTACH_CONFIRMED", reason="synthetic",
        )
        session.add(evaluation); session.commit()

        with pytest.raises(ValueError, match="identity_guard_decision"):
            record_reviewer_action(session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:tester")
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Raw-history immutability proof
# ---------------------------------------------------------------------------

class TestRawHistoryImmutability:
    def test_no_mutation_of_any_governed_table(self):
        engine, session = make_session()
        assertion = _sa235_shaped(session)

        before_raw = assertion.identity_guard_decision
        before_eb_count = session.query(AirportAlias).count()
        before_evaluations = session.query(IdentityGuardEvaluation).count()
        before_attestations = session.query(SourceAssertionCrossSourceAliasAttestation).count()
        before_claims = session.query(ManualClaimEvidence).count()

        record_reviewer_action(
            session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:tester",
        )
        session.commit()

        assert assertion.identity_guard_decision == before_raw
        assert session.query(AirportAlias).count() == before_eb_count
        assert session.query(IdentityGuardEvaluation).count() == before_evaluations
        assert session.query(SourceAssertionCrossSourceAliasAttestation).count() == before_attestations
        assert session.query(ManualClaimEvidence).count() == before_claims
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Generic EB5 consumption - no source-family special-casing
# ---------------------------------------------------------------------------

class TestGenericEB5Consumption:
    def test_legacy_attestation_effective_confirmed_allows_approve_signal(self):
        engine, session = make_session()
        assertion = _legacy_attested_shaped(session)
        assert assertion.identity_guard_decision is None

        action = record_reviewer_action(
            session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:tester",
        )
        session.commit()
        assert action.action == "APPROVE_SIGNAL"
        assert assertion.identity_guard_decision is None
        session.close(); engine.dispose()

    def test_eb4_reevaluation_effective_confirmed_allows_approve_signal(self):
        engine, session = make_session()
        assertion = _eb4_reevaluated_shaped(session)
        assert assertion.identity_guard_decision == "INSUFFICIENT_IDENTITY"

        action = record_reviewer_action(
            session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:tester",
        )
        session.commit()
        assert action.action == "APPROVE_SIGNAL"
        assert assertion.identity_guard_decision == "INSUFFICIENT_IDENTITY"
        session.close(); engine.dispose()

    def test_cross_source_alias_attestation_effective_confirmed_allows_confirm_distinct(self):
        engine, session = make_session()
        assertion = _sa235_shaped(session)
        approve = record_reviewer_action(
            session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:tester",
        )
        session.commit()
        action = record_reviewer_action(
            session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:tester",
            reconciliation_fingerprint=VALID_FINGERPRINT, supersedes_action_id=approve.id,
        )
        session.commit()
        assert action.action == "CONFIRM_DISTINCT_SIGNAL"
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Other gates preserved
# ---------------------------------------------------------------------------

class TestOtherGatesPreserved:
    def test_intelligence_review_gate_still_enforced_even_with_effective_confirmed(self):
        engine, session = make_session()
        assertion = _sa235_shaped(session)
        assertion.intelligence_review_decision = "INSUFFICIENT_MATERIALITY"
        session.commit()
        with pytest.raises(ValueError, match="intelligence_review_decision"):
            record_reviewer_action(session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:tester")
        session.close(); engine.dispose()

    def test_promotion_policy_gate_still_enforced_even_with_effective_confirmed(self):
        engine, session = make_session()
        assertion = _sa235_shaped(session)
        assertion.promotion_policy_decision = "AUTO_ELIGIBLE"
        session.commit()
        with pytest.raises(ValueError, match="promotion_policy_decision"):
            record_reviewer_action(session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:tester")
        session.close(); engine.dispose()

    def test_reason_and_reviewer_still_required(self):
        engine, session = make_session()
        assertion = _sa235_shaped(session)
        with pytest.raises(ValueError, match="reason is required"):
            record_reviewer_action(session, assertion, action="APPROVE_SIGNAL", reason="  ", reviewer="human:tester")
        session.close(); engine.dispose()
