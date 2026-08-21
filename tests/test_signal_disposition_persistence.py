"""Tests for app/models/signal_disposition.py and
app/services/signal_disposition_persistence.py (D4D1,
docs/architecture/fh-d4-signal-disposition-design.md).

Every test uses an isolated in-memory SQLite database. Modeled on the
already-proven pattern in tests/test_reviewer_action_persistence.py.
Nothing in this file ever opens data/runway_safe.db - grep-verified (see
TestNoRealDatabaseAccess).
"""
from __future__ import annotations

import ast
import inspect as inspect_module

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Signal
from app.models.signal_disposition import (
    ACCEPTING_INITIAL_MEMBERS_ATTR,
    SIGNAL_DISPOSITION_DECISIONS,
    SignalDisposition,
    SignalDispositionMember,
)
from app.services.signal_disposition_persistence import (
    MINIMUM_GROUP_CARDINALITY,
    record_signal_group_disposition,
)


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def make_session_with_foreign_keys_enforced():
    """Mirrors app/database.py's own connect-event listener for the real
    engine - plain create_engine("sqlite:///:memory:") does NOT enforce
    SQLite foreign keys by default."""
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


def _signal(session, *, title="Signal", category="replacement", confidence="high", airport=None, **kwargs):
    if airport is None:
        airport = Airport(name="Test Airport", country="XX")
        session.add(airport)
    s = Signal(airport=airport, title=title, category=category, confidence=confidence, **kwargs)
    session.add(s)
    return s


def _two_signals(session):
    airport = Airport(name="Test Airport", country="XX")
    session.add(airport)
    a = Signal(airport=airport, title="Signal A", category="replacement", confidence="high")
    b = Signal(airport=airport, title="Signal B", category="replacement", confidence="high")
    session.add_all([a, b])
    session.commit()
    return a, b


# ---------------------------------------------------------------------------
# Model shape / decision vocabulary
# ---------------------------------------------------------------------------


class TestModelShape:
    def test_decision_vocabulary_is_exactly_two_values(self):
        assert SIGNAL_DISPOSITION_DECISIONS == ("DISTINCT", "SAME_REAL_WORLD_EFFORT")

    def test_signal_disposition_columns(self):
        cols = set(SignalDisposition.__table__.columns.keys())
        assert cols == {"id", "decision", "reason", "reviewer", "created_at", "supersedes_id"}

    def test_signal_disposition_member_columns(self):
        cols = set(SignalDispositionMember.__table__.columns.keys())
        assert cols == {"id", "disposition_id", "signal_id"}

    def test_no_forbidden_columns_anywhere(self):
        forbidden = {
            "canonical_signal_id", "confidence", "score", "rank", "ranking", "weight",
            "published", "publication", "resolution_state", "fingerprint",
            "raw_evidence", "vendor", "provider", "supplier",
        }
        all_cols = set(SignalDisposition.__table__.columns.keys()) | set(
            SignalDispositionMember.__table__.columns.keys()
        )
        assert not (all_cols & forbidden)


