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
from app.services.signal_disposition_conflicts import find_signal_disposition_conflicts
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

    def test_missing_schema_blocks_subgroup_mode_too(self, tmp_path):
        """D4D8D critical-review addition: the schema gate is checked
        BEFORE any mode branching in run_review() - confirms it applies
        uniformly whether or not subgroup mode was requested, and renders
        correctly (the generic early-blockers path in render_result(), not
        the subgroup-specific one, since subgroup_mode/parent_found are
        both still at their unset defaults at this point)."""
        db = tmp_path / "t.db"
        _make_pre_d4d2_db(db)
        result = run_review(SignalDispositionReviewConfig(database=db, parent_signal_ids=(1, 2, 3), signal_ids=(1, 2)))
        assert result.blockers == (SCHEMA_MIGRATION_REQUIRED_BLOCKER,)
        assert result.subgroup_mode is False
        text = render_result(result)
        assert "BLOCKED: SIGNAL_DISPOSITION_SCHEMA_MIGRATION_REQUIRED" in text

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
        """AST-verified (not a substring search): EVERY call to
        record_signal_group_disposition() anywhere in this module (D4D8D:
        exactly two now - whole-group mode's own write path and subgroup
        mode's own, both independently reviewed, deliberately not merged
        into one shared helper so each stays a plain, directly-readable
        call site rather than one level of indirection removed from
        `config` - see run_review()'s/`_run_subgroup_review()`'s own
        top-of-file "PERSISTENCE" documentation) must pass its `decision=`
        keyword argument as a plain attribute-access expression on `config`
        (`config.decision`) - never a literal string, a conditional
        expression, or any value derived from another field (title/summary/
        related history/etc.)."""
        tree = ast.parse(inspect_module.getsource(review_module))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "record_signal_group_disposition"
        ]
        assert len(calls) == 2, f"expected exactly two call sites (whole-group + subgroup), found {len(calls)}"
        for call in calls:
            decision_kwargs = [kw for kw in call.keywords if kw.arg == "decision"]
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


# ---------------------------------------------------------------------------
# D4D8D - human subgroup review CLI (mission's own lettered test matrix A-S)
# ---------------------------------------------------------------------------


from scripts.review_signal_disposition import PARENT_GROUP_NOT_CURRENT_ACTIVE_BLOCKER  # noqa: E402


class TestSubgroupInspectDryRunWrite:
    def test_a_inspect_works_no_history(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        result = run_review(SignalDispositionReviewConfig(database=db, parent_signal_ids=ids, signal_ids=(a, b)))
        assert result.subgroup_mode is True
        assert result.parent_found is True
        assert result.parent_signal_ids == tuple(sorted(ids))
        assert result.parent_status == "UNREVIEWED"
        assert result.signal_ids == tuple(sorted([a, b]))
        assert result.status == "UNREVIEWED"
        assert result.target_remainder_signal_ids == (c,)
        assert result.conflicts == ()
        assert result.written is False
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 0

    def test_b_dry_run_works_no_write(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        result = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r",
        ))
        assert result.action_eligible is True
        assert result.planned_supersedes_id is None
        assert result.written is False
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 0

    def test_c_write_creates_one_header_two_members(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        result = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r", allow_database_write=True,
        ))
        assert result.written is True
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 1
            header = verify.query(SignalDisposition).one()
            assert header.decision == "SAME_REAL_WORLD_EFFORT"
            assert header.supersedes_id is None
            members = verify.query(SignalDispositionMember).filter_by(disposition_id=header.id).all()
            assert {m.signal_id for m in members} == {a, b}
            assert len(members) == 2


class TestSubgroupIdempotencyAndSupersession:
    def test_d_same_decision_after_write_is_idempotent(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r", allow_database_write=True,
        ))
        result = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:y", reason="r2",
        ))
        assert result.action_eligible is False
        assert "ALREADY_CONFIRMED_CURRENT_DECISION" in result.action_refusal_reason
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 1

    def test_e_changed_decision_supersedes_exact_set_subgroup_disposition(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        first = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r", allow_database_write=True,
        ))
        second = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="DISTINCT", reviewer="human:y", reason="corrected", allow_database_write=True,
        ))
        assert second.written is True
        assert second.planned_supersedes_id == first.written_disposition_id
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 2
            new_header = verify.get(SignalDisposition, second.written_disposition_id)
            assert new_header.supersedes_id == first.written_disposition_id


