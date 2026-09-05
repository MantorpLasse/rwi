"""Tests for the lightweight known-Airport funding human-review and
Signal-promotion services (RWI HQ "Funding Human Review Gate - Slice B"):

    app/services/known_airport_funding_lightweight_path_guard.py
    app/services/known_airport_funding_reviewer_action.py
    app/services/known_airport_funding_signal_creation.py

Every test uses an isolated in-memory SQLite database and synthetic
fixtures only - never the real data/runway_safe.db, never the real
SBP/MHT/SDF records. Modeled on tests/test_reviewer_action_persistence.py's
and tests/test_governed_signal_creation_reconciliation.py's own established
patterns.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, ReviewerAction, Runway, Signal, Source, SourceAssertion
from app.services.existing_signal_reconciliation import ExistingSignalReconciliationOutcome
from app.services.governed_signal_creation import (
    ExistingSignalPossibleMatchError,
    link_source_assertion_to_duplicate_signal,
)
from app.services.known_airport_funding_lightweight_path_guard import (
    FUNDING_SOURCE_NAMESPACE_PREFIXES,
    NotLightweightFundingAssertionError,
    check_lightweight_funding_path_eligibility,
)
from app.services.known_airport_funding_reviewer_action import (
    LIGHTWEIGHT_FUNDING_REVIEWER_ACTIONS,
    get_latest_reviewer_action,
    record_lightweight_funding_reviewer_action,
)
from app.services.known_airport_funding_signal_creation import (
    create_signal_from_lightweight_funding_review,
)

POSSIBLE = ExistingSignalReconciliationOutcome.POSSIBLE_EXISTING_SIGNAL_MATCH
_BASE_FIELDS = dict(title="Proposed funding signal", category="replacement", confidence="medium")


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def _airport(session, name="Test Airport", code="ZZZ") -> Airport:
    airport = Airport(name=name, iata_code=code, country="USA")
    session.add(airport)
    session.commit()
    return airport


_source_counter = iter(range(1_000_000))


_NO_DEFAULT = object()


def _source(session, title="USAspending grant: Test Recipient", external_id=_NO_DEFAULT) -> Source:
    """Defaults to a real "usaspending:" namespace external_id (RWI HQ
    "Lightweight Funding Eligibility Hardening") - every existing call site
    in this file represents evidence that SHOULD be eligible for the
    lightweight path, matching this helper's own pre-existing
    USAspending-shaped default title. Pass `external_id=None` (missing),
    `external_id=""` (empty), or a "discovery:"-prefixed string explicitly
    to build a fixture the namespace check must reject."""
    if external_id is _NO_DEFAULT:
        external_id = f"usaspending:test-fixture-{next(_source_counter)}"
    source = Source(title=title, source_type="usaspending_grant", external_id=external_id)
    session.add(source)
    session.commit()
    return source


_counter = iter(range(1_000_000))


def _lightweight_assertion(session, source, airport, **overrides) -> SourceAssertion:
    """The exact field shape
    app.services.known_airport_evidence_persistence.apply_known_airport_evidence_persistence()
    guarantees for assertion_type='project_construction' - see that
    module's own 'FIELD SEMANTICS' docstring section."""
    kwargs = dict(
        source=source,
        airport=airport,
        assertion_type="project_construction",
        source_record_identifier=f"test-fixture-{next(_counter)}",
        raw_relevant_text="RECONSTRUCTS EXISTING RUNWAY ENGINEERED MATERIAL ARRESTING SYSTEM. Award $3,370,194.",
        evidence_quality="unverified_candidate",
        review_state="unreviewed",
        identity_guard_decision=None,
        identity_guard_reason=None,
        intelligence_review_decision=None,
        intelligence_review_reason=None,
        promotion_policy_decision=None,
        promotion_policy_reason=None,
    )
    kwargs.update(overrides)
    assertion = SourceAssertion(**kwargs)
    session.add(assertion)
    session.commit()
    return assertion


