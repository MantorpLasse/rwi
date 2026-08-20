"""Tests for app/services/reviewer_action_persistence.py and
app/models/reviewer_action.py (Slice 9B,
docs/architecture/reviewer-action-persistence-slice9b-report.md).

Every test uses an isolated in-memory SQLite database. Modeled on the
already-proven pattern in tests/test_physical_installation_reconciliation.py.
"""
from __future__ import annotations

import ast
import inspect as inspect_module

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Signal, Source, SourceAssertion
from app.models.reviewer_action import REVIEWER_ACTIONS, ReviewerAction
from app.services.reviewer_action_persistence import (
    get_latest_reviewer_action,
    record_reviewer_action,
)


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def make_session_with_foreign_keys_enforced():
    """Like make_session(), but with PRAGMA foreign_keys=ON on every
    connection - mirroring app/database.py's own connect-event listener
    for the real engine. Plain create_engine("sqlite:///:memory:") does
    NOT enforce SQLite foreign keys by default (a well-known SQLAlchemy/
    SQLite gotcha); this helper is for tests that specifically need to
    verify DB-level FK-constraint behavior, matching production."""
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
) -> SourceAssertion:
    """The real SourceAssertion #222 governance shape (MSP EMAS advance
    deposit memo) - not its full evidence text, just the three governed
    decision fields this module's validation actually reads."""
    airport = Airport(name="Minneapolis St. Paul International", iata_code="MSP", country="USA")
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


# ---------------------------------------------------------------------------
# 2. Action vocabulary
# ---------------------------------------------------------------------------


def test_reviewer_actions_vocabulary_is_exactly_the_six_approved_actions():
    """Updated by R4B
    (docs/architecture/existing-signal-reconciliation-r4b-reviewer-action-report.md)
    to add CONFIRM_DISTINCT_SIGNAL - see
    tests/test_reviewer_action_confirm_distinct_signal.py for that action's
    own dedicated coverage; every pre-existing action's behavior in this
    file is otherwise unmodified."""
    assert REVIEWER_ACTIONS == (
        "APPROVE_SIGNAL", "REJECT_SIGNAL", "DEFER", "NEEDS_MORE_EVIDENCE",
        "MARK_DUPLICATE", "CONFIRM_DISTINCT_SIGNAL",
    )


def test_malformed_action_fails():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    with pytest.raises(ValueError, match="action must be one of"):
        record_reviewer_action(
            session, assertion, action="APPROVE_EVERYTHING", reason="x", reviewer="human:reviewer",
        )
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 3-6, 9, 14, 15. APPROVE_SIGNAL golden case and the approval gate
# ---------------------------------------------------------------------------


def test_approve_signal_valid_msp_222_shape():
    engine, session = make_session()
    assertion = _msp_222_shape(session)

    action = record_reviewer_action(
        session, assertion, action="APPROVE_SIGNAL",
        reason="Deposit PO + CIP ceiling both concern the same 30L EMAS replacement; "
        "sole-source vendor request is credible even though not yet awarded.",
        reviewer="reviewer@example.test",
    )
    session.commit()

    assert action.id is not None
    assert action.source_assertion_id == assertion.id
    assert action.action == "APPROVE_SIGNAL"
    assert session.query(ReviewerAction).count() == 1
    assert session.query(Signal).count() == 0
    session.close(); engine.dispose()


def test_approval_fails_with_invalid_identity_decision():
    engine, session = make_session()
    assertion = _msp_222_shape(session, identity_guard_decision="ATTACH_PROVISIONAL")
    with pytest.raises(ValueError, match="identity_guard_decision"):
        record_reviewer_action(
            session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:reviewer",
        )
    assert session.query(ReviewerAction).count() == 0
    session.close(); engine.dispose()


@pytest.mark.parametrize("promotion_policy_decision", ["AUTO_ELIGIBLE", "DO_NOT_PROMOTE", None, "GARBAGE_VALUE"])
def test_approval_fails_with_any_non_human_review_required_promotion_policy(promotion_policy_decision):
    engine, session = make_session()
    assertion = _msp_222_shape(session, promotion_policy_decision=promotion_policy_decision)
    with pytest.raises(ValueError, match="promotion_policy_decision"):
        record_reviewer_action(
            session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:reviewer",
        )
    assert session.query(ReviewerAction).count() == 0
    session.close(); engine.dispose()