class TestSubgroupTargetValidation:
    def test_f_target_equal_to_parent_rejected(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        with pytest.raises(ValueError, match="STRICT, PROPER subset"):
            run_review(SignalDispositionReviewConfig(database=db, parent_signal_ids=ids, signal_ids=ids))

    def test_g_target_not_subset_of_parent_rejected(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        outsider = _grow_group(engine, airport_id, title="OUTSIDER")
        with pytest.raises(ValueError, match="STRICT, PROPER subset"):
            run_review(SignalDispositionReviewConfig(database=db, parent_signal_ids=(a, b, c), signal_ids=(a, outsider)))

    def test_parent_below_minimum_cardinality_rejected(self, tmp_path):
        db = tmp_path / "t.db"
        with pytest.raises(ValueError, match="parent-signal-id"):
            run_review(SignalDispositionReviewConfig(database=db, parent_signal_ids=(1,), signal_ids=(1, 2)))

    def test_parent_without_target_rejected(self, tmp_path):
        db = tmp_path / "t.db"
        with pytest.raises(ValueError, match="requires --signal-id"):
            run_review(SignalDispositionReviewConfig(database=db, parent_signal_ids=(1, 2, 3)))


class TestSubgroupParentReReadBeforeWrite:
    def test_h_parent_disappears_entirely_between_dry_run_and_write(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        dry_run = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r",
        ))
        assert dry_run.action_eligible is True

        # Remove enough members that the raw FH-D4 group vanishes entirely.
        _remove_from_group(engine, b)
        _remove_from_group(engine, c)

        write_attempt = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r", allow_database_write=True,
        ))
        assert write_attempt.written is False
        assert write_attempt.parent_found is False
        assert write_attempt.blockers == (PARENT_GROUP_NOT_CURRENT_ACTIVE_BLOCKER,)
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 0

    def test_i_parent_grows_between_dry_run_and_write_refuses_stale_parent(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        dry_run = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r",
        ))
        assert dry_run.action_eligible is True

        _grow_group(engine, airport_id)  # parent {a,b,c} -> {a,b,c,d}, still a valid proper superset of {a,b}

        write_attempt = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r", allow_database_write=True,
        ))
        assert write_attempt.written is False
        # The ORIGINALLY-specified parent {a,b,c} is no longer a current
        # active raw group at all (the live one is now {a,b,c,d}) - refused
        # even though {a,b} is technically still a valid subset of the NEW
        # parent, exactly per this mission's own explicit "REFUSE stale
        # parent" requirement.
        assert write_attempt.parent_found is False
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 0

    def test_j_parent_shrinks_between_dry_run_and_write_refuses(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 4)
        a, b, c, d = ids
        dry_run = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r",
        ))
        assert dry_run.action_eligible is True

        _remove_from_group(engine, d)  # parent {a,b,c,d} -> {a,b,c}, still active but a different exact set

        write_attempt = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r", allow_database_write=True,
        ))
        assert write_attempt.written is False
        assert write_attempt.parent_found is False
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 0

    def test_k_new_overlapping_disposition_appears_between_dry_run_and_write(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        dry_run = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r",
        ))
        assert dry_run.action_eligible is True
        assert dry_run.conflicts == ()

        # A different reviewer independently records a genuinely overlapping
        # disposition for {b,c} in between the dry-run and the write.
        with Session(engine) as other:
            record_signal_group_disposition(other, signal_ids=[b, c], decision="DISTINCT", reviewer="human:other", reason="concurrent")
            other.commit()

        write_attempt = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r", allow_database_write=True,
        ))
        assert write_attempt.written is False
        assert write_attempt.action_eligible is False
        assert "SUBGROUP_OVERLAP_CONFLICT_DETECTED" in write_attempt.action_refusal_reason
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 1  # only the concurrent one - our write never landed

    def test_parent_shrinks_to_exactly_equal_target_refuses(self, tmp_path):
        """Critical-review addition: parent {a,b,c} shrinks to EXACTLY
        {a,b} (== the target itself) between dry-run and write - the
        ORIGINALLY-specified parent {a,b,c} is no longer found via exact
        match, so the write must refuse even though {a,b} would now,
        confusingly, itself look like a perfectly fine EXACT-set target
        (not a subgroup at all) if re-evaluated from scratch."""
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        dry_run = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r",
        ))
        assert dry_run.action_eligible is True

        _remove_from_group(engine, c)  # parent {a,b,c} -> {a,b}, exactly the target

        write_attempt = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r", allow_database_write=True,
        ))
        assert write_attempt.written is False
        assert write_attempt.parent_found is False
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 0

    def test_target_member_itself_leaves_parent_refuses(self, tmp_path):
        """Critical-review addition: distinct from a REMAINDER member
        leaving (already covered by test_i/test_j) - here a member of the
        TARGET subgroup itself (not the remainder) leaves the raw group
        between dry-run and write, so the new raw parent is {a,c} - the
        originally-specified parent {a,b,c} is gone, refused."""
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        dry_run = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r",
        ))
        assert dry_run.action_eligible is True

        _remove_from_group(engine, b)  # b is a TARGET member, not remainder

        write_attempt = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r", allow_database_write=True,
        ))
        assert write_attempt.written is False
        assert write_attempt.parent_found is False
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 0

    def test_cross_airport_global_conflict_still_refuses(self, tmp_path):
        """Critical-review addition: D4D8C's conflict scan is genuinely
        global (never airport-scoped) - an existing disposition spanning a
        Signal from a COMPLETELY DIFFERENT airport plus one target member
        still correctly refuses the subgroup write."""
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        with Session(engine) as session:
            other_airport = Airport(name="Other Airport", country="XX")
            session.add(other_airport)
            session.commit()
            other_signal = Signal(airport=other_airport, title="OTHER", category="replacement", confidence="high")
            session.add(other_signal)
            session.commit()
            other_id = other_signal.id
            record_signal_group_disposition(session, signal_ids=[b, other_id], decision="DISTINCT", reviewer="human:other", reason="cross-airport")
            session.commit()

        result = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r",
        ))
        assert len(result.conflicts) == 1
        assert result.conflicts[0].overlap_signal_ids == (b,)
        assert result.action_eligible is False


