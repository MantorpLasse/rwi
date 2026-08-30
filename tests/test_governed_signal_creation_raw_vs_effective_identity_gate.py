"""Tests for the "RWI - Raw-vs-Effective Signal Creation Gate - Narrow Fix"
mission: `create_signal_from_approved_review()`'s identity component in
app/services/governed_signal_creation.py now consumes EB5's EFFECTIVE
decision (resolve_effective_identity_guard_decision()), never the raw
SourceAssertion.identity_guard_decision column directly - mirroring
app.services.reviewer_action_persistence's own already-fixed
APPROVE_SIGNAL/CONFIRM_DISTINCT_SIGNAL gate exactly.

A deliberately separate file from tests/test_governed_signal_creation.py
(and its sibling reconciliation/distinct-confirmation/migration test
files), all of which must keep passing completely unmodified as evidence
this mission changed nothing else about Signal creation.

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
from app.services.governed_signal_creation import create_signal_from_approved_review
from app.services.reviewer_action_persistence import record_reviewer_action
from app.services.source_assertion_legacy_identity_attestation import record_legacy_identity_attestation


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
    """raw ATTACH_PROVISIONAL, effective ATTACH_CONFIRMED via
    CrossSourceAliasAttestation - the central regression fixture."""
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
    """raw NULL, effective ATTACH_CONFIRMED via LEGACY_HUMAN_ATTESTATION."""
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
    """raw INSUFFICIENT_IDENTITY, effective ATTACH_CONFIRMED via
    LATEST_REEVALUATION."""
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


def _approve(session, assertion, **kwargs) -> ReviewerAction:
    action = record_reviewer_action(
        session, assertion, action="APPROVE_SIGNAL", reason="Effectively confirmed identity, human-approved.",
        reviewer="human:tester", **kwargs,
    )
    session.commit()
    return action


_SIGNAL_FIELDS = dict(
    title="Test Airport EMAS project", category="replacement", confidence="medium", status="identified",
)


# ---------------------------------------------------------------------------
# Central SA235-shaped full gate test
# ---------------------------------------------------------------------------

class TestSA235ShapedFullGate:
    def test_signal_creation_succeeds_raw_provisional_effective_confirmed(self):
        engine, session = make_session()
        assertion = _sa235_shaped(session)
        _approve(session, assertion)
        assert assertion.identity_guard_decision == "ATTACH_PROVISIONAL"

        result = create_signal_from_approved_review(session, assertion, **_SIGNAL_FIELDS)
        assert result.created is True
        assert session.query(Signal).count() == 1
        assert assertion.identity_guard_decision == "ATTACH_PROVISIONAL"  # raw untouched
        session.rollback()
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Effective identity basis matrix
# ---------------------------------------------------------------------------

class TestEffectiveBasisMatrix:
    def test_original_decision_confirmed_allows_creation(self):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        assertion = _governed_assertion(session, airport, source, identity_guard_decision="ATTACH_CONFIRMED")
        _approve(session, assertion)
        result = create_signal_from_approved_review(session, assertion, **_SIGNAL_FIELDS)
        assert result.created is True
        session.close(); engine.dispose()

    def test_latest_reevaluation_confirmed_allows_creation(self):
        engine, session = make_session()
        assertion = _eb4_reevaluated_shaped(session)
        _approve(session, assertion)
        assert assertion.identity_guard_decision == "INSUFFICIENT_IDENTITY"
        result = create_signal_from_approved_review(session, assertion, **_SIGNAL_FIELDS)
        assert result.created is True
        assert assertion.identity_guard_decision == "INSUFFICIENT_IDENTITY"  # raw untouched
        session.close(); engine.dispose()

    def test_legacy_human_attestation_confirmed_allows_creation(self):
        engine, session = make_session()
        assertion = _legacy_attested_shaped(session)
        _approve(session, assertion)
        assert assertion.identity_guard_decision is None
        result = create_signal_from_approved_review(session, assertion, **_SIGNAL_FIELDS)
        assert result.created is True
        assert assertion.identity_guard_decision is None  # raw untouched
        session.close(); engine.dispose()

    def test_cross_source_alias_attestation_confirmed_allows_creation(self):
        engine, session = make_session()
        assertion = _sa235_shaped(session)
        _approve(session, assertion)
        result = create_signal_from_approved_review(session, assertion, **_SIGNAL_FIELDS)
        assert result.created is True
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Negative matrix
#
# NOTE: these tests insert the ReviewerAction row directly (bypassing
# record_reviewer_action()'s own, already-fixed, identical effective-
# identity gate) rather than calling _approve() - a genuinely
# not-effectively-confirmed row can never legally reach a real
# APPROVE_SIGNAL in production (that earlier gate would refuse it first),
# so this is the only way to isolate and test
# create_signal_from_approved_review()'s OWN identity gate on its own
# merits, exactly as this mission's own Phase 7 requires.
# ---------------------------------------------------------------------------

def _insert_raw_reviewer_action(session, assertion) -> None:
    action = ReviewerAction(
        source_assertion_id=assertion.id, action="APPROVE_SIGNAL",
        reason="synthetic - bypasses record_reviewer_action()'s own gate to isolate this module's gate",
        reviewer="human:tester",
    )
    session.add(action)
    session.commit()


class TestNegativeMatrix:
    @pytest.mark.parametrize("identity_guard_decision", ["ATTACH_PROVISIONAL", "INSUFFICIENT_IDENTITY", "REJECT_CROSS_AIRPORT", None])
    def test_effective_not_confirmed_refused(self, identity_guard_decision):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        assertion = _governed_assertion(session, airport, source, identity_guard_decision=identity_guard_decision)
        _insert_raw_reviewer_action(session, assertion)
        with pytest.raises(ValueError, match="identity_guard_decision"):
            create_signal_from_approved_review(session, assertion, **_SIGNAL_FIELDS)
        assert session.query(Signal).count() == 0
        session.close(); engine.dispose()

    def test_inconsistent_reevaluation_refused(self):
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
        _insert_raw_reviewer_action(session, assertion)
        with pytest.raises(ValueError, match="identity_guard_decision"):
            create_signal_from_approved_review(session, assertion, **_SIGNAL_FIELDS)
        assert session.query(Signal).count() == 0
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Raw-history immutability
# ---------------------------------------------------------------------------

class TestRawHistoryImmutability:
    def test_successful_creation_mutates_only_intended_rows(self):
        engine, session = make_session()
        assertion = _sa235_shaped(session)
        _approve(session, assertion)

        before_raw = assertion.identity_guard_decision
        before_intel_decision = assertion.intelligence_review_decision
        before_intel_reason = assertion.intelligence_review_reason
        before_promo_decision = assertion.promotion_policy_decision
        before_promo_reason = assertion.promotion_policy_reason
        before_aliases = session.query(AirportAlias).count()
        before_evaluations = session.query(IdentityGuardEvaluation).count()
        before_attestations = session.query(SourceAssertionCrossSourceAliasAttestation).count()
        before_claims = session.query(ManualClaimEvidence).count()
        before_reviewer_actions = session.query(ReviewerAction).count()

        result = create_signal_from_approved_review(session, assertion, **_SIGNAL_FIELDS)

        assert result.created is True
        assert session.query(Signal).count() == 1
        assert assertion.signal_id == result.signal.id
        assert assertion.identity_guard_decision == before_raw
        assert assertion.intelligence_review_decision == before_intel_decision
        assert assertion.intelligence_review_reason == before_intel_reason
        assert assertion.promotion_policy_decision == before_promo_decision
        assert assertion.promotion_policy_reason == before_promo_reason
        assert session.query(AirportAlias).count() == before_aliases
        assert session.query(IdentityGuardEvaluation).count() == before_evaluations
        assert session.query(SourceAssertionCrossSourceAliasAttestation).count() == before_attestations
        assert session.query(ManualClaimEvidence).count() == before_claims
        assert session.query(ReviewerAction).count() == before_reviewer_actions  # no new ReviewerAction
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Gate order proof - the raw blocker is genuinely removed, not moved
# ---------------------------------------------------------------------------

class TestGateOrder:
    def test_first_blocker_without_reviewer_action_is_missing_reviewer_action_not_identity(self):
        engine, session = make_session()
        assertion = _sa235_shaped(session)
        # No ReviewerAction recorded.
        with pytest.raises(ValueError, match="no ReviewerAction has been recorded"):
            create_signal_from_approved_review(session, assertion, **_SIGNAL_FIELDS)
        session.close(); engine.dispose()

    def test_with_approve_signal_governance_gates_pass(self):
        engine, session = make_session()
        assertion = _sa235_shaped(session)
        _approve(session, assertion)
        # Reaching Signal construction (not raising on governance gates) proves
        # the identity blocker is genuinely gone, not merely relocated.
        result = create_signal_from_approved_review(session, assertion, **_SIGNAL_FIELDS)
        assert result.created is True
        session.close(); engine.dispose()