class TestConstraints:
    def test_invalid_decision_rejected_at_db_level_bypassing_service(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        bad = SignalDisposition(decision="MAYBE", reason="x", reviewer="human:reviewer")
        session.add(bad)
        with pytest.raises(Exception):
            session.commit()
        session.rollback()
        session.close(); engine.dispose()

    def test_duplicate_member_row_rejected_at_db_level(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        disposition = SignalDisposition(decision="DISTINCT", reason="x", reviewer="human:reviewer")
        setattr(disposition, ACCEPTING_INITIAL_MEMBERS_ATTR, True)  # this test targets UNIQUE, not the seal guard
        session.add(disposition)
        session.flush()
        session.add(SignalDispositionMember(disposition_id=disposition.id, signal_id=a.id))
        session.add(SignalDispositionMember(disposition_id=disposition.id, signal_id=a.id))
        with pytest.raises(Exception, match="UNIQUE constraint failed"):
            session.commit()
        session.rollback()
        session.close(); engine.dispose()

    def test_member_signal_fk_enforced_at_db_level(self):
        engine, session = make_session_with_foreign_keys_enforced()
        disposition = SignalDisposition(decision="DISTINCT", reason="x", reviewer="human:reviewer")
        setattr(disposition, ACCEPTING_INITIAL_MEMBERS_ATTR, True)  # this test targets the FK, not the seal guard
        session.add(disposition)
        session.flush()
        session.add(SignalDispositionMember(disposition_id=disposition.id, signal_id=999999))
        with pytest.raises(Exception, match="FOREIGN KEY constraint failed"):
            session.commit()
        session.rollback()
        session.close(); engine.dispose()

    def test_supersedes_fk_enforced_at_db_level(self):
        engine, session = make_session_with_foreign_keys_enforced()
        bad = SignalDisposition(decision="DISTINCT", reason="x", reviewer="human:reviewer", supersedes_id=999999)
        session.add(bad)
        with pytest.raises(Exception, match="FOREIGN KEY constraint failed"):
            session.commit()
        session.rollback()
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Golden path / group shapes
# ---------------------------------------------------------------------------


class TestGoldenPath:
    def test_pair(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        disposition = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT",
            reviewer="human:reviewer", reason="Two unrelated grants.",
        )
        session.commit()

        assert disposition.id is not None
        assert disposition.decision == "DISTINCT"
        assert session.query(SignalDisposition).count() == 1
        members = session.query(SignalDispositionMember).filter_by(disposition_id=disposition.id).all()
        assert {m.signal_id for m in members} == {a.id, b.id}
        assert len(members) == 2
        session.close(); engine.dispose()

    def test_triple(self):
        engine, session = make_session()
        airport = Airport(name="Triple Airport", country="XX")
        session.add(airport)
        signals = [Signal(airport=airport, title=f"S{i}", category="replacement", confidence="high") for i in range(3)]
        session.add_all(signals)
        session.commit()
        ids = [s.id for s in signals]

        disposition = record_signal_group_disposition(
            session, signal_ids=ids, decision="DISTINCT", reviewer="human:reviewer",
            reason="Three separate incident-catalogue entries.",
        )
        session.commit()

        members = session.query(SignalDispositionMember).filter_by(disposition_id=disposition.id).all()
        assert {m.signal_id for m in members} == set(ids)
        assert len(members) == 3
        session.close(); engine.dispose()

    def test_quintuple(self):
        engine, session = make_session()
        airport = Airport(name="Quintuple Airport", country="XX")
        session.add(airport)
        signals = [Signal(airport=airport, title=f"S{i}", category="replacement", confidence="high") for i in range(5)]
        session.add_all(signals)
        session.commit()
        ids = [s.id for s in signals]

        disposition = record_signal_group_disposition(
            session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:reviewer",
            reason="Five USAspending grants for one phased project.",
        )
        session.commit()

        members = session.query(SignalDispositionMember).filter_by(disposition_id=disposition.id).all()
        assert {m.signal_id for m in members} == set(ids)
        assert len(members) == 5
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_invalid_decision_value_rejected_by_service(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        with pytest.raises(ValueError, match="decision must be one of"):
            record_signal_group_disposition(
                session, signal_ids=[a.id, b.id], decision="MAYBE",
                reviewer="human:reviewer", reason="x",
            )
        session.close(); engine.dispose()

    def test_single_signal_id_rejected_minimum_cardinality(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        with pytest.raises(ValueError, match="at least 2 distinct"):
            record_signal_group_disposition(
                session, signal_ids=[a.id], decision="DISTINCT",
                reviewer="human:reviewer", reason="x",
            )
        session.close(); engine.dispose()

    def test_empty_signal_ids_rejected(self):
        engine, session = make_session()
        with pytest.raises(ValueError, match="at least 2 distinct"):
            record_signal_group_disposition(
                session, signal_ids=[], decision="DISTINCT",
                reviewer="human:reviewer", reason="x",
            )
        session.close(); engine.dispose()

    def test_minimum_cardinality_constant_is_two(self):
        assert MINIMUM_GROUP_CARDINALITY == 2

    def test_missing_signal_id_rejected(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        with pytest.raises(ValueError, match="referenced Signal does not exist"):
            record_signal_group_disposition(
                session, signal_ids=[a.id, 999999], decision="DISTINCT",
                reviewer="human:reviewer", reason="x",
            )
        session.close(); engine.dispose()

    def test_empty_reviewer_rejected(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        with pytest.raises(ValueError, match="reviewer is required"):
            record_signal_group_disposition(
                session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="", reason="x",
            )
        session.close(); engine.dispose()

    def test_whitespace_only_reviewer_rejected(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        with pytest.raises(ValueError, match="reviewer is required"):
            record_signal_group_disposition(
                session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="   ", reason="x",
            )
        session.close(); engine.dispose()

    def test_empty_reason_rejected(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        with pytest.raises(ValueError, match="reason is required"):
            record_signal_group_disposition(
                session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:reviewer", reason="",
            )
        session.close(); engine.dispose()

    def test_whitespace_only_reason_rejected(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        with pytest.raises(ValueError, match="reason is required"):
            record_signal_group_disposition(
                session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:reviewer", reason="   ",
            )
        session.close(); engine.dispose()

    def test_none_reviewer_raises_matching_established_precedent(self):
        """record_reviewer_action() (the established precedent this
        service is deliberately modeled on) has the identical behavior for
        None: `.strip()` on a None value raises AttributeError, not a
        friendly ValueError, since both functions trust their own `str`
        type hints rather than defending against a caller passing the
        wrong type outright. Verified directly against
        app.services.reviewer_action_persistence.record_reviewer_action()
        during the D4D1 critical review - this is matched, established
        pipeline-wide behavior, not a defect unique to this service."""
        engine, session = make_session()
        a, b = _two_signals(session)
        with pytest.raises(AttributeError):
            record_signal_group_disposition(
                session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer=None, reason="x",
            )
        session.close(); engine.dispose()

    def test_none_reason_raises_matching_established_precedent(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        with pytest.raises(AttributeError):
            record_signal_group_disposition(
                session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:x", reason=None,
            )
        session.close(); engine.dispose()

    def test_tab_and_newline_only_reviewer_reason_rejected(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        with pytest.raises(ValueError, match="reviewer is required"):
            record_signal_group_disposition(
                session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="\t\n", reason="x",
            )
        with pytest.raises(ValueError, match="reason is required"):
            record_signal_group_disposition(
                session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:x", reason="\t\n",
            )
        session.close(); engine.dispose()

    def test_reviewer_and_reason_are_stripped_before_storage(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        disposition = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT",
            reviewer="  human:reviewer  ", reason="  Some reason.  ",
        )
        session.commit()
        assert disposition.reviewer == "human:reviewer"
        assert disposition.reason == "Some reason."
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Duplicate-id normalization / deterministic member set
# ---------------------------------------------------------------------------


class TestDuplicateIdNormalization:
    def test_duplicate_caller_input_normalized_to_exact_member_set(self):
        engine, session = make_session()
        airport = Airport(name="Dedup Airport", country="XX")
        session.add(airport)
        signals = {i: Signal(airport=airport, title=f"S{i}", category="replacement", confidence="high") for i in (2, 3, 5)}
        session.add_all(signals.values())
        session.commit()
        real_ids = {i: signals[i].id for i in (2, 3, 5)}

        disposition = record_signal_group_disposition(
            session,
            signal_ids=[real_ids[5], real_ids[2], real_ids[5], real_ids[3], real_ids[2]],
            decision="DISTINCT", reviewer="human:reviewer", reason="x",
        )
        session.commit()

        members = session.query(SignalDispositionMember).filter_by(disposition_id=disposition.id).all()
        assert {m.signal_id for m in members} == set(real_ids.values())
        assert len(members) == 3  # exactly one row per distinct Signal, not 5
        session.close(); engine.dispose()

    def test_input_order_does_not_affect_stored_member_set(self):
        engine, session = make_session()
        airport = Airport(name="Order Airport", country="XX")
        session.add(airport)
        signals = [Signal(airport=airport, title=f"S{i}", category="replacement", confidence="high") for i in range(3)]
        session.add_all(signals)
        session.commit()
        ids = [s.id for s in signals]

        d1 = record_signal_group_disposition(
            session, signal_ids=list(reversed(ids)), decision="DISTINCT",
            reviewer="human:reviewer", reason="reversed order",
        )
        session.flush()
        members = {m.signal_id for m in session.query(SignalDispositionMember).filter_by(disposition_id=d1.id).all()}
        assert members == set(ids)
        session.rollback()
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Supersession
# ---------------------------------------------------------------------------


class TestSupersession:
    def test_same_exact_set_supersession_succeeds(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        d1 = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT",
            reviewer="human:reviewer_a", reason="Initial review.",
        )
        session.commit()

        d2 = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="SAME_REAL_WORLD_EFFORT",
            reviewer="human:reviewer_b", reason="Corrected on re-review.", supersedes_id=d1.id,
        )
        session.commit()

        assert d2.supersedes_id == d1.id
        assert session.query(SignalDisposition).count() == 2
        session.close(); engine.dispose()

    def test_same_set_different_order_still_supersedes(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        d1 = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:a", reason="x",
        )
        session.commit()
        d2 = record_signal_group_disposition(
            session, signal_ids=[b.id, a.id], decision="SAME_REAL_WORLD_EFFORT",
            reviewer="human:b", reason="y", supersedes_id=d1.id,
        )
        session.commit()
        assert d2.supersedes_id == d1.id
        session.close(); engine.dispose()

    def test_grown_group_rejected_as_supersession(self):
        """old (41,67) -> new (41,67,80) rejected."""
        engine, session = make_session()
        airport = Airport(name="Grow Airport", country="XX")
        session.add(airport)
        s41 = Signal(airport=airport, title="S41", category="replacement", confidence="high")
        s67 = Signal(airport=airport, title="S67", category="replacement", confidence="high")
        s80 = Signal(airport=airport, title="S80", category="replacement", confidence="high")
        session.add_all([s41, s67, s80])
        session.commit()

        d1 = record_signal_group_disposition(
            session, signal_ids=[s41.id, s67.id], decision="DISTINCT", reviewer="human:a", reason="x",
        )
        session.commit()

        with pytest.raises(ValueError, match="exact same member Signal-id set"):
            record_signal_group_disposition(
                session, signal_ids=[s41.id, s67.id, s80.id], decision="DISTINCT",
                reviewer="human:b", reason="y", supersedes_id=d1.id,
            )
        session.close(); engine.dispose()

    def test_shrunk_group_rejected_as_supersession(self):
        """old (41,67,80) -> new (41,67) rejected."""
        engine, session = make_session()
        airport = Airport(name="Shrink Airport", country="XX")
        session.add(airport)
        s41 = Signal(airport=airport, title="S41", category="replacement", confidence="high")
        s67 = Signal(airport=airport, title="S67", category="replacement", confidence="high")
        s80 = Signal(airport=airport, title="S80", category="replacement", confidence="high")
        session.add_all([s41, s67, s80])
        session.commit()

        d1 = record_signal_group_disposition(
            session, signal_ids=[s41.id, s67.id, s80.id], decision="DISTINCT",
            reviewer="human:a", reason="x",
        )
        session.commit()

        with pytest.raises(ValueError, match="exact same member Signal-id set"):
            record_signal_group_disposition(
                session, signal_ids=[s41.id, s67.id], decision="DISTINCT",
                reviewer="human:b", reason="y", supersedes_id=d1.id,
            )
        session.close(); engine.dispose()

    def test_different_same_size_group_rejected_as_supersession(self):
        """old (41,67) -> new (41,80) rejected."""
        engine, session = make_session()
        airport = Airport(name="Different Airport", country="XX")
        session.add(airport)
        s41 = Signal(airport=airport, title="S41", category="replacement", confidence="high")
        s67 = Signal(airport=airport, title="S67", category="replacement", confidence="high")
        s80 = Signal(airport=airport, title="S80", category="replacement", confidence="high")
        session.add_all([s41, s67, s80])
        session.commit()

        d1 = record_signal_group_disposition(
            session, signal_ids=[s41.id, s67.id], decision="DISTINCT", reviewer="human:a", reason="x",
        )
        session.commit()

        with pytest.raises(ValueError, match="exact same member Signal-id set"):
            record_signal_group_disposition(
                session, signal_ids=[s41.id, s80.id], decision="DISTINCT",
                reviewer="human:b", reason="y", supersedes_id=d1.id,
            )
        session.close(); engine.dispose()

    def test_supersedes_id_must_reference_existing_disposition(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        with pytest.raises(ValueError, match="must reference an existing SignalDisposition"):
            record_signal_group_disposition(
                session, signal_ids=[a.id, b.id], decision="DISTINCT",
                reviewer="human:reviewer", reason="x", supersedes_id=999999,
            )
        session.close(); engine.dispose()

    def test_supersession_does_not_mutate_previous_disposition(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        d1 = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT",
            reviewer="human:a", reason="Initial.",
        )
        session.commit()
        snapshot = (d1.decision, d1.reason, d1.reviewer, d1.created_at, d1.supersedes_id)

        record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="SAME_REAL_WORLD_EFFORT",
            reviewer="human:b", reason="Corrected.", supersedes_id=d1.id,
        )
        session.commit()

        assert (d1.decision, d1.reason, d1.reviewer, d1.created_at, d1.supersedes_id) == snapshot
        session.close(); engine.dispose()

    def test_previous_members_remain_untouched_after_supersession(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        d1 = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:a", reason="x",
        )
        session.commit()
        d1_members_before = sorted(
            (m.id, m.disposition_id, m.signal_id)
            for m in session.query(SignalDispositionMember).filter_by(disposition_id=d1.id).all()
        )

        d2 = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="SAME_REAL_WORLD_EFFORT",
            reviewer="human:b", reason="y", supersedes_id=d1.id,
        )
        session.commit()

        d1_members_after = sorted(
            (m.id, m.disposition_id, m.signal_id)
            for m in session.query(SignalDispositionMember).filter_by(disposition_id=d1.id).all()
        )
        assert d1_members_after == d1_members_before
        assert session.query(SignalDispositionMember).filter_by(disposition_id=d2.id).count() == 2
        session.close(); engine.dispose()

    def test_supersedes_target_with_no_persisted_members_rejected(self):
        """§14 attack: an old disposition header exists (constructed
        directly, bypassing the service) but has ZERO member rows -
        malformed relative to what the service itself would ever produce.
        The exact-set comparison is derived from the ACTUAL persisted
        member rows, never from caller assumptions, so an empty real set
        can never equal a non-empty requested set - this fails closed
        without any special-case code for "malformed" input."""
        engine, session = make_session()
        a, b = _two_signals(session)
        malformed = SignalDisposition(decision="DISTINCT", reason="x", reviewer="human:x")
        session.add(malformed)
        session.commit()  # header only - no members were ever added for it

        with pytest.raises(ValueError, match="exact same member Signal-id set"):
            record_signal_group_disposition(
                session, signal_ids=[a.id, b.id], decision="SAME_REAL_WORLD_EFFORT",
                reviewer="human:y", reason="y", supersedes_id=malformed.id,
            )
        session.close(); engine.dispose()

    def test_supersession_cycle_blocked_by_header_immutability(self):
        """§15: true self-supersession is structurally impossible through
        the service's own API (a disposition has no id until after it is
        already validated and flushed, and supersedes_id is only ever
        accepted as a reference to something already existing). A cycle
        (D1 later "superseding" D2, after D2 already legitimately
        supersedes D1) is attacked directly at the model level - blocked by
        the same pre-existing header immutability listener that blocks any
        other post-creation field mutation, not a new mechanism."""
        engine, session = make_session()
        a, b = _two_signals(session)
        d1 = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:x", reason="x",
        )
        session.commit()
        d2 = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="SAME_REAL_WORLD_EFFORT",
            reviewer="human:y", reason="y", supersedes_id=d1.id,
        )
        session.commit()

        d1.supersedes_id = d2.id  # attempt to close the cycle
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
        session.rollback()
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Append-only history
# ---------------------------------------------------------------------------


class TestAppendOnlyHistory:
    def test_full_history_chain_both_entries_remain_queryable(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        d1 = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT",
            reviewer="human:a", reason="Initial DISTINCT call.",
        )
        session.commit()
        d2 = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="SAME_REAL_WORLD_EFFORT",
            reviewer="human:b", reason="Later evidence changed the call.", supersedes_id=d1.id,
        )
        session.commit()

        assert session.query(SignalDisposition).count() == 2
        fetched_d1 = session.get(SignalDisposition, d1.id)
        fetched_d2 = session.get(SignalDisposition, d2.id)
        assert fetched_d1.decision == "DISTINCT"
        assert fetched_d2.decision == "SAME_REAL_WORLD_EFFORT"
        assert fetched_d2.supersedes_id == fetched_d1.id
        session.close(); engine.dispose()


class TestImmutability:
    def test_header_update_blocked(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        d = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:a", reason="x",
        )
        session.commit()
        d.reason = "mutated"
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
        session.rollback()
        session.close(); engine.dispose()

    def test_header_delete_blocked(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        d = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:a", reason="x",
        )
        session.commit()
        session.delete(d)
        with pytest.raises(ValueError, match="auditable and cannot be deleted"):
            session.commit()
        session.rollback()
        session.close(); engine.dispose()

    def test_member_update_blocked(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        d = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:a", reason="x",
        )
        session.commit()
        member = session.query(SignalDispositionMember).filter_by(disposition_id=d.id).first()
        current_signal_id = member.signal_id
        other_signal_id = b.id if current_signal_id == a.id else a.id
        member.signal_id = other_signal_id
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
        session.rollback()
        session.close(); engine.dispose()

    def test_member_delete_blocked(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        d = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:a", reason="x",
        )
        session.commit()
        member = session.query(SignalDispositionMember).filter_by(disposition_id=d.id).first()
        session.delete(member)
        with pytest.raises(ValueError, match="auditable and cannot be deleted"):
            session.commit()
        session.rollback()
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Member-set immutability after persistence (critical-review addition)
#
# before_update/before_delete alone do NOT protect a disposition's own
# member SET: inserting a brand-new SignalDispositionMember row is neither
# an UPDATE nor a DELETE of any existing row, so neither pre-existing
# listener ever fired for it - a real, reproduced gap where a plain
# session.add(SignalDispositionMember(disposition_id=<already-committed>,
# signal_id=<new>)) silently extended an already-reviewed group's own
# historical membership. Fixed via ACCEPTING_INITIAL_MEMBERS_ATTR, a
# transient (never persisted) attribute record_signal_group_disposition()
# sets only for the duration of its own initial member-insert batch; see
# app/models/signal_disposition.py's own before_insert listener.
# ---------------------------------------------------------------------------


class TestMemberSetImmutabilityAfterPersistence:
    def test_direct_session_add_member_after_disposition_committed_is_blocked(self):
        engine, session = make_session()
        airport = Airport(name="Seal Airport", country="XX")
        session.add(airport)
        a = Signal(airport=airport, title="A", category="replacement", confidence="high")
        b = Signal(airport=airport, title="B", category="replacement", confidence="high")
        c = Signal(airport=airport, title="C - injected later", category="replacement", confidence="high")
        session.add_all([a, b, c])
        session.commit()

        d = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:x", reason="y",
        )
        session.commit()

        session.add(SignalDispositionMember(disposition_id=d.id, signal_id=c.id))
        with pytest.raises(ValueError, match="cannot be appended to an already-persisted disposition"):
            session.commit()
        session.rollback()

        members = {m.signal_id for m in session.query(SignalDispositionMember).filter_by(disposition_id=d.id).all()}
        assert members == {a.id, b.id}  # unchanged - the injected member never landed
        session.close(); engine.dispose()

    def test_direct_session_add_member_same_session_no_intervening_commit_is_blocked(self):
        """Attacks the narrower window: the caller never commits between the
        legitimate creation and the attack - only rollback happens after."""
        engine, session = make_session()
        a, b = _two_signals(session)
        airport = session.get(Airport, a.airport_id)
        c = Signal(airport=airport, title="C", category="replacement", confidence="high")
        session.add(c)
        session.flush()

        d = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:x", reason="y",
        )
        # No commit here - straight into the attack within the same open transaction.
        session.add(SignalDispositionMember(disposition_id=d.id, signal_id=c.id))
        with pytest.raises(ValueError, match="cannot be appended to an already-persisted disposition"):
            session.flush()
        session.rollback()
        session.close(); engine.dispose()

    def test_appending_member_to_disposition_loaded_in_a_fresh_session_is_blocked(self):
        """The disposition was never constructed in THIS session at all -
        proves the guard works from cold, not just via a leftover marker on
        a live Python object."""
        engine, session = make_session()
        a, b = _two_signals(session)
        d = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:x", reason="y",
        )
        session.commit()
        disposition_id = d.id
        airport_id = a.airport_id
        session.close()

        fresh_session = Session(engine)
        airport = fresh_session.get(Airport, airport_id)
        c = Signal(airport=airport, title="C", category="replacement", confidence="high")
        fresh_session.add(c)
        fresh_session.commit()

        fresh_session.add(SignalDispositionMember(disposition_id=disposition_id, signal_id=c.id))
        with pytest.raises(ValueError, match="cannot be appended to an already-persisted disposition"):
            fresh_session.commit()
        fresh_session.rollback()
        fresh_session.close()
        engine.dispose()

    def test_relationship_collection_append_after_persistence_is_blocked(self):
        """§18-style attack: disposition.members.append(...) rather than a
        bare session.add(). SQLAlchemy marks the parent as dirty when its
        collection changes, which independently trips SignalDisposition's
        own pre-existing before_update listener - a different mechanism
        than the before_insert guard above, worth its own permanent test
        since it is not obvious in advance that this path is protected at
        all."""
        engine, session = make_session()
        a, b = _two_signals(session)
        airport = session.get(Airport, a.airport_id)
        c = Signal(airport=airport, title="C", category="replacement", confidence="high")
        session.add(c)
        session.commit()

        d = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:x", reason="y",
        )
        session.commit()

        d.members.append(SignalDispositionMember(signal_id=c.id))
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
        session.rollback()
        session.close(); engine.dispose()

    def test_marker_attribute_cleared_after_successful_creation(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        d = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:x", reason="y",
        )
        assert getattr(d, ACCEPTING_INITIAL_MEMBERS_ATTR) is False
        session.commit()
        session.close(); engine.dispose()

    def test_disposition_stays_sealed_after_a_failed_member_insertion_attempt(self, monkeypatch):
        """Attacks the window right after a failed
        record_signal_group_disposition() call, before the caller rolls
        back: the header row is pending (flushed, has a real id - see
        TestFailureAtomicity), and a rogue member insert against that same
        disposition_id must still be blocked.

        Deliberately does NOT assert identity (`is`) against the original
        `disposition` object the service constructed: since the call raises
        before returning it, the caller here never receives a reference,
        the service's own local variable is the only one that existed, and
        it is freed once the function's stack frame unwinds - SQLAlchemy's
        identity map holds objects by weak reference, so a later query in
        this exact scenario may legitimately construct a fresh instance
        rather than return the original one. That is fine and not a
        weakening of the fix: a freshly-loaded instance was never marked
        with ACCEPTING_INITIAL_MEMBERS_ATTR at all, so the guard's own
        fail-closed default (missing attribute => blocked) protects it
        just as strongly as it would protect the original object - this
        test proves that real property directly, rather than an identity
        detail that depends on garbage-collection timing."""
        engine, session = make_session()
        a, b = _two_signals(session)

        real_init = SignalDispositionMember.__init__
        call_count = {"n": 0}

        def _boom(self, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated failure")
            real_init(self, *args, **kwargs)

        monkeypatch.setattr(SignalDispositionMember, "__init__", _boom)
        try:
            with pytest.raises(RuntimeError, match="simulated failure"):
                record_signal_group_disposition(
                    session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:x", reason="y",
                )
        finally:
            monkeypatch.undo()

        pending_disposition = session.query(SignalDisposition).one()
        session.add(SignalDispositionMember(disposition_id=pending_disposition.id, signal_id=a.id))
        with pytest.raises(ValueError, match="cannot be appended to an already-persisted disposition"):
            session.commit()
        session.rollback()
        session.close(); engine.dispose()

    def test_marker_attribute_is_never_a_mapped_column(self):
        assert ACCEPTING_INITIAL_MEMBERS_ATTR not in SignalDisposition.__table__.columns.keys()
        assert ACCEPTING_INITIAL_MEMBERS_ATTR not in SignalDispositionMember.__table__.columns.keys()

    def test_two_dispositions_created_in_the_same_session_do_not_interfere(self):
        """Isolation: the marker is per-instance, not global/session-wide -
        creating disposition D1 must not leave a stray True flag that lets
        an attack against a completely unrelated, pre-existing disposition
        D0 slip through."""
        engine, session = make_session()
        a, b = _two_signals(session)
        airport = session.get(Airport, a.airport_id)
        c = Signal(airport=airport, title="C", category="replacement", confidence="high")
        d_extra = Signal(airport=airport, title="D", category="replacement", confidence="high")
        session.add_all([c, d_extra])
        session.commit()

        d0 = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:x", reason="first",
        )
        session.commit()

        # Legitimate second, unrelated disposition in the same session.
        d1 = record_signal_group_disposition(
            session, signal_ids=[c.id, d_extra.id], decision="DISTINCT", reviewer="human:y", reason="second",
        )
        session.commit()
        assert {m.signal_id for m in d1.members} == {c.id, d_extra.id}

        # d0 must still be sealed even after d1's own creation cycle ran.
        session.add(SignalDispositionMember(disposition_id=d0.id, signal_id=c.id))
        with pytest.raises(ValueError, match="cannot be appended to an already-persisted disposition"):
            session.commit()
        session.rollback()
        session.close(); engine.dispose()

    def test_legitimate_supersession_still_works_after_the_fix(self):
        """Confirms the fix does not collaterally break the one legitimate
        case that also creates a fresh disposition + fresh member batch
        referencing Signals that already belong to an older, sealed
        disposition."""
        engine, session = make_session()
        a, b = _two_signals(session)
        d1 = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:x", reason="x",
        )
        session.commit()
        d2 = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="SAME_REAL_WORLD_EFFORT",
            reviewer="human:y", reason="y", supersedes_id=d1.id,
        )
        session.commit()
        assert {m.signal_id for m in d2.members} == {a.id, b.id}
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Transaction ownership / non-commit
# ---------------------------------------------------------------------------