class TestSubgroupFailureInjection:
    def test_subgroup_write_failure_after_flush_rolls_back_completely(self, tmp_path):
        """Mirrors the whole-group TestTransactionBoundary test exactly,
        for the subgroup write call site specifically - both call sites
        share the same module-level `record_signal_group_disposition` name,
        but this proves the SUBGROUP path's own surrounding try/except
        genuinely rolls back too, not merely assumed from the whole-group
        test alone."""
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids

        original_record = review_module.record_signal_group_disposition

        def _failing_record(*args, **kwargs):
            original_record(*args, **kwargs)
            raise RuntimeError("simulated failure after flush, before commit")

        review_module.record_signal_group_disposition = _failing_record
        try:
            with pytest.raises(RuntimeError):
                run_review(SignalDispositionReviewConfig(
                    database=db, parent_signal_ids=ids, signal_ids=(a, b),
                    decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r", allow_database_write=True,
                ))
        finally:
            review_module.record_signal_group_disposition = original_record

        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 0
            assert verify.query(SignalDispositionMember).count() == 0


class TestSubgroupDisjointAndOverlap:
    def test_l_existing_disjoint_subgroup_target_permitted(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 6)
        a, b, c, d, e, f = ids
        first_write = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r", allow_database_write=True,
        ))
        assert first_write.written is True

        result = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(c, d),
            decision="DISTINCT", reviewer="human:y", reason="r2",
        ))
        assert result.conflicts == ()
        assert result.action_eligible is True
        write = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(c, d),
            decision="DISTINCT", reviewer="human:y", reason="r2", allow_database_write=True,
        ))
        assert write.written is True
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 2

    def test_m_existing_overlapping_subgroup_blocked(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r", allow_database_write=True,
        ))
        result = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(b, c),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:y", reason="r2",
        ))
        assert len(result.conflicts) == 1
        assert result.conflicts[0].conflicting_signal_ids == tuple(sorted([a, b]))
        assert result.action_eligible is False
        assert "SUBGROUP_OVERLAP_CONFLICT_DETECTED" in result.action_refusal_reason
        # Same decision or different decision makes no difference (syntactic policy).
        result_same_decision = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(b, c),
            decision="DISTINCT", reviewer="human:y", reason="r2",
        ))
        assert result_same_decision.action_eligible is False

    def test_n_strict_subset_superset_overlap_blocked(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 4)
        a, b, c, d = ids
        run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r", allow_database_write=True,
        ))
        result = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b, c),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:y", reason="r2",
        ))
        assert len(result.conflicts) == 1
        assert result.conflicts[0].relation == "STRICT_SUBSET"
        assert result.action_eligible is False


