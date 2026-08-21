"""Tests for scripts/review_signal_disposition.py (D4D5).

Every test uses an isolated tmp_path-scoped SQLite database file - nothing
here ever opens data/runway_safe.db (see TestNoRealDatabaseAccess).
"""
from __future__ import annotations

import ast
import dataclasses
import hashlib
import inspect as inspect_module

import pytest
from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Signal, Source, SourceAssertion
from app.models.reviewer_action import ReviewerAction
from app.models.signal_disposition import SignalDisposition, SignalDispositionMember
from app.services.signal_disposition_persistence import record_signal_group_disposition
import scripts.migrate_signal_disposition_d4d2 as migration
import scripts.review_signal_disposition as review_module
from scripts.review_signal_disposition import (
    SCHEMA_MIGRATION_REQUIRED_BLOCKER,
    TARGET_GROUP_NOT_CURRENT_BLOCKER,
    SignalDispositionReviewConfig,
    build_engine,
    render_result,
    run_review,
)


def _make_full_db(path):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    return engine


def _make_pre_d4d2_db(path):
    engine = create_engine(f"sqlite:///{path}")
    meta = MetaData()
    for name, table in Base.metadata.tables.items():
        if name not in ("signal_dispositions", "signal_disposition_members"):
            table.to_metadata(meta)
    meta.create_all(engine)
    return engine


def _add_fh_d4_group(engine, n, airport_name="Test Airport"):
    with Session(engine) as session:
        airport = Airport(name=airport_name, country="XX")
        session.add(airport)
        session.commit()
        sigs = [Signal(airport=airport, title=f"S{i}", category="replacement", confidence="high") for i in range(n)]
        session.add_all(sigs)
        session.commit()
        return airport.id, tuple(s.id for s in sigs)


def _grow_group(engine, airport_id, title="EXTRA"):
    with Session(engine) as session:
        extra = Signal(airport_id=airport_id, title=title, category="replacement", confidence="high")
        session.add(extra)
        session.commit()
        return extra.id


def _remove_from_group(engine, signal_id):
    """Gives a Signal a runway claim so FH-D4 no longer includes it - never
    deletes a Signal (which could violate FK protections once
    dispositioned)."""
    from app.models import Runway

    with Session(engine) as session:
        signal = session.get(Signal, signal_id)
        runway = Runway(airport_id=signal.airport_id, designation="09/27")
        session.add(runway)
        session.commit()
        signal.runway_id = runway.id
        session.commit()


def _file_sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


# ---------------------------------------------------------------------------
# Schema gate
# ---------------------------------------------------------------------------


class TestSchemaGate:
    def test_missing_schema_blocks_overview(self, tmp_path):
        db = tmp_path / "t.db"
        _make_pre_d4d2_db(db)
        result = run_review(SignalDispositionReviewConfig(database=db))
        assert result.blockers == (SCHEMA_MIGRATION_REQUIRED_BLOCKER,)
        assert result.schema_readiness["ready"] is False

    def test_missing_schema_blocks_targeted_inspect(self, tmp_path):
        db = tmp_path / "t.db"
        _make_pre_d4d2_db(db)
        result = run_review(SignalDispositionReviewConfig(database=db, signal_ids=(1, 2)))
        assert result.blockers == (SCHEMA_MIGRATION_REQUIRED_BLOCKER,)

    def test_missing_schema_blocks_write(self, tmp_path):
        db = tmp_path / "t.db"
        _make_pre_d4d2_db(db)
        result = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=(1, 2), decision="DISTINCT",
            reviewer="human:x", reason="x", allow_database_write=True,
        ))
        assert result.blockers == (SCHEMA_MIGRATION_REQUIRED_BLOCKER,)

    def test_no_raw_sqlalchemy_stack_trace_for_missing_schema(self, tmp_path):
        """The blocker is a structured, deterministic result field - never
        an uncaught exception for this expected, common case."""
        db = tmp_path / "t.db"
        _make_pre_d4d2_db(db)
        result = run_review(SignalDispositionReviewConfig(database=db))
        assert isinstance(result.blockers, tuple)

    def test_migration_created_schema_ready(self, tmp_path):
        db = tmp_path / "t.db"
        _make_pre_d4d2_db(db)
        migration.upgrade(db)
        result = run_review(SignalDispositionReviewConfig(database=db))
        assert result.blockers == ()
        assert result.schema_readiness["ready"] is True


# ---------------------------------------------------------------------------
# Overview (inspect, no target)
# ---------------------------------------------------------------------------