class TestTransactionOwnership:
    def test_record_signal_group_disposition_never_commits(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:a", reason="x",
        )
        session.rollback()
        assert session.query(SignalDisposition).count() == 0
        assert session.query(SignalDispositionMember).count() == 0
        session.close(); engine.dispose()

    def test_service_module_never_calls_session_commit_ast(self):
        import app.services.signal_disposition_persistence as module

        tree = ast.parse(inspect_module.getsource(module))
        commit_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "commit"
        ]
        assert commit_calls == []

    def test_service_does_not_construct_its_own_engine_or_session(self):
        # AST-based, not a raw substring scan: the module's own docstring
        # legitimately mentions "SessionLocal" as prose explaining what it
        # deliberately does NOT do - only real code identifiers count here.
        import app.services.signal_disposition_persistence as module

        tree = ast.parse(inspect_module.getsource(module))
        code_identifiers = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert "create_engine" not in code_identifiers
        assert "SessionLocal" not in code_identifiers


# ---------------------------------------------------------------------------
# Failure atomicity
# ---------------------------------------------------------------------------


class TestFailureAtomicity:
    def test_invalid_member_signal_leaves_no_pending_state(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        with pytest.raises(ValueError, match="referenced Signal does not exist"):
            record_signal_group_disposition(
                session, signal_ids=[a.id, 999999], decision="DISTINCT",
                reviewer="human:reviewer", reason="x",
            )
        # Validation-only failure: nothing was ever added to the session.
        assert len(session.new) == 0
        assert session.query(SignalDisposition).count() == 0
        session.close(); engine.dispose()

    def test_supersession_mismatch_leaves_no_pending_state(self):
        engine, session = make_session()
        a, b = _two_signals(session)
        d1 = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:a", reason="x",
        )
        session.commit()
        airport = Airport(name="Other", country="XX")
        session.add(airport)
        c = Signal(airport=airport, title="C", category="replacement", confidence="high")
        session.add(c)
        session.commit()

        with pytest.raises(ValueError, match="exact same member Signal-id set"):
            record_signal_group_disposition(
                session, signal_ids=[a.id, c.id], decision="DISTINCT",
                reviewer="human:b", reason="y", supersedes_id=d1.id,
            )
        assert len(session.new) == 0
        assert session.query(SignalDisposition).count() == 1  # only d1
        session.close(); engine.dispose()

    def test_forced_failure_between_header_and_member_flush_is_recoverable_by_caller_rollback(self, monkeypatch):
        """Documents the real atomicity boundary (design doc / D4D1 report):
        the header row is flushed (and thus visible within the same
        uncommitted transaction) before member rows are added - a failure
        in that window leaves a PENDING, UNCOMMITTED header row with zero
        members. This is not silently durable: nothing is committed unless
        the caller calls session.commit() - and a caller that instead
        rolls back (the documented, expected recovery path, matching
        governed_signal_creation.py's own identical two-step
        Signal-then-link precedent) ends up with zero rows of either kind.
        The service's own guarantee is 'flush only, never commit'; full
        atomicity across the header+member boundary is the CALLER's
        responsibility via rollback, not something this non-committing
        service can unilaterally provide - this test proves that boundary
        precisely rather than overclaiming it."""
        engine, session = make_session()
        a, b = _two_signals(session)

        import app.services.signal_disposition_persistence as module

        real_member_init = SignalDispositionMember.__init__
        call_count = {"n": 0}

        def _boom(self, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated failure while inserting the first member row")
            real_member_init(self, *args, **kwargs)

        monkeypatch.setattr(SignalDispositionMember, "__init__", _boom)
        with pytest.raises(RuntimeError, match="simulated failure"):
            record_signal_group_disposition(
                session, signal_ids=[a.id, b.id], decision="DISTINCT",
                reviewer="human:reviewer", reason="x",
            )
        monkeypatch.undo()

        # The header row IS pending in the session (flushed, has a real id)
        # at the moment of failure - proving the two-flush window is real.
        pending_headers = session.query(SignalDisposition).all()
        assert len(pending_headers) == 1
        assert session.query(SignalDispositionMember).count() == 0

        # But nothing is durable: the caller's rollback discards it fully.
        session.rollback()
        assert session.query(SignalDisposition).count() == 0
        assert session.query(SignalDispositionMember).count() == 0
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Signal safety / provenance preservation
# ---------------------------------------------------------------------------


class TestSignalSafety:
    def test_signals_unchanged_after_disposition(self):
        engine, session = make_session()
        airport = Airport(name="Safety Airport", country="XX")
        session.add(airport)
        a = Signal(
            airport=airport, title="Signal A", category="replacement", confidence="high",
            source_id=None, published=True, status="identified",
        )
        b = Signal(
            airport=airport, title="Signal B", category="new_installation", confidence="medium",
            published=False,
        )
        session.add_all([a, b])
        session.commit()

        def snapshot(sig):
            return (
                sig.airport_id, sig.runway_id, sig.source_id, sig.title, sig.category,
                sig.confidence, sig.status, sig.published, sig.installation_id,
            )

        before_a, before_b = snapshot(a), snapshot(b)

        record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="SAME_REAL_WORLD_EFFORT",
            reviewer="human:reviewer", reason="x",
        )
        session.commit()

        assert snapshot(a) == before_a
        assert snapshot(b) == before_b
        session.close(); engine.dispose()

    def test_source_id_and_publication_unchanged(self):
        engine, session = make_session()
        from app.models import Source
        airport = Airport(name="Prov Airport", country="XX")
        source = Source(title="Some Source", source_type="official")
        session.add_all([airport, source])
        session.commit()
        a = Signal(airport=airport, title="A", category="replacement", confidence="high", source_id=source.id, published=True)
        b = Signal(airport=airport, title="B", category="replacement", confidence="high", published=False)
        session.add_all([a, b])
        session.commit()

        record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:reviewer", reason="x",
        )
        session.commit()

        assert a.source_id == source.id
        assert a.published is True
        assert b.source_id is None
        assert b.published is False
        session.close(); engine.dispose()

    def test_existing_source_assertion_links_unchanged(self):
        engine, session = make_session()
        from app.models import Source, SourceAssertion
        airport = Airport(name="SA Airport", country="XX")
        source = Source(title="Some Source", source_type="official")
        session.add_all([airport, source])
        session.commit()
        a = Signal(airport=airport, title="A", category="replacement", confidence="high")
        b = Signal(airport=airport, title="B", category="replacement", confidence="high")
        session.add_all([a, b])
        session.commit()
        assertion = SourceAssertion(
            source=source, airport=airport, assertion_type="project_construction",
            source_record_identifier="sa-1", signal_id=a.id,
        )
        session.add(assertion)
        session.commit()
        assertion_id = assertion.id

        record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="SAME_REAL_WORLD_EFFORT",
            reviewer="human:reviewer", reason="x",
        )
        session.commit()

        refetched = session.get(SourceAssertion, assertion_id)
        assert refetched.signal_id == a.id  # untouched - no provenance moved to b
        session.close(); engine.dispose()

    def test_deleting_a_member_signal_fails_safely_with_fk_enforced(self):
        engine, session = make_session_with_foreign_keys_enforced()
        airport = Airport(name="Delete Airport", country="XX")
        session.add(airport)
        a = Signal(airport=airport, title="A", category="replacement", confidence="high")
        b = Signal(airport=airport, title="B", category="replacement", confidence="high")
        session.add_all([a, b])
        session.commit()

        record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:reviewer", reason="x",
        )
        session.commit()

        session.delete(a)
        with pytest.raises(Exception, match="FOREIGN KEY constraint failed"):
            session.commit()
        session.rollback()
        assert session.query(Signal).count() == 2  # signal not lost
        assert session.query(SignalDispositionMember).count() == 2  # membership not lost
        session.close(); engine.dispose()

    def test_deleting_a_non_member_signal_is_unaffected(self):
        engine, session = make_session_with_foreign_keys_enforced()
        airport = Airport(name="Unrelated Airport", country="XX")
        session.add(airport)
        a = Signal(airport=airport, title="A", category="replacement", confidence="high")
        b = Signal(airport=airport, title="B", category="replacement", confidence="high")
        c = Signal(airport=airport, title="C (not in any disposition)", category="replacement", confidence="high")
        session.add_all([a, b, c])
        session.commit()

        record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:reviewer", reason="x",
        )
        session.commit()

        session.delete(c)
        session.commit()  # not a disposition member - deletes cleanly
        assert session.query(Signal).count() == 2
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Information firewall
# ---------------------------------------------------------------------------


