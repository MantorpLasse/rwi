"""Tests for "RWI - Signal Publication Governance - Design + Implementation":
`app.models.signal_publication_action.SignalPublicationAction` and
`app.services.signal_publication` (`evaluate_publication_eligibility()`,
`publish_signal()`, `unpublish_signal()`, `get_latest_signal_publication_action()`,
`record_signal_publication_action()`).

Every test uses an isolated in-memory SQLite database - data/runway_safe.db
is never opened here. Fixture helpers (`_airport`, `_source`,
`_governed_assertion`, `_sa235_shaped`, `_approve`) are copied verbatim from
tests/test_governed_signal_creation_raw_vs_effective_identity_gate.py, the
established precedent for building a governed SA235-shaped chain in an
isolated session.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, ReviewerAction, Signal, Source, SourceAssertion
from app.models.airport_alias import AirportAlias
from app.models.signal_publication_action import SignalPublicationAction
from app.services.cross_source_alias_attestation import record_cross_source_alias_attestation
from app.services.governed_signal_creation import create_signal_from_approved_review
from app.services.reviewer_action_persistence import record_reviewer_action
from app.services.signal_publication import (
    evaluate_publication_eligibility,
    get_latest_signal_publication_action,
    publish_signal,
    record_signal_publication_action,
    unpublish_signal,
)


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


def _sa235_shaped(session, *, source_url=None):
    """raw ATTACH_PROVISIONAL, effective ATTACH_CONFIRMED via
    CrossSourceAliasAttestation - the central Signal69-shaped fixture."""
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

    council_source = _source(session, title="Independent council", url=source_url)
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


def _approve(session, assertion, **kwargs) -> ReviewerAction:
    action = record_reviewer_action(
        session, assertion, action="APPROVE_SIGNAL", reason="Effectively confirmed identity, human-approved.",
        reviewer="human:tester", **kwargs,
    )
    session.commit()
    return action


_SIGNAL_FIELDS = dict(
    title="Test Airport EMAS installation", category="new_installation", confidence="medium", status="identified",
)


def _signal69_shaped(session, *, source_url="https://example.gov/council-record"):
    """The exact real-Signal69 shape: governed SourceAssertion (raw
    ATTACH_PROVISIONAL, effective ATTACH_CONFIRMED via
    CrossSourceAliasAttestation), APPROVE_SIGNAL, and one Signal created
    through create_signal_from_approved_review() (published=False,
    category=new_installation, confidence=medium, status=identified) -
    Phase 11's own synthetic fixture. Returns (assertion, signal)."""
    assertion = _sa235_shaped(session, source_url=source_url)
    _approve(session, assertion)
    result = create_signal_from_approved_review(session, assertion, **_SIGNAL_FIELDS)
    session.commit()
    return assertion, result.signal


def _legacy_signal(session, **overrides) -> Signal:
    """A Signal with no linked SourceAssertion at all - the shape every
    pre-existing legacy writer (signal_rules.py, the five scripts/*.py
    importers) produces. published defaults True (the Slice 9A model
    default), matching every real legacy row's own actual value."""
    kwargs = dict(
        airport=_airport(session, name="Legacy Airport"), title="Legacy signal",
        category="replacement", confidence="medium", status="identified",
    )
    kwargs.update(overrides)
    signal = Signal(**kwargs)
    session.add(signal); session.commit()
    return signal


# ---------------------------------------------------------------------------
# Model / immutability
# ---------------------------------------------------------------------------

class TestModelImmutability:
    def test_action_check_constraint_rejects_unknown_value(self):
        engine, session = make_session()
        signal = _legacy_signal(session)
        record = SignalPublicationAction(
            signal_id=signal.id, action="DELETE_EVERYTHING", reviewer="human:tester", reason="bad",
        )
        session.add(record)
        with pytest.raises(Exception):
            session.commit()
        session.rollback()
        session.close(); engine.dispose()

    def test_update_is_refused(self):
        engine, session = make_session()
        signal = _legacy_signal(session, published=False)
        record = record_signal_publication_action(
            session, signal, action="PUBLISH", reviewer="human:tester", reason="test",
        )
        session.commit()
        record.reason = "changed my mind"
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
        session.rollback()
        session.close(); engine.dispose()

    def test_delete_is_refused(self):
        engine, session = make_session()
        signal = _legacy_signal(session, published=False)
        record = record_signal_publication_action(
            session, signal, action="PUBLISH", reviewer="human:tester", reason="test",
        )
        session.commit()
        session.delete(record)
        with pytest.raises(ValueError, match="cannot be deleted"):
            session.commit()
        session.rollback()
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# record_signal_publication_action() validation
# ---------------------------------------------------------------------------