@pytest.mark.parametrize(
    "intelligence_review_decision",
    ["INSUFFICIENT_MATERIALITY", "IDENTITY_NOT_CONFIRMED", "CONTRADICTED", None, "GARBAGE_VALUE"],
)
def test_approval_fails_with_any_non_review_required_intelligence_decision(intelligence_review_decision):
    """Defense-in-depth: even though the pipeline's own construction means a
    row can never reach promotion_policy_decision=HUMAN_REVIEW_REQUIRED
    without intelligence_review_decision=REVIEW_REQUIRED already being true,
    this module checks it explicitly rather than assuming it (see the
    module docstring and the Slice 9B report's own §15 reasoning)."""
    engine, session = make_session()
    assertion = _msp_222_shape(session, intelligence_review_decision=intelligence_review_decision)
    with pytest.raises(ValueError, match="intelligence_review_decision"):
        record_reviewer_action(
            session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:reviewer",
        )
    assert session.query(ReviewerAction).count() == 0
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 7-9. REJECT_SIGNAL / DEFER / NEEDS_MORE_EVIDENCE - none require the
# approval gate, none create a Signal, evidence stays preserved.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["REJECT_SIGNAL", "DEFER", "NEEDS_MORE_EVIDENCE"])
def test_non_approval_actions_are_recorded_without_the_approval_gate(action):
    engine, session = make_session()
    # Deliberately an identity/promotion shape that would fail APPROVE_SIGNAL,
    # to prove these three actions do not go through that gate at all.
    assertion = _msp_222_shape(
        session, identity_guard_decision="INSUFFICIENT_IDENTITY", promotion_policy_decision=None,
    )
    before_text = assertion.raw_relevant_text
    before_identity = assertion.identity_guard_decision

    recorded = record_reviewer_action(
        session, assertion, action=action, reason="Reviewer rationale.", reviewer="human:reviewer",
    )
    session.commit()

    assert recorded.action == action
    assert session.query(Signal).count() == 0
    assert assertion.raw_relevant_text == before_text  # evidence preserved
    assert assertion.identity_guard_decision == before_identity  # no SourceAssertion mutation
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 10-12. MARK_DUPLICATE
# ---------------------------------------------------------------------------


def test_mark_duplicate_valid_references_existing_signal():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    existing_signal = Signal(
        airport=assertion.airport, title="Existing MSP EMAS signal", category="replacement", confidence="high",
    )
    session.add(existing_signal)
    session.commit()

    action = record_reviewer_action(
        session, assertion, action="MARK_DUPLICATE",
        reason="This memo concerns the same EMAS replacement as the existing signal.",
        reviewer="human:reviewer", duplicate_of_signal_id=existing_signal.id,
    )
    session.commit()

    assert action.duplicate_of_signal_id == existing_signal.id
    assert session.query(Signal).count() == 1  # no new Signal created
    session.close(); engine.dispose()


def test_mark_duplicate_without_target_fails_closed():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    with pytest.raises(ValueError, match="MARK_DUPLICATE requires duplicate_of_signal_id"):
        record_reviewer_action(
            session, assertion, action="MARK_DUPLICATE", reason="x", reviewer="human:reviewer",
        )
    session.close(); engine.dispose()


def test_mark_duplicate_target_must_exist():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    with pytest.raises(ValueError, match="referenced Signal"):
        record_reviewer_action(
            session, assertion, action="MARK_DUPLICATE", reason="x", reviewer="human:reviewer",
            duplicate_of_signal_id=999999,
        )
    session.close(); engine.dispose()


def test_duplicate_target_on_non_duplicate_action_fails_closed():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    existing_signal = Signal(
        airport=assertion.airport, title="Existing signal", category="replacement", confidence="high",
    )
    session.add(existing_signal)
    session.commit()
    with pytest.raises(ValueError, match="only valid when action == MARK_DUPLICATE"):
        record_reviewer_action(
            session, assertion, action="DEFER", reason="x", reviewer="human:reviewer",
            duplicate_of_signal_id=existing_signal.id,
        )
    session.close(); engine.dispose()