class TestSubgroupExactSetHistoryNotOverlap:
    def test_o_own_exact_set_history_never_treated_as_overlap_conflict(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r", allow_database_write=True,
        ))
        inspect_result = run_review(SignalDispositionReviewConfig(database=db, parent_signal_ids=ids, signal_ids=(a, b)))
        assert inspect_result.status == "CONFIRMED_SAME_REAL_WORLD_EFFORT"
        assert inspect_result.conflicts == ()  # its own exact-set history is never a conflict against itself

        reReview = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="DISTINCT", reviewer="human:y", reason="corrected",
        ))
        assert reReview.conflicts == ()
        assert reReview.action_eligible is True


class TestSubgroupAmbiguousHistory:
    def test_p_ambiguous_target_history_fails_closed(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        with Session(engine) as session:
            record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r1")
            record_signal_group_disposition(session, signal_ids=[a, b], decision="DISTINCT", reviewer="human:y", reason="r2")  # independent root, no supersedes_id
            session.commit()

        inspect_result = run_review(SignalDispositionReviewConfig(database=db, parent_signal_ids=ids, signal_ids=(a, b)))
        assert inspect_result.ambiguous_history is True
        assert inspect_result.independent_root_count == 2

        dry_run = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:z", reason="r3",
        ))
        assert dry_run.action_eligible is False
        assert "AMBIGUOUS_HISTORY_REQUIRES_EXPLICIT_RESOLUTION" in dry_run.action_refusal_reason
        with Session(engine) as verify:
            assert verify.query(SignalDisposition).count() == 2  # unchanged


class TestSubgroupRemainder:
    def test_q_singleton_remainder_displayed_no_inference(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        result = run_review(SignalDispositionReviewConfig(database=db, parent_signal_ids=ids, signal_ids=(a, b)))
        assert result.target_remainder_signal_ids == (c,)
        # No disposition of any kind exists that names c - never inferred.
        with Session(engine) as verify:
            assert verify.query(SignalDispositionMember).filter_by(signal_id=c).count() == 0

    def test_r_multi_member_remainder(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 5)
        a, b, c, d, e = ids
        result = run_review(SignalDispositionReviewConfig(database=db, parent_signal_ids=ids, signal_ids=(a, b)))
        assert result.target_remainder_signal_ids == tuple(sorted([c, d, e]))


class TestSubgroupRealCaseShapes:
    """Synthetic replays of the real D4D7 topologies - fully synthetic ids,
    never touching data/runway_safe.db."""

    def test_s_roanoke_shape_pair_subgroup(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3, airport_name="Roanoke-shape")
        thirty_seven, fifty_one, sixty_one = ids
        result = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(fifty_one, sixty_one),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:rwi-owner",
            reason="Shared Installation, same runway_end='34', explicit AIP cross-reference text.",
        ))
        assert result.action_eligible is True
        assert result.target_remainder_signal_ids == (thirty_seven,)
        assert result.conflicts == ()

    def test_s_binghamton_shape_four_member_subgroup(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 5, airport_name="Binghamton-shape")
        four, sixty = ids[:4], ids[4]
        result = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=four,
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:rwi-owner",
            reason="All four explicitly, consistently name Runway 34 end; gapless phase sequence.",
        ))
        assert result.action_eligible is True
        assert result.target_remainder_signal_ids == (sixty,)
        assert result.conflicts == ()