class TestOverviewInspect:
    def test_overview_shows_attention_required_group(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)

        result = run_review(SignalDispositionReviewConfig(database=db))

        assert len(result.attention_required) == 1
        summary = result.attention_required[0]
        assert summary.signal_ids == tuple(sorted(ids))
        assert summary.status == "UNREVIEWED"
        assert summary.ambiguous_history is False
        assert result.confirmed_distinct == ()
        assert result.confirmed_same_effort == ()

    def test_overview_separates_resolved_groups(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)
        with Session(engine) as session:
            record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
            session.commit()

        result = run_review(SignalDispositionReviewConfig(database=db))
        assert result.attention_required == ()
        assert len(result.confirmed_distinct) == 1
        assert result.confirmed_distinct[0].signal_ids == tuple(sorted(ids))

    def test_overview_is_read_only(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        _add_fh_d4_group(engine, 2)

        statements = []
        engine2 = create_engine(f"sqlite:///{db}")

        def _capture(_conn, _cursor, statement, *_args, **_kwargs):
            statements.append(statement.strip().upper())

        event.listen(engine2, "before_cursor_execute", _capture)
        # Route the CLI through an instrumented engine by monkeypatching
        # build_engine for this one call.
        original_build = review_module.build_engine
        review_module.build_engine = lambda database, *, writable: engine2
        try:
            run_review(SignalDispositionReviewConfig(database=db))
        finally:
            review_module.build_engine = original_build
            event.remove(engine2, "before_cursor_execute", _capture)

        writes = [s for s in statements if s.startswith(("INSERT", "UPDATE", "DELETE"))]
        assert writes == []


# ---------------------------------------------------------------------------
# Target group identity / current-group validation
# ---------------------------------------------------------------------------


class TestTargetedInspect:
    def test_target_exact_active_group(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)

        result = run_review(SignalDispositionReviewConfig(database=db, signal_ids=ids))
        assert result.target_group_found is True
        assert result.signal_ids == tuple(sorted(ids))
        assert result.status == "UNREVIEWED"
        assert result.airport_id == airport_id
        assert result.raw_summary is not None

    def test_target_not_a_current_group_rejected(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        _add_fh_d4_group(engine, 2)

        result = run_review(SignalDispositionReviewConfig(database=db, signal_ids=(999997, 999998)))
        assert result.target_group_found is False
        assert result.blockers == (TARGET_GROUP_NOT_CURRENT_BLOCKER,)

    def test_normalizes_duplicate_and_reversed_ids(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)

        result = run_review(SignalDispositionReviewConfig(database=db, signal_ids=(ids[1], ids[0], ids[1])))
        assert result.target_group_found is True
        assert result.signal_ids == tuple(sorted(ids))

    def test_fewer_than_two_ids_rejected(self, tmp_path):
        db = tmp_path / "t.db"
        _make_full_db(db)
        with pytest.raises(ValueError, match="at least 2"):
            run_review(SignalDispositionReviewConfig(database=db, signal_ids=(1,)))

    @pytest.mark.parametrize("n", [2, 3, 5])
    def test_pair_triple_quintuple_targets(self, tmp_path, n):
        db = tmp_path / f"t{n}.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, n)
        result = run_review(SignalDispositionReviewConfig(database=db, signal_ids=ids))
        assert result.target_group_found is True
        assert result.signal_ids == tuple(sorted(ids))


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_distinct_dry_run_shows_plan_no_write(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)

        result = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="reason text",
        ))
        assert result.action_eligible is True
        assert result.written is False
        assert result.proposed_decision == "DISTINCT"
        assert result.planned_reviewer == "human:x"
        assert result.planned_reason == "reason text"
        assert result.planned_supersedes_id is None  # UNREVIEWED - no prior exact disposition

        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 0

    def test_same_dry_run_shows_plan_no_write(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)

        result = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="x",
        ))
        assert result.action_eligible is True
        assert result.written is False
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 0

    def test_dry_run_requires_reviewer_and_reason(self, tmp_path):
        db = tmp_path / "t.db"
        _make_full_db(db)
        with pytest.raises(ValueError, match="reviewer"):
            run_review(SignalDispositionReviewConfig(database=db, signal_ids=(1, 2), decision="DISTINCT", reason="x"))
        with pytest.raises(ValueError, match="reason"):
            run_review(SignalDispositionReviewConfig(database=db, signal_ids=(1, 2), decision="DISTINCT", reviewer="human:x"))

    def test_dry_run_is_read_only_instrumented(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)

        statements = []
        engine2 = create_engine(f"sqlite:///{db}")

        def _capture(_conn, _cursor, statement, *_args, **_kwargs):
            statements.append(statement.strip().upper())

        event.listen(engine2, "before_cursor_execute", _capture)
        original_build = review_module.build_engine
        review_module.build_engine = lambda database, *, writable: engine2
        try:
            run_review(SignalDispositionReviewConfig(
                database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x",
            ))
        finally:
            review_module.build_engine = original_build
            event.remove(engine2, "before_cursor_execute", _capture)

        writes = [s for s in statements if s.startswith(("INSERT", "UPDATE", "DELETE"))]
        assert writes == []