def test_duplicate_check_constraint_enforced_at_db_level_bypassing_the_service():
    """Belt-and-suspenders: even if some future caller constructs the ORM
    object directly instead of going through record_reviewer_action(), the
    CHECK constraints still refuse an invalid combination."""
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    bad = ReviewerAction(
        source_assertion_id=assertion.id, action="DEFER", reason="x", reviewer="human:reviewer",
        duplicate_of_signal_id=1,
    )
    session.add(bad)
    with pytest.raises(Exception):
        session.commit()
    session.rollback()
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 13-14. Immutability
# ---------------------------------------------------------------------------


def test_immutable_update_is_blocked():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    action = record_reviewer_action(
        session, assertion, action="DEFER", reason="Initial note.", reviewer="human:reviewer",
    )
    session.commit()

    action.reason = "mutated"
    with pytest.raises(ValueError, match="immutable"):
        session.commit()
    session.rollback()
    session.close(); engine.dispose()


def test_immutable_delete_is_blocked():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    action = record_reviewer_action(
        session, assertion, action="DEFER", reason="Initial note.", reviewer="human:reviewer",
    )
    session.commit()

    session.delete(action)
    with pytest.raises(ValueError, match="auditable and cannot be deleted"):
        session.commit()
    session.rollback()
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Cascade / delete-behavior audit (review checkpoint, §20): audit history
# must not be able to silently disappear because a SourceAssertion or Signal
# it references gets deleted. Neither relationship declares cascade="delete"
# or "delete-orphan", so this must fail safely rather than orphan/lose rows.
# ---------------------------------------------------------------------------


def test_deleting_a_source_assertion_with_reviewer_actions_fails_safely():
    """SQLAlchemy's own relationship-aware dependency resolution (via the
    back_populates="reviewer_actions"/"source_assertion" pair) attempts to
    re-persist the still-attached child row before the parent delete, which
    hits this table's own immutability listener - the same mechanism
    app.services.physical_installation_reconciliation's own
    InstallationAssertionLink precedent already relies on for this exact
    protection, reproduced here to confirm ReviewerAction inherits it."""
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    record_reviewer_action(session, assertion, action="DEFER", reason="x", reviewer="human:reviewer")
    session.commit()

    session.delete(assertion)
    with pytest.raises(ValueError, match="immutable"):
        session.commit()
    session.rollback()
    assert session.query(ReviewerAction).count() == 1  # history not lost
    session.close(); engine.dispose()


def test_deleting_a_duplicate_target_signal_fails_safely_with_fk_enforced():
    """Unlike source_assertion_id, duplicate_of_signal has no back_populates
    on Signal, so the ORM has no relationship-level awareness of this
    reference - protection here comes entirely from the raw DB-level
    FOREIGN KEY constraint, which requires PRAGMA foreign_keys=ON (always
    true in production via app/database.py's own connect-event listener,
    reproduced by make_session_with_foreign_keys_enforced() here)."""
    engine, session = make_session_with_foreign_keys_enforced()
    assertion = _msp_222_shape(session)
    existing_signal = Signal(
        airport=assertion.airport, title="Existing signal", category="replacement", confidence="high",
    )
    session.add(existing_signal)
    session.commit()
    record_reviewer_action(
        session, assertion, action="MARK_DUPLICATE", reason="x", reviewer="human:reviewer",
        duplicate_of_signal_id=existing_signal.id,
    )
    session.commit()

    session.delete(existing_signal)
    with pytest.raises(Exception, match="FOREIGN KEY constraint failed"):
        session.commit()
    session.rollback()
    assert session.query(Signal).count() == 1  # signal not lost
    assert session.query(ReviewerAction).count() == 1  # reference not lost
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 15-17. Supersession / history / latest-action determinism
# ---------------------------------------------------------------------------