def _signal(session, airport, **kwargs) -> Signal:
    kwargs.setdefault("title", "Existing signal")
    kwargs.setdefault("category", "replacement")
    kwargs.setdefault("confidence", "medium")
    kwargs.setdefault("published", False)
    signal = Signal(airport_id=airport.id, **kwargs)
    session.add(signal)
    session.commit()
    return signal


def _approve(session, assertion, **kwargs):
    action = record_lightweight_funding_reviewer_action(
        session, assertion, action="APPROVE_SIGNAL", reason="Human reviewed and approved.",
        reviewer="human:reviewer@example.test", **kwargs,
    )
    session.commit()
    return action


# --- 1. APPROVE_SIGNAL on a valid lightweight row ---------------------------


def test_lightweight_row_can_receive_approve_signal():
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session)
    assertion = _lightweight_assertion(session, source, airport)

    action = _approve(session, assertion)

    assert action.action == "APPROVE_SIGNAL"
    assert action.source_assertion_id == assertion.id
    assert get_latest_reviewer_action(session, assertion.id).id == action.id
    session.close(); engine.dispose()


# --- 2. MARK_DUPLICATE with an existing Signal -------------------------------


def test_lightweight_row_can_receive_mark_duplicate_with_existing_signal():
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session)
    existing = _signal(session, airport)
    assertion = _lightweight_assertion(session, source, airport)

    action = record_lightweight_funding_reviewer_action(
        session, assertion, action="MARK_DUPLICATE", reason="Same effort as existing Signal.",
        reviewer="human:reviewer@example.test", duplicate_of_signal_id=existing.id,
    )
    session.commit()

    assert action.action == "MARK_DUPLICATE"
    assert action.duplicate_of_signal_id == existing.id
    session.close(); engine.dispose()


# --- 3. MARK_DUPLICATE without duplicate_of_signal_id fails ------------------


def test_mark_duplicate_without_duplicate_of_signal_id_fails():
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session)
    assertion = _lightweight_assertion(session, source, airport)

    with pytest.raises(ValueError, match="MARK_DUPLICATE requires duplicate_of_signal_id"):
        record_lightweight_funding_reviewer_action(
            session, assertion, action="MARK_DUPLICATE", reason="x", reviewer="human:reviewer@example.test",
        )
    session.close(); engine.dispose()


# --- 4/5. NEEDS_MORE_EVIDENCE / REJECT_SIGNAL create no Signal ---------------


def test_needs_more_evidence_creates_no_signal():
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session)
    assertion = _lightweight_assertion(session, source, airport)
    before = session.query(Signal).count()

    action = record_lightweight_funding_reviewer_action(
        session, assertion, action="NEEDS_MORE_EVIDENCE", reason="Need more context.",
        reviewer="human:reviewer@example.test",
    )
    session.commit()

    assert action.action == "NEEDS_MORE_EVIDENCE"
    assert session.query(Signal).count() == before
    assert assertion.signal_id is None
    session.close(); engine.dispose()


def test_reject_signal_creates_no_signal():
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session)
    assertion = _lightweight_assertion(session, source, airport)
    before = session.query(Signal).count()

    action = record_lightweight_funding_reviewer_action(
        session, assertion, action="REJECT_SIGNAL", reason="Not relevant.",
        reviewer="human:reviewer@example.test",
    )
    session.commit()

    assert action.action == "REJECT_SIGNAL"
    assert session.query(Signal).count() == before
    assert assertion.signal_id is None
    session.close(); engine.dispose()


# --- 6. airport_id=NULL fails closed for approval/promotion -----------------


def test_airport_id_null_fails_closed_for_approval_and_promotion():
    engine, session = make_session()
    source = _source(session)
    assertion = _lightweight_assertion(session, source, None)

    with pytest.raises(NotLightweightFundingAssertionError):
        record_lightweight_funding_reviewer_action(
            session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:reviewer@example.test",
        )
    with pytest.raises(NotLightweightFundingAssertionError):
        create_signal_from_lightweight_funding_review(session, assertion, **_BASE_FIELDS)
    assert session.query(Signal).count() == 0
    session.close(); engine.dispose()