class TestInformationFirewall:
    _FORBIDDEN_SIGNAL_ATTRS = (
        "title", "notes", "source_notes", "estimated_total_value_usd",
        "estimated_emas_value_usd", "supplier", "likely_supplier", "supplier_reason",
        "confirmed_vendor", "category", "confidence", "status", "manual_year_estimate",
    )

    def test_service_module_never_references_forbidden_signal_attributes_ast(self):
        import app.services.signal_disposition_persistence as module

        tree = ast.parse(inspect_module.getsource(module))
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert not (attrs & set(self._FORBIDDEN_SIGNAL_ATTRS))

    def test_service_module_only_imports_expected_names(self):
        import app.services.signal_disposition_persistence as module

        tree = ast.parse(inspect_module.getsource(module))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module != "__future__":
                imported |= {alias.name for alias in node.names}
        assert imported == {
            "Optional", "Sequence", "Session", "Signal",
            "ACCEPTING_INITIAL_MEMBERS_ATTR", "SIGNAL_DISPOSITION_DECISIONS",
            "SignalDisposition", "SignalDispositionMember",
        }

    def test_behavioral_no_financial_or_title_leak_into_disposition_fields(self):
        engine, session = make_session()
        airport = Airport(name="Firewall Airport", country="XX")
        session.add(airport)
        a = Signal(
            airport=airport, title="SECRET TITLE TEXT", category="replacement", confidence="high",
            estimated_total_value_usd=123456789,
        )
        b = Signal(airport=airport, title="OTHER SECRET TITLE", category="replacement", confidence="high")
        session.add_all([a, b])
        session.commit()

        disposition = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:reviewer", reason="x",
        )
        session.commit()

        assert "SECRET" not in disposition.reason
        assert "123456789" not in disposition.reason
        for member in disposition.members:
            assert not hasattr(member, "title")
            assert not hasattr(member, "estimated_total_value_usd")
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# International / Unicode
# ---------------------------------------------------------------------------