class TestSubgroupParentValidation:
    def test_parent_not_found_at_all_refused(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        result = run_review(SignalDispositionReviewConfig(database=db, parent_signal_ids=(9001, 9002, 9003), signal_ids=(9001, 9002)))
        assert result.parent_found is False
        assert result.blockers == (PARENT_GROUP_NOT_CURRENT_ACTIVE_BLOCKER,)

    def test_parent_already_resolved_distinct_at_whole_group_level_refused(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="r", allow_database_write=True,
        ))
        result = run_review(SignalDispositionReviewConfig(database=db, parent_signal_ids=ids, signal_ids=(a, b)))
        assert result.parent_found is False
        assert result.blockers == (PARENT_GROUP_NOT_CURRENT_ACTIVE_BLOCKER,)
        # Independently confirms this is not merely an earlier-message
        # convenience: a subset write against this exact-set-DISTINCT
        # parent would ALWAYS also be blocked by D4D8C's own conflict scan
        # (STRICT_SUPERSET), so no legitimate capability is lost.
        with Session(engine) as verify:
            conflicts = find_signal_disposition_conflicts(verify, signal_ids=(a, b))
            assert len(conflicts) == 1
            assert conflicts[0].relation == "STRICT_SUPERSET"

    def test_parent_already_resolved_same_at_whole_group_level_refused(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        run_review(SignalDispositionReviewConfig(
            database=db, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r", allow_database_write=True,
        ))
        result = run_review(SignalDispositionReviewConfig(database=db, parent_signal_ids=ids, signal_ids=(a, b)))
        assert result.parent_found is False
        assert result.blockers == (PARENT_GROUP_NOT_CURRENT_ACTIVE_BLOCKER,)

    def test_parent_with_ambiguous_whole_group_history_refused(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        with Session(engine) as session:
            record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="r1")
            record_signal_group_disposition(session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:y", reason="r2")
            session.commit()
        result = run_review(SignalDispositionReviewConfig(database=db, parent_signal_ids=ids, signal_ids=(a, b)))
        assert result.parent_found is False
        assert result.blockers == (PARENT_GROUP_NOT_CURRENT_ACTIVE_BLOCKER,)

    def test_parent_with_conflicting_existing_subgroup_metadata_still_valid_and_displayed(self, tmp_path):
        """Distinct from the three tests above: a parent whose OWN
        exact-set status is still UNREVIEWED (so it correctly remains a
        valid, active parent) but whose EXISTING resolved subgroups
        conflict with EACH OTHER (D4D8B's own subgroup_conflict flag) -
        must remain a usable parent, with the conflict displayed honestly,
        never hidden and never used to block parent validity itself."""
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 5)
        a, b, c, d, e = ids
        with Session(engine) as session:
            record_signal_group_disposition(session, signal_ids=[a, b, c], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r1")
            record_signal_group_disposition(session, signal_ids=[c, d], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:y", reason="r2")
            session.commit()

        result = run_review(SignalDispositionReviewConfig(database=db, parent_signal_ids=ids, signal_ids=(b, e)))
        assert result.parent_found is True
        assert result.parent_status == "UNREVIEWED"
        assert result.parent_subgroup_conflict is True
        seen = {s.signal_ids for s in result.parent_resolved_subgroups}
        assert seen == {tuple(sorted([a, b, c])), tuple(sorted([c, d]))}
        # The NEW target itself also correctly conflicts (shares b with the
        # first existing subgroup).
        assert len(result.conflicts) == 1


class TestSubgroupDisplayExplainability:
    def test_parent_and_target_never_confused_in_output(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        result = run_review(SignalDispositionReviewConfig(database=db, parent_signal_ids=ids, signal_ids=(a, b)))
        text = render_result(result)
        assert "PARENT RAW FH-D4 GROUP" in text
        assert "TARGET SUBGROUP" in text
        assert "REMAINDER" in text
        assert "CONFLICTS" in text
        parent_idx = text.index("PARENT RAW FH-D4 GROUP")
        target_idx = text.index("TARGET SUBGROUP")
        assert parent_idx < target_idx  # parent always shown first, distinctly labeled

    def test_write_mode_prints_same_plan_before_committing(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        dry_run = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r",
        ))
        write = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r", allow_database_write=True,
        ))
        dry_text = render_result(dry_run)
        write_text = render_result(write)
        assert "Proposed decision: SAME_REAL_WORLD_EFFORT" in dry_text
        assert "Proposed decision: SAME_REAL_WORLD_EFFORT" in write_text
        assert "TARGET SUBGROUP: [1, 2]" in dry_text
        assert "TARGET SUBGROUP: [1, 2]" in write_text

    def test_mode_label_prefixed_subgroup(self, tmp_path):
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        inspect_result = run_review(SignalDispositionReviewConfig(database=db, parent_signal_ids=ids, signal_ids=(a, b)))
        assert "Mode: SUBGROUP INSPECT (read-only)" in render_result(inspect_result)
        dry_run = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r",
        ))
        assert "Mode: SUBGROUP DRY RUN (no write)" in render_result(dry_run)
        write = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r", allow_database_write=True,
        ))
        assert "Mode: SUBGROUP WRITE (committed)" in render_result(write)


