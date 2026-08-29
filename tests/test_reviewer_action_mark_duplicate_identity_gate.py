"""Tests for the MARK_DUPLICATE effective-identity gate in
app/services/reviewer_action_persistence.py (approved Option B invariant,
rwi-mark-duplicate-upstream-governance-gate design/review).

MARK_DUPLICATE may only be recorded when
resolve_effective_identity_guard_decision() (EB5) resolves to
ATTACH_CONFIRMED - checked via the EFFECTIVE decision, never the raw
identity_guard_decision column, so a legacy-attested row (whose raw column
is permanently NULL by design) is still correctly treated as governed.
Intelligence-review/promotion-policy state is deliberately NOT a
prerequisite. Every test uses an isolated in-memory SQLite database -
data/runway_safe.db is never opened here.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, ReviewerAction, Signal, Source, SourceAssertion
from app.services.reviewer_action_persistence import get_latest_reviewer_action, record_reviewer_action
from app.services.source_assertion_legacy_identity_attestation import record_legacy_identity_attestation


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def _airport(session, name="Test Airport", code="ZZZ") -> Airport:
    airport = Airport(name=name, iata_code=code, country="USA")
    session.add(airport)
    session.commit()
    return airport


def _signal(session, airport, **kwargs) -> Signal:
    kwargs.setdefault("title", "Existing signal")
    kwargs.setdefault("category", "replacement")
    kwargs.setdefault("confidence", "medium")
    signal = Signal(airport_id=airport.id, **kwargs)
    session.add(signal)
    session.commit()
    return signal


def _modern_assertion(session, airport, *, identity_guard_decision="ATTACH_CONFIRMED", **kwargs) -> SourceAssertion:
    """SA222-shaped: a real identity_guard_decision written directly onto
    the raw column, as the modern identity-guard pipeline does."""
    source = Source(title="Test source", source_type="web_discovery")
    session.add(source)
    session.commit()
    kwargs.setdefault("assertion_type", "project_construction")
    kwargs.setdefault("source_record_identifier", f"rec-{airport.id}-{source.id}")
    assertion = SourceAssertion(
        source=source, airport=airport, identity_guard_decision=identity_guard_decision, **kwargs
    )
    session.add(assertion)
    session.commit()
    return assertion


def _legacy_attested_assertion(session, airport, *, action="CONFIRM_EXISTING_ATTACHMENT") -> SourceAssertion:
    """SA81-shaped: raw identity_guard_decision stays NULL; identity is
    governed exclusively via SourceAssertionLegacyIdentityAttestation
    (EB5 LEGACY_HUMAN_ATTESTATION basis)."""
    source = Source(title="USAspending grant", source_type="usaspending_grant")
    session.add(source)
    session.commit()
    assertion = SourceAssertion(
        source=source, airport=airport, assertion_type="project_construction",
        raw_relevant_text="ENGINEERED MATERIAL ARRESTING SYSTEM project text",
        source_record_identifier=f"rec-legacy-{airport.id}-{source.id}",
        evidence_quality="direct_strong",
    )
    session.add(assertion)
    session.commit()
    record_legacy_identity_attestation(
        session, source_assertion_id=assertion.id, action=action, reason="Evidence matches this airport.",
        reviewer="human:tester", matched_airport_id=(airport.id if action == "CONFIRM_EXISTING_ATTACHMENT" else None),
    )
    session.commit()
    return assertion


def _ungoverned_assertion(session, airport) -> SourceAssertion:
    """SA76-shaped: raw identity_guard_decision NULL, no legacy attestation
    at all - EB5 falls back to INSUFFICIENT_IDENTITY."""
    source = Source(title="USAspending grant", source_type="usaspending_grant")
    session.add(source)
    session.commit()
    assertion = SourceAssertion(
        source=source, airport=airport, assertion_type="project_construction",
        raw_relevant_text="ENGINEERED MATERIAL ARRESTING SYSTEM project text",
        source_record_identifier=f"rec-ungoverned-{airport.id}-{source.id}",
        evidence_quality="direct_strong",
    )
    session.add(assertion)
    session.commit()
    return assertion


class TestLegacyAttestedAllowed:
    """1, 19: SA81-shaped - raw NULL, effective ATTACH_CONFIRMED via
    LEGACY_HUMAN_ATTESTATION -> MARK_DUPLICATE succeeds."""

    def test_legacy_attested_mark_duplicate_succeeds(self):
        engine, session = make_session()
        airport = _airport(session)
        existing = _signal(session, airport, source_id=None)
        assertion = _legacy_attested_assertion(session, airport)
        assert assertion.identity_guard_decision is None  # raw column stays NULL by design

        action = record_reviewer_action(
            session, assertion, action="MARK_DUPLICATE",
            reason="Same grant/source as the existing legacy Signal.",
            reviewer="human:tester", duplicate_of_signal_id=existing.id,
        )
        session.commit()

        assert action.action == "MARK_DUPLICATE"
        assert action.duplicate_of_signal_id == existing.id
        session.close(); engine.dispose()


class TestModernAssertionAllowed:
    """2, 18: SA222-shaped - raw ATTACH_CONFIRMED -> MARK_DUPLICATE still
    succeeds (zero regression against the real historical precedent)."""

    def test_modern_raw_confirmed_mark_duplicate_succeeds(self):
        engine, session = make_session()
        airport = _airport(session)
        existing = _signal(session, airport)
        assertion = _modern_assertion(session, airport)

        action = record_reviewer_action(
            session, assertion, action="MARK_DUPLICATE",
            reason="Corroborates existing signal.", reviewer="human:tester",
            duplicate_of_signal_id=existing.id,
        )
        session.commit()
        assert action.duplicate_of_signal_id == existing.id
        session.close(); engine.dispose()


class TestUngovernedRefused:
    """3, 10, 11, 12, 20: SA76-shaped - EB5 INSUFFICIENT_IDENTITY ->
    MARK_DUPLICATE refused; proves no ReviewerAction, no signal_id link, no
    Signal mutation."""

    def test_insufficient_identity_refused(self):
        engine, session = make_session()
        airport = _airport(session)
        existing = _signal(session, airport, title="Untouched signal")
        assertion = _ungoverned_assertion(session, airport)

        with pytest.raises(ValueError, match="effective identity decision"):
            record_reviewer_action(
                session, assertion, action="MARK_DUPLICATE",
                reason="Attempted duplicate link.", reviewer="human:tester",
                duplicate_of_signal_id=existing.id,
            )

        assert session.query(ReviewerAction).count() == 0
        assert assertion.signal_id is None
        refreshed_signal = session.get(Signal, existing.id)
        assert refreshed_signal.title == "Untouched signal"
        session.close(); engine.dispose()

    def test_direct_service_call_cannot_bypass_gate(self):
        """8, 11: calling record_reviewer_action() directly (no CLI, no
        wrapper) still refuses - the invariant lives in the authoritative
        write layer, not merely in the CLI's own validation."""
        engine, session = make_session()
        airport = _airport(session)
        existing = _signal(session, airport)
        assertion = _ungoverned_assertion(session, airport)

        with pytest.raises(ValueError):
            record_reviewer_action(
                session, assertion, action="MARK_DUPLICATE", reason="x", reviewer="human:tester",
                duplicate_of_signal_id=existing.id,
            )
        session.close(); engine.dispose()