def test_supersession_history_preserved_and_latest_action_is_the_newer_one():
    engine, session = make_session()
    assertion = _msp_222_shape(session)

    first = record_reviewer_action(
        session, assertion, action="DEFER", reason="Need a second opinion.", reviewer="human:reviewer_a",
    )
    session.commit()

    second = record_reviewer_action(
        session, assertion, action="APPROVE_SIGNAL", reason="Second opinion agrees.",
        reviewer="human:reviewer_b", supersedes_action_id=first.id,
    )
    session.commit()

    # Both rows remain; first is untouched.
    assert session.query(ReviewerAction).count() == 2
    assert first.action == "DEFER"
    assert second.supersedes_action_id == first.id

    latest = get_latest_reviewer_action(session, assertion.id)
    assert latest.id == second.id
    assert latest.action == "APPROVE_SIGNAL"
    session.close(); engine.dispose()


def test_third_action_in_a_chain_becomes_the_new_latest():
    engine, session = make_session()
    assertion = _msp_222_shape(session)

    first = record_reviewer_action(
        session, assertion, action="DEFER", reason="Initial.", reviewer="human:a",
    )
    session.commit()
    second = record_reviewer_action(
        session, assertion, action="NEEDS_MORE_EVIDENCE", reason="Need more.",
        reviewer="human:b", supersedes_action_id=first.id,
    )
    session.commit()
    third = record_reviewer_action(
        session, assertion, action="APPROVE_SIGNAL", reason="Evidence supplied.",
        reviewer="human:c", supersedes_action_id=second.id,
    )
    session.commit()

    assert session.query(ReviewerAction).count() == 3
    latest = get_latest_reviewer_action(session, assertion.id)
    assert latest.id == third.id
    assert latest.action == "APPROVE_SIGNAL"
    session.close(); engine.dispose()


def test_latest_action_is_deterministic_ordered_by_created_at_then_id():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    record_reviewer_action(session, assertion, action="DEFER", reason="a", reviewer="human:a")
    session.commit()
    second = record_reviewer_action(session, assertion, action="NEEDS_MORE_EVIDENCE", reason="b", reviewer="human:b")
    session.commit()

    latest = get_latest_reviewer_action(session, assertion.id)
    assert latest.id == second.id


def test_latest_reviewer_action_returns_none_when_no_action_recorded():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    assert get_latest_reviewer_action(session, assertion.id) is None
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 13/16. Cross-assertion supersession rejected
# ---------------------------------------------------------------------------


def test_cross_assertion_supersession_is_rejected():
    engine, session = make_session()
    assertion_a = _msp_222_shape(session)
    airport_b = Airport(name="Other Airport", country="USA")
    source_b = Source(title="Other source", source_type="official")
    assertion_b = SourceAssertion(
        source=source_b, airport=airport_b, assertion_type="project_construction",
        source_record_identifier="other-1",
        identity_guard_decision="ATTACH_CONFIRMED",
        intelligence_review_decision="REVIEW_REQUIRED",
        promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
    )
    session.add_all([airport_b, source_b, assertion_b])
    session.commit()

    action_on_a = record_reviewer_action(
        session, assertion_a, action="DEFER", reason="x", reviewer="human:reviewer",
    )
    session.commit()

    with pytest.raises(ValueError, match="same SourceAssertion"):
        record_reviewer_action(
            session, assertion_b, action="APPROVE_SIGNAL", reason="y", reviewer="human:reviewer",
            supersedes_action_id=action_on_a.id,
        )
    session.close(); engine.dispose()


def test_supersedes_action_id_must_reference_an_existing_action():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    with pytest.raises(ValueError, match="must exist"):
        record_reviewer_action(
            session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:reviewer",
            supersedes_action_id=999999,
        )
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 18-19. No SourceAssertion / no Signal mutation
# ---------------------------------------------------------------------------