class TestInternational:
    def test_unicode_reviewer_and_reason_round_trip(self):
        engine, session = make_session()
        airport = Airport(name="Örnsköldsvik Flygplats", country="Sverige")
        session.add(airport)
        a = Signal(airport=airport, title="Örnsköldsvik – signal A", category="replacement", confidence="high")
        b = Signal(airport=airport, title="Örnsköldsvik – signal B", category="replacement", confidence="high")
        session.add_all([a, b])
        session.commit()

        disposition = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="SAME_REAL_WORLD_EFFORT",
            reviewer="Åsa Lindqvist", reason="Två signaler beskriver samma EMAS-ersättning vid samma bana.",
        )
        session.commit()

        refetched = session.get(SignalDisposition, disposition.id)
        assert refetched.reviewer == "Åsa Lindqvist"
        assert refetched.reason == "Två signaler beskriver samma EMAS-ersättning vid samma bana."
        session.close(); engine.dispose()

    def test_no_us_specific_assumption_in_generic_international_fixture(self):
        engine, session = make_session()
        airport = Airport(name="東京国際空港", country="日本")
        session.add(airport)
        a = Signal(airport=airport, title="東京 - シグナル A", category="replacement", confidence="high")
        b = Signal(airport=airport, title="東京 - シグナル B", category="replacement", confidence="high")
        session.add_all([a, b])
        session.commit()

        disposition = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT",
            reviewer="レビュアー", reason="別々の空港整備事業。",
        )
        session.commit()
        assert disposition.id is not None
        session.close(); engine.dispose()