class TestOtherEffectiveIdentityStatesRefused:
    """4, 5: ATTACH_PROVISIONAL and REJECT_CROSS_AIRPORT, both directly
    representable via the raw column (EB5's ORIGINAL_DECISION fallback),
    must also refuse MARK_DUPLICATE - only ATTACH_CONFIRMED passes."""

    def test_attach_provisional_refused(self):
        engine, session = make_session()
        airport = _airport(session)
        existing = _signal(session, airport)
        assertion = _modern_assertion(session, airport, identity_guard_decision="ATTACH_PROVISIONAL")

        with pytest.raises(ValueError, match="effective identity decision"):
            record_reviewer_action(
                session, assertion, action="MARK_DUPLICATE", reason="x", reviewer="human:tester",
                duplicate_of_signal_id=existing.id,
            )
        session.close(); engine.dispose()

    def test_reject_cross_airport_refused(self):
        engine, session = make_session()
        airport = _airport(session)
        existing = _signal(session, airport)
        assertion = _modern_assertion(session, airport, identity_guard_decision="REJECT_CROSS_AIRPORT")

        with pytest.raises(ValueError, match="effective identity decision"):
            record_reviewer_action(
                session, assertion, action="MARK_DUPLICATE", reason="x", reviewer="human:tester",
                duplicate_of_signal_id=existing.id,
            )
        session.close(); engine.dispose()

    def test_legacy_rejected_attachment_refused(self):
        """The legacy-attestation analog of item 5: a human explicitly
        REJECTed the legacy attachment - effective identity stays
        INSUFFICIENT_IDENTITY, never a silent pass."""
        engine, session = make_session()
        airport = _airport(session)
        existing = _signal(session, airport)
        assertion = _legacy_attested_assertion(session, airport, action="REJECT_EXISTING_ATTACHMENT")

        with pytest.raises(ValueError, match="effective identity decision"):
            record_reviewer_action(
                session, assertion, action="MARK_DUPLICATE", reason="x", reviewer="human:tester",
                duplicate_of_signal_id=existing.id,
            )
        session.close(); engine.dispose()