def test_recording_an_action_never_mutates_source_assertion_fields():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    snapshot_before = (
        assertion.identity_guard_decision, assertion.identity_guard_reason,
        assertion.intelligence_review_decision, assertion.intelligence_review_reason,
        assertion.promotion_policy_decision, assertion.promotion_policy_reason,
        assertion.raw_relevant_text, assertion.review_state,
    )
    record_reviewer_action(session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:reviewer")
    session.commit()
    snapshot_after = (
        assertion.identity_guard_decision, assertion.identity_guard_reason,
        assertion.intelligence_review_decision, assertion.intelligence_review_reason,
        assertion.promotion_policy_decision, assertion.promotion_policy_reason,
        assertion.raw_relevant_text, assertion.review_state,
    )
    assert snapshot_after == snapshot_before
    session.close(); engine.dispose()


def test_mark_duplicate_never_mutates_the_referenced_signal():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    existing_signal = Signal(
        airport=assertion.airport, title="Existing signal", category="replacement",
        confidence="high", status="identified", published=True,
    )
    session.add(existing_signal)
    session.commit()
    snapshot_before = (
        existing_signal.title, existing_signal.category, existing_signal.confidence,
        existing_signal.status, existing_signal.published,
    )
    record_reviewer_action(
        session, assertion, action="MARK_DUPLICATE", reason="x", reviewer="human:reviewer",
        duplicate_of_signal_id=existing_signal.id,
    )
    session.commit()
    snapshot_after = (
        existing_signal.title, existing_signal.category, existing_signal.confidence,
        existing_signal.status, existing_signal.published,
    )
    assert snapshot_after == snapshot_before
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 20. No Signal creation, structurally (AST) and behaviorally, across every
# action type.
# ---------------------------------------------------------------------------


def test_reviewer_action_persistence_module_never_constructs_a_signal():
    import app.services.reviewer_action_persistence as module

    tree = ast.parse(inspect_module.getsource(module))
    signal_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Signal"
    ]
    assert signal_calls == []


@pytest.mark.parametrize("action", ["APPROVE_SIGNAL", "REJECT_SIGNAL", "DEFER", "NEEDS_MORE_EVIDENCE"])
def test_signal_count_unchanged_after_every_non_duplicate_action(action):
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    before = session.query(Signal).count()
    record_reviewer_action(session, assertion, action=action, reason="x", reviewer="human:reviewer")
    session.commit()
    after = session.query(Signal).count()
    assert after == before == 0
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 21-22. Transaction ownership: never commits; rollback removes the action;
# failed validation leaves nothing behind.
# ---------------------------------------------------------------------------


def test_record_reviewer_action_never_commits():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    record_reviewer_action(session, assertion, action="DEFER", reason="x", reviewer="human:reviewer")
    # Still inside the same uncommitted transaction - a fresh connection to
    # the same file-backed DB would not see it. Using :memory: here, so
    # instead assert the session itself still has it pending (not yet
    # durable) by checking session.new/dirty semantics via a rollback.
    session.rollback()
    assert session.query(ReviewerAction).count() == 0
    session.close(); engine.dispose()


def test_failed_validation_leaves_no_partial_action_history():
    engine, session = make_session()
    assertion = _msp_222_shape(session, identity_guard_decision="ATTACH_PROVISIONAL")
    with pytest.raises(ValueError):
        record_reviewer_action(session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:reviewer")
    assert session.query(ReviewerAction).count() == 0
    session.commit()  # nothing pending to commit
    assert session.query(ReviewerAction).count() == 0
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 27. International / generic reviewer string - no format assumed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reviewer", ["human:reviewer", "jane.doe@example.test", "Åsa Lindqvist", "reviewer-42"])
def test_reviewer_field_accepts_generic_free_text_identity(reviewer):
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    action = record_reviewer_action(session, assertion, action="DEFER", reason="x", reviewer=reviewer)
    session.commit()
    assert action.reviewer == reviewer
    session.close(); engine.dispose()


def test_blank_reason_or_reviewer_rejected():
    engine, session = make_session()
    assertion = _msp_222_shape(session)
    with pytest.raises(ValueError, match="reason is required"):
        record_reviewer_action(session, assertion, action="DEFER", reason="   ", reviewer="human:reviewer")
    with pytest.raises(ValueError, match="reviewer is required"):
        record_reviewer_action(session, assertion, action="DEFER", reason="x", reviewer="   ")
    session.close(); engine.dispose()