class TestDeterminism:
    def test_repeated_construction_of_same_group_produces_independent_rows(self):
        """Not idempotent by design - two calls for the same member set with
        no supersession relationship are two independent dispositions (the
        design doc never claims idempotency at this layer; 'latest' logic
        is explicitly deferred to D4D3)."""
        engine, session = make_session()
        a, b = _two_signals(session)
        d1 = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:a", reason="x",
        )
        session.commit()
        d2 = record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:a", reason="x",
        )
        session.commit()
        assert d1.id != d2.id
        assert session.query(SignalDisposition).count() == 2
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Real-database non-access proof
# ---------------------------------------------------------------------------


def _string_constants_excluding_module_docstring(tree: ast.Module) -> "list[str]":
    """Every string literal in a module EXCEPT its own leading module
    docstring - so a module (or this very test file) may legitimately
    explain in prose what it deliberately does not do (e.g. name the real
    database file to say it is never opened) without tripping a substring
    scan meant to catch real, executable references."""
    body = list(tree.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    literals: "list[str]" = []
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.append(node.value)
    return literals


class TestNoRealDatabaseAccess:
    def test_no_reference_to_the_real_database_path_anywhere_in_this_file(self):
        this_file_source = inspect_module.getsource(inspect_module.getmodule(TestNoRealDatabaseAccess))
        tree = ast.parse(this_file_source)
        literals = _string_constants_excluding_module_docstring(tree)
        real_db_filename = "runway_safe" + ".db"  # built at runtime, not a literal, so this
        # check does not trip over its own search target when scanning this file's own AST
        assert not any(real_db_filename in literal for literal in literals)

    def test_service_and_model_modules_never_reference_the_real_database_path(self):
        import app.models.signal_disposition as model_module
        import app.services.signal_disposition_persistence as service_module

        for module in (model_module, service_module):
            tree = ast.parse(inspect_module.getsource(module))
            literals = _string_constants_excluding_module_docstring(tree)
            real_db_filename = "runway_safe" + ".db"  # built at runtime, not a literal, so this
        # check does not trip over its own search target when scanning this file's own AST
        assert not any(real_db_filename in literal for literal in literals)

        service_tree = ast.parse(inspect_module.getsource(service_module))
        code_identifiers = {
            node.id for node in ast.walk(service_tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(service_tree) if isinstance(node, ast.Attribute)
        }
        assert "SessionLocal" not in code_identifiers