class TestRecordValidation:
    def test_blank_reviewer_refused(self):
        engine, session = make_session()
        signal = _legacy_signal(session, published=False)
        with pytest.raises(ValueError, match="reviewer"):
            record_signal_publication_action(session, signal, action="PUBLISH", reviewer="   ", reason="test")
        session.close(); engine.dispose()

    def test_blank_reason_refused(self):
        engine, session = make_session()
        signal = _legacy_signal(session, published=False)
        with pytest.raises(ValueError, match="reason"):
            record_signal_publication_action(session, signal, action="PUBLISH", reviewer="human:tester", reason="")
        session.close(); engine.dispose()

    def test_unknown_action_refused(self):
        engine, session = make_session()
        signal = _legacy_signal(session, published=False)
        with pytest.raises(ValueError, match="action must be one of"):
            record_signal_publication_action(session, signal, action="NUKE", reviewer="human:tester", reason="test")
        session.close(); engine.dispose()

    def test_supersedes_must_belong_to_same_signal(self):
        engine, session = make_session()
        s1 = _legacy_signal(session, published=False)
        s2 = _legacy_signal(session, published=False)
        other_action = record_signal_publication_action(
            session, s2, action="PUBLISH", reviewer="human:tester", reason="test",
        )
        session.commit()
        with pytest.raises(ValueError, match="same Signal"):
            record_signal_publication_action(
                session, s1, action="PUBLISH", reviewer="human:tester", reason="test",
                supersedes_action_id=other_action.id,
            )
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Signal69-shaped synthetic case (Phase 11)
# ---------------------------------------------------------------------------