class TestIntelligenceReviewNotRequired:
    """6, 7: intelligence_review_decision/promotion_policy_decision stay
    NULL throughout - MARK_DUPLICATE must still succeed. This is
    intentional architecture, not an oversight."""

    def test_null_intelligence_review_does_not_block(self):
        engine, session = make_session()
        airport = _airport(session)
        existing = _signal(session, airport, source_id=None)
        assertion = _legacy_attested_assertion(session, airport)
        assert assertion.intelligence_review_decision is None
        assert assertion.promotion_policy_decision is None

        action = record_reviewer_action(
            session, assertion, action="MARK_DUPLICATE", reason="x", reviewer="human:tester",
            duplicate_of_signal_id=existing.id,
        )
        session.commit()
        assert action.action == "MARK_DUPLICATE"
        session.close(); engine.dispose()

    def test_no_claims_or_promotion_policy_parameter_exists(self):
        """Structural proof this gate never grew a claims/promotion-policy
        dependency: record_reviewer_action()'s own signature has no such
        parameter."""
        import inspect

        params = set(inspect.signature(record_reviewer_action).parameters)
        assert "claims" not in params
        assert "promotion_policy_decision" not in params


class TestOtherReviewerActionsUnaffected:
    """13, 14, 15: REJECT_SIGNAL/DEFER/NEEDS_MORE_EVIDENCE remain fully
    ungated - proven specifically against an UNGOVERNED (EB5
    INSUFFICIENT_IDENTITY) fixture, since every pre-existing test in this
    repository happened to use an already-identity-confirmed fixture and so
    could never have caught a newly-added identity requirement on these
    three actions."""

    @pytest.mark.parametrize("action", ["REJECT_SIGNAL", "DEFER", "NEEDS_MORE_EVIDENCE"])
    def test_generic_actions_succeed_without_any_identity_governance(self, action):
        engine, session = make_session()
        airport = _airport(session)
        assertion = _ungoverned_assertion(session, airport)

        recorded = record_reviewer_action(
            session, assertion, action=action, reason="Reviewer rationale.", reviewer="human:tester",
        )
        session.commit()
        assert recorded.action == action
        session.close(); engine.dispose()


class TestApprovalGatesUnchanged:
    """16, 17: at the time this MARK_DUPLICATE mission was built,
    APPROVE_SIGNAL/CONFIRM_DISTINCT_SIGNAL's own raw-column checks were
    deliberately left untouched (explicitly out of scope for THAT mission).
    A LATER, separate mission ("RWI - Raw-vs-Effective APPROVE_SIGNAL Gate
    - Narrow Fix") closed that exact inconsistency by making these two
    actions ALSO consume EB5's effective decision, mirroring
    MARK_DUPLICATE's own gate below - so a legacy-attested (EB5-confirmed,
    raw-NULL) row now correctly SUCCEEDS here too, no longer permanently
    blocked merely because its raw column happens to be NULL by the legacy
    attestation mechanism's own design. The raw-confirmed shape still
    succeeds exactly as before (effective reduces to raw when no richer
    governance history exists)."""

    def test_approve_signal_now_succeeds_for_legacy_attested_effective_confirmed_row(self):
        engine, session = make_session()
        airport = _airport(session)
        assertion = _legacy_attested_assertion(session, airport)
        assertion.intelligence_review_decision = "REVIEW_REQUIRED"
        assertion.promotion_policy_decision = "HUMAN_REVIEW_REQUIRED"
        session.commit()
        assert assertion.identity_guard_decision is None  # raw column stays NULL by design

        action = record_reviewer_action(
            session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:tester",
        )
        session.commit()
        assert action.action == "APPROVE_SIGNAL"
        assert assertion.identity_guard_decision is None  # raw history never mutated
        session.close(); engine.dispose()

    def test_approve_signal_still_succeeds_for_raw_confirmed_fully_governed_row(self):
        engine, session = make_session()
        airport = _airport(session)
        assertion = _modern_assertion(
            session, airport, intelligence_review_decision="REVIEW_REQUIRED",
            promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
        )
        action = record_reviewer_action(
            session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:tester",
        )
        session.commit()
        assert action.action == "APPROVE_SIGNAL"
        session.close(); engine.dispose()


class TestRefusalMessageIdentifiesPrerequisite:
    def test_refusal_message_names_effective_identity(self):
        engine, session = make_session()
        airport = _airport(session)
        existing = _signal(session, airport)
        assertion = _ungoverned_assertion(session, airport)

        with pytest.raises(ValueError) as excinfo:
            record_reviewer_action(
                session, assertion, action="MARK_DUPLICATE", reason="x", reviewer="human:tester",
                duplicate_of_signal_id=existing.id,
            )
        message = str(excinfo.value)
        assert "effective identity decision" in message
        assert "ATTACH_CONFIRMED" in message
        assert "INSUFFICIENT_IDENTITY" in message
        session.close(); engine.dispose()