# ---------------------------------------------------------------------------
# Write authorization / write path
# ---------------------------------------------------------------------------


class TestWriteAuthorization:
    def test_write_requires_allow_database_write_flag(self, tmp_path):
        """Without --allow-database-write, no write occurs even when
        eligible - dry-run is the default for any decision-bearing call."""
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)

        result = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x",
        ))
        assert result.written is False
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 0

    def test_allow_database_write_requires_decision(self, tmp_path):
        db = tmp_path / "t.db"
        _make_full_db(db)
        with pytest.raises(ValueError, match="--decision"):
            run_review(SignalDispositionReviewConfig(database=db, signal_ids=(1, 2), allow_database_write=True))

    def test_inspect_uses_readonly_engine(self, tmp_path):
        db = tmp_path / "t.db"
        _make_full_db(db)
        engine = build_engine(db, writable=False)
        with pytest.raises(Exception):
            with engine.connect() as conn:
                conn.exec_driver_sql("INSERT INTO airports (name, country) VALUES ('x','XX')")
        engine.dispose()


class TestDistinctWrite:
    def test_distinct_write_records_exactly_one_disposition(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)

        result = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x", allow_database_write=True,
        ))
        assert result.written is True
        assert result.written_disposition_id is not None

        with Session(engine) as verify:
            headers = verify.query(SignalDisposition).all()
            members = verify.query(SignalDispositionMember).all()
            assert len(headers) == 1
            assert headers[0].decision == "DISTINCT"
            assert headers[0].supersedes_id is None
            assert len(members) == len(ids)
            assert {m.signal_id for m in members} == set(ids)


class TestSameWrite:
    def test_same_write_records_exactly_one_disposition_no_canonical_signal(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)

        result = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="x", allow_database_write=True,
        ))
        assert result.written is True
        assert not hasattr(result, "canonical_signal_id")

        with Session(engine) as verify:
            headers = verify.query(SignalDisposition).all()
            members = verify.query(SignalDispositionMember).all()
            assert len(headers) == 1
            assert headers[0].decision == "SAME_REAL_WORLD_EFFORT"
            assert len(members) == 3


class TestReviewerReasonValidation:
    def test_blank_reviewer_rejected(self, tmp_path):
        db = tmp_path / "t.db"
        _make_full_db(db)
        with pytest.raises(ValueError):
            run_review(SignalDispositionReviewConfig(database=db, signal_ids=(1, 2), decision="DISTINCT", reviewer="   ", reason="x"))

    def test_blank_reason_rejected(self, tmp_path):
        db = tmp_path / "t.db"
        _make_full_db(db)
        with pytest.raises(ValueError):
            run_review(SignalDispositionReviewConfig(database=db, signal_ids=(1, 2), decision="DISTINCT", reviewer="human:x", reason="  "))

    def test_unicode_reviewer_reason_round_trip(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)
        result = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT",
            reviewer="Åsa Lindqvist", reason="Två signaler beskriver samma EMAS-ersättning.",
            allow_database_write=True,
        ))
        assert result.written is True
        with Session(engine) as verify:
            header = verify.query(SignalDisposition).one()
            assert header.reviewer == "Åsa Lindqvist"
            assert header.reason == "Två signaler beskriver samma EMAS-ersättning."


# ---------------------------------------------------------------------------
# Idempotency / re-review supersession
# ---------------------------------------------------------------------------