class TestSignal69ShapedPublication:
    def test_eligibility_passes(self):
        engine, session = make_session()
        _assertion, signal = _signal69_shaped(session)
        decision = evaluate_publication_eligibility(session, signal)
        assert decision.eligible is True
        assert decision.blockers == ()
        session.close(); engine.dispose()

    def test_publish_flips_state_and_appends_history(self):
        engine, session = make_session()
        assertion, signal = _signal69_shaped(session)
        assert signal.published is False
        before_count = session.query(SignalPublicationAction).count()

        result = publish_signal(session, signal, reviewer="human:lkarlsson@gmail.com", reason="Governed review approved.")

        assert result.changed is True
        assert signal.published is True
        assert session.query(SignalPublicationAction).count() == before_count + 1
        assert result.action.action == "PUBLISH"
        assert result.action.reviewer == "human:lkarlsson@gmail.com"
        assert result.action.signal_id == signal.id
        # Category/confidence/status/title/source_id untouched by publication.
        assert signal.category == "new_installation"
        assert signal.confidence == "medium"
        assert signal.status == "identified"
        assert signal.source_id == assertion.source_id
        session.rollback()  # no real write - this is a synthetic in-memory fixture
        session.close(); engine.dispose()

    def test_static_exporter_predicate_flips_with_publish(self):
        from app.static_export.build import _is_public_signal
        engine, session = make_session()
        _assertion, signal = _signal69_shaped(session)
        assert _is_public_signal(signal) is False
        publish_signal(session, signal, reviewer="human:tester", reason="test")
        assert _is_public_signal(signal) is True
        session.rollback()
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Idempotency (Phase 8)
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_publish_already_published_is_a_safe_noop(self):
        engine, session = make_session()
        _assertion, signal = _signal69_shaped(session)
        first = publish_signal(session, signal, reviewer="human:tester", reason="first publish")
        assert first.changed is True
        count_after_first = session.query(SignalPublicationAction).count()

        second = publish_signal(session, signal, reviewer="human:someone-else", reason="repeat call")
        assert second.changed is False
        assert second.action.id == first.action.id  # same row, not a new one
        assert session.query(SignalPublicationAction).count() == count_after_first
        session.close(); engine.dispose()

    def test_unpublish_already_unpublished_is_a_safe_noop(self):
        engine, session = make_session()
        _assertion, signal = _signal69_shaped(session)
        assert signal.published is False
        result = unpublish_signal(session, signal, reviewer="human:tester", reason="already off")
        assert result.changed is False
        assert result.action is None  # never published, so no history exists yet
        assert session.query(SignalPublicationAction).count() == 0
        session.close(); engine.dispose()

    def test_idempotent_publish_still_validates_reviewer_and_reason(self):
        engine, session = make_session()
        _assertion, signal = _signal69_shaped(session)
        publish_signal(session, signal, reviewer="human:tester", reason="first")
        with pytest.raises(ValueError, match="reviewer"):
            publish_signal(session, signal, reviewer="  ", reason="second")
        session.close(); engine.dispose()

    def test_publish_then_unpublish_then_publish_appends_three_rows(self):
        engine, session = make_session()
        _assertion, signal = _signal69_shaped(session)
        r1 = publish_signal(session, signal, reviewer="human:a", reason="publish")
        r2 = unpublish_signal(session, signal, reviewer="human:b", reason="retract")
        r3 = publish_signal(session, signal, reviewer="human:c", reason="republish")
        assert [r1.changed, r2.changed, r3.changed] == [True, True, True]
        assert session.query(SignalPublicationAction).count() == 3
        assert signal.published is True
        assert r2.action.supersedes_action_id == r1.action.id
        assert r3.action.supersedes_action_id == r2.action.id
        # No prior row was mutated or removed.
        assert session.get(SignalPublicationAction, r1.action.id).action == "PUBLISH"
        assert session.get(SignalPublicationAction, r2.action.id).action == "UNPUBLISH"
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Negative eligibility matrix (Phase 12)
# ---------------------------------------------------------------------------