# --- 7. heavy-pipeline-shaped row cannot enter the lightweight path ---------


def test_heavy_pipeline_shaped_row_cannot_enter_lightweight_path():
    """The real SA222/MSP shape: identity_guard_decision/intelligence_review_decision/
    promotion_policy_decision all set - exactly the row shape this guard must
    refuse, per the recon mission's own explicit warning."""
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session)
    assertion = _lightweight_assertion(
        session, source, airport,
        identity_guard_decision="ATTACH_CONFIRMED",
        intelligence_review_decision="REVIEW_REQUIRED",
        promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
    )

    with pytest.raises(NotLightweightFundingAssertionError) as excinfo:
        check_lightweight_funding_path_eligibility(assertion, source_external_id=source.external_id)
    assert excinfo.value.field == "identity_guard_decision"

    with pytest.raises(NotLightweightFundingAssertionError):
        record_lightweight_funding_reviewer_action(
            session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:reviewer@example.test",
        )
    with pytest.raises(NotLightweightFundingAssertionError):
        create_signal_from_lightweight_funding_review(session, assertion, **_BASE_FIELDS)
    assert session.query(Signal).count() == 0
    session.close(); engine.dispose()


# --- 8. altered/noncanonical lightweight field shape fails closed -----------


def test_noncanonical_evidence_quality_fails_closed():
    """Mirrors the real SourceAssertion 76-100 shape found in the production
    database during this mission's own recon: assertion_type='project_construction',
    airport_id set, all three governance columns NULL, but
    evidence_quality='direct_strong' (never the KAR/stage-only default
    'unverified_candidate') - proof the guard's evidence_quality check is
    load-bearing, not redundant with the governance-column checks."""
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session)
    assertion = _lightweight_assertion(session, source, airport, evidence_quality="direct_strong")

    with pytest.raises(NotLightweightFundingAssertionError) as excinfo:
        check_lightweight_funding_path_eligibility(assertion, source_external_id=source.external_id)
    assert excinfo.value.field == "evidence_quality"
    session.close(); engine.dispose()


def test_wrong_assertion_type_fails_closed():
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session)
    assertion = _lightweight_assertion(session, source, airport, assertion_type="airport_inventory")

    with pytest.raises(NotLightweightFundingAssertionError) as excinfo:
        check_lightweight_funding_path_eligibility(assertion, source_external_id=source.external_id)
    assert excinfo.value.field == "assertion_type"
    session.close(); engine.dispose()


# --- 9. APPROVE_SIGNAL + CLEAR_TO_CREATE creates exactly one unpublished Signal --


def test_approve_and_clear_to_create_creates_exactly_one_unpublished_signal():
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session)
    assertion = _lightweight_assertion(session, source, airport)
    _approve(session, assertion)

    result = create_signal_from_lightweight_funding_review(session, assertion, **_BASE_FIELDS)
    session.commit()

    assert result.created is True
    assert result.signal.published is False
    assert result.signal.airport_id == airport.id
    assert assertion.signal_id == result.signal.id
    assert session.query(Signal).count() == 1
    session.close(); engine.dispose()


# --- 10. POSSIBLE_EXISTING_SIGNAL_MATCH creates no Signal -------------------


def test_possible_existing_match_creates_no_signal():
    engine, session = make_session()
    airport = _airport(session)
    runway = Runway(airport_id=airport.id, designation="11/29")
    session.add(runway)
    session.commit()
    existing = _signal(session, airport, runway_id=runway.id)
    source = _source(session)
    assertion = _lightweight_assertion(session, source, airport, runway_id=runway.id)
    _approve(session, assertion)

    signals_before = session.query(Signal).count()
    with pytest.raises(ExistingSignalPossibleMatchError) as excinfo:
        create_signal_from_lightweight_funding_review(session, assertion, **_BASE_FIELDS)

    assert excinfo.value.decision.outcome == POSSIBLE
    assert excinfo.value.decision.candidate_signal_ids == (existing.id,)
    assert session.query(Signal).count() == signals_before
    assert assertion.signal_id is None
    session.close(); engine.dispose()