class TestIdempotencyAndReReview:
    def test_same_decision_again_refused_no_new_row(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)
        with Session(engine) as session:
            record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:a", reason="a")
            session.commit()

        result = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x", allow_database_write=True,
        ))
        assert result.written is False
        assert result.action_eligible is False
        assert "ALREADY_CONFIRMED_CURRENT_DECISION" in result.action_refusal_reason
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 1

    def test_changed_decision_supersedes_latest(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)
        with Session(engine) as session:
            first = record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:a", reason="a")
            session.commit()
            first_id = first.id

        result = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="re-review",
            allow_database_write=True,
        ))
        assert result.written is True
        assert result.planned_supersedes_id == first_id
        with Session(engine) as verify:
            headers = verify.query(SignalDisposition).order_by(SignalDisposition.id).all()
            assert len(headers) == 2
            assert headers[1].supersedes_id == first_id
            assert headers[1].decision == "SAME_REAL_WORLD_EFFORT"


# ---------------------------------------------------------------------------
# Ambiguous groups
# ---------------------------------------------------------------------------


class TestAmbiguousGroups:
    def test_ambiguous_group_visible_in_inspect(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)
        with Session(engine) as session:
            record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:a", reason="a")
            record_signal_group_disposition(session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:b", reason="b", supersedes_id=None)
            session.commit()

        overview = run_review(SignalDispositionReviewConfig(database=db))
        assert len(overview.attention_required) == 1
        assert overview.attention_required[0].ambiguous_history is True

        targeted = run_review(SignalDispositionReviewConfig(database=db, signal_ids=ids))
        assert targeted.target_group_found is True
        assert targeted.ambiguous_history is True
        assert targeted.independent_root_count == 2

    def test_ambiguous_group_dry_run_refused(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)
        with Session(engine) as session:
            record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:a", reason="a")
            record_signal_group_disposition(session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:b", reason="b", supersedes_id=None)
            session.commit()

        result = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x",
        ))
        assert result.action_eligible is False
        assert "AMBIGUOUS_HISTORY_REQUIRES_EXPLICIT_RESOLUTION" in result.action_refusal_reason

    def test_ambiguous_group_write_refused_no_new_row(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)
        with Session(engine) as session:
            record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:a", reason="a")
            record_signal_group_disposition(session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:b", reason="b", supersedes_id=None)
            session.commit()

        result = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x", allow_database_write=True,
        ))
        assert result.written is False
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 2


# ---------------------------------------------------------------------------
# Re-read before write / staleness
# ---------------------------------------------------------------------------


class TestReReadBeforeWrite:
    def test_state_changed_between_reads_refuses_write(self, tmp_path):
        """Directly attacks the pre-write re-read guard: the second
        (pre-write) call to run_disposition_aware_fh_d4_review() returns a
        result that differs from the first (planning) call - the write
        must be refused and zero rows persisted."""
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)

        call_count = [0]
        original = review_module.run_disposition_aware_fh_d4_review

        def _wrapper(session):
            result = original(session)
            call_count[0] += 1
            if call_count[0] == 2:
                group = result.active_findings[0]
                mutated = dataclasses.replace(group, independent_root_count=2, ambiguous_history=True)
                result = dataclasses.replace(result, active_findings=(mutated,))
            return result

        review_module.run_disposition_aware_fh_d4_review = _wrapper
        try:
            result = run_review(SignalDispositionReviewConfig(
                database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x", allow_database_write=True,
            ))
        finally:
            review_module.run_disposition_aware_fh_d4_review = original

        assert call_count[0] == 2  # proves the re-read actually happened
        assert result.written is False
        assert "STATE_CHANGED_BEFORE_WRITE" in result.action_refusal_reason
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 0

    def test_group_disappears_between_reads_refuses_write(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)

        call_count = [0]
        original = review_module.run_disposition_aware_fh_d4_review

        def _wrapper(session):
            result = original(session)
            call_count[0] += 1
            if call_count[0] == 2:
                result = dataclasses.replace(result, active_findings=())
            return result

        review_module.run_disposition_aware_fh_d4_review = _wrapper
        try:
            result = run_review(SignalDispositionReviewConfig(
                database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x", allow_database_write=True,
            ))
        finally:
            review_module.run_disposition_aware_fh_d4_review = original

        assert result.written is False
        assert "STATE_CHANGED_BEFORE_WRITE" in result.action_refusal_reason
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 0

    def test_natural_cross_invocation_staleness_group_grew(self, tmp_path):
        """No monkeypatching - a real, separate dry-run invocation followed
        by real DB mutation, followed by a real second (write) invocation.
        {A,B} grows to {A,B,C} between the two calls; the write invocation
        must refuse via TARGET_GROUP_NOT_CURRENT (the target set is no
        longer a current raw FH-D4 group at all)."""
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)

        dry_run = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x",
        ))
        assert dry_run.action_eligible is True

        _grow_group(engine, airport_id)

        write_attempt = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x", allow_database_write=True,
        ))
        assert write_attempt.target_group_found is False
        assert write_attempt.blockers == (TARGET_GROUP_NOT_CURRENT_BLOCKER,)
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 0

    def test_real_mid_call_mutation_via_separate_connection_refuses_write(self, tmp_path):
        """Strengthens the dataclasses.replace-based guard test above with
        a GENUINELY mutated database, not a crafted mock object: on the
        FIRST (planning) call to run_disposition_aware_fh_d4_review()
        within one run_review() invocation, a real write is committed via
        a completely separate engine/session/connection to the same file
        database (simulating another operator), before the SECOND
        (pre-write) call runs. The second call must independently,
        genuinely observe the new state and refuse the write."""
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)

        call_count = [0]
        original = review_module.run_disposition_aware_fh_d4_review

        def _wrapper(session):
            result = original(session)
            call_count[0] += 1
            if call_count[0] == 1:
                # A real, independent write via a SEPARATE connection -
                # not the session run_review() itself is using.
                other_engine = create_engine(f"sqlite:///{db}")
                with Session(other_engine) as other_session:
                    extra = Signal(airport_id=airport_id, title="REAL-MUTATION", category="replacement", confidence="high")
                    other_session.add(extra)
                    other_session.commit()
                other_engine.dispose()
            return result

        review_module.run_disposition_aware_fh_d4_review = _wrapper
        try:
            result = run_review(SignalDispositionReviewConfig(
                database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x", allow_database_write=True,
            ))
        finally:
            review_module.run_disposition_aware_fh_d4_review = original

        assert call_count[0] == 2
        assert result.written is False
        assert result.action_eligible is False
        assert "STATE_CHANGED_BEFORE_WRITE" in result.action_refusal_reason
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 0

    def test_natural_cross_invocation_staleness_group_disappeared(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)

        dry_run = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x",
        ))
        assert dry_run.action_eligible is True

        _remove_from_group(engine, ids[0])

        write_attempt = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x", allow_database_write=True,
        ))
        assert write_attempt.target_group_found is False
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 0


