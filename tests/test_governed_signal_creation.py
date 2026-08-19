"""Tests for app/services/governed_signal_creation.py (Slice 9C,
docs/architecture/human-approved-governed-signal-creation-slice9c-report.md).

Every test uses an isolated in-memory SQLite database. Modeled on the
already-proven patterns in tests/test_reviewer_action_persistence.py and
tests/test_physical_installation_reconciliation.py.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Runway, Signal, Source, SourceAssertion
from app.services.reviewer_action_persistence import record_reviewer_action
from app.services.governed_signal_creation import (
    create_signal_from_approved_review,
    link_source_assertion_to_duplicate_signal,
)
from app.static_export import build_site


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def make_session_with_foreign_keys_enforced():
    """Like make_session(), but with PRAGMA foreign_keys=ON on every
    connection - mirroring app/database.py's own connect-event listener for
    the real engine, and tests/test_reviewer_action_persistence.py's own
    identically-named helper. Needed here specifically to verify DB-level
    FK-constraint behavior when deleting a Signal, matching production."""
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


def _msp_222_shape(
    session,
    *,
    identity_guard_decision="ATTACH_CONFIRMED",
    intelligence_review_decision="REVIEW_REQUIRED",
    promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
    airport_name="Minneapolis St. Paul International", airport_code="MSP",
) -> SourceAssertion:
    airport = Airport(name=airport_name, iata_code=airport_code, country="USA")
    source = Source(title="EMAS Procurement Advance Deposit memo", source_type="web_discovery")
    assertion = SourceAssertion(
        source=source, airport=airport, assertion_type="project_construction",
        source_record_identifier="mac.granicus.document.4.2349.105406",
        identity_guard_decision=identity_guard_decision,
        intelligence_review_decision=intelligence_review_decision,
        promotion_policy_decision=promotion_policy_decision,
    )
    session.add_all([airport, source, assertion])
    session.commit()
    return assertion


def _approve(session, assertion, **kwargs):
    action = record_reviewer_action(
        session, assertion, action="APPROVE_SIGNAL",
        reason="Deposit PO + CIP ceiling both concern the same 30L EMAS replacement.",
        reviewer="reviewer@example.test", **kwargs,
    )
    session.commit()
    return action


_MSP_FIELDS = dict(
    title="MSP Runway 30L EMAS replacement - advance deposit requested",
    category="replacement", confidence="medium", status="identified", likely_supplier="Runway Safe",
)


# ---------------------------------------------------------------------------
# 1-7. MSP #222 golden creation: exactly one Signal, published=False,
# provenance set, safe mapping, financial fields NULL.
# ---------------------------------------------------------------------------


def test_valid_msp_approval_creates_exactly_one_signal():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)

    result = create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    session.commit()

    assert result.created is True
    assert session.query(Signal).count() == 1
    session.close(); engine.dispose()


def test_created_signal_is_explicitly_unpublished():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    result = create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    session.commit()
    assert result.signal.published is False
    session.close(); engine.dispose()


def test_source_assertion_signal_id_is_set():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    result = create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    session.commit()
    assert assertion.signal_id == result.signal.id
    session.close(); engine.dispose()


def test_source_id_preserved_from_source_assertion():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    result = create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    session.commit()
    assert result.signal.source_id == assertion.source_id
    session.close(); engine.dispose()


def test_airport_id_preserved_from_source_assertion():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    result = create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    session.commit()
    assert result.signal.airport_id == assertion.airport_id
    session.close(); engine.dispose()


def test_likely_supplier_safe_mapping_never_sets_confirmed_vendor():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    result = create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    session.commit()
    assert result.signal.likely_supplier == "Runway Safe"
    assert result.signal.confirmed_vendor is None
    session.close(); engine.dispose()


def test_financial_fields_are_always_null():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    result = create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    session.commit()
    assert result.signal.estimated_total_value_usd is None
    assert result.signal.estimated_emas_value_usd is None
    session.close(); engine.dispose()


def test_service_has_no_parameter_capable_of_setting_a_financial_value():
    """Structural proof, not just a behavioral one: the function signature
    itself has no way to accept a dollar amount - passing one is a
    TypeError, not a value that gets silently dropped."""
    import inspect as inspect_module
    signature = inspect_module.signature(create_signal_from_approved_review)
    financial_looking_params = {
        name for name in signature.parameters
        if "value" in name.lower() or "amount" in name.lower() or "usd" in name.lower() or "cost" in name.lower()
    }
    assert financial_looking_params == set()


# ---------------------------------------------------------------------------
# 8-11. Governance gates: identity / intelligence / promotion policy /
# AUTO_ELIGIBLE.
# ---------------------------------------------------------------------------


def test_invalid_identity_decision_blocks_creation():
    """Recording a valid APPROVE_SIGNAL first (which itself requires a
    conforming shape - Slice 9B's own gate), then simulating drift by
    mutating identity_guard_decision afterward, proves this service checks
    the SourceAssertion's *current* state itself rather than trusting that
    a recorded approval still implies a conforming shape - the same
    "checked explicitly rather than assumed" defense-in-depth both modules'
    docstrings describe."""
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    assertion.identity_guard_decision = "ATTACH_PROVISIONAL"
    session.commit()
    with pytest.raises(ValueError, match="identity_guard_decision"):
        create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    assert session.query(Signal).count() == 0
    session.close(); engine.dispose()


def test_invalid_intelligence_decision_blocks_creation():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    assertion.intelligence_review_decision = "INSUFFICIENT_MATERIALITY"
    session.commit()
    with pytest.raises(ValueError, match="intelligence_review_decision"):
        create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    assert session.query(Signal).count() == 0
    session.close(); engine.dispose()


def test_invalid_promotion_policy_decision_blocks_creation():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    assertion.promotion_policy_decision = "DO_NOT_PROMOTE"
    session.commit()
    with pytest.raises(ValueError, match="promotion_policy_decision"):
        create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    assert session.query(Signal).count() == 0
    session.close(); engine.dispose()


def test_auto_eligible_blocks_the_human_route():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    assertion.promotion_policy_decision = "AUTO_ELIGIBLE"
    session.commit()
    with pytest.raises(ValueError, match="promotion_policy_decision"):
        create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    assert session.query(Signal).count() == 0
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 12-18. ReviewerAction gate: none / non-approval actions / historical
# approval superseded.
# ---------------------------------------------------------------------------


def test_no_reviewer_action_blocks_creation():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    with pytest.raises(ValueError, match="no ReviewerAction"):
        create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    assert session.query(Signal).count() == 0
    session.close(); engine.dispose()


@pytest.mark.parametrize("action", ["DEFER", "REJECT_SIGNAL", "NEEDS_MORE_EVIDENCE"])
def test_non_approval_latest_action_blocks_creation(action):
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    record_reviewer_action(session, assertion, action=action, reason="x", reviewer="human:reviewer")
    session.commit()
    with pytest.raises(ValueError, match="latest ReviewerAction"):
        create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    assert session.query(Signal).count() == 0
    session.close(); engine.dispose()


def test_latest_mark_duplicate_does_not_create_a_new_signal():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    existing_signal = Signal(
        airport=assertion.airport, title="Existing signal", category="replacement", confidence="high",
        published=False,
    )
    session.add(existing_signal)
    session.commit()
    record_reviewer_action(
        session, assertion, action="MARK_DUPLICATE", reason="x", reviewer="human:reviewer",
        duplicate_of_signal_id=existing_signal.id,
    )
    session.commit()

    with pytest.raises(ValueError, match="latest ReviewerAction"):
        create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    assert session.query(Signal).count() == 1  # only the pre-existing one

    # The correct, explicit, separate path links instead of creating.
    link_result = link_source_assertion_to_duplicate_signal(session, assertion)
    session.commit()
    assert link_result.created is False
    assert link_result.signal.id == existing_signal.id
    assert assertion.signal_id == existing_signal.id
    assert session.query(Signal).count() == 1
    session.close(); engine.dispose()


def test_historical_approval_superseded_by_later_reject_blocks_creation():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    approve = record_reviewer_action(session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:a")
    session.commit()
    record_reviewer_action(
        session, assertion, action="REJECT_SIGNAL", reason="Changed my mind.", reviewer="human:b",
        supersedes_action_id=approve.id,
    )
    session.commit()

    with pytest.raises(ValueError, match="latest ReviewerAction"):
        create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    assert session.query(Signal).count() == 0
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 18-19. Idempotency and drift.
# ---------------------------------------------------------------------------


def test_idempotent_repeat_reuses_the_same_signal():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    first = create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    session.commit()

    second = create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    session.commit()

    assert second.created is False
    assert second.signal.id == first.signal.id
    assert session.query(Signal).count() == 1
    session.close(); engine.dispose()


def test_incompatible_existing_signal_id_fails_closed_on_drift():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    session.commit()

    drifted_fields = dict(_MSP_FIELDS, category="new_installation")  # different core field
    with pytest.raises(ValueError, match="different core fields"):
        create_signal_from_approved_review(session, assertion, **drifted_fields)
    assert session.query(Signal).count() == 1  # no second Signal created
    session.close(); engine.dispose()


def test_signal_id_pointing_at_deleted_signal_fails_closed():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    result = create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    session.commit()
    # Simulate drift: point signal_id at a nonexistent id directly.
    assertion.signal_id = result.signal.id + 999
    session.commit()

    with pytest.raises(ValueError, match="no longer exists"):
        create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 20-22. Transaction atomicity, no commit, ReviewerAction unchanged.
# ---------------------------------------------------------------------------


def test_rollback_leaves_no_signal_and_no_link():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)

    session.rollback()

    assert session.query(Signal).count() == 0
    session.refresh(assertion) if assertion in session else None
    fresh = session.get(SourceAssertion, assertion.id)
    assert fresh.signal_id is None
    session.close(); engine.dispose()


def test_service_never_commits():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    # Still uncommitted - a rollback must undo it entirely.
    session.rollback()
    assert session.query(Signal).count() == 0
    session.close(); engine.dispose()


def test_reviewer_action_rows_unchanged_by_signal_creation():
    from app.models.reviewer_action import ReviewerAction

    engine, session = make_session()
    assertion = _msp_222_shape(session)
    action = _approve(session, assertion)
    snapshot_before = (action.action, action.reason, action.reviewer, action.created_at)

    create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    session.commit()

    assert session.query(ReviewerAction).count() == 1
    snapshot_after = (action.action, action.reason, action.reviewer, action.created_at)
    assert snapshot_after == snapshot_before
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 23. SFO-$40M adversarial financial safety.
# ---------------------------------------------------------------------------


def test_sfo_style_unlabeled_large_amount_never_reaches_a_financial_field():
    """Even with an adversarial title/notes containing a large unlabeled
    dollar figure, no Signal field ends up holding it - there is simply no
    parameter through which any amount could arrive."""
    engine, session = make_session()
    assertion = _msp_222_shape(
        session, airport_name="San Francisco International", airport_code="SFO",
    )
    _approve(session, assertion)
    result = create_signal_from_approved_review(
        session, assertion, title="SFO runway seam replacement - $40,000,000 project mentioned in passing",
        category="maintenance", confidence="low", notes="Source text mentions $40,000,000 without a clear role.",
    )
    session.commit()
    assert result.signal.estimated_total_value_usd is None
    assert result.signal.estimated_emas_value_usd is None
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 24-25. Procedural / temporal safety: status cannot be strengthened beyond
# what mere review approval can establish.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["completed", "awarded", "executed", "contracted", "COMPLETED"])
def test_disallowed_terminal_status_fails_closed(status):
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    fields = dict(_MSP_FIELDS, status=status)
    with pytest.raises(ValueError, match="not a state human review approval alone can establish"):
        create_signal_from_approved_review(session, assertion, **fields)
    assert session.query(Signal).count() == 0
    session.close(); engine.dispose()


def test_planned_pending_status_is_accepted_unchanged():
    """MSP's own evidence is a pending, planned request - 'identified' (or
    similarly provisional statuses) must remain acceptable; only strictly
    terminal/confirmed-sounding statuses are refused."""
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    result = create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    session.commit()
    assert result.signal.status == "identified"
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 26-27. Publication safety: public export excludes it, internal query
# includes it.
# ---------------------------------------------------------------------------


def test_public_export_excludes_governed_signal(tmp_path):
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    result = create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    session.commit()

    output = tmp_path / "site"
    build_site(output, session=session)

    assert (output / "signals" / f"{result.signal.id}.html").exists() is False
    index_html = (output / "index.html").read_text(encoding="utf-8")
    assert _MSP_FIELDS["title"] not in index_html
    data = json.loads((output / "data.json").read_text(encoding="utf-8"))
    assert all(s["title"] != _MSP_FIELDS["title"] for s in data["signals"])
    session.close(); engine.dispose()


def test_internal_query_includes_the_governed_signal():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    result = create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    session.commit()

    refetched = session.get(Signal, result.signal.id)
    assert refetched is not None
    assert refetched.title == _MSP_FIELDS["title"]
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 28-29. Legacy writers unchanged; no canonical (Airport/Source) writes.
# ---------------------------------------------------------------------------


def test_legacy_signal_writer_sites_are_untouched():
    import subprocess

    result = subprocess.run(
        ["git", "diff", "--stat", "--", "app/services/signal_rules.py",
         "scripts/import_usaspending_grants.py", "scripts/import_faa_construction_report.py",
         "scripts/add_mdw_emas_bed_repairs_signal.py", "scripts/add_rw_shareholder_letter_signals.py",
         "scripts/add_brazil_expansion.py"],
        cwd=".", capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == ""


def test_no_canonical_airport_or_source_mutation_from_governed_creation():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    airport_snapshot_before = (assertion.airport.name, assertion.airport.iata_code, assertion.airport.country)
    source_snapshot_before = (assertion.source.title, assertion.source.source_type)
    _approve(session, assertion)
    create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    session.commit()

    airport_snapshot_after = (assertion.airport.name, assertion.airport.iata_code, assertion.airport.country)
    source_snapshot_after = (assertion.source.title, assertion.source.source_type)
    assert airport_snapshot_after == airport_snapshot_before
    assert source_snapshot_after == source_snapshot_before
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 30. Reverse provenance.
# ---------------------------------------------------------------------------


def test_reverse_provenance_via_supporting_source_assertions():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    result = create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    session.commit()

    session.expire_all()
    signal = session.get(Signal, result.signal.id)
    assert [a.id for a in signal.supporting_source_assertions] == [assertion.id]
    session.close(); engine.dispose()


def test_deleting_a_signal_with_supporting_source_assertions_fails_safely():
    """Cascade/delete-behavior audit (review checkpoint): evidence history
    must not disappear because a Signal it points at gets deleted. Without
    passive_deletes=True on Signal.supporting_source_assertions,
    SQLAlchemy's default relationship management would silently issue
    `UPDATE source_assertions SET signal_id=NULL` for every referencing row
    before deleting the Signal - discarding the governed provenance link
    instead of blocking the delete (found and fixed during this review;
    verified empirically before this test was written). No code path in
    this repository deletes a Signal today, but the invariant must hold for
    a future caller regardless."""
    engine, session = make_session_with_foreign_keys_enforced()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    result = create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    session.commit()
    signal_id = result.signal.id

    signal_obj = session.get(Signal, signal_id)
    session.delete(signal_obj)
    with pytest.raises(Exception, match="FOREIGN KEY constraint failed"):
        session.commit()
    session.rollback()

    fresh = session.get(SourceAssertion, assertion.id)
    assert fresh.signal_id == signal_id  # provenance link not lost
    assert session.get(Signal, signal_id) is not None  # signal not lost
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 31. Malformed human-selected fields fail closed.
# ---------------------------------------------------------------------------


def test_blank_title_fails_closed():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    fields = dict(_MSP_FIELDS, title="   ")
    with pytest.raises(ValueError, match="title is required"):
        create_signal_from_approved_review(session, assertion, **fields)
    session.close(); engine.dispose()


def test_blank_category_fails_closed():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    fields = dict(_MSP_FIELDS, category="")
    with pytest.raises(ValueError, match="category is required"):
        create_signal_from_approved_review(session, assertion, **fields)
    session.close(); engine.dispose()


def test_unrecognized_confidence_fails_closed():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    fields = dict(_MSP_FIELDS, confidence="extremely_high")
    with pytest.raises(ValueError, match="confidence must be one of"):
        create_signal_from_approved_review(session, assertion, **fields)
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 32. International / non-USD-flavored governed example - no currency or
# region assumption baked into the service.
# ---------------------------------------------------------------------------


def test_international_airport_governed_creation_has_no_usd_assumption():
    engine, session = make_session()
    assertion = _msp_222_shape(
        session, airport_name="Copenhagen Airport", airport_code="CPH",
    )
    assertion.source.title = "Copenhagen Airports A/S EMAS notice"
    _approve(session, assertion)
    result = create_signal_from_approved_review(
        session, assertion, title="CPH runway EMAS study", category="study", confidence="low",
    )
    session.commit()
    assert result.signal.airport.iata_code == "CPH"
    assert result.signal.estimated_total_value_usd is None
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Additional: runway_id cross-airport validation, matching the established
# physical_installation_reconciliation precedent's own validation shape.
# ---------------------------------------------------------------------------


def test_runway_id_must_belong_to_the_same_airport():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    other_airport = Airport(name="Other Airport", country="USA")
    other_runway = Runway(airport=other_airport, designation="09/27")
    session.add_all([other_airport, other_runway])
    session.commit()
    _approve(session, assertion)

    fields = dict(_MSP_FIELDS, runway_id=other_runway.id)
    with pytest.raises(ValueError, match="runway_id"):
        create_signal_from_approved_review(session, assertion, **fields)
    session.close(); engine.dispose()