# --- 11/12. Replay idempotency / conflicting replay -------------------------


def test_replay_of_identical_promotion_reuses_existing_signal():
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session)
    assertion = _lightweight_assertion(session, source, airport)
    _approve(session, assertion)

    first = create_signal_from_lightweight_funding_review(session, assertion, **_BASE_FIELDS)
    session.commit()
    second = create_signal_from_lightweight_funding_review(session, assertion, **_BASE_FIELDS)
    session.commit()

    assert first.created is True
    assert second.created is False
    assert second.signal.id == first.signal.id
    assert session.query(Signal).count() == 1
    session.close(); engine.dispose()


def test_conflicting_replay_fails_rather_than_overwriting():
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session)
    assertion = _lightweight_assertion(session, source, airport)
    _approve(session, assertion)

    create_signal_from_lightweight_funding_review(session, assertion, **_BASE_FIELDS)
    session.commit()
    signals_before = session.query(Signal).count()

    with pytest.raises(ValueError, match="refusing to silently overwrite"):
        create_signal_from_lightweight_funding_review(
            session, assertion, title="A different title", category="replacement", confidence="medium",
        )
    assert session.query(Signal).count() == signals_before
    session.close(); engine.dispose()


# --- 13. No dollar/value fields accepted or populated -----------------------


def test_no_dollar_value_fields_accepted_or_populated():
    import inspect

    sig = inspect.signature(create_signal_from_lightweight_funding_review)
    assert "estimated_total_value_usd" not in sig.parameters
    assert "estimated_emas_value_usd" not in sig.parameters
    assert "likely_supplier" not in sig.parameters
    assert "supplier" not in sig.parameters

    engine, session = make_session()
    airport = _airport(session)
    source = _source(session)
    # raw_relevant_text (default fixture text) mentions a dollar amount -
    # proves nothing is auto-extracted from it.
    assertion = _lightweight_assertion(session, source, airport)
    _approve(session, assertion)

    result = create_signal_from_lightweight_funding_review(session, assertion, **_BASE_FIELDS)
    session.commit()

    assert result.signal.estimated_total_value_usd is None
    assert result.signal.estimated_emas_value_usd is None
    assert result.signal.supplier is None
    assert result.signal.likely_supplier is None
    session.close(); engine.dispose()


# --- 14. MARK_DUPLICATE stays compatible with the EXISTING duplicate-link service --


def test_mark_duplicate_compatible_with_existing_duplicate_link_service_and_does_not_mutate_target():
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session)
    existing = _signal(session, airport, title="Original signal", notes="original notes")
    assertion = _lightweight_assertion(session, source, airport)

    before_snapshot = (existing.title, existing.category, existing.confidence, existing.notes, existing.published)

    record_lightweight_funding_reviewer_action(
        session, assertion, action="MARK_DUPLICATE", reason="Same effort.",
        reviewer="human:reviewer@example.test", duplicate_of_signal_id=existing.id,
    )
    session.commit()

    # The EXISTING, unmodified governed_signal_creation function - never a
    # new one built by this mission.
    result = link_source_assertion_to_duplicate_signal(session, assertion)
    session.commit()

    assert result.created is False
    assert result.signal.id == existing.id
    assert assertion.signal_id == existing.id
    after_snapshot = (existing.title, existing.category, existing.confidence, existing.notes, existing.published)
    assert after_snapshot == before_snapshot  # target Signal completely unchanged
    assert session.query(Signal).count() == 1  # no new Signal created
    session.close(); engine.dispose()


# --- 15. Append-only ReviewerAction history / supersession intact -----------