class TestSubgroupNoOverrideFlag:
    def test_no_force_flag_exists(self):
        """Checks argparse's own registered option strings, not rendered
        help text - the module's own top-of-file docstring legitimately
        contains the substring "--force" in prose explaining its absence,
        which a naive text-search would false-positive on."""
        parser = review_module._parser()
        option_strings = {opt for action in parser._actions for opt in action.option_strings}
        assert not any("force" in opt.lower() for opt in option_strings)

    def test_conflict_refusal_has_no_bypass_kwarg(self, tmp_path):
        """SignalDispositionReviewConfig itself has no field capable of
        bypassing a conflict refusal - structural proof, not just absence
        of a CLI flag."""
        field_names = {f.name for f in dataclasses.fields(SignalDispositionReviewConfig)}
        assert not (field_names & {"force", "override", "ignore_conflicts", "skip_conflict_check"})


class TestSubgroupInformationFirewall:
    def test_no_forbidden_signal_attribute_access_ast(self):
        """Reuses TestNoAutoDecision's own forbidden-attribute list - the
        subgroup path is part of the SAME module and must satisfy the
        identical firewall the whole-group path already does."""
        tree = ast.parse(inspect_module.getsource(review_module))
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert not (attrs & set(TestNoAutoDecision._FORBIDDEN_SIGNAL_ATTRS))

    def test_subgroup_eligibility_decided_only_by_governance_structure(self, tmp_path):
        """A subgroup with SECRET-laden Signal titles/values is validated
        identically to one without - eligibility never depends on Signal
        content, only on parent/target/conflict governance structure."""
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        with Session(engine) as session:
            airport = Airport(name="Firewall Airport", country="XX")
            session.add(airport)
            sigs = [
                Signal(airport=airport, title="SECRET TITLE", category="replacement", confidence="high", estimated_total_value_usd=999999999),
                Signal(airport=airport, title="OTHER SECRET", category="replacement", confidence="high"),
                Signal(airport=airport, title="THIRD SECRET", category="replacement", confidence="high"),
            ]
            session.add_all(sigs)
            session.commit()
            ids = tuple(s.id for s in sigs)
        a, b, c = ids
        result = run_review(SignalDispositionReviewConfig(
            database=db, parent_signal_ids=ids, signal_ids=(a, b),
            decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r",
        ))
        assert result.action_eligible is True
        assert "SECRET" not in render_result(result)
        assert "999999999" not in render_result(result)


class TestSubgroupModeNeverInferredFromLegacyCall:
    def test_bare_signal_ids_without_parent_is_ordinary_whole_group_mode(self, tmp_path):
        """Passing FEWER signal ids than some raw group's own size, with NO
        --parent-signal-id, must never be silently reinterpreted as
        subgroup mode - it is either an ordinary (possibly-not-found) whole
        -group target or a validation error, exactly as before D4D8D."""
        db = tmp_path / "t.db"
        engine = _make_full_db(db)
        airport_id, ids = _add_fh_d4_group(engine, 3)
        a, b, c = ids
        result = run_review(SignalDispositionReviewConfig(database=db, signal_ids=(a, b)))
        assert result.subgroup_mode is False
        assert result.blockers == (TARGET_GROUP_NOT_CURRENT_BLOCKER,)  # {a,b} alone is not a current raw group
        assert result.parent_found is None