# ---------------------------------------------------------------------------
# Group disappears / grows AFTER a decision was recorded
# ---------------------------------------------------------------------------


class TestGroupDisappearsAfterDecision:
    def test_history_preserved_but_no_active_finding_fabricated(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)
        write_result = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x", allow_database_write=True,
        ))
        assert write_result.written is True

        _remove_from_group(engine, ids[0])

        overview = run_review(SignalDispositionReviewConfig(database=db))
        assert overview.attention_required == ()
        assert overview.confirmed_distinct == ()  # the group no longer exists at all, current or resolved
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 1  # history intact


class TestGroupGrowsAfterDecision:
    def test_new_current_group_active_old_group_related_only(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)
        run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x", allow_database_write=True,
        ))

        third_id = _grow_group(engine, airport_id)
        grown_ids = tuple(sorted(ids + (third_id,)))

        overview = run_review(SignalDispositionReviewConfig(database=db))
        assert len(overview.attention_required) == 1
        assert overview.attention_required[0].signal_ids == grown_ids
        assert overview.attention_required[0].has_related_history is True
        assert overview.confirmed_distinct == ()  # old {A,B} is no longer a current group at all

        targeted = run_review(SignalDispositionReviewConfig(database=db, signal_ids=grown_ids))
        assert targeted.status == "UNREVIEWED"
        assert len(targeted.related_history) == 1
        assert targeted.related_history[0].relation == "SUBSET"

    def test_grown_group_decision_never_supersedes_old_narrower_disposition(self, tmp_path):
        """CRITICAL (mission §17): a decision recorded for the new,
        GROWN exact group must plan supersedes_id=None - it must never
        supersede the old, narrower disposition, which remains permanently
        valid, un-superseded, merely-related history."""
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)
        old_write = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x", allow_database_write=True,
        ))
        old_disposition_id = old_write.written_disposition_id

        third_id = _grow_group(engine, airport_id)
        grown_ids = tuple(sorted(ids + (third_id,)))

        dry_run = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=grown_ids, decision="DISTINCT", reviewer="human:y", reason="triple review",
        ))
        assert dry_run.action_eligible is True
        assert dry_run.planned_supersedes_id is None

        write = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=grown_ids, decision="DISTINCT", reviewer="human:y", reason="triple review",
            allow_database_write=True,
        ))
        assert write.written is True
        assert write.planned_supersedes_id is None

        with Session(engine) as verify:
            old_header = verify.get(SignalDisposition, old_disposition_id)
            assert old_header.supersedes_id is None  # never touched
            new_header = verify.get(SignalDisposition, write.written_disposition_id)
            assert new_header.supersedes_id is None
            assert verify.query(SignalDisposition).count() == 2


