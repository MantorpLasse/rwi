"""Tests for app/services/signal_disposition_resolution.py (D4D3,
docs/architecture/fh-d4-signal-disposition-design.md).

Every test uses an isolated in-memory or tmp_path-scoped SQLite database.
Nothing in this file ever opens data/runway_safe.db - grep-verified (see
TestNoRealDatabaseAccess).
"""
from __future__ import annotations

import ast
import inspect as inspect_module
import sqlite3

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Signal
from app.models.signal_disposition import SignalDisposition, SignalDispositionMember
from app.services.signal_disposition_persistence import record_signal_group_disposition
from app.services.signal_disposition_resolution import (
    CONFIRMED_DISTINCT,
    CONFIRMED_SAME_REAL_WORLD_EFFORT,
    UNREVIEWED,
    RelatedHistoricalDisposition,
    SignalDispositionStatus,
    find_related_historical_dispositions,
    get_latest_disposition_for_member_set,
    resolve_fh_d4_group_status,
    resolve_fh_d4_group_statuses,
)
import scripts.migrate_signal_disposition_d4d2 as migration


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def make_session_with_foreign_keys_enforced():
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


def _signals(session, n, airport=None):
    if airport is None:
        airport = Airport(name="Resolution Test Airport", country="XX")
        session.add(airport)
    sigs = [Signal(airport=airport, title=f"S{i}", category="replacement", confidence="high") for i in range(n)]
    session.add_all(sigs)
    session.commit()
    return tuple(s.id for s in sigs)


# ---------------------------------------------------------------------------
# No history
# ---------------------------------------------------------------------------