def test_append_only_history_and_supersession_intact():
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session)
    assertion = _lightweight_assertion(session, source, airport)

    first = record_lightweight_funding_reviewer_action(
        session, assertion, action="NEEDS_MORE_EVIDENCE", reason="Need more.", reviewer="human:reviewer@example.test",
    )
    session.commit()
    second = record_lightweight_funding_reviewer_action(
        session, assertion, action="APPROVE_SIGNAL", reason="Now approved.", reviewer="human:reviewer@example.test",
        supersedes_action_id=first.id,
    )
    session.commit()

    assert get_latest_reviewer_action(session, assertion.id).id == second.id
    assert session.query(ReviewerAction).count() == 2

    # Immutability - the SAME before_update/before_delete event listeners on
    # the ReviewerAction model, unmodified, apply to rows this module wrote.
    first.reason = "attempted edit"
    with pytest.raises(ValueError, match="immutable"):
        session.flush()
    session.rollback()
    session.close(); engine.dispose()


# --- 16-27. Funding-provenance namespace hardening (RWI HQ "Lightweight ----
# Funding Eligibility Hardening") ---------------------------------------


def test_faa_aip_namespace_accepted():
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session, external_id="faa_aip:https://www.faa.gov/x.pdf#ZZZ#deadbeef")
    assertion = _lightweight_assertion(session, source, airport)
    check_lightweight_funding_path_eligibility(assertion, source_external_id=source.external_id)  # does not raise
    session.close(); engine.dispose()


def test_usaspending_namespace_accepted():
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session, external_id="usaspending:CONT_AWD_TEST_1")
    assertion = _lightweight_assertion(session, source, airport)
    check_lightweight_funding_path_eligibility(assertion, source_external_id=source.external_id)  # does not raise
    session.close(); engine.dispose()


def test_discovery_namespace_rejected():
    """The real, confirmed SA258 defect this hardening fixes: Research
    Loop / Selection-KEEP-derived evidence uses the "discovery:" namespace
    and must never be admitted to the funding review path."""
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session, external_id="discovery:generic_web:deadbeef:cafef00d")
    assertion = _lightweight_assertion(session, source, airport)

    with pytest.raises(NotLightweightFundingAssertionError) as excinfo:
        check_lightweight_funding_path_eligibility(assertion, source_external_id=source.external_id)
    assert excinfo.value.field == "source.external_id"
    session.close(); engine.dispose()


def test_arbitrary_unknown_namespace_rejected():
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session, external_id="some_other_namespace:12345")
    assertion = _lightweight_assertion(session, source, airport)

    with pytest.raises(NotLightweightFundingAssertionError) as excinfo:
        check_lightweight_funding_path_eligibility(assertion, source_external_id=source.external_id)
    assert excinfo.value.field == "source.external_id"
    session.close(); engine.dispose()


def test_missing_source_external_id_rejected():
    """Passed explicitly as None - e.g. a Source row that was never given
    an external_id at all."""
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session, external_id=None)
    assertion = _lightweight_assertion(session, source, airport)

    with pytest.raises(NotLightweightFundingAssertionError) as excinfo:
        check_lightweight_funding_path_eligibility(assertion, source_external_id=None)
    assert excinfo.value.field == "source.external_id"
    session.close(); engine.dispose()


def test_empty_string_external_id_rejected():
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session, external_id="")
    assertion = _lightweight_assertion(session, source, airport)

    with pytest.raises(NotLightweightFundingAssertionError) as excinfo:
        check_lightweight_funding_path_eligibility(assertion, source_external_id="")
    assert excinfo.value.field == "source.external_id"
    session.close(); engine.dispose()


def test_missing_source_object_rejected_via_none():
    """A caller resolving `source_assertion.source` and finding None (a
    theoretically-orphaned assertion) must pass None through, not fabricate
    a value - and None is rejected identically to an empty string."""
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session)
    assertion = _lightweight_assertion(session, source, airport)

    with pytest.raises(NotLightweightFundingAssertionError):
        check_lightweight_funding_path_eligibility(assertion, source_external_id=None)
    session.close(); engine.dispose()