class TestGroupShrinksAfterDecision:
    def test_shrunk_group_decision_never_supersedes_old_wider_disposition(self, tmp_path):
        """Mirror of the growth case (mission §18): stored {A,B,C},
        current shrinks to {A,B} - the new pair's own decision must not
        supersede the old triple's disposition."""
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        old_write = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x", allow_database_write=True,
        ))
        old_disposition_id = old_write.written_disposition_id

        _remove_from_group(engine, ids[2])
        shrunk_ids = tuple(sorted(ids[:2]))

        write = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=shrunk_ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:y", reason="pair review",
            allow_database_write=True,
        ))
        assert write.written is True
        assert write.planned_supersedes_id is None

        with Session(engine) as verify:
            old_header = verify.get(SignalDisposition, old_disposition_id)
            assert old_header.supersedes_id is None
            assert verify.query(SignalDisposition).count() == 2


# ---------------------------------------------------------------------------
# Transaction boundary
# ---------------------------------------------------------------------------


class TestTransactionBoundary:
    def test_failure_after_flush_before_commit_rolls_back_completely(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)

        original_record = review_module.record_signal_group_disposition

        def _failing_record(*args, **kwargs):
            original_record(*args, **kwargs)  # performs the real flush-based insert
            raise RuntimeError("simulated failure after flush, before commit")

        review_module.record_signal_group_disposition = _failing_record
        try:
            with pytest.raises(RuntimeError):
                run_review(SignalDispositionReviewConfig(
                    database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x", allow_database_write=True,
                ))
        finally:
            review_module.record_signal_group_disposition = original_record

        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 0
            assert verify.query(SignalDispositionMember).count() == 0


# ---------------------------------------------------------------------------
# Wrong-DB safety
# ---------------------------------------------------------------------------


class TestCommitCount:
    def test_successful_write_commits_exactly_once(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)

        commit_count = [0]
        engine2 = create_engine(f"sqlite:///{db}")

        def _on_commit(_conn):
            commit_count[0] += 1

        event.listen(engine2, "commit", _on_commit)
        original_build = review_module.build_engine
        review_module.build_engine = lambda database, *, writable: engine2
        try:
            result = run_review(SignalDispositionReviewConfig(
                database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x", allow_database_write=True,
            ))
        finally:
            review_module.build_engine = original_build
            event.remove(engine2, "commit", _on_commit)

        assert result.written is True
        assert commit_count[0] == 1

    def test_refused_action_commits_zero_times(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)
        with Session(engine) as session:
            record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:a", reason="a")
            session.commit()

        commit_count = [0]
        engine2 = create_engine(f"sqlite:///{db}")

        def _on_commit(_conn):
            commit_count[0] += 1

        event.listen(engine2, "commit", _on_commit)
        original_build = review_module.build_engine
        review_module.build_engine = lambda database, *, writable: engine2
        try:
            result = run_review(SignalDispositionReviewConfig(
                database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x", allow_database_write=True,
            ))
        finally:
            review_module.build_engine = original_build
            event.remove(engine2, "commit", _on_commit)

        assert result.written is False  # idempotent refusal
        assert commit_count[0] == 0


class TestArgumentContractEdgeCases:
    def test_unknown_decision_rejected_even_bypassing_argparse(self, tmp_path):
        db = tmp_path / "t.db"
        _make_full_db(db)
        with pytest.raises(ValueError, match="--decision must be one of"):
            run_review(SignalDispositionReviewConfig(
                database=db, signal_ids=(1, 2), decision="MERGE", reviewer="human:x", reason="x",
            ))

    def test_duplicate_only_ids_rejected(self, tmp_path):
        """--signal-id 1 --signal-id 1 (no second DISTINCT id) must be
        rejected exactly like a single id - deduplication must never
        silently satisfy the minimum-cardinality requirement."""
        db = tmp_path / "t.db"
        _make_full_db(db)
        with pytest.raises(ValueError, match="at least 2"):
            run_review(SignalDispositionReviewConfig(database=db, signal_ids=(7, 7, 7)))

    def test_argparse_rejects_unknown_decision(self):
        parser = review_module._parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--database", "x.db", "--decision", "MERGE"])

    def test_argparse_signal_id_repeatable(self):
        parser = review_module._parser()
        args = parser.parse_args(["--database", "x.db", "--signal-id", "41", "--signal-id", "67"])
        assert args.signal_ids == [41, 67]


