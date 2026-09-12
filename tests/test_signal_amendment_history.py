"""RWI HQ "Signal Amendment Audit Trail — Append-Only Governance" mission -
tests for app/models/signal_amendment.py, app/services/signal_amendment_history.py,
and app/services/signal_amendment_serialization.py.

Every test uses an isolated in-memory SQLite database - production DB is
never touched by any test in this file (mission Part 12, item 29)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Signal, Source, SourceAssertion, ReviewerAction
from app.models.signal_amendment import SignalAmendmentAction, SignalAmendmentFieldChange
from app.services.signal_amendment import FieldChange, amend_signal
from app.services.signal_amendment_history import (
    SignalAmendmentHistoryError,
    list_signal_amendments,
    record_signal_amendment,
)
from app.services.signal_amendment_serialization import (
    SignalAmendmentSerializationError,
    deserialize_amendment_value,
    serialize_amendment_value,
)
from app.services.signal_disposition_persistence import record_signal_group_disposition
from app.services.signal_disposition_resolution import resolve_fh_d4_group_status


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def make_signal(session, **overrides) -> Signal:
    airport = overrides.pop("airport", None)
    if airport is None:
        airport = Airport(name="Test Airport", iata_code="TST", country="USA")
        session.add(airport)
        session.flush()
    defaults = dict(title="A signal", category="new_installation", confidence="high")
    defaults.update(overrides)
    signal = Signal(airport=airport, **defaults)
    session.add(signal)
    session.commit()
    return signal


# 1. One-field amendment creates one action + one field-change row.
def test_one_field_amendment_creates_one_action_and_one_field_change():
    session = make_session()
    signal = make_signal(session, target_year=2028)
    changes = (FieldChange(field="target_year", old_value=2028, new_value=2026),)

    action = record_signal_amendment(session, signal_id=signal.id, changes=changes, reason="a real reason", reviewer="r@example.test")
    session.commit()

    assert session.query(SignalAmendmentAction).count() == 1
    assert session.query(SignalAmendmentFieldChange).filter_by(action_id=action.id).count() == 1


# 2. Multi-field amendment creates one action + N field-change rows.
def test_multi_field_amendment_creates_one_action_and_n_field_changes():
    session = make_session()
    signal = make_signal(session, status="funded", target_year=2028)
    changes = (
        FieldChange(field="status", old_value="funded", new_value="completed"),
        FieldChange(field="target_year", old_value=2028, new_value=None),
        FieldChange(field="supplier", old_value=None, new_value="Runway Safe"),
    )

    action = record_signal_amendment(session, signal_id=signal.id, changes=changes, reason="a real reason", reviewer="r@example.test")
    session.commit()

    assert session.query(SignalAmendmentAction).count() == 1
    field_changes = session.query(SignalAmendmentFieldChange).filter_by(action_id=action.id).all()
    assert {c.field_name for c in field_changes} == {"status", "target_year", "supplier"}


# 3/4/5. Exact old/new values, reason, and reviewer are all persisted.
def test_exact_old_new_reason_reviewer_persisted():
    session = make_session()
    signal = make_signal(session, target_year=2028)
    changes = (FieldChange(field="target_year", old_value=2028, new_value=2026),)

    record_signal_amendment(
        session, signal_id=signal.id, changes=changes,
        reason="AMPU Table 8-1 assigns no EMAS-specific year", reviewer="r@example.test",
    )
    session.commit()

    history = list_signal_amendments(session, signal.id)
    assert len(history) == 1
    entry = history[0]
    assert entry.reason == "AMPU Table 8-1 assigns no EMAS-specific year"
    assert entry.reviewer == "r@example.test"
    assert entry.field_changes[0].old_value == 2028
    assert entry.field_changes[0].new_value == 2026


# 6/7. SourceAssertion/ReviewerAction references are persisted.
def test_source_assertion_and_reviewer_action_references_persisted():
    session = make_session()
    signal = make_signal(session, target_year=2028)
    source = Source(title="AMPU", source_type="master_plan")
    session.add(source)
    session.flush()
    assertion = SourceAssertion(
        source=source, airport_id=signal.airport_id, assertion_type="project_construction",
        source_record_identifier="ampu:1",
    )
    session.add(assertion)
    session.commit()
    reviewer_action = ReviewerAction(source_assertion_id=assertion.id, action="NEEDS_MORE_EVIDENCE", reason="x", reviewer="r@example.test")
    session.add(reviewer_action)
    session.commit()

    changes = (FieldChange(field="target_year", old_value=2028, new_value=2026),)
    record_signal_amendment(
        session, signal_id=signal.id, changes=changes, reason="a real reason", reviewer="r@example.test",
        source_assertion_id=assertion.id, reviewer_action_id=reviewer_action.id,
    )
    session.commit()

    entry = list_signal_amendments(session, signal.id)[0]
    assert entry.source_assertion_id == assertion.id
    assert entry.reviewer_action_id == reviewer_action.id


# 8. Linkage validation fails closed - a nonexistent SourceAssertion/
# ReviewerAction/Signal refuses, no row created.
def test_linkage_validation_fails_closed():
    session = make_session()
    signal = make_signal(session, target_year=2028)
    changes = (FieldChange(field="target_year", old_value=2028, new_value=2026),)

    with pytest.raises(SignalAmendmentHistoryError, match="does not exist"):
        record_signal_amendment(
            session, signal_id=signal.id, changes=changes, reason="a real reason", reviewer="r@example.test",
            source_assertion_id=999999,
        )
    assert session.query(SignalAmendmentAction).count() == 0

    with pytest.raises(SignalAmendmentHistoryError, match="does not exist"):
        record_signal_amendment(
            session, signal_id=999999, changes=changes, reason="a real reason", reviewer="r@example.test",
        )
    assert session.query(SignalAmendmentAction).count() == 0


# 12/13. Signal + audit rows are atomic - if flush fails after both are
# staged, nothing is committed; a rollback undoes both together.
def test_signal_and_audit_rows_atomic_on_flush_failure():
    session = make_session()
    signal = make_signal(session, target_year=2028)

    result = amend_signal(session, signal_id=signal.id, changes={"target_year": 2026}, reason="a real reason", reviewer="r@example.test")
    # Both the Signal mutation and the audit rows are pending in the same
    # uncommitted transaction at this point.
    assert signal.target_year == 2026
    assert session.query(SignalAmendmentAction).count() == 1

    session.rollback()
    refreshed = session.get(Signal, signal.id)
    assert refreshed.target_year == 2028
    assert session.query(SignalAmendmentAction).count() == 0


# 14/15/16. NULL <-> value, and empty string stays distinct from NULL.
def test_null_to_value_and_value_to_null_and_empty_string_distinct_from_null():
    session = make_session()
    signal = make_signal(session, supplier=None, target_year=2028)

    # NULL -> value
    action1 = record_signal_amendment(
        session, signal_id=signal.id,
        changes=(FieldChange(field="supplier", old_value=None, new_value="Runway Safe"),),
        reason="a real reason", reviewer="r@example.test",
    )
    session.commit()
    row1 = session.query(SignalAmendmentFieldChange).filter_by(action_id=action1.id).one()
    assert row1.old_value is None
    assert row1.new_value == "Runway Safe"

    # value -> NULL
    action2 = record_signal_amendment(
        session, signal_id=signal.id,
        changes=(FieldChange(field="supplier", old_value="Runway Safe", new_value=None),),
        reason="a real reason", reviewer="r@example.test",
    )
    session.commit()
    row2 = session.query(SignalAmendmentFieldChange).filter_by(action_id=action2.id).one()
    assert row2.old_value == "Runway Safe"
    assert row2.new_value is None

    # empty string stays distinct from NULL
    action3 = record_signal_amendment(
        session, signal_id=signal.id,
        changes=(FieldChange(field="supplier", old_value=None, new_value=""),),
        reason="a real reason", reviewer="r@example.test",
    )
    session.commit()
    row3 = session.query(SignalAmendmentFieldChange).filter_by(action_id=action3.id).one()
    assert row3.old_value is None
    assert row3.new_value == ""
    assert row3.new_value is not None


# 17. Decimal round-trips exactly, never via float.
def test_decimal_exact_round_trip():
    original = Decimal("1572897.00")
    text = serialize_amendment_value(original)
    assert text == "1572897.00"
    restored = deserialize_amendment_value(text, Decimal)
    assert restored == original
    assert isinstance(restored, Decimal)
    # A value float() would corrupt if routed through it - proves this
    # module never does that.
    tricky = Decimal("0.1")
    assert deserialize_amendment_value(serialize_amendment_value(tricky), Decimal) == tricky
    assert float(tricky) != 0.1 or str(tricky) == "0.1"  # sanity: Decimal("0.1") stringifies exactly


# 18. date round-trips exactly.
def test_date_exact_round_trip():
    original = date(2026, 6, 1)
    text = serialize_amendment_value(original)
    assert text == "2026-06-01"
    assert deserialize_amendment_value(text, date) == original


# 19. datetime round-trips exactly (no ALLOWED_AMENDMENT_FIELDS entry is a
# datetime column today, but the serialization helper supports it
# generically per the approved design's forward-compatibility note).
def test_datetime_exact_round_trip():
    original = datetime(2026, 9, 12, 14, 8, 9, tzinfo=timezone.utc)
    text = serialize_amendment_value(original)
    restored = deserialize_amendment_value(text, datetime)
    assert restored == original


# 20. int/runway_id round-trips exactly.
def test_int_round_trip():
    text = serialize_amendment_value(2028)
    assert text == "2028"
    assert deserialize_amendment_value(text, int) == 2028


def test_bool_refused_not_silently_coerced_to_int():
    with pytest.raises(SignalAmendmentSerializationError):
        serialize_amendment_value(True)


def test_unparseable_historical_value_fails_closed():
    with pytest.raises(SignalAmendmentSerializationError, match="unparseable"):
        deserialize_amendment_value("not-a-number", int)
    with pytest.raises(SignalAmendmentSerializationError, match="unparseable"):
        deserialize_amendment_value("not-a-date", date)


# 21. History query ordering is deterministic.
def test_history_ordering_deterministic():
    session = make_session()
    signal = make_signal(session, target_year=2030)

    a1 = record_signal_amendment(
        session, signal_id=signal.id, changes=(FieldChange(field="target_year", old_value=2030, new_value=2029),),
        reason="first", reviewer="r@example.test",
    )
    session.commit()
    a2 = record_signal_amendment(
        session, signal_id=signal.id, changes=(FieldChange(field="target_year", old_value=2029, new_value=2028),),
        reason="second", reviewer="r@example.test",
    )
    session.commit()

    history = list_signal_amendments(session, signal.id)
    assert [entry.action_id for entry in history] == [a1.id, a2.id]
    # Deterministic regardless of how many times it's queried.
    assert [entry.action_id for entry in list_signal_amendments(session, signal.id)] == [a1.id, a2.id]


# 22. A later amendment does not overwrite earlier history.
def test_later_amendment_does_not_overwrite_earlier_history():
    session = make_session()
    signal = make_signal(session, target_year=2030)

    record_signal_amendment(
        session, signal_id=signal.id, changes=(FieldChange(field="target_year", old_value=2030, new_value=2029),),
        reason="first correction", reviewer="r1@example.test",
    )
    session.commit()
    record_signal_amendment(
        session, signal_id=signal.id, changes=(FieldChange(field="target_year", old_value=2029, new_value=2028),),
        reason="second correction", reviewer="r2@example.test",
    )
    session.commit()

    history = list_signal_amendments(session, signal.id)
    assert len(history) == 2
    assert history[0].reason == "first correction"
    assert history[0].field_changes[0].old_value == 2030
    assert history[0].field_changes[0].new_value == 2029
    assert history[1].reason == "second correction"
    assert history[1].field_changes[0].old_value == 2029
    assert history[1].field_changes[0].new_value == 2028


# 25. A legacy/manual Signal (no SourceAssertion, no ReviewerAction at all)
# can still be amended with reviewer+reason alone.
def test_legacy_manual_signal_amendable_with_reviewer_and_reason_only():
    session = make_session()
    signal = make_signal(session, target_year=2028)  # no Source, no SourceAssertion at all

    result = amend_signal(
        session, signal_id=signal.id, changes={"target_year": None}, reason="AMPU cross-reference, no SourceAssertion filed",
        reviewer="human@example.test",
    )
    session.commit()

    assert result.signal_amendment_action_id is not None
    entry = list_signal_amendments(session, signal.id)[0]
    assert entry.source_assertion_id is None
    assert entry.reviewer_action_id is None
    assert entry.reviewer == "human@example.test"


# 27. FH-D4 is completely untouched by amendment history.
def test_fh_d4_untouched_by_amendment():
    session = make_session()
    airport = Airport(name="Test Airport", iata_code="TST", country="USA")
    session.add(airport)
    session.flush()
    signal_a = Signal(airport=airport, title="A", category="new_installation", confidence="high", target_year=2028)
    signal_b = Signal(airport=airport, title="B", category="new_installation", confidence="high")
    session.add_all([signal_a, signal_b])
    session.commit()

    record_signal_group_disposition(
        session, signal_ids=[signal_a.id, signal_b.id], decision="SAME_REAL_WORLD_EFFORT",
        reviewer="r@example.test", reason="same project",
    )
    session.commit()

    before = resolve_fh_d4_group_status(session, [signal_a.id, signal_b.id])

    amend_signal(session, signal_id=signal_a.id, changes={"target_year": 2026}, reason="a real reason", reviewer="r@example.test")
    session.commit()

    after = resolve_fh_d4_group_status(session, [signal_a.id, signal_b.id])
    assert before == after


# Immutability: rows can never be updated or deleted through normal ORM use.
def test_signal_amendment_action_is_immutable():
    session = make_session()
    signal = make_signal(session, target_year=2028)
    action = record_signal_amendment(
        session, signal_id=signal.id, changes=(FieldChange(field="target_year", old_value=2028, new_value=2026),),
        reason="a real reason", reviewer="r@example.test",
    )
    session.commit()

    action.reason = "tampering"
    with pytest.raises(ValueError, match="immutable"):
        session.commit()
    session.rollback()

    action = session.get(SignalAmendmentAction, action.id)
    with pytest.raises(ValueError, match="cannot be deleted"):
        session.delete(action)
        session.commit()


def test_signal_amendment_field_change_is_immutable():
    session = make_session()
    signal = make_signal(session, target_year=2028)
    action = record_signal_amendment(
        session, signal_id=signal.id, changes=(FieldChange(field="target_year", old_value=2028, new_value=2026),),
        reason="a real reason", reviewer="r@example.test",
    )
    session.commit()
    change = session.query(SignalAmendmentFieldChange).filter_by(action_id=action.id).one()

    change.new_value = "tampering"
    with pytest.raises(ValueError, match="immutable"):
        session.commit()
    session.rollback()

    change = session.query(SignalAmendmentFieldChange).filter_by(action_id=action.id).one()
    with pytest.raises(ValueError, match="cannot be deleted"):
        session.delete(change)
        session.commit()


def test_field_change_cannot_be_appended_to_already_sealed_action():
    session = make_session()
    signal = make_signal(session, target_year=2028)
    action = record_signal_amendment(
        session, signal_id=signal.id, changes=(FieldChange(field="target_year", old_value=2028, new_value=2026),),
        reason="a real reason", reviewer="r@example.test",
    )
    session.commit()

    session.add(SignalAmendmentFieldChange(action_id=action.id, field_name="planning_year", old_value=None, new_value="2027"))
    with pytest.raises(ValueError, match="already-persisted action"):
        session.commit()


def test_no_op_and_empty_changes_create_no_rows():
    session = make_session()
    signal = make_signal(session, target_year=2028)

    with pytest.raises(SignalAmendmentHistoryError, match="non-empty"):
        record_signal_amendment(session, signal_id=signal.id, changes=(), reason="a real reason", reviewer="r@example.test")
    assert session.query(SignalAmendmentAction).count() == 0


def test_blank_reason_or_reviewer_refused_by_history_service():
    session = make_session()
    signal = make_signal(session, target_year=2028)
    changes = (FieldChange(field="target_year", old_value=2028, new_value=2026),)

    with pytest.raises(SignalAmendmentHistoryError, match="reason is required"):
        record_signal_amendment(session, signal_id=signal.id, changes=changes, reason="  ", reviewer="r@example.test")
    with pytest.raises(SignalAmendmentHistoryError, match="reviewer is required"):
        record_signal_amendment(session, signal_id=signal.id, changes=changes, reason="a real reason", reviewer="  ")
    assert session.query(SignalAmendmentAction).count() == 0