class TestNoHistory:
    def test_no_history_is_unreviewed(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        status = resolve_fh_d4_group_status(session, ids)
        assert status.status == UNREVIEWED
        assert status.signal_ids == tuple(sorted(ids))
        assert status.latest_disposition_id is None
        assert status.decision is None
        assert status.reviewer is None
        assert status.reason is None
        assert status.created_at is None
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# DISTINCT / SAME exact match
# ---------------------------------------------------------------------------


class TestExactMatch:
    def test_distinct_exact_match(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        d = record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        status = resolve_fh_d4_group_status(session, ids)
        assert status.status == CONFIRMED_DISTINCT
        assert status.latest_disposition_id == d.id
        assert status.decision == "DISTINCT"
        assert status.reviewer == "human:x"
        assert status.reason == "x"
        assert status.created_at == d.created_at
        session.close(); engine.dispose()

    def test_same_real_world_effort_exact_match(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        record_signal_group_disposition(
            session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:y", reason="y",
        )
        session.commit()
        status = resolve_fh_d4_group_status(session, ids)
        assert status.status == CONFIRMED_SAME_REAL_WORLD_EFFORT
        session.close(); engine.dispose()

    def test_reversed_input_same_result(self):
        engine, session = make_session()
        ids = _signals(session, 3)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        forward = resolve_fh_d4_group_status(session, ids)
        reversed_result = resolve_fh_d4_group_status(session, tuple(reversed(ids)))
        assert forward == reversed_result
        session.close(); engine.dispose()

    def test_duplicate_caller_ids_normalized(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        status = resolve_fh_d4_group_status(session, [ids[0], ids[1], ids[0], ids[1]])
        assert status.status == CONFIRMED_DISTINCT
        assert status.signal_ids == tuple(sorted(ids))
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Pair / triple / quintuple
# ---------------------------------------------------------------------------


class TestGroupSizesDistinct:
    def test_pair(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        assert resolve_fh_d4_group_status(session, ids).status == CONFIRMED_DISTINCT
        session.close(); engine.dispose()

    def test_triple(self):
        engine, session = make_session()
        ids = _signals(session, 3)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        assert resolve_fh_d4_group_status(session, ids).status == CONFIRMED_DISTINCT
        session.close(); engine.dispose()

    def test_quintuple(self):
        engine, session = make_session()
        ids = _signals(session, 5)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        assert resolve_fh_d4_group_status(session, ids).status == CONFIRMED_DISTINCT
        session.close(); engine.dispose()

    def test_subset_of_dispositioned_triple_is_unreviewed(self):
        engine, session = make_session()
        ids = _signals(session, 3)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        assert resolve_fh_d4_group_status(session, ids[:2]).status == UNREVIEWED
        session.close(); engine.dispose()

    def test_superset_of_dispositioned_pair_is_unreviewed(self):
        engine, session = make_session()
        ids = _signals(session, 3)
        record_signal_group_disposition(session, signal_ids=ids[:2], decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        assert resolve_fh_d4_group_status(session, ids).status == UNREVIEWED
        session.close(); engine.dispose()


class TestGroupSizesSame:
    def test_pair_same(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        record_signal_group_disposition(
            session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="x",
        )
        session.commit()
        status = resolve_fh_d4_group_status(session, ids)
        assert status.status == CONFIRMED_SAME_REAL_WORLD_EFFORT
        session.close(); engine.dispose()

    def test_triple_same(self):
        engine, session = make_session()
        ids = _signals(session, 3)
        record_signal_group_disposition(
            session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="x",
        )
        session.commit()
        assert resolve_fh_d4_group_status(session, ids).status == CONFIRMED_SAME_REAL_WORLD_EFFORT
        session.close(); engine.dispose()

    def test_quintuple_same(self):
        engine, session = make_session()
        ids = _signals(session, 5)
        record_signal_group_disposition(
            session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="x",
        )
        session.commit()
        status = resolve_fh_d4_group_status(session, ids)
        assert status.status == CONFIRMED_SAME_REAL_WORLD_EFFORT
        # No canonical Signal inferred - status carries no such field at all.
        assert not hasattr(status, "canonical_signal_id")
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_single_id_rejected(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        with pytest.raises(ValueError, match="at least 2 distinct"):
            resolve_fh_d4_group_status(session, [ids[0]])
        session.close(); engine.dispose()

    def test_empty_ids_rejected(self):
        engine, session = make_session()
        with pytest.raises(ValueError, match="at least 2 distinct"):
            resolve_fh_d4_group_status(session, [])
        session.close(); engine.dispose()

    def test_missing_signal_id_fails_loud_not_unreviewed(self):
        """§6: a caller claiming a real current group made of nonexistent
        ids must get a clear failure, never a silently-wrong UNREVIEWED."""
        engine, session = make_session()
        ids = _signals(session, 2)
        with pytest.raises(ValueError, match="do not exist"):
            resolve_fh_d4_group_status(session, [ids[0], 999999])
        session.close(); engine.dispose()

    def test_all_missing_signal_ids_fails_loud(self):
        engine, session = make_session()
        with pytest.raises(ValueError, match="do not exist"):
            resolve_fh_d4_group_status(session, [888888, 999999])
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Latest decision / determinism / tie-break
# ---------------------------------------------------------------------------


class TestLatestDecision:
    def test_multiple_historical_decisions_latest_wins(self):
        """D1 DISTINCT {A,B}; D2 SAME {A,B}; D3 DISTINCT {A,B} - expected
        latest result: D3/CONFIRMED_DISTINCT. All historical rows remain
        queryable (D4D1's own append-only guarantee, unaffected here)."""
        engine, session = make_session()
        ids = _signals(session, 2)
        d1 = record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:a", reason="1")
        session.commit()
        d2 = record_signal_group_disposition(
            session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:b", reason="2", supersedes_id=d1.id,
        )
        session.commit()
        d3 = record_signal_group_disposition(
            session, signal_ids=ids, decision="DISTINCT", reviewer="human:c", reason="3", supersedes_id=d2.id,
        )
        session.commit()

        status = resolve_fh_d4_group_status(session, ids)
        assert status.latest_disposition_id == d3.id
        assert status.status == CONFIRMED_DISTINCT
        assert session.query(SignalDisposition).count() == 3  # all history intact
        session.close(); engine.dispose()

    def test_same_timestamp_id_tiebreak(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        d1 = record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:a", reason="1")
        session.flush()
        # created_at is set on the initial INSERT, before any flush of d2 -
        # setting it afterward would be a post-flush UPDATE, which the
        # header's own immutability listener correctly blocks (proven
        # separately in tests/test_signal_disposition_persistence.py).
        d2 = SignalDisposition(
            decision="SAME_REAL_WORLD_EFFORT", reason="2", reviewer="human:b",
            supersedes_id=d1.id, created_at=d1.created_at,
        )
        from app.models.signal_disposition import ACCEPTING_INITIAL_MEMBERS_ATTR
        setattr(d2, ACCEPTING_INITIAL_MEMBERS_ATTR, True)
        session.add(d2)
        session.flush()
        session.add(SignalDispositionMember(disposition_id=d2.id, signal_id=ids[0]))
        session.add(SignalDispositionMember(disposition_id=d2.id, signal_id=ids[1]))
        session.flush()
        setattr(d2, ACCEPTING_INITIAL_MEMBERS_ATTR, False)
        session.commit()

        assert d1.created_at == d2.created_at
        assert d2.id > d1.id
        status = resolve_fh_d4_group_status(session, ids)
        assert status.latest_disposition_id == d2.id  # higher id wins on tie
        session.close(); engine.dispose()

    def test_determinism_repeated_calls_equal(self):
        engine, session = make_session()
        ids = _signals(session, 3)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        r1 = resolve_fh_d4_group_status(session, ids)
        r2 = resolve_fh_d4_group_status(session, ids)
        assert r1 == r2
        session.close(); engine.dispose()

    def test_reversed_db_insertion_order_no_effect(self):
        engine, session = make_session()
        airport = Airport(name="X", country="XX")
        session.add(airport)
        sigs = [Signal(airport=airport, title=f"S{i}", category="replacement", confidence="high") for i in range(3)]
        session.add_all(sigs)
        session.commit()
        ids = tuple(s.id for s in sigs)
        record_signal_group_disposition(
            session, signal_ids=tuple(reversed(ids)), decision="DISTINCT", reviewer="human:x", reason="x",
        )
        session.commit()
        status = resolve_fh_d4_group_status(session, ids)
        assert status.status == CONFIRMED_DISTINCT
        assert status.signal_ids == tuple(sorted(ids))
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Supersession chain / competing unsuperseded history
# ---------------------------------------------------------------------------


class TestSupersessionChain:
    def test_chain_resolution_consistent(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        d1 = record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:a", reason="1")
        session.commit()
        d2 = record_signal_group_disposition(
            session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:b", reason="2", supersedes_id=d1.id,
        )
        session.commit()
        d3 = record_signal_group_disposition(
            session, signal_ids=ids, decision="DISTINCT", reviewer="human:c", reason="3", supersedes_id=d2.id,
        )
        session.commit()
        status = resolve_fh_d4_group_status(session, ids)
        assert status.latest_disposition_id == d3.id

    def test_competing_unsuperseded_disposition_still_becomes_latest(self):
        """§19: D4D1 does not require a later same-exact-set disposition to
        supersede the previous one. A later row with supersedes_id=None
        still becomes 'latest' by recency alone - matching
        get_latest_reviewer_action()'s own established precedent
        (recency, never chain-walking). This is a deliberate design
        decision (documented in this module's own docstring and the D4D3
        report), not a silently-invented governance rule."""
        engine, session = make_session()
        ids = _signals(session, 2)
        d1 = record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:a", reason="1")
        session.commit()
        d2 = record_signal_group_disposition(
            session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:b", reason="2", supersedes_id=d1.id,
        )
        session.commit()
        # A competing, unsuperseded row for the SAME exact set - no supersedes_id at all.
        d_competing = record_signal_group_disposition(
            session, signal_ids=ids, decision="DISTINCT", reviewer="human:d", reason="competing", supersedes_id=None,
        )
        session.commit()

        assert d_competing.created_at >= d2.created_at
        status = resolve_fh_d4_group_status(session, ids)
        assert status.latest_disposition_id == d_competing.id
        assert status.status == CONFIRMED_DISTINCT
        # Review-checkpoint addition: two independently-recorded roots
        # exist for this exact set (d1 and d_competing both have
        # supersedes_id=None) - d2's own supersedes_id=d1.id does not
        # create a third root. See TestIndependentRootCount for focused
        # coverage of this field.
        assert status.independent_root_count == 2
        session.close(); engine.dispose()


class TestIndependentRootCount:
    """Review-checkpoint addition: regression tests for the
    `independent_root_count` field added to close a real visibility gap
    (mission §9) - two independent reviewers can legitimately create
    unlinked competing dispositions for the same exact set via D4D1's own
    already-committed write API, and the read side previously gave zero
    visibility into that. This field never changes `status`/
    `latest_disposition_id` - only 'latest wins' resolution does."""

    def test_unreviewed_group_has_zero_independent_roots(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        status = resolve_fh_d4_group_status(session, ids)
        assert status.independent_root_count == 0
        session.close(); engine.dispose()

    def test_single_disposition_has_one_independent_root(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        status = resolve_fh_d4_group_status(session, ids)
        assert status.independent_root_count == 1
        session.close(); engine.dispose()

    def test_properly_chained_supersession_stays_at_one_root(self):
        """A clean supersession chain (every later row links via
        supersedes_id) must report exactly one independent root - this is
        the well-governed, expected shape, never inflated by chain length."""
        engine, session = make_session()
        ids = _signals(session, 2)
        d1 = record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:a", reason="1")
        session.commit()
        d2 = record_signal_group_disposition(
            session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:b", reason="2", supersedes_id=d1.id,
        )
        session.commit()
        record_signal_group_disposition(
            session, signal_ids=ids, decision="DISTINCT", reviewer="human:c", reason="3", supersedes_id=d2.id,
        )
        session.commit()
        status = resolve_fh_d4_group_status(session, ids)
        assert status.independent_root_count == 1
        session.close(); engine.dispose()

    def test_competing_unsuperseded_dispositions_report_two_independent_roots(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:a", reason="1")
        session.commit()
        # A second, entirely independent root for the same exact set - no supersedes_id at all.
        d_second_root = record_signal_group_disposition(
            session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:d", reason="competing", supersedes_id=None,
        )
        session.commit()
        status = resolve_fh_d4_group_status(session, ids)
        assert status.independent_root_count == 2
        # The count is additional visibility only - status/latest still
        # resolve purely by recency, unaffected by the count's existence.
        assert status.latest_disposition_id == d_second_root.id
        assert status.status == CONFIRMED_SAME_REAL_WORLD_EFFORT
        session.close(); engine.dispose()

    def test_independent_root_count_present_in_batch_results_too(self):
        """The field must be populated identically whether reached via
        the single-group or the batch resolver - they are provably
        equivalent by construction (resolve_fh_d4_group_status() is a
        trivial one-group special case of resolve_fh_d4_group_statuses())."""
        engine, session = make_session()
        ids = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:a", reason="1")
        session.commit()
        record_signal_group_disposition(
            session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:d", reason="competing", supersedes_id=None,
        )
        session.commit()
        batch = resolve_fh_d4_group_statuses(session, [ids])
        assert batch[tuple(sorted(ids))].independent_root_count == 2
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Related / stale historical context
# ---------------------------------------------------------------------------


class TestRelatedHistory:
    def test_subset_case_A_B_grew_to_A_B_C(self):
        engine, session = make_session()
        ids = _signals(session, 3)
        d = record_signal_group_disposition(session, signal_ids=ids[:2], decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        related = find_related_historical_dispositions(session, ids)  # current is the triple
        assert len(related) == 1
        assert related[0].disposition_id == d.id
        assert related[0].relation == "SUBSET"
        assert related[0].member_signal_ids == tuple(sorted(ids[:2]))
        session.close(); engine.dispose()

    def test_superset_case_A_B_C_shrank_to_A_B(self):
        engine, session = make_session()
        ids = _signals(session, 3)
        d = record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        related = find_related_historical_dispositions(session, ids[:2])  # current is the pair
        assert len(related) == 1
        assert related[0].disposition_id == d.id
        assert related[0].relation == "SUPERSET"
        session.close(); engine.dispose()

    def test_exact_match_never_appears_in_related_results(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        related = find_related_historical_dispositions(session, ids)
        assert related == ()  # the exact match is "current," not "related"
        session.close(); engine.dispose()

    def test_merely_overlapping_unrelated_set_not_returned(self):
        """old {A,B}, current {A,C} - shares only A, deliberately NOT
        treated as related (module docstring's own reasoning: too likely
        to be a genuinely different historical case, and bare intersection
        would flood a presentation layer with noise)."""
        engine, session = make_session()
        airport = Airport(name="X", country="XX")
        session.add(airport)
        a = Signal(airport=airport, title="A", category="replacement", confidence="high")
        b = Signal(airport=airport, title="B", category="replacement", confidence="high")
        c = Signal(airport=airport, title="C", category="replacement", confidence="high")
        session.add_all([a, b, c])
        session.commit()
        record_signal_group_disposition(session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        related = find_related_historical_dispositions(session, [a.id, c.id])
        assert related == ()
        session.close(); engine.dispose()

    def test_third_member_joining_pair_old_disposition_does_not_resolve_new_group(self):
        """§10/§25 (#41/#67-shaped, generic ids only): stored {A,B}
        DISTINCT; current group is {A,B,C} (a third Signal joined the
        candidate set). Expected: current group is UNREVIEWED; the old
        {A,B} disposition remains inspectable as related/stale context
        only, never treated as resolving the new group."""
        engine, session = make_session()
        ids = _signals(session, 3)
        record_signal_group_disposition(session, signal_ids=ids[:2], decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()

        current_status = resolve_fh_d4_group_status(session, ids)
        assert current_status.status == UNREVIEWED

        related = find_related_historical_dispositions(session, ids)
        assert len(related) == 1
        assert related[0].relation == "SUBSET"
        session.close(); engine.dispose()

    def test_related_history_sorted_deterministically(self):
        engine, session = make_session()
        ids = _signals(session, 5)
        record_signal_group_disposition(session, signal_ids=ids[:2], decision="DISTINCT", reviewer="human:a", reason="a")
        session.commit()
        record_signal_group_disposition(session, signal_ids=ids[:3], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:b", reason="b")
        session.commit()
        related = find_related_historical_dispositions(session, ids)
        assert len(related) == 2
        assert related == tuple(sorted(related, key=lambda r: (r.created_at, r.disposition_id)))
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Batch resolution
# ---------------------------------------------------------------------------


class TestBatchResolution:
    def test_batch_equivalence_to_single_group_resolution(self):
        engine, session = make_session()
        ids = _signals(session, 10)
        groups = [ids[0:2], ids[2:5], ids[5:10]]
        record_signal_group_disposition(session, signal_ids=groups[0], decision="DISTINCT", reviewer="human:a", reason="a")
        record_signal_group_disposition(session, signal_ids=groups[1], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:b", reason="b")
        session.commit()
        # groups[2] left unreviewed

        batch = resolve_fh_d4_group_statuses(session, groups)
        for group in groups:
            key = tuple(sorted(set(group)))
            assert batch[key] == resolve_fh_d4_group_status(session, group)
        session.close(); engine.dispose()

    def test_no_cross_group_contamination(self):
        engine, session = make_session()
        ids = _signals(session, 4)
        record_signal_group_disposition(session, signal_ids=ids[:2], decision="DISTINCT", reviewer="human:a", reason="a")
        session.commit()
        batch = resolve_fh_d4_group_statuses(session, [ids[:2], ids[2:]])
        assert batch[tuple(sorted(ids[:2]))].status == CONFIRMED_DISTINCT
        assert batch[tuple(sorted(ids[2:]))].status == UNREVIEWED
        session.close(); engine.dispose()

    def test_batch_query_count_bounded_not_n_plus_one(self):
        engine, session = make_session()
        ids = _signals(session, 12)
        groups = [ids[0:2], ids[2:5], ids[5:10], ids[10:12]]
        record_signal_group_disposition(session, signal_ids=groups[0], decision="DISTINCT", reviewer="human:a", reason="a")
        record_signal_group_disposition(session, signal_ids=groups[2], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:b", reason="b")
        session.commit()

        statements = []

        def _capture(_conn, _cursor, statement, *_args, **_kwargs):
            statements.append(statement.strip().upper())

        engine_for_events = session.get_bind()
        event.listen(engine_for_events, "before_cursor_execute", _capture)
        try:
            resolve_fh_d4_group_statuses(session, groups)
        finally:
            event.remove(engine_for_events, "before_cursor_execute", _capture)

        select_statements = [s for s in statements if s.startswith("SELECT")]
        # Review-checkpoint fix: tightened from a "generous" `< 30` bound
        # (which did NOT catch the real per-group existence-check defect -
        # see TestBatchResolutionQueryCost below for the reproduction) to
        # an exact count: 1 existence-check query + 3 candidate/member/
        # header queries = 4, regardless of group/Signal count.
        assert len(select_statements) == 4
        session.close(); engine.dispose()


class TestBatchResolutionQueryCost:
    """Review-checkpoint addition: the original `test_batch_query_count_
    bounded_not_n_plus_one` asserted only a generous `< 30` bound, which did
    not actually catch the real defect (a per-group existence-check loop
    that emitted one query per distinct Signal id). These tests independently
    reproduce the fix's exact, scale-invariant query cost, per this
    mission's own §13 (CRITICAL: 1/12/100/large-membership scenarios)."""

    @pytest.mark.parametrize("n_groups", [1, 12, 100])
    def test_batch_query_count_constant_regardless_of_scale(self, n_groups):
        engine, session = make_session()
        ids = _signals(session, n_groups * 2)
        groups = [ids[i * 2 : i * 2 + 2] for i in range(n_groups)]
        record_signal_group_disposition(session, signal_ids=groups[0], decision="DISTINCT", reviewer="human:a", reason="a")
        session.commit()

        statements = []

        def _capture(_conn, _cursor, statement, *_args, **_kwargs):
            statements.append(statement.strip().upper())

        engine_for_events = session.get_bind()
        event.listen(engine_for_events, "before_cursor_execute", _capture)
        try:
            resolve_fh_d4_group_statuses(session, groups)
        finally:
            event.remove(engine_for_events, "before_cursor_execute", _capture)

        select_statements = [s for s in statements if s.startswith("SELECT")]
        assert len(select_statements) == 4, (
            f"expected exactly 4 SELECT statements for {n_groups} groups, got "
            f"{len(select_statements)}: {select_statements}"
        )
        session.close(); engine.dispose()

    def test_existence_check_alone_is_one_query_for_large_id_set(self):
        """Isolates `_validate_all_exist()`'s own batching directly: a
        single call covering 100 distinct Signal ids must be exactly one
        `SELECT ... IN (...)` statement, never one per id (the real,
        reproduced defect this review closed: 100 two-Signal groups
        originally emitted 179 statements via a per-id `session.get()`
        loop, called once per group)."""
        engine, session = make_session()
        ids = _signals(session, 100)

        statements = []

        def _capture(_conn, _cursor, statement, *_args, **_kwargs):
            statements.append(statement.strip().upper())

        event.listen(engine, "before_cursor_execute", _capture)
        try:
            from app.services.signal_disposition_resolution import _validate_all_exist
            with session.no_autoflush:
                _validate_all_exist(session, set(ids))
        finally:
            event.remove(engine, "before_cursor_execute", _capture)

        select_statements = [s for s in statements if s.startswith("SELECT")]
        assert len(select_statements) == 1
        session.close(); engine.dispose()

    def test_batch_with_no_matching_dispositions_short_circuits_to_two_queries(self):
        """When none of the batch's Signal ids appear in any disposition
        member row, `_member_sets_and_headers_for_candidates()` must stop
        after discovering that - no member-row or header query needed -
        so existence check (1) + candidate-id lookup (1) = 2 total, not 4."""
        engine, session = make_session()
        ids = _signals(session, 4)
        groups = [ids[:2], ids[2:]]

        statements = []

        def _capture(_conn, _cursor, statement, *_args, **_kwargs):
            statements.append(statement.strip().upper())

        event.listen(engine, "before_cursor_execute", _capture)
        try:
            resolve_fh_d4_group_statuses(session, groups)
        finally:
            event.remove(engine, "before_cursor_execute", _capture)

        select_statements = [s for s in statements if s.startswith("SELECT")]
        assert len(select_statements) == 2
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Real FH-D4 shapes (synthetic only) - 6 pairs, 4 triples, 2 quintuples
# ---------------------------------------------------------------------------


class TestRealFhD4ShapesSynthetic:
    def test_12_group_cardinality_mixture(self):
        engine, session = make_session()
        airport = Airport(name="Synthetic Airport", country="XX")
        session.add(airport)
        session.commit()

        groups = []
        next_id = [0]

        def _new_signals(n):
            sigs = [
                Signal(airport=airport, title=f"S{next_id[0] + i}", category="replacement", confidence="high")
                for i in range(n)
            ]
            session.add_all(sigs)
            session.commit()
            next_id[0] += n
            return tuple(s.id for s in sigs)

        for _ in range(6):
            groups.append(_new_signals(2))
        for _ in range(4):
            groups.append(_new_signals(3))
        for _ in range(2):
            groups.append(_new_signals(5))

        assert len(groups) == 12
        assert sum(len(g) for g in groups) == 6 * 2 + 4 * 3 + 2 * 5  # 34

        # Mixture: some DISTINCT, some SAME, some untouched.
        record_signal_group_disposition(session, signal_ids=groups[0], decision="DISTINCT", reviewer="human:a", reason="a")
        record_signal_group_disposition(session, signal_ids=groups[1], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:b", reason="b")
        record_signal_group_disposition(session, signal_ids=groups[6], decision="DISTINCT", reviewer="human:c", reason="c")
        record_signal_group_disposition(session, signal_ids=groups[10], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:d", reason="d")
        session.commit()

        batch = resolve_fh_d4_group_statuses(session, groups)
        assert len(batch) == 12
        assert batch[tuple(sorted(groups[0]))].status == CONFIRMED_DISTINCT
        assert batch[tuple(sorted(groups[1]))].status == CONFIRMED_SAME_REAL_WORLD_EFFORT
        assert batch[tuple(sorted(groups[6]))].status == CONFIRMED_DISTINCT
        assert batch[tuple(sorted(groups[10]))].status == CONFIRMED_SAME_REAL_WORLD_EFFORT
        untouched_count = sum(1 for g in groups if batch[tuple(sorted(g))].status == UNREVIEWED)
        assert untouched_count == 8
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Malformed historical shapes
# ---------------------------------------------------------------------------


class TestMalformedHistory:
    def test_zero_member_disposition_never_matches_anything(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        # Constructed directly, bypassing record_signal_group_disposition()'s
        # own >=2 cardinality enforcement - a header with no members at all.
        orphan = SignalDisposition(decision="DISTINCT", reason="x", reviewer="human:x")
        session.add(orphan)
        session.commit()

        status = resolve_fh_d4_group_status(session, ids)
        assert status.status == UNREVIEWED  # correctly never matches - not special-cased, just structurally impossible to equal a real group
        session.close(); engine.dispose()

    def test_one_member_disposition_never_matches_a_real_group(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        from app.models.signal_disposition import ACCEPTING_INITIAL_MEMBERS_ATTR
        single = SignalDisposition(decision="DISTINCT", reason="x", reviewer="human:x")
        setattr(single, ACCEPTING_INITIAL_MEMBERS_ATTR, True)
        session.add(single)
        session.flush()
        session.add(SignalDispositionMember(disposition_id=single.id, signal_id=ids[0]))
        session.flush()
        setattr(single, ACCEPTING_INITIAL_MEMBERS_ATTR, False)
        session.commit()

        status = resolve_fh_d4_group_status(session, ids)
        assert status.status == UNREVIEWED
        session.close(); engine.dispose()

    def test_zero_member_disposition_never_appears_in_related_history(self):
        engine, session = make_session()
        ids = _signals(session, 3)
        orphan = SignalDisposition(decision="DISTINCT", reason="x", reviewer="human:x")
        session.add(orphan)
        session.commit()
        related = find_related_historical_dispositions(session, ids)
        assert related == ()  # empty set is a (trivial) subset of everything, but never surfaced as noise
        session.close(); engine.dispose()

    def test_orphaned_member_row_with_no_header_fails_loud_not_silently(self):
        """§21: a SignalDispositionMember whose disposition_id has no
        matching SignalDisposition header must never be silently treated
        as a valid, resolvable disposition. In practice this is already
        blocked one layer earlier, at write time, by D4D1's own
        `before_insert` seal guard (`ACCEPTING_INITIAL_MEMBERS_ATTR`) -
        which is itself a second, independent fail-loud mechanism on top
        of this module's own `headers_by_id[...]` KeyError that would fire
        if such an orphaned row ever did reach the database (e.g. via a
        raw SQL insert that bypasses the ORM entirely, as
        `_validate_all_exist()`'s own docstring already documents this
        pipeline's trust boundary for). Either way: fail loud, never a
        fabricated status."""
        engine, session = make_session()
        ids = _signals(session, 2)
        with pytest.raises(ValueError, match="cannot be appended"):
            session.add(SignalDispositionMember(disposition_id=999999, signal_id=ids[0]))
            session.add(SignalDispositionMember(disposition_id=999999, signal_id=ids[1]))
            session.commit()
        session.rollback()

        with engine.begin() as conn:
            # Bypass the ORM (and its seal guard) entirely via raw SQL -
            # the only way an orphaned member row can reach this schema.
            conn.exec_driver_sql(
                "INSERT INTO signal_disposition_members (disposition_id, signal_id) VALUES (999999, ?), (999999, ?)",
                (ids[0], ids[1]),
            )
        session2 = Session(engine)
        with pytest.raises(KeyError):
            resolve_fh_d4_group_status(session2, ids)
        session2.close(); engine.dispose()

    def test_check_bypassed_impossible_decision_fails_loud_not_silently(self):
        """§21: if the `decision` CHECK constraint is ever bypassed
        (e.g. a hand-built schema without it, or a raw SQL path that
        skips ORM-level validation) and a header row ends up with a
        decision value outside {DISTINCT, SAME_REAL_WORLD_EFFORT}, this
        module must fail loud (KeyError from `_DECISION_TO_STATUS`) -
        never silently coerce it into a valid resolved status."""
        from sqlalchemy import create_engine as _create_engine

        engine = _create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            # Deliberately built without the decision CHECK constraint -
            # simulates a CHECK-bypassed row reaching this module.
            conn.exec_driver_sql(
                "CREATE TABLE signals (id INTEGER PRIMARY KEY)"
            )
            conn.exec_driver_sql("INSERT INTO signals (id) VALUES (1), (2)")
            conn.exec_driver_sql(
                "CREATE TABLE signal_dispositions ("
                "id INTEGER PRIMARY KEY, decision TEXT NOT NULL, reason TEXT NOT NULL, "
                "reviewer TEXT NOT NULL, created_at DATETIME NOT NULL, supersedes_id INTEGER)"
            )
            conn.exec_driver_sql(
                "CREATE TABLE signal_disposition_members ("
                "id INTEGER PRIMARY KEY, disposition_id INTEGER NOT NULL, signal_id INTEGER NOT NULL)"
            )
            conn.exec_driver_sql(
                "INSERT INTO signal_dispositions (id, decision, reason, reviewer, created_at) "
                "VALUES (1, 'BOGUS_IMPOSSIBLE_DECISION', 'x', 'human:x', '2026-01-01 00:00:00')"
            )
            conn.exec_driver_sql(
                "INSERT INTO signal_disposition_members (disposition_id, signal_id) VALUES (1, 1), (1, 2)"
            )
        session = Session(engine)
        with pytest.raises(KeyError):
            resolve_fh_d4_group_status(session, [1, 2])
        session.close(); engine.dispose()


class TestSignalDeleteSafetyPostDisposition:
    def test_migrated_or_create_all_fixture_relies_on_real_fk_not_compensation(self):
        engine, session = make_session_with_foreign_keys_enforced()
        ids = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()

        member_signal = session.get(Signal, ids[0])
        session.delete(member_signal)
        with pytest.raises(Exception, match="FOREIGN KEY constraint failed"):
            session.commit()
        session.rollback()

        # The read service never needed to compensate for a dangling
        # member FK - it relies entirely on DB integrity.
        status = resolve_fh_d4_group_status(session, ids)
        assert status.status == CONFIRMED_DISTINCT
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# No-autoflush / read-only / zero writes
# ---------------------------------------------------------------------------


class TestReadOnlyGuarantee:
    def test_never_autoflushes_callers_pending_mutation(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()

        airport = session.get(Airport, session.get(Signal, ids[0]).airport_id)
        airport.name = "PENDING - MUST NOT BE FLUSHED"
        assert airport in session.dirty

        resolve_fh_d4_group_status(session, ids)
        find_related_historical_dispositions(session, ids)

        assert airport in session.dirty
        session.rollback()
        session.close(); engine.dispose()

    def test_no_write_sql_statements_emitted_instrumented(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()

        statements = []

        def _capture(_conn, _cursor, statement, *_args, **_kwargs):
            statements.append(statement.strip().upper())

        event.listen(engine, "before_cursor_execute", _capture)
        try:
            resolve_fh_d4_group_status(session, ids)
            resolve_fh_d4_group_statuses(session, [ids])
            find_related_historical_dispositions(session, ids)
            get_latest_disposition_for_member_set(session, ids)
        finally:
            event.remove(engine, "before_cursor_execute", _capture)

        write_statements = [s for s in statements if s.startswith(("INSERT", "UPDATE", "DELETE"))]
        assert write_statements == []
        session.close(); engine.dispose()

    def test_session_stays_clean_after_every_read_function(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()

        resolve_fh_d4_group_status(session, ids)
        find_related_historical_dispositions(session, ids)
        get_latest_disposition_for_member_set(session, ids)

        assert not session.new
        assert not session.dirty
        assert not session.deleted
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Information firewall
# ---------------------------------------------------------------------------


class TestInformationFirewall:
    _FORBIDDEN_SIGNAL_ATTRS = (
        "title", "notes", "source_notes", "estimated_total_value_usd",
        "estimated_emas_value_usd", "supplier", "likely_supplier", "supplier_reason",
        "confirmed_vendor", "category", "confidence", "status", "manual_year_estimate",
        "published", "airport_id", "runway_id", "installation_id",
    )

    def test_module_never_references_forbidden_signal_attributes_ast(self):
        import app.services.signal_disposition_resolution as module

        tree = ast.parse(inspect_module.getsource(module))
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert not (attrs & set(self._FORBIDDEN_SIGNAL_ATTRS))

    def test_behavioral_no_signal_content_leak(self):
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
        record_signal_group_disposition(session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()

        status = resolve_fh_d4_group_status(session, [a.id, b.id])
        status_repr = repr(status)
        assert "SECRET" not in status_repr
        assert "123456789" not in status_repr
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# International / Unicode
# ---------------------------------------------------------------------------


class TestInternational:
    def test_unicode_reviewer_reason_round_trip(self):
        engine, session = make_session()
        airport = Airport(name="Örnsköldsvik Flygplats", country="Sverige")
        session.add(airport)
        a = Signal(airport=airport, title="A", category="replacement", confidence="high")
        b = Signal(airport=airport, title="B", category="replacement", confidence="high")
        session.add_all([a, b])
        session.commit()
        record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="SAME_REAL_WORLD_EFFORT",
            reviewer="Åsa Lindqvist", reason="Två signaler beskriver samma EMAS-ersättning.",
        )
        session.commit()

        status = resolve_fh_d4_group_status(session, [a.id, b.id])
        assert status.reviewer == "Åsa Lindqvist"
        assert status.reason == "Två signaler beskriver samma EMAS-ersättning."
        session.close(); engine.dispose()

    def test_generic_international_fixture_no_us_assumption(self):
        engine, session = make_session()
        airport = Airport(name="東京国際空港", country="日本")
        session.add(airport)
        a = Signal(airport=airport, title="A", category="replacement", confidence="high")
        b = Signal(airport=airport, title="B", category="replacement", confidence="high")
        session.add_all([a, b])
        session.commit()
        record_signal_group_disposition(
            session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="レビュアー", reason="別々の事業。",
        )
        session.commit()
        status = resolve_fh_d4_group_status(session, [a.id, b.id])
        assert status.status == CONFIRMED_DISTINCT
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Migration-created DB parity
# ---------------------------------------------------------------------------


class TestMigrationSchemaParity:
    def test_resolution_works_against_migration_created_db(self, tmp_path):
        from sqlalchemy import MetaData

        db = tmp_path / "migrated.db"
        engine = create_engine(f"sqlite:///{db}")
        pre_meta = MetaData()
        for name, table in Base.metadata.tables.items():
            if name not in ("signal_dispositions", "signal_disposition_members"):
                table.to_metadata(pre_meta)
        pre_meta.create_all(engine)
        engine.dispose()

        migration.upgrade(db)

        engine2 = create_engine(f"sqlite:///{db}")
        with Session(engine2) as session:
            ids = _signals(session, 3)
            record_signal_group_disposition(session, signal_ids=ids[:2], decision="DISTINCT", reviewer="human:x", reason="x")
            session.commit()

            status = resolve_fh_d4_group_status(session, ids[:2])
            assert status.status == CONFIRMED_DISTINCT
            related = find_related_historical_dispositions(session, ids)
            assert len(related) == 1
            assert related[0].relation == "SUBSET"
        engine2.dispose()


# ---------------------------------------------------------------------------
# Failure behavior - fail loud
# ---------------------------------------------------------------------------


class TestFailureLoud:
    def test_missing_schema_raises_not_unreviewed(self):
        """No signal_dispositions/signal_disposition_members table at all -
        must propagate a real, uncaught exception, never silently resolve
        to UNREVIEWED."""
        engine = create_engine("sqlite:///:memory:")
        # Deliberately create only Signal/Airport - not the disposition tables.
        from sqlalchemy import MetaData
        partial_meta = MetaData()
        for name, table in Base.metadata.tables.items():
            if name not in ("signal_dispositions", "signal_disposition_members"):
                table.to_metadata(partial_meta)
        partial_meta.create_all(engine)
        session = Session(engine)
        airport = Airport(name="X", country="XX")
        session.add(airport)
        a = Signal(airport=airport, title="A", category="replacement", confidence="high")
        b = Signal(airport=airport, title="B", category="replacement", confidence="high")
        session.add_all([a, b])
        session.commit()

        with pytest.raises(Exception):  # sqlalchemy.exc.OperationalError in practice
            resolve_fh_d4_group_status(session, [a.id, b.id])
        session.close(); engine.dispose()

    def test_no_try_except_anywhere_in_module_ast(self):
        import app.services.signal_disposition_resolution as module

        tree = ast.parse(inspect_module.getsource(module))
        try_nodes = [node for node in ast.walk(tree) if isinstance(node, ast.Try)]
        assert try_nodes == []


# ---------------------------------------------------------------------------
# No real database access
# ---------------------------------------------------------------------------


class TestNoRealDatabaseAccess:
    def test_no_reference_to_real_database_path(self):
        import app.services.signal_disposition_resolution as module

        tree = ast.parse(inspect_module.getsource(module))
        body = list(tree.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body = body[1:]
        literals = [
            node.value for node in ast.walk(ast.Module(body=body, type_ignores=[]))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        real_db_filename = "runway_safe" + ".db"
        assert not any(real_db_filename in literal for literal in literals)