class TestOutputModeLabel:
    def test_inspect_mode_label(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)
        result = run_review(SignalDispositionReviewConfig(database=db, signal_ids=ids))
        text = render_result(result)
        assert "Mode: INSPECT (read-only)" in text

    def test_overview_mode_label(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        _add_fh_d4_group(engine, 2)
        result = run_review(SignalDispositionReviewConfig(database=db))
        text = render_result(result)
        assert "Mode: INSPECT (read-only)" in text
        assert "ATTENTION REQUIRED FH-D4 GROUPS" in text

    def test_dry_run_mode_label(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)
        result = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x",
        ))
        text = render_result(result)
        assert "Mode: DRY RUN (no write)" in text
        assert "DRY RUN - no write performed" in text

    def test_write_mode_label(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)
        result = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x", allow_database_write=True,
        ))
        text = render_result(result)
        assert "Mode: WRITE (committed)" in text
        assert "WRITTEN: SignalDisposition #" in text


class TestMigrationCreatedSchemaFullWritePath:
    def test_distinct_write_against_migration_created_db(self, tmp_path):
        db = tmp_path / "migrated.db"
        pre_engine = _make_pre_d4d2_db(db)
        pre_engine.dispose()
        migration.upgrade(db)

        engine = create_engine(f"sqlite:///{db}")
        airport_id, ids = _add_fh_d4_group(engine, 2)
        engine.dispose()

        result = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x", allow_database_write=True,
        ))
        assert result.written is True

        verify_engine = create_engine(f"sqlite:///{db}")
        with Session(verify_engine) as verify:
            headers = verify.query(SignalDisposition).all()
            members = verify.query(SignalDispositionMember).all()
            assert len(headers) == 1
            assert headers[0].decision == "DISTINCT"
            assert len(members) == 2
        verify_engine.dispose()


class TestWrongDbSafety:
    def test_protected_db_byte_identical_after_target_write(self, tmp_path):
        target_db = tmp_path / "target.db"
        protected_db = tmp_path / "protected.db"
        target_engine = _make_full_db(target_db)
        protected_engine = _make_full_db(protected_db)
        _airport_id, target_ids = _add_fh_d4_group(target_engine, 2, airport_name="Target Airport")
        _add_fh_d4_group(protected_engine, 2, airport_name="Protected Airport")
        protected_engine.dispose()
        target_engine.dispose()

        before_hash = _file_sha256(protected_db)

        run_review(SignalDispositionReviewConfig(database=target_db))
        run_review(SignalDispositionReviewConfig(
            database=target_db, signal_ids=target_ids, decision="DISTINCT", reviewer="human:x", reason="x",
        ))
        result = run_review(SignalDispositionReviewConfig(
            database=target_db, signal_ids=target_ids, decision="DISTINCT", reviewer="human:x", reason="x",
            allow_database_write=True,
        ))
        assert result.written is True
        # Content proof (not merely a hash check): the CLI's own result
        # reflects TARGET's own Signal ids, never protected's.
        assert result.signal_ids == tuple(sorted(target_ids))
        assert result.airport_id is not None

        after_hash = _file_sha256(protected_db)
        assert before_hash == after_hash

        # Protected's own group remains entirely untouched, unreviewed.
        protected_verify_engine = create_engine(f"sqlite:///{protected_db}")
        with Session(protected_verify_engine) as verify:
            assert verify.query(SignalDisposition).count() == 0
        protected_verify_engine.dispose()


# ---------------------------------------------------------------------------
# Signal / provenance immutability
# ---------------------------------------------------------------------------


class TestSignalImmutability:
    def test_signal_rows_unchanged_after_write(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)

        with Session(engine) as session:
            before = [
                {c.name: getattr(session.get(Signal, sid), c.name) for c in Signal.__table__.columns}
                for sid in ids
            ]

        run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x", allow_database_write=True,
        ))

        with Session(engine) as session:
            after = [
                {c.name: getattr(session.get(Signal, sid), c.name) for c in Signal.__table__.columns}
                for sid in ids
            ]
        assert before == after