class TestNegativeEligibilityMatrix:
    def test_no_linked_source_assertion_refused(self):
        engine, session = make_session()
        signal = _legacy_signal(session, published=False)
        decision = evaluate_publication_eligibility(session, signal)
        assert decision.eligible is False
        assert any("no linked SourceAssertion" in b for b in decision.blockers)
        with pytest.raises(ValueError, match="no linked SourceAssertion"):
            publish_signal(session, signal, reviewer="human:tester", reason="test")
        session.close(); engine.dispose()

    def test_effective_identity_not_confirmed_refused(self):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        assertion = _governed_assertion(session, airport, source, identity_guard_decision="ATTACH_PROVISIONAL")
        # Bypass record_reviewer_action()'s own (already-fixed) identity gate
        # to isolate this module's own gate, exactly like
        # test_governed_signal_creation_raw_vs_effective_identity_gate.py's
        # own negative matrix.
        ra = ReviewerAction(source_assertion_id=assertion.id, action="APPROVE_SIGNAL", reason="x", reviewer="human:t")
        session.add(ra); session.commit()
        signal = Signal(airport=airport, **_SIGNAL_FIELDS, published=False, source_id=source.id)
        session.add(signal); session.flush()
        assertion.signal_id = signal.id
        session.commit()

        decision = evaluate_publication_eligibility(session, signal)
        assert decision.eligible is False
        assert any("effective identity decision" in b for b in decision.blockers)
        session.close(); engine.dispose()

    @pytest.mark.parametrize("bad_review", ["INSUFFICIENT_MATERIALITY", "CONTRADICTED", None])
    def test_missing_or_wrong_intelligence_review_refused(self, bad_review):
        engine, session = make_session()
        assertion, signal = _signal69_shaped(session)
        assertion.intelligence_review_decision = bad_review
        session.commit()
        decision = evaluate_publication_eligibility(session, signal)
        assert decision.eligible is False
        assert any("intelligence_review_decision" in b for b in decision.blockers)
        session.close(); engine.dispose()

    @pytest.mark.parametrize("bad_promo", ["AUTO_ELIGIBLE", "DO_NOT_PROMOTE", None])
    def test_missing_or_wrong_promotion_policy_refused(self, bad_promo):
        engine, session = make_session()
        assertion, signal = _signal69_shaped(session)
        assertion.promotion_policy_decision = bad_promo
        session.commit()
        decision = evaluate_publication_eligibility(session, signal)
        assert decision.eligible is False
        assert any("promotion_policy_decision" in b for b in decision.blockers)
        session.close(); engine.dispose()

    def test_no_reviewer_action_refused(self):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        assertion = _governed_assertion(session, airport, source)
        signal = Signal(airport=airport, **_SIGNAL_FIELDS, published=False, source_id=source.id)
        session.add(signal); session.flush()
        assertion.signal_id = signal.id
        session.commit()
        decision = evaluate_publication_eligibility(session, signal)
        assert decision.eligible is False
        assert any("latest ReviewerAction is None" in b for b in decision.blockers)
        session.close(); engine.dispose()

    @pytest.mark.parametrize("wrong_action", ["REJECT_SIGNAL", "DEFER", "NEEDS_MORE_EVIDENCE"])
    def test_latest_reviewer_action_not_approving_refused(self, wrong_action):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        assertion = _governed_assertion(session, airport, source)
        signal = Signal(airport=airport, **_SIGNAL_FIELDS, published=False, source_id=source.id)
        session.add(signal); session.flush()
        assertion.signal_id = signal.id
        session.commit()
        ra = ReviewerAction(source_assertion_id=assertion.id, action=wrong_action, reason="x", reviewer="human:t")
        session.add(ra); session.commit()
        decision = evaluate_publication_eligibility(session, signal)
        assert decision.eligible is False
        assert any("latest ReviewerAction is" in b for b in decision.blockers)
        session.close(); engine.dispose()

    def test_empty_title_refused(self):
        engine, session = make_session()
        _assertion, signal = _signal69_shaped(session)
        signal.title = "   "
        session.commit()
        decision = evaluate_publication_eligibility(session, signal)
        assert decision.eligible is False
        assert any("title is empty" in b for b in decision.blockers)
        session.close(); engine.dispose()

    def test_empty_category_refused(self):
        engine, session = make_session()
        _assertion, signal = _signal69_shaped(session)
        signal.category = ""
        session.commit()
        decision = evaluate_publication_eligibility(session, signal)
        assert decision.eligible is False
        assert any("category is empty" in b for b in decision.blockers)
        session.close(); engine.dispose()

    def test_invalid_confidence_refused(self):
        engine, session = make_session()
        _assertion, signal = _signal69_shaped(session)
        signal.confidence = "extremely_sure"
        session.commit()
        decision = evaluate_publication_eligibility(session, signal)
        assert decision.eligible is False
        assert any("signal.confidence" in b for b in decision.blockers)
        session.close(); engine.dispose()

    def test_empty_status_refused(self):
        engine, session = make_session()
        _assertion, signal = _signal69_shaped(session)
        signal.status = None
        session.commit()
        decision = evaluate_publication_eligibility(session, signal)
        assert decision.eligible is False
        assert any("status is empty" in b for b in decision.blockers)
        session.close(); engine.dispose()

    def test_broken_signal_source_assertion_linkage_multiple_blockers_collected(self):
        """Two independent SourceAssertions both point at the same Signal;
        one is fully governed and one is not - eligibility must fail closed
        and report the specific offending assertion, not silently pass
        because at least one assertion looked fine."""
        engine, session = make_session()
        assertion, signal = _signal69_shaped(session)
        airport = signal.airport
        bad_source = _source(session, title="Second, ungoverned source")
        bad_assertion = _governed_assertion(
            session, airport, bad_source, identity_guard_decision="ATTACH_PROVISIONAL",
        )
        ra = ReviewerAction(source_assertion_id=bad_assertion.id, action="APPROVE_SIGNAL", reason="x", reviewer="human:t")
        session.add(ra); session.commit()
        bad_assertion.signal_id = signal.id
        session.commit()

        decision = evaluate_publication_eligibility(session, signal)
        assert decision.eligible is False
        assert len(decision.checked_source_assertion_ids) == 2
        assert any(f"SourceAssertion #{bad_assertion.id}" in b for b in decision.blockers)
        session.close(); engine.dispose()

    def test_broken_source_reference_refused(self):
        engine, session = make_session()
        _assertion, signal = _signal69_shaped(session)
        signal.source_id = 999999  # does not exist
        session.commit()
        decision = evaluate_publication_eligibility(session, signal)
        assert decision.eligible is False
        assert any("does not reference an existing Source" in b for b in decision.blockers)
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Source provenance preservation (Phase 5)
# ---------------------------------------------------------------------------