def test_all_existing_assertion_shape_checks_still_enforced_alongside_namespace():
    """The namespace check is ADDITIVE - a row with a valid funding
    namespace but a violated assertion-shape field must still fail, on
    that field, not silently pass because the namespace happened to be
    valid."""
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session)  # valid usaspending: namespace
    assertion = _lightweight_assertion(session, source, airport, evidence_quality="direct_strong")

    with pytest.raises(NotLightweightFundingAssertionError) as excinfo:
        check_lightweight_funding_path_eligibility(assertion, source_external_id=source.external_id)
    assert excinfo.value.field == "evidence_quality"
    session.close(); engine.dispose()


def test_sa258_shaped_fixture_rejected_end_to_end():
    """Reproduces the real, confirmed production defect exactly: a
    known-airport-staged, assertion_type='project_construction' row from
    the "discovery:" namespace (Selection/KEEP -> persist_selected_fragments
    --known-airport-id shape) must be refused by BOTH lightweight services,
    not merely by the bare guard function."""
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session, title="Discovered web page", external_id="discovery:generic_web:abc:def")
    assertion = _lightweight_assertion(session, source, airport)

    with pytest.raises(NotLightweightFundingAssertionError):
        record_lightweight_funding_reviewer_action(
            session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:reviewer@example.test",
        )
    with pytest.raises(NotLightweightFundingAssertionError):
        create_signal_from_lightweight_funding_review(session, assertion, **_BASE_FIELDS)
    assert session.query(ReviewerAction).count() == 0
    assert session.query(Signal).count() == 0
    session.close(); engine.dispose()


def test_sa255_sa256_sa257_shaped_fixtures_accepted():
    """FAA-AIP-namespaced, known-airport-staged funding evidence (the real
    SA255/256/257 shape) remains fully accepted through both services."""
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session, title="AIP grant: test", external_id="faa_aip:https://www.faa.gov/x.pdf#ZZZ#deadbeef")
    assertion = _lightweight_assertion(session, source, airport)

    action = _approve(session, assertion)
    assert action.action == "APPROVE_SIGNAL"

    result = create_signal_from_lightweight_funding_review(session, assertion, **_BASE_FIELDS)
    session.commit()
    assert result.created is True
    assert result.signal.published is False
    session.close(); engine.dispose()


def test_both_services_use_the_same_hardened_gate_no_bypass():
    """AST-level: both callers must import check_lightweight_funding_path_eligibility
    from the SAME guard module - never a second, parallel eligibility
    implementation that could silently omit the namespace check."""
    import ast
    import inspect

    from app.services import known_airport_funding_reviewer_action as reviewer_module
    from app.services import known_airport_funding_signal_creation as signal_module

    for module in (reviewer_module, signal_module):
        tree = ast.parse(inspect.getsource(module))
        imports = [
            alias.name
            for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module == "app.services.known_airport_funding_lightweight_path_guard"
            for alias in node.names
        ]
        assert "check_lightweight_funding_path_eligibility" in imports


def test_no_db_mutation_during_eligibility_check():
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session)
    assertion = _lightweight_assertion(session, source, airport)

    before_sa = (assertion.id, assertion.signal_id, assertion.review_state)
    before_counts = (session.query(Source).count(), session.query(SourceAssertion).count(), session.query(Signal).count(), session.query(ReviewerAction).count())

    with pytest.raises(NotLightweightFundingAssertionError):
        check_lightweight_funding_path_eligibility(assertion, source_external_id="discovery:x")
    check_lightweight_funding_path_eligibility(assertion, source_external_id=source.external_id)

    after_sa = (assertion.id, assertion.signal_id, assertion.review_state)
    after_counts = (session.query(Source).count(), session.query(SourceAssertion).count(), session.query(Signal).count(), session.query(ReviewerAction).count())
    assert before_sa == after_sa
    assert before_counts == after_counts
    session.close(); engine.dispose()


def test_namespace_prefixes_are_exactly_the_two_evidenced_values():
    assert FUNDING_SOURCE_NAMESPACE_PREFIXES == ("faa_aip:", "usaspending:")