class TestProvenanceImmutability:
    def test_sources_source_assertions_reviewer_actions_unchanged(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)
        with Session(engine) as session:
            source = Source(title="Test Source", source_type="official_document", url="https://example.test")
            session.add(source)
            session.commit()
            assertion = SourceAssertion(
                source_id=source.id, airport_id=airport_id, signal_id=ids[0],
                assertion_type="historical", source_record_identifier="test-record-1",
            )
            session.add(assertion)
            session.commit()

        with Session(engine) as session:
            sources_before = [
                {c.name: getattr(row, c.name) for c in Source.__table__.columns}
                for row in session.query(Source).all()
            ]
            assertions_before = [
                {c.name: getattr(row, c.name) for c in SourceAssertion.__table__.columns}
                for row in session.query(SourceAssertion).all()
            ]
            reviewer_actions_before = session.query(ReviewerAction).count()

        run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x", allow_database_write=True,
        ))

        with Session(engine) as session:
            sources_after = [
                {c.name: getattr(row, c.name) for c in Source.__table__.columns}
                for row in session.query(Source).all()
            ]
            assertions_after = [
                {c.name: getattr(row, c.name) for c in SourceAssertion.__table__.columns}
                for row in session.query(SourceAssertion).all()
            ]
            reviewer_actions_after = session.query(ReviewerAction).count()

        assert sources_before == sources_after
        assert assertions_before == assertions_after
        assert reviewer_actions_before == reviewer_actions_after == 0


# ---------------------------------------------------------------------------
# No auto-decision / information firewall
# ---------------------------------------------------------------------------


class TestNoAutoDecision:
    _FORBIDDEN_SIGNAL_ATTRS = (
        "title", "notes", "source_notes", "estimated_total_value_usd",
        "estimated_emas_value_usd", "supplier", "likely_supplier", "supplier_reason",
        "confirmed_vendor", "category", "confidence", "manual_year_estimate",
        "published", "runway_id", "installation_id",
    )

    def test_no_forbidden_signal_attribute_access_ast(self):
        tree = ast.parse(inspect_module.getsource(review_module))
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert not (attrs & set(self._FORBIDDEN_SIGNAL_ATTRS))

    def test_decision_comes_only_from_config_ast(self):
        """AST-verified (not a substring search): the ONLY call to
        record_signal_group_disposition() anywhere in this module must
        pass its `decision=` keyword argument as a plain attribute-access
        expression on `config` (`config.decision`) - never a literal
        string, a conditional expression, or any value derived from
        another field (title/summary/related history/etc.)."""
        tree = ast.parse(inspect_module.getsource(review_module))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "record_signal_group_disposition"
        ]
        assert len(calls) == 1, f"expected exactly one call site, found {len(calls)}"
        decision_kwargs = [kw for kw in calls[0].keywords if kw.arg == "decision"]
        assert len(decision_kwargs) == 1
        value_node = decision_kwargs[0].value
        assert isinstance(value_node, ast.Attribute) and value_node.attr == "decision"
        assert isinstance(value_node.value, ast.Name) and value_node.value.id == "config"

    def test_misleading_summary_never_changes_written_decision(self, tmp_path):
        """A raw FH-D4 summary/evidence never influences what gets
        persisted - only the explicit --decision argument does."""
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)

        result = run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="x", allow_database_write=True,
        ))
        assert result.written is True
        with Session(engine) as verify:
            header = verify.query(SignalDisposition).one()
            assert header.decision == "SAME_REAL_WORLD_EFFORT"


# ---------------------------------------------------------------------------
# Deterministic output / result
# ---------------------------------------------------------------------------


class TestDeterministicOutput:
    def test_repeated_read_only_calls_equal(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)

        r1 = run_review(SignalDispositionReviewConfig(database=db, signal_ids=ids))
        r2 = run_review(SignalDispositionReviewConfig(database=db, signal_ids=ids))
        assert r1 == r2

    def test_render_result_deterministic_text(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 2)
        result = run_review(SignalDispositionReviewConfig(database=db, signal_ids=ids))
        text1 = render_result(result)
        text2 = render_result(result)
        assert text1 == text2
        assert "Target Signal group" in text1


# ---------------------------------------------------------------------------
# No real database access
# ---------------------------------------------------------------------------


class TestNoRealDatabaseAccess:
    def test_no_reference_to_real_database_path(self):
        tree = ast.parse(inspect_module.getsource(review_module))
        body = list(tree.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body = body[1:]
        literals = [
            node.value for node in ast.walk(ast.Module(body=body, type_ignores=[]))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        real_db_filename = "runway_safe" + ".db"
        assert not any(real_db_filename in literal for literal in literals)

    def test_no_default_database_argument(self):
        parser = review_module._parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])