class TestSourceProvenance:
    def test_publish_never_touches_source_id_or_url(self):
        engine, session = make_session()
        assertion, signal = _signal69_shaped(session, source_url="https://example.gov/real-record")
        source_id_before = signal.source_id
        source = session.get(Source, source_id_before)
        url_before = source.url

        publish_signal(session, signal, reviewer="human:tester", reason="test")

        assert signal.source_id == source_id_before
        assert session.get(Source, source_id_before).url == url_before == "https://example.gov/real-record"
        session.close(); engine.dispose()

    def test_source_with_no_url_is_not_a_blocker(self):
        engine, session = make_session()
        _assertion, signal = _signal69_shaped(session, source_url=None)
        decision = evaluate_publication_eligibility(session, signal)
        assert decision.eligible is True
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Legacy Signal safety (Phase 10)
# ---------------------------------------------------------------------------

class TestLegacySignalSafety:
    def test_legacy_signal_untouched_by_migration_or_service_by_default(self):
        engine, session = make_session()
        legacy = _legacy_signal(session)  # published defaults True
        assert legacy.published is True
        assert session.query(SignalPublicationAction).filter_by(signal_id=legacy.id).count() == 0

        decision = evaluate_publication_eligibility(session, legacy)
        assert decision.eligible is False
        assert decision.blockers == (
            "signal has no linked SourceAssertion (no SourceAssertion.signal_id points at this "
            "Signal) - this service governs publication only for Signals created through the "
            "governed discovery/intelligence pipeline "
            "(app.services.governed_signal_creation.create_signal_from_approved_review()); a "
            "legacy Signal with no governed evidence link is out of scope for this mechanism",
        )
        session.close(); engine.dispose()

    def test_publish_signal_refuses_legacy_signal_without_appending_any_row(self):
        engine, session = make_session()
        legacy = _legacy_signal(session, published=False)
        with pytest.raises(ValueError):
            publish_signal(session, legacy, reviewer="human:tester", reason="test")
        assert session.query(SignalPublicationAction).count() == 0
        assert legacy.published is False  # untouched
        session.close(); engine.dispose()

    def test_legacy_published_signal_is_never_reinterpreted_by_this_module(self):
        """A legacy Signal that is already published=True (the ordinary
        Slice 9A default) is never read, mutated, or given a manufactured
        publication-action row by anything in this mission - simply
        constructing it and never calling any publish_signal()/
        unpublish_signal() proves that."""
        engine, session = make_session()
        legacy = _legacy_signal(session)
        assert legacy.published is True
        assert get_latest_signal_publication_action(session, legacy.id) is None
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# get_latest_signal_publication_action()
# ---------------------------------------------------------------------------

class TestGetLatest:
    def test_returns_none_when_no_history(self):
        engine, session = make_session()
        _assertion, signal = _signal69_shaped(session)
        assert get_latest_signal_publication_action(session, signal.id) is None
        session.close(); engine.dispose()

    def test_returns_most_recent_row(self):
        engine, session = make_session()
        _assertion, signal = _signal69_shaped(session)
        r1 = publish_signal(session, signal, reviewer="human:a", reason="publish")
        r2 = unpublish_signal(session, signal, reviewer="human:b", reason="retract")
        latest = get_latest_signal_publication_action(session, signal.id)
        assert latest.id == r2.action.id
        assert latest.id != r1.action.id
        session.close(); engine.dispose()
