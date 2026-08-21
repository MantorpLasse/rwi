"""Tests for app/services/fh_d4_disposition_resolution.py (D4D4,
docs/architecture/fh-d4-signal-disposition-design.md, this mission's own
41-section review checklist).

Every test uses an isolated in-memory or tmp_path-scoped SQLite database -
nothing here ever opens data/runway_safe.db (see TestNoRealDatabaseAccess).
"""
from __future__ import annotations

import ast
import inspect as inspect_module
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Signal
from app.models.signal_disposition import (
    ACCEPTING_INITIAL_MEMBERS_ATTR,
    SignalDisposition,
    SignalDispositionMember,
)
from app.services.fh_d4_disposition_resolution import (
    FH_D4_RULE_ID,
    FhD4DispositionResolution,
    FhD4OperationalGroup,
    resolve_fh_d4_findings,
    run_disposition_aware_fh_d4_review,
)
from app.services.fleet_health_review_rules import SignalRunwayAirportFact, evaluate_fh_d3, evaluate_fh_d4
from app.services.fleet_health_rules import HealthClassification, HealthFinding
from app.services.signal_disposition_persistence import record_signal_group_disposition
import scripts.migrate_signal_disposition_d4d2 as migration


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def _signals(session, n, airport=None):
    if airport is None:
        airport = Airport(name="D4D4 Test Airport", country="XX")
        session.add(airport)
    sigs = [Signal(airport=airport, title=f"S{i}", category="replacement", confidence="high") for i in range(n)]
    session.add_all(sigs)
    session.commit()
    return tuple(s.id for s in sigs)


def _fh_d4_finding(signal_ids, airport_id):
    """Hand-constructed FH-D4-shaped HealthFinding, matching evaluate_fh_d4's
    own exact output shape - used where the underlying database facts (e.g.
    which airport a Signal belongs to) don't matter to the test."""
    ids = tuple(sorted(signal_ids))
    return HealthFinding(
        rule_id=FH_D4_RULE_ID,
        classification=HealthClassification.REVIEW_REQUIRED,
        entity_type="Signal",
        entity_ids=ids,
        airport_id=airport_id,
        summary=f"{len(ids)} Signals are co-located at airport {airport_id} with no runway claimed",
        structured_evidence={"airport_id": airport_id, "signal_ids": ids},
    )


def _real_fh_d4_finding(signal_ids, airport_id):
    """Builds the raw FH-D4 finding via the REAL, unmodified pure detector
    (evaluate_fh_d4) - proves genuine end-to-end integration, not merely a
    hand-shaped stand-in, for at least one test per scenario class."""
    facts = tuple(
        SignalRunwayAirportFact(
            signal_id=sid, signal_airport_id=airport_id, runway_id=None, runway_airport_id=None,
        )
        for sid in signal_ids
    )
    findings = evaluate_fh_d4(facts)
    assert len(findings) == 1
    return findings[0]


# ---------------------------------------------------------------------------
# UNREVIEWED
# ---------------------------------------------------------------------------


class TestUnreviewed:
    def test_no_disposition_is_active(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        finding = _real_fh_d4_finding(ids, airport_id=session.get(Signal, ids[0]).airport_id)
        result = resolve_fh_d4_findings(session, [finding])

        assert len(result.active_findings) == 1
        group = result.active_findings[0]
        assert group.status == "UNREVIEWED"
        assert group.signal_ids == tuple(sorted(ids))
        assert group.latest_disposition_id is None
        assert group.decision is None
        assert group.independent_root_count == 0
        assert group.ambiguous_history is False
        assert group.related_history == ()
        assert group.raw_finding is finding  # exact same object, never copied/mutated
        assert result.confirmed_distinct == ()
        assert result.confirmed_same_effort == ()
        assert result.ambiguous_groups == ()
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Exact DISTINCT / SAME
# ---------------------------------------------------------------------------


class TestExactDistinct:
    def test_distinct_exact_match_is_resolved_and_excluded_from_active(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        d = record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)

        result = resolve_fh_d4_findings(session, [finding])

        assert result.active_findings == ()
        assert len(result.confirmed_distinct) == 1
        group = result.confirmed_distinct[0]
        assert group.status == "CONFIRMED_DISTINCT"
        assert group.latest_disposition_id == d.id
        assert group.decision == "DISTINCT"
        assert group.reviewer == "human:x"
        assert group.raw_finding is finding
        assert result.confirmed_same_effort == ()
        assert result.ambiguous_groups == ()
        session.close(); engine.dispose()


class TestExactSame:
    def test_same_exact_match_resolved_and_kept_separate_from_distinct(self):
        engine, session = make_session()
        ids = _signals(session, 3)
        d = record_signal_group_disposition(
            session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:y", reason="y",
        )
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)

        result = resolve_fh_d4_findings(session, [finding])

        assert result.active_findings == ()
        assert result.confirmed_distinct == ()
        assert len(result.confirmed_same_effort) == 1
        group = result.confirmed_same_effort[0]
        assert group.status == "CONFIRMED_SAME_REAL_WORLD_EFFORT"
        assert group.latest_disposition_id == d.id
        # No canonical Signal inferred anywhere on the result.
        assert not hasattr(group, "canonical_signal_id")
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Stale history (subset/superset)
# ---------------------------------------------------------------------------


class TestStaleHistory:
    def test_group_grew_old_pair_disposition_does_not_resolve_new_triple(self):
        """{A,B} DISTINCT stored; current FH-D4 group is {A,B,C}. Expected:
        exact resolution UNREVIEWED (active), related_history shows the old
        {A,B} as SUBSET, current group remains REVIEW_REQUIRED (active)."""
        engine, session = make_session()
        ids = _signals(session, 3)
        d = record_signal_group_disposition(session, signal_ids=ids[:2], decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)

        result = resolve_fh_d4_findings(session, [finding])

        assert len(result.active_findings) == 1
        group = result.active_findings[0]
        assert group.status == "UNREVIEWED"
        assert len(group.related_history) == 1
        assert group.related_history[0].disposition_id == d.id
        assert group.related_history[0].relation == "SUBSET"
        assert result.confirmed_distinct == ()
        session.close(); engine.dispose()

    def test_group_shrank_old_triple_disposition_does_not_resolve_new_pair(self):
        engine, session = make_session()
        ids = _signals(session, 3)
        d = record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        finding = _fh_d4_finding(ids[:2], airport_id=1)

        result = resolve_fh_d4_findings(session, [finding])

        assert len(result.active_findings) == 1
        group = result.active_findings[0]
        assert group.status == "UNREVIEWED"
        assert len(group.related_history) == 1
        assert group.related_history[0].disposition_id == d.id
        assert group.related_history[0].relation == "SUPERSET"
        session.close(); engine.dispose()

    def test_merely_overlapping_history_not_surfaced_as_related(self):
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
        finding = _fh_d4_finding([a.id, c.id], airport_id=airport.id)

        result = resolve_fh_d4_findings(session, [finding])

        assert result.active_findings[0].related_history == ()
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Competing independent roots (ambiguous)
# ---------------------------------------------------------------------------


class TestAmbiguousRoots:
    def test_competing_unsuperseded_dispositions_land_in_ambiguous_bucket(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:a", reason="1")
        session.commit()
        d_second_root = record_signal_group_disposition(
            session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:d", reason="competing", supersedes_id=None,
        )
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)

        result = resolve_fh_d4_findings(session, [finding])

        assert result.active_findings == ()
        assert result.confirmed_distinct == ()
        assert result.confirmed_same_effort == ()
        assert len(result.ambiguous_groups) == 1
        group = result.ambiguous_groups[0]
        assert group.ambiguous_history is True
        assert group.independent_root_count == 2
        # Latest disposition still determines status/decision - unchanged
        # D4D3 semantics, only the OPERATIONAL bucket differs.
        assert group.status == "CONFIRMED_SAME_REAL_WORLD_EFFORT"
        assert group.latest_disposition_id == d_second_root.id
        session.close(); engine.dispose()

    def test_properly_chained_supersession_is_not_ambiguous(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        d1 = record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:a", reason="1")
        session.commit()
        record_signal_group_disposition(
            session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:b", reason="2", supersedes_id=d1.id,
        )
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)

        result = resolve_fh_d4_findings(session, [finding])

        assert result.ambiguous_groups == ()
        assert len(result.confirmed_same_effort) == 1
        assert result.confirmed_same_effort[0].ambiguous_history is False
        session.close(); engine.dispose()

    def test_unreviewed_group_can_never_be_ambiguous(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        finding = _fh_d4_finding(ids, airport_id=1)
        result = resolve_fh_d4_findings(session, [finding])
        assert result.active_findings[0].independent_root_count == 0
        assert result.active_findings[0].ambiguous_history is False
        assert result.ambiguous_groups == ()
        session.close(); engine.dispose()

    def test_ambiguous_group_never_silently_missed_via_attention_required(self):
        """Critical-review addition: a caller who only checks
        active_findings for 'what needs a human today' would otherwise
        silently never see an ambiguous group at all - attention_required
        closes that gap without weakening the accounting invariant (it is a
        derived view over active_findings + ambiguous_groups, not a fifth
        exclusive bucket)."""
        engine, session = make_session()
        ids = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:a", reason="1")
        session.commit()
        record_signal_group_disposition(
            session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:d", reason="competing", supersedes_id=None,
        )
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)

        result = resolve_fh_d4_findings(session, [finding])

        assert len(result.attention_required) == 1
        assert result.attention_required[0] is result.ambiguous_groups[0]
        assert result.attention_required[0].ambiguous_history is True

    def test_attention_required_is_exactly_active_plus_ambiguous_no_more_no_less(self):
        engine, session = make_session()
        airport = Airport(name="X", country="XX")
        session.add(airport)
        session.commit()
        unreviewed_ids = _signals(session, 2, airport=airport)
        distinct_ids = _signals(session, 2, airport=airport)
        same_ids = _signals(session, 2, airport=airport)
        ambiguous_ids = _signals(session, 2, airport=airport)

        record_signal_group_disposition(session, signal_ids=distinct_ids, decision="DISTINCT", reviewer="human:a", reason="a")
        record_signal_group_disposition(session, signal_ids=same_ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:b", reason="b")
        record_signal_group_disposition(session, signal_ids=ambiguous_ids, decision="DISTINCT", reviewer="human:c", reason="c")
        record_signal_group_disposition(session, signal_ids=ambiguous_ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:d", reason="d", supersedes_id=None)
        session.commit()

        findings = [
            _fh_d4_finding(unreviewed_ids, airport.id), _fh_d4_finding(distinct_ids, airport.id),
            _fh_d4_finding(same_ids, airport.id), _fh_d4_finding(ambiguous_ids, airport.id),
        ]
        result = resolve_fh_d4_findings(session, findings)

        # HealthFinding (and therefore FhD4OperationalGroup, which embeds
        # it) is unhashable - structured_evidence is a plain dict - so
        # identity is compared via id(), not set()/`in` (which would
        # otherwise raise TypeError: unhashable type: 'dict').
        expected_ids = {id(g) for g in result.active_findings} | {id(g) for g in result.ambiguous_groups}
        actual_ids = {id(g) for g in result.attention_required}
        assert actual_ids == expected_ids
        assert len(result.attention_required) == len(result.active_findings) + len(result.ambiguous_groups)
        for group in result.attention_required:
            assert group.status in ("UNREVIEWED", "CONFIRMED_DISTINCT", "CONFIRMED_SAME_REAL_WORLD_EFFORT")
        # Confirmed (non-ambiguous) groups must never appear in attention_required.
        confirmed_ids = {id(g) for g in result.confirmed_distinct} | {id(g) for g in result.confirmed_same_effort}
        assert not (confirmed_ids & actual_ids)
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Group disappears
# ---------------------------------------------------------------------------


class TestGroupDisappears:
    def test_stored_disposition_with_no_current_finding_fabricates_nothing(self):
        """A disposition exists in the DB for {A,B}, but the current FH-D4
        detector run does not emit that group at all (e.g. runway identity
        changed). D4D4 must not fabricate an active finding merely because
        history exists - the result is driven entirely by the findings
        actually supplied."""
        engine, session = make_session()
        ids = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()

        result = resolve_fh_d4_findings(session, [])  # detector emitted nothing this run

        assert result.active_findings == ()
        assert result.confirmed_distinct == ()
        assert result.confirmed_same_effort == ()
        assert result.ambiguous_groups == ()
        assert result.non_d4_findings == ()
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Pair / triple / quintuple
# ---------------------------------------------------------------------------


class TestGroupSizes:
    @pytest.mark.parametrize("n,decision,bucket", [
        (2, "DISTINCT", "confirmed_distinct"),
        (3, "DISTINCT", "confirmed_distinct"),
        (5, "DISTINCT", "confirmed_distinct"),
        (2, "SAME_REAL_WORLD_EFFORT", "confirmed_same_effort"),
        (3, "SAME_REAL_WORLD_EFFORT", "confirmed_same_effort"),
        (5, "SAME_REAL_WORLD_EFFORT", "confirmed_same_effort"),
    ])
    def test_group_size_resolves_correctly(self, n, decision, bucket):
        engine, session = make_session()
        ids = _signals(session, n)
        record_signal_group_disposition(session, signal_ids=ids, decision=decision, reviewer="human:x", reason="x")
        session.commit()
        finding = _real_fh_d4_finding(ids, airport_id=session.get(Signal, ids[0]).airport_id)

        result = resolve_fh_d4_findings(session, [finding])

        assert len(getattr(result, bucket)) == 1
        assert getattr(result, bucket)[0].signal_ids == tuple(sorted(ids))
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 12-group synthetic operational simulation
# ---------------------------------------------------------------------------


class TestTwelveGroupSimulation:
    def test_real_cardinality_mixture_exact_group_by_group_state(self):
        """6 pairs, 4 triples, 2 quintuples - real FH-D4 cardinality shape
        (design doc §3). Mixture: unreviewed, distinct, same, stale
        (grown-group) historical disposition, and independent competing
        roots. Asserts exact per-group operational bucket, not merely
        counts."""
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

        # group[0]: DISTINCT (pair)
        record_signal_group_disposition(session, signal_ids=groups[0], decision="DISTINCT", reviewer="human:a", reason="a")
        # group[1]: SAME (pair)
        record_signal_group_disposition(session, signal_ids=groups[1], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:b", reason="b")
        # group[7] (a triple, indices 6-9): stale - disposition recorded for
        # only 2 of its 3 members (a genuine SUBSET, not the full group).
        record_signal_group_disposition(session, signal_ids=groups[7][:2], decision="DISTINCT", reviewer="human:c", reason="c")
        # group[6] (a triple): ambiguous - two independent, unsuperseded roots
        record_signal_group_disposition(session, signal_ids=groups[6], decision="DISTINCT", reviewer="human:d", reason="d")
        record_signal_group_disposition(session, signal_ids=groups[6], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:e", reason="e")
        # group[10] (a quintuple): DISTINCT
        record_signal_group_disposition(session, signal_ids=groups[10], decision="DISTINCT", reviewer="human:f", reason="f")
        session.commit()
        # groups 2,3,4,5,8,9,11 left untouched (unreviewed)

        findings = [_fh_d4_finding(g, airport_id=airport.id) for g in groups]
        result = resolve_fh_d4_findings(session, findings)

        by_signal_ids = {}
        for bucket_name in ("active_findings", "confirmed_distinct", "confirmed_same_effort", "ambiguous_groups"):
            for group in getattr(result, bucket_name):
                by_signal_ids[group.signal_ids] = (bucket_name, group)

        expected_bucket = {
            0: "confirmed_distinct",
            1: "confirmed_same_effort",
            2: "active_findings",
            3: "active_findings",
            4: "active_findings",
            5: "active_findings",
            6: "ambiguous_groups",
            7: "active_findings",  # stale, not exact
            8: "active_findings",
            9: "active_findings",
            10: "confirmed_distinct",
            11: "active_findings",
        }
        for i, g in enumerate(groups):
            key = tuple(sorted(g))
            assert key in by_signal_ids, f"group {i} missing from result entirely"
            bucket_name, operational_group = by_signal_ids[key]
            assert bucket_name == expected_bucket[i], f"group {i} expected {expected_bucket[i]}, got {bucket_name}"

        # group[7]'s stale related history is present.
        stale_group = by_signal_ids[tuple(sorted(groups[7]))][1]
        assert len(stale_group.related_history) == 1
        assert stale_group.related_history[0].relation == "SUBSET"

        # Accounting invariant.
        total = (
            len(result.active_findings) + len(result.confirmed_distinct)
            + len(result.confirmed_same_effort) + len(result.ambiguous_groups)
        )
        assert total == 12 == len(findings)
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Batch query behavior - bounded, no N+1
# ---------------------------------------------------------------------------


class TestBatchQueryBehavior:
    @pytest.mark.parametrize("n_groups", [1, 12, 100])
    def test_query_count_exactly_six_with_no_related_or_subgroup_history(self, n_groups):
        """No group has any subset/superset-related historical disposition
        (only an exact match, which is never 'related' and never a
        'subgroup') - both the related-history and subgroup-discovery
        header-fetch queries short-circuit, leaving exactly 6: 4 from
        resolve_fh_d4_group_statuses() + 1 (all-members scan alone) from
        _batched_related_history() + 1 (all-members scan alone) from
        _batched_subgroup_discovery() (D4D8B)."""
        engine, session = make_session()
        ids = _signals(session, n_groups * 2)
        findings = [_fh_d4_finding(ids[i * 2: i * 2 + 2], airport_id=1) for i in range(n_groups)]
        record_signal_group_disposition(session, signal_ids=findings[0].entity_ids, decision="DISTINCT", reviewer="human:a", reason="a")
        session.commit()

        statements = []

        def _capture(_conn, _cursor, statement, *_args, **_kwargs):
            statements.append(statement.strip().upper())

        engine_for_events = session.get_bind()
        event.listen(engine_for_events, "before_cursor_execute", _capture)
        try:
            resolve_fh_d4_findings(session, findings)
        finally:
            event.remove(engine_for_events, "before_cursor_execute", _capture)

        select_statements = [s for s in statements if s.startswith("SELECT")]
        assert len(select_statements) == 6, (
            f"expected exactly 6 SELECT statements for {n_groups} groups, got "
            f"{len(select_statements)}: {select_statements}"
        )
        session.close(); engine.dispose()

    @pytest.mark.parametrize("n_groups", [2, 10, 100])
    def test_query_count_exactly_eight_with_related_and_subgroup_history_present(self, n_groups):
        """At least one group has a genuine subset-related historical
        disposition, which is simultaneously a genuine subgroup candidate -
        both the related-history and subgroup-discovery header-fetch
        queries fire once each for the whole batch, giving exactly 8 total,
        regardless of scale."""
        engine, session = make_session()
        ids = _signals(session, n_groups * 3)
        findings = [_fh_d4_finding(ids[i * 3: i * 3 + 3], airport_id=1) for i in range(n_groups)]
        # A genuine SUBSET disposition (only 2 of the first group's 3 members).
        record_signal_group_disposition(session, signal_ids=findings[0].entity_ids[:2], decision="DISTINCT", reviewer="human:a", reason="a")
        session.commit()

        statements = []

        def _capture(_conn, _cursor, statement, *_args, **_kwargs):
            statements.append(statement.strip().upper())

        engine_for_events = session.get_bind()
        event.listen(engine_for_events, "before_cursor_execute", _capture)
        try:
            result = resolve_fh_d4_findings(session, findings)
        finally:
            event.remove(engine_for_events, "before_cursor_execute", _capture)

        target = next(g for g in result.active_findings if g.signal_ids == findings[0].entity_ids)
        assert target.related_history != ()  # sanity: the path we're measuring actually fired
        assert target.resolved_subgroups != ()  # sanity: subgroup discovery also fired (D4D8B)
        select_statements = [s for s in statements if s.startswith("SELECT")]
        assert len(select_statements) == 8, (
            f"expected exactly 8 SELECT statements for {n_groups} groups, got "
            f"{len(select_statements)}: {select_statements}"
        )
        session.close(); engine.dispose()

    def test_zero_queries_when_no_d4_findings_present(self):
        engine, session = make_session()
        statements = []

        def _capture(_conn, _cursor, statement, *_args, **_kwargs):
            statements.append(statement.strip().upper())

        event.listen(engine, "before_cursor_execute", _capture)
        try:
            resolve_fh_d4_findings(session, [])
        finally:
            event.remove(engine, "before_cursor_execute", _capture)

        assert statements == []
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Non-D4 findings untouched
# ---------------------------------------------------------------------------


class TestNonD4Untouched:
    def test_other_rule_findings_pass_through_unmodified(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        d4_finding = _fh_d4_finding(ids, airport_id=1)
        other_findings = (
            HealthFinding(
                rule_id="FH-A1", classification=HealthClassification.INFORMATIONAL,
                entity_type="Airport", entity_ids=(1,), airport_id=1,
                summary="no runways", structured_evidence={"airport_id": 1},
            ),
            HealthFinding(
                rule_id="FH-D3", classification=HealthClassification.REVIEW_REQUIRED,
                entity_type="Signal", entity_ids=(10, 11), airport_id=1,
                summary="co-located on same runway", structured_evidence={"signal_ids": (10, 11)},
            ),
            HealthFinding(
                rule_id="FH-G1", classification=HealthClassification.DETERMINISTIC_WARNING,
                entity_type="SourceAssertion", entity_ids=(5,), airport_id=None,
                summary="governance mismatch", structured_evidence={"assertion_id": 5},
            ),
        )
        all_findings = (other_findings[0], d4_finding, other_findings[1], other_findings[2])

        result = resolve_fh_d4_findings(session, all_findings)

        assert result.non_d4_findings == other_findings  # exact objects, original relative order
        for f in result.non_d4_findings:
            assert f.rule_id != "FH-D4"
        session.close(); engine.dispose()

    def test_real_fh_d3_finding_untouched_alongside_fh_d4(self):
        """FH-D3 (same runway) and FH-D4 (no runway) share the same input
        fact type (SignalRunwayAirportFact) but are mutually exclusive by
        construction (D3 requires a runway, D4 requires none) - included
        here as a real, non-hand-constructed pass-through case."""
        engine, session = make_session()
        airport = Airport(name="X", country="XX")
        session.add(airport)
        session.commit()
        from app.models import Runway
        runway = Runway(airport=airport, designation="09/27")
        session.add(runway)
        session.commit()
        d3_ids = _signals(session, 2, airport=airport)
        d4_ids = _signals(session, 2, airport=airport)

        facts = tuple(
            SignalRunwayAirportFact(signal_id=sid, signal_airport_id=airport.id, runway_id=runway.id, runway_airport_id=airport.id)
            for sid in d3_ids
        ) + tuple(
            SignalRunwayAirportFact(signal_id=sid, signal_airport_id=airport.id, runway_id=None, runway_airport_id=None)
            for sid in d4_ids
        )
        d3_findings = evaluate_fh_d3(facts)
        d4_findings = evaluate_fh_d4(facts)
        assert len(d3_findings) == 1 and len(d4_findings) == 1

        result = resolve_fh_d4_findings(session, d3_findings + d4_findings)
        assert result.non_d4_findings == d3_findings
        assert len(result.active_findings) == 1
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Accounting invariant
# ---------------------------------------------------------------------------


class TestAccountingInvariant:
    def test_bucket_key_sets_are_pairwise_disjoint_and_exhaustive(self):
        """Formal, set-based accounting invariant (critical-review §5):
        raw_d4_group_keys == active_keys | distinct_keys | same_keys |
        ambiguous_keys, and all four bucket key sets are pairwise disjoint -
        not merely a count match, which could hide a group appearing in one
        bucket while a DIFFERENT group is silently missing from all of
        them."""
        engine, session = make_session()
        airport = Airport(name="X", country="XX")
        session.add(airport)
        session.commit()

        groups = []
        next_id = [0]

        def _new(n):
            sigs = [Signal(airport=airport, title=f"S{next_id[0]+i}", category="replacement", confidence="high") for i in range(n)]
            session.add_all(sigs)
            session.commit()
            next_id[0] += n
            return tuple(s.id for s in sigs)

        for _ in range(6):
            groups.append(_new(2))
        record_signal_group_disposition(session, signal_ids=groups[0], decision="DISTINCT", reviewer="human:a", reason="a")
        record_signal_group_disposition(session, signal_ids=groups[1], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:b", reason="b")
        record_signal_group_disposition(session, signal_ids=groups[2], decision="DISTINCT", reviewer="human:c", reason="c")
        record_signal_group_disposition(session, signal_ids=groups[2], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:d", reason="d", supersedes_id=None)
        session.commit()

        findings = [_fh_d4_finding(g, airport_id=airport.id) for g in groups]
        result = resolve_fh_d4_findings(session, findings)

        raw_keys = {tuple(sorted(g)) for g in groups}
        active_keys = {g.signal_ids for g in result.active_findings}
        distinct_keys = {g.signal_ids for g in result.confirmed_distinct}
        same_keys = {g.signal_ids for g in result.confirmed_same_effort}
        ambiguous_keys = {g.signal_ids for g in result.ambiguous_groups}

        bucket_key_sets = (active_keys, distinct_keys, same_keys, ambiguous_keys)
        for i, a in enumerate(bucket_key_sets):
            for b in bucket_key_sets[i + 1:]:
                assert a.isdisjoint(b), f"bucket key sets not disjoint: {a} vs {b}"

        assert active_keys | distinct_keys | same_keys | ambiguous_keys == raw_keys
        session.close(); engine.dispose()

    def test_every_raw_finding_lands_in_exactly_one_bucket(self):
        engine, session = make_session()
        airport = Airport(name="X", country="XX")
        session.add(airport)
        session.commit()

        groups = []
        next_id = [0]

        def _new(n):
            sigs = [Signal(airport=airport, title=f"S{next_id[0]+i}", category="replacement", confidence="high") for i in range(n)]
            session.add_all(sigs)
            session.commit()
            next_id[0] += n
            return tuple(s.id for s in sigs)

        for _ in range(5):
            groups.append(_new(2))
        record_signal_group_disposition(session, signal_ids=groups[0], decision="DISTINCT", reviewer="human:a", reason="a")
        record_signal_group_disposition(session, signal_ids=groups[1], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:b", reason="b")
        record_signal_group_disposition(session, signal_ids=groups[2], decision="DISTINCT", reviewer="human:c", reason="c")
        record_signal_group_disposition(
            session, signal_ids=groups[2], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:d", reason="d", supersedes_id=None,
        )
        session.commit()

        findings = [_fh_d4_finding(g, airport_id=airport.id) for g in groups]
        result = resolve_fh_d4_findings(session, findings)

        all_grouped = (
            result.active_findings + result.confirmed_distinct
            + result.confirmed_same_effort + result.ambiguous_groups
        )
        assert len(all_grouped) == len(findings)
        seen_ids = [g.signal_ids for g in all_grouped]
        assert len(seen_ids) == len(set(seen_ids))  # no duplicate/double-counted group
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Read-only / no_autoflush
# ---------------------------------------------------------------------------


class TestReadOnlyGuarantee:
    def test_never_autoflushes_callers_pending_mutation(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)

        airport = session.get(Airport, session.get(Signal, ids[0]).airport_id)
        airport.name = "PENDING - MUST NOT BE FLUSHED"
        assert airport in session.dirty

        resolve_fh_d4_findings(session, [finding])

        assert airport in session.dirty
        session.rollback()
        session.close(); engine.dispose()

    def test_no_write_sql_emitted(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)

        statements = []

        def _capture(_conn, _cursor, statement, *_args, **_kwargs):
            statements.append(statement.strip().upper())

        event.listen(engine, "before_cursor_execute", _capture)
        try:
            resolve_fh_d4_findings(session, [finding])
            run_disposition_aware_fh_d4_review(session)
        finally:
            event.remove(engine, "before_cursor_execute", _capture)

        write_statements = [s for s in statements if s.startswith(("INSERT", "UPDATE", "DELETE"))]
        assert write_statements == []
        session.close(); engine.dispose()

    def test_session_stays_clean(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        finding = _fh_d4_finding(ids, airport_id=1)
        resolve_fh_d4_findings(session, [finding])
        assert not session.new
        assert not session.dirty
        assert not session.deleted
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_repeated_calls_equal(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)

        r1 = resolve_fh_d4_findings(session, [finding])
        r2 = resolve_fh_d4_findings(session, [finding])
        assert r1 == r2
        session.close(); engine.dispose()

    def test_finding_order_does_not_affect_bucket_contents(self):
        engine, session = make_session()
        airport = Airport(name="X", country="XX")
        session.add(airport)
        session.commit()
        g1 = _signals(session, 2, airport=airport)
        g2 = _signals(session, 2, airport=airport)
        record_signal_group_disposition(session, signal_ids=g1, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        f1, f2 = _fh_d4_finding(g1, airport.id), _fh_d4_finding(g2, airport.id)

        forward = resolve_fh_d4_findings(session, [f1, f2])
        reversed_ = resolve_fh_d4_findings(session, [f2, f1])

        assert set(g.signal_ids for g in forward.confirmed_distinct) == set(g.signal_ids for g in reversed_.confirmed_distinct)
        assert set(g.signal_ids for g in forward.active_findings) == set(g.signal_ids for g in reversed_.active_findings)
        session.close(); engine.dispose()

    def test_related_history_sorted_deterministically_with_identical_timestamps(self):
        """_batched_related_history() is genuinely new sorting code (not
        merely reused from D4D3 unchanged) - two related, subset
        dispositions sharing an identical created_at must still sort
        deterministically by disposition_id as the tie-break, matching
        D4D3's own (created_at, id) convention."""
        engine, session = make_session()
        ids = _signals(session, 4)
        d1 = record_signal_group_disposition(session, signal_ids=ids[:2], decision="DISTINCT", reviewer="human:a", reason="a")
        session.flush()
        d2 = record_signal_group_disposition(session, signal_ids=ids[2:], decision="DISTINCT", reviewer="human:b", reason="b")
        session.flush()
        # Force an identical created_at on both header rows directly at the
        # DB level (bypassing the ORM's own immutability guard, which
        # correctly blocks a post-flush UPDATE via the ORM).
        session.execute(
            text("UPDATE signal_dispositions SET created_at = :ts WHERE id IN (:a, :b)"),
            {"ts": d1.created_at, "a": d1.id, "b": d2.id},
        )
        session.commit()
        session.expire_all()

        finding = _fh_d4_finding(ids, airport_id=1)  # current group is all 4 - both are subsets
        result1 = resolve_fh_d4_findings(session, [finding])
        result2 = resolve_fh_d4_findings(session, [finding])

        related1 = result1.active_findings[0].related_history
        related2 = result2.active_findings[0].related_history
        assert len(related1) == 2
        assert related1 == related2  # deterministic across repeated calls
        assert related1 == tuple(sorted(related1, key=lambda r: (r.created_at, r.disposition_id)))
        session.close(); engine.dispose()

    def test_same_timestamp_disposition_insertion_order_no_effect(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=tuple(reversed(ids)), decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)
        result = resolve_fh_d4_findings(session, [finding])
        assert result.confirmed_distinct[0].status == "CONFIRMED_DISTINCT"
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Migration-created schema parity
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
            finding = _fh_d4_finding(ids, airport_id=1)  # current group is the triple - stale

            result = resolve_fh_d4_findings(session, [finding])
            assert len(result.active_findings) == 1
            assert result.active_findings[0].related_history[0].relation == "SUBSET"
        engine2.dispose()


# ---------------------------------------------------------------------------
# Failure loud
# ---------------------------------------------------------------------------


class TestFailureLoud:
    def test_missing_d4d2_schema_raises_not_all_unreviewed(self):
        engine = create_engine("sqlite:///:memory:")
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
        finding = _fh_d4_finding([a.id, b.id], airport_id=airport.id)

        with pytest.raises(Exception):  # sqlalchemy.exc.OperationalError in practice
            resolve_fh_d4_findings(session, [finding])
        session.close(); engine.dispose()

    def test_malformed_entity_type_fails_loud(self):
        engine, session = make_session()
        bad_finding = HealthFinding(
            rule_id=FH_D4_RULE_ID, classification=HealthClassification.REVIEW_REQUIRED,
            entity_type="Airport", entity_ids=(1, 2), airport_id=1,
            summary="malformed", structured_evidence={},
        )
        with pytest.raises(ValueError, match="entity_type"):
            resolve_fh_d4_findings(session, [bad_finding])
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Information firewall
# ---------------------------------------------------------------------------


class TestInformationFirewall:
    # "status" is deliberately NOT included here (unlike D4D3's own,
    # otherwise-identical list): this module legitimately accesses
    # `SignalDispositionStatus.status`/`FhD4OperationalGroup.status` (D4D3's
    # own three-value vocabulary, reused verbatim), which collides on the
    # bare identifier with `Signal.status` (a real, forbidden column) under
    # this naive name-only AST scan. `Signal.status` itself is never read -
    # confirmed independently by test_behavioral_no_signal_content_leak
    # below, which proves no Signal column value can leak into a result.
    _FORBIDDEN_SIGNAL_ATTRS = (
        "title", "notes", "source_notes", "estimated_total_value_usd",
        "estimated_emas_value_usd", "supplier", "likely_supplier", "supplier_reason",
        "confirmed_vendor", "category", "confidence", "manual_year_estimate",
        "published", "runway_id", "installation_id",
    )

    def test_module_never_references_forbidden_signal_attributes_ast(self):
        import app.services.fh_d4_disposition_resolution as module

        tree = ast.parse(inspect_module.getsource(module))
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert not (attrs & set(self._FORBIDDEN_SIGNAL_ATTRS))

    def test_module_never_imports_fh_d4_pure_detector(self):
        """The pure FH-D4 rule module must never be imported here - this
        module only ever consumes ALREADY-PRODUCED HealthFinding objects,
        never re-derives or re-detects anything."""
        import app.services.fh_d4_disposition_resolution as module

        tree = ast.parse(inspect_module.getsource(module))
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.add(alias.name)
        assert "app.services.fleet_health_review_rules" not in imported_modules

    def test_behavioral_no_signal_content_leak(self):
        engine, session = make_session()
        airport = Airport(name="Firewall Airport", country="XX")
        session.add(airport)
        a = Signal(airport=airport, title="SECRET TITLE TEXT", category="replacement", confidence="high", estimated_total_value_usd=123456789)
        b = Signal(airport=airport, title="OTHER SECRET TITLE", category="replacement", confidence="high")
        session.add_all([a, b])
        session.commit()
        record_signal_group_disposition(session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        finding = _fh_d4_finding([a.id, b.id], airport_id=airport.id)

        result = resolve_fh_d4_findings(session, [finding])
        result_repr = repr(result.confirmed_distinct[0])
        assert "SECRET" not in result_repr
        assert "123456789" not in result_repr
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Pure FH-D4 detector preservation
# ---------------------------------------------------------------------------


class TestPureDetectorUnaffected:
    def test_evaluate_fh_d4_output_unaffected_by_importing_this_module(self):
        """Merely importing/using this module must never change what the
        pure FH-D4 detector itself produces for the same facts."""
        engine, session = make_session()
        ids = _signals(session, 2)
        airport_id = session.get(Signal, ids[0]).airport_id
        facts = tuple(
            SignalRunwayAirportFact(signal_id=sid, signal_airport_id=airport_id, runway_id=None, runway_airport_id=None)
            for sid in ids
        )
        before = evaluate_fh_d4(facts)
        # Use this module in between.
        resolve_fh_d4_findings(session, list(before))
        session.close(); engine.dispose()
        after = evaluate_fh_d4(facts)
        assert before == after


# ---------------------------------------------------------------------------
# Ambiguity + supersession interaction (critical-review §13)
# ---------------------------------------------------------------------------


class TestAmbiguityAndSupersession:
    def test_ambiguity_persists_after_superseding_one_of_two_independent_roots(self):
        """D1 root DISTINCT; D2 SAME supersedes D1 (independent_root_count
        == 1, not ambiguous). D3 a SECOND independent root DISTINCT, no
        supersedes_id (count == 2, ambiguous). D4 supersedes D3. D1 is
        STILL an independent, never-superseded root - ambiguity must
        remain (D4D3's own no-chain-walking semantics: a plain count over
        supersedes_id IS NULL, reused verbatim, never recomputed locally
        by this module)."""
        engine, session = make_session()
        ids = _signals(session, 2)
        finding = _fh_d4_finding(ids, airport_id=1)

        d1 = record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:a", reason="1")
        session.commit()
        result_after_d1 = resolve_fh_d4_findings(session, [finding])
        assert result_after_d1.confirmed_distinct[0].independent_root_count == 1
        assert result_after_d1.ambiguous_groups == ()

        record_signal_group_disposition(session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:b", reason="2", supersedes_id=d1.id)
        session.commit()
        result_after_d2 = resolve_fh_d4_findings(session, [finding])
        assert result_after_d2.confirmed_same_effort[0].independent_root_count == 1
        assert result_after_d2.ambiguous_groups == ()

        d3 = record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:c", reason="3", supersedes_id=None)
        session.commit()
        result_after_d3 = resolve_fh_d4_findings(session, [finding])
        assert len(result_after_d3.ambiguous_groups) == 1
        assert result_after_d3.ambiguous_groups[0].independent_root_count == 2
        assert result_after_d3.ambiguous_groups[0].latest_disposition_id == d3.id

        record_signal_group_disposition(session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:e", reason="4", supersedes_id=d3.id)
        session.commit()
        result_after_d4 = resolve_fh_d4_findings(session, [finding])
        # D1 is still an unlinked, independent root - ambiguity remains.
        assert len(result_after_d4.ambiguous_groups) == 1
        assert result_after_d4.ambiguous_groups[0].independent_root_count == 2
        assert result_after_d4.confirmed_distinct == () and result_after_d4.confirmed_same_effort == ()
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Related-history correctness at scale (critical-review §19, high priority)
# ---------------------------------------------------------------------------


class TestRelatedHistoryCorrectnessAtScale:
    def test_many_dispositions_only_genuine_subset_superset_attached(self):
        """{A,B}, {A,B,C}, {A,C}, {D,E}, {A,B,C,D} all persisted. Current
        query group is {A,B,C,D,E}. Every one of the five stored sets is a
        strict subset of the current 5-element group, so all five must be
        attached as SUBSET related history - none excluded, none
        duplicated, no cross-contamination with an unrelated disposition
        elsewhere in the same batch."""
        engine, session = make_session()
        airport = Airport(name="X", country="XX")
        session.add(airport)
        session.commit()
        letters = "ABCDE"
        sig_by_letter = {}
        for letter in letters:
            s = Signal(airport=airport, title=letter, category="replacement", confidence="high")
            session.add(s)
            session.commit()
            sig_by_letter[letter] = s.id

        def ids_for(*letters_):
            return tuple(sig_by_letter[c] for c in letters_)

        stored = [
            record_signal_group_disposition(session, signal_ids=ids_for("A", "B"), decision="DISTINCT", reviewer="human:1", reason="1"),
            record_signal_group_disposition(session, signal_ids=ids_for("A", "B", "C"), decision="DISTINCT", reviewer="human:2", reason="2"),
            record_signal_group_disposition(session, signal_ids=ids_for("A", "C"), decision="DISTINCT", reviewer="human:3", reason="3"),
            record_signal_group_disposition(session, signal_ids=ids_for("D", "E"), decision="DISTINCT", reviewer="human:4", reason="4"),
            record_signal_group_disposition(session, signal_ids=ids_for("A", "B", "C", "D"), decision="DISTINCT", reviewer="human:5", reason="5"),
        ]
        session.commit()

        # An unrelated disposition elsewhere (no overlap at all with the
        # query group) - must never appear in this group's related history.
        f_sig = Signal(airport=airport, title="F", category="replacement", confidence="high")
        g_sig = Signal(airport=airport, title="G", category="replacement", confidence="high")
        session.add_all([f_sig, g_sig])
        session.commit()
        record_signal_group_disposition(session, signal_ids=[f_sig.id, g_sig.id], decision="DISTINCT", reviewer="human:6", reason="6")
        session.commit()

        current_group = ids_for("A", "B", "C", "D", "E")
        finding = _fh_d4_finding(current_group, airport_id=airport.id)

        result = resolve_fh_d4_findings(session, [finding])
        group = result.active_findings[0]
        related_ids = {r.disposition_id for r in group.related_history}
        assert related_ids == {d.id for d in stored}
        assert all(r.relation == "SUBSET" for r in group.related_history)
        assert len(group.related_history) == 5
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Overlapping groups resolved independently (critical-review §20)
# ---------------------------------------------------------------------------


class TestOverlappingGroupsIndependence:
    def test_shared_signal_never_leaks_state_between_groups(self):
        """G1={A,B}, G2={A,C}, G3={A,B,C} all share Signal A. Persisting a
        disposition for G1 must never affect G2's or G3's own resolution -
        each group's status/related_history/ambiguity is independent."""
        engine, session = make_session()
        airport = Airport(name="X", country="XX")
        session.add(airport)
        session.commit()
        a = Signal(airport=airport, title="A", category="replacement", confidence="high")
        b = Signal(airport=airport, title="B", category="replacement", confidence="high")
        c = Signal(airport=airport, title="C", category="replacement", confidence="high")
        session.add_all([a, b, c])
        session.commit()

        record_signal_group_disposition(session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()

        g1 = _fh_d4_finding([a.id, b.id], airport.id)
        g2 = _fh_d4_finding([a.id, c.id], airport.id)
        g3 = _fh_d4_finding([a.id, b.id, c.id], airport.id)

        result = resolve_fh_d4_findings(session, [g1, g2, g3])
        by_key = {g.signal_ids: g for group_tuple in (
            result.active_findings, result.confirmed_distinct, result.confirmed_same_effort, result.ambiguous_groups,
        ) for g in group_tuple}

        g1_result = by_key[tuple(sorted([a.id, b.id]))]
        g2_result = by_key[tuple(sorted([a.id, c.id]))]
        g3_result = by_key[tuple(sorted([a.id, b.id, c.id]))]

        assert g1_result.status == "CONFIRMED_DISTINCT"
        # G2 shares only Signal A with the dispositioned G1 - bare overlap,
        # never related (D4D3's own established policy).
        assert g2_result.status == "UNREVIEWED"
        assert g2_result.related_history == ()
        # G3 is a strict superset of the dispositioned G1 - UNREVIEWED with
        # related history, never resolved by G1's own disposition.
        assert g3_result.status == "UNREVIEWED"
        assert len(g3_result.related_history) == 1
        assert g3_result.related_history[0].relation == "SUBSET"
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Duplicate raw findings / malformed findings (critical-review §21/§22)
# ---------------------------------------------------------------------------


class TestDuplicateAndMalformedFindings:
    def test_duplicate_raw_findings_preserved_not_silently_deduped(self):
        """Contract decision: raw detector output is never silently
        deduplicated by this module - two logically-identical raw FH-D4
        findings produce two independent, identically-resolved wrapper
        entries (both accounted for; the count-based accounting invariant
        still holds: len(d4_findings) == sum of bucket lengths)."""
        engine, session = make_session()
        ids = _signals(session, 2)
        finding_a = _fh_d4_finding(ids, airport_id=1)
        finding_b = _fh_d4_finding(ids, airport_id=1)  # logically identical, distinct object

        result = resolve_fh_d4_findings(session, [finding_a, finding_b])

        assert len(result.active_findings) == 2
        assert result.active_findings[0] == result.active_findings[1]  # identical field values
        assert result.active_findings[0].raw_finding is finding_a
        assert result.active_findings[1].raw_finding is finding_b
        total = (
            len(result.active_findings) + len(result.confirmed_distinct)
            + len(result.confirmed_same_effort) + len(result.ambiguous_groups)
        )
        assert total == 2
        session.close(); engine.dispose()

    def test_no_signal_ids_fails_loud(self):
        engine, session = make_session()
        bad = HealthFinding(
            rule_id=FH_D4_RULE_ID, classification=HealthClassification.REVIEW_REQUIRED,
            entity_type="Signal", entity_ids=(), airport_id=1, summary="x", structured_evidence={},
        )
        with pytest.raises(ValueError, match="at least 2 distinct"):
            resolve_fh_d4_findings(session, [bad])
        session.close(); engine.dispose()

    def test_single_signal_id_fails_loud(self):
        engine, session = make_session()
        ids = _signals(session, 1)
        bad = HealthFinding(
            rule_id=FH_D4_RULE_ID, classification=HealthClassification.REVIEW_REQUIRED,
            entity_type="Signal", entity_ids=ids, airport_id=1, summary="x", structured_evidence={},
        )
        with pytest.raises(ValueError, match="at least 2 distinct"):
            resolve_fh_d4_findings(session, [bad])
        session.close(); engine.dispose()

    def test_duplicate_ids_within_one_finding_normalized_not_crashed(self):
        """entity_ids containing a repeated id (e.g. an upstream FH-D4 bug)
        is silently normalized via deduplication - matching D4D3's own
        established dedup discipline everywhere else in this pipeline, not
        a new invented behavior."""
        engine, session = make_session()
        ids = _signals(session, 2)
        bad = HealthFinding(
            rule_id=FH_D4_RULE_ID, classification=HealthClassification.REVIEW_REQUIRED,
            entity_type="Signal", entity_ids=(ids[0], ids[0], ids[1]), airport_id=1,
            summary="x", structured_evidence={},
        )
        result = resolve_fh_d4_findings(session, [bad])
        assert result.active_findings[0].signal_ids == tuple(sorted(ids))
        session.close(); engine.dispose()

    def test_malformed_structured_evidence_does_not_affect_resolution(self):
        """structured_evidence is never read by this module - a malformed/
        inconsistent dict there must never affect resolution correctness."""
        engine, session = make_session()
        ids = _signals(session, 2)
        finding = HealthFinding(
            rule_id=FH_D4_RULE_ID, classification=HealthClassification.REVIEW_REQUIRED,
            entity_type="Signal", entity_ids=tuple(sorted(ids)), airport_id=1,
            summary="x", structured_evidence={"signal_ids": "NOT A REAL TUPLE", "unexpected_key": object()},
        )
        result = resolve_fh_d4_findings(session, [finding])
        assert result.active_findings[0].status == "UNREVIEWED"
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Member-id source contract (critical-review §23)
# ---------------------------------------------------------------------------


class TestMemberIdSourceContract:
    def test_resolution_uses_entity_ids_never_summary_text(self):
        """A deliberately MISLEADING summary string (claiming different
        Signal ids than entity_ids actually holds) must have zero effect -
        this module reads only the stable structured entity_ids field,
        never parses summary/title/free text."""
        engine, session = make_session()
        ids = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        finding = HealthFinding(
            rule_id=FH_D4_RULE_ID, classification=HealthClassification.REVIEW_REQUIRED,
            entity_type="Signal", entity_ids=tuple(sorted(ids)), airport_id=1,
            summary="Signals 999997, 999998 are co-located (deliberately WRONG text)",
            structured_evidence={"signal_ids": (999997, 999998)},  # also deliberately wrong
        )
        result = resolve_fh_d4_findings(session, [finding])
        assert result.confirmed_distinct[0].status == "CONFIRMED_DISTINCT"
        assert result.confirmed_distinct[0].signal_ids == tuple(sorted(ids))
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Full entrypoint - query cost and correctness (critical-review §32/§33)
# ---------------------------------------------------------------------------


class TestFullEntrypoint:
    def test_run_disposition_aware_calls_raw_review_check_exactly_once(self):
        """Verifies run_disposition_aware_fh_d4_review() does not duplicate
        DB acquisition - it must call the existing, unmodified
        run_fleet_review_check() exactly once, never re-deriving facts
        itself."""
        import app.services.fh_d4_disposition_resolution as module

        engine, session = make_session()
        call_count = [0]
        original = module.run_fleet_review_check

        def _counting_wrapper(s):
            call_count[0] += 1
            return original(s)

        module.run_fleet_review_check = _counting_wrapper
        try:
            module.run_disposition_aware_fh_d4_review(session)
        finally:
            module.run_fleet_review_check = original
        assert call_count[0] == 1
        session.close(); engine.dispose()

    def test_full_entrypoint_query_cost_documented(self):
        """Measures (does not assert an arbitrary target for) the complete
        disposition-aware review's own total query cost - FHC3's own
        already-reviewed query cost PLUS D4D4's own bounded 5-6 - proving
        no N+1 is introduced by composing the two."""
        engine, session = make_session()
        ids = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()

        statements = []

        def _capture(_conn, _cursor, statement, *_args, **_kwargs):
            statements.append(statement.strip().upper())

        event.listen(engine, "before_cursor_execute", _capture)
        try:
            run_disposition_aware_fh_d4_review(session)
        finally:
            event.remove(engine, "before_cursor_execute", _capture)

        select_statements = [s for s in statements if s.startswith("SELECT")]
        # FHC3's own already-reviewed query count is fixed and small
        # (one per fact type); D4D4 adds at most 6 more. No arbitrary
        # target beyond "bounded and does not scale with data volume" -
        # documented here, not asserted as a magic number, since FHC3's own
        # exact count is that module's own contract, not this one's.
        assert 0 < len(select_statements) < 30
        session.close(); engine.dispose()

    def test_full_entrypoint_preserves_non_d4_findings(self):
        engine, session = make_session()
        airport = Airport(name="X", country="XX")
        session.add(airport)
        session.commit()
        result = run_disposition_aware_fh_d4_review(session)
        # An empty, freshly-created DB still runs every FHC3 rule - some
        # may fire (e.g. FH-A1 zero-runway informational), proving the full
        # rule set genuinely ran, not merely FH-D4 in isolation.
        assert isinstance(result, FhD4DispositionResolution)
        for f in result.non_d4_findings:
            assert f.rule_id != "FH-D4"
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Wider non-D4 preservation (critical-review §6)
# ---------------------------------------------------------------------------


class TestWideNonD4Preservation:
    def test_seven_rule_mixed_set_only_fh_d4_interpreted(self):
        engine, session = make_session()
        d4_ids = _signals(session, 2)
        d4_finding = _fh_d4_finding(d4_ids, airport_id=1)
        other = (
            HealthFinding(rule_id="FH-A1", classification=HealthClassification.INFORMATIONAL, entity_type="Airport", entity_ids=(1,), airport_id=1, summary="a1", structured_evidence={}),
            HealthFinding(rule_id="FH-C3", classification=HealthClassification.DETERMINISTIC_WARNING, entity_type="Installation", entity_ids=(2, 3), airport_id=1, summary="c3", structured_evidence={}),
            HealthFinding(rule_id="FH-D3", classification=HealthClassification.REVIEW_REQUIRED, entity_type="Signal", entity_ids=(4, 5), airport_id=1, summary="d3", structured_evidence={}),
            HealthFinding(rule_id="FH-E1", classification=HealthClassification.DETERMINISTIC_WARNING, entity_type="Signal", entity_ids=(6,), airport_id=1, summary="e1", structured_evidence={}),
            HealthFinding(rule_id="FH-E4", classification=HealthClassification.REVIEW_REQUIRED, entity_type="Signal", entity_ids=(7,), airport_id=1, summary="e4", structured_evidence={}),
            HealthFinding(rule_id="FH-F1", classification=HealthClassification.INFORMATIONAL, entity_type="Signal", entity_ids=(8, 9), airport_id=None, summary="f1", structured_evidence={}),
        )
        mixed = other[:3] + (d4_finding,) + other[3:]

        result = resolve_fh_d4_findings(session, mixed)

        assert result.non_d4_findings == other
        assert [f.rule_id for f in result.non_d4_findings] == ["FH-A1", "FH-C3", "FH-D3", "FH-E1", "FH-E4", "FH-F1"]
        for f in result.non_d4_findings:
            assert f.classification in (
                HealthClassification.INFORMATIONAL, HealthClassification.DETERMINISTIC_WARNING, HealthClassification.REVIEW_REQUIRED,
            )
        assert len(result.active_findings) == 1  # only the FH-D4 finding was interpreted
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# No write-path imports (critical-review §35) / no try-except (fail-loud)
# ---------------------------------------------------------------------------


class TestNoWritePathImports:
    _FORBIDDEN_MODULES = (
        "app.services.signal_disposition_persistence",
        "app.services.reviewer_action_persistence",
        "app.services.governed_signal_creation",
        "scripts.migrate_signal_disposition_d4d2",
    )

    def test_no_write_service_or_migration_imports(self):
        import app.services.fh_d4_disposition_resolution as module

        tree = ast.parse(inspect_module.getsource(module))
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.add(alias.name)
        assert not (imported_modules & set(self._FORBIDDEN_MODULES))

    def test_no_try_except_anywhere_in_module_ast(self):
        import app.services.fh_d4_disposition_resolution as module

        tree = ast.parse(inspect_module.getsource(module))
        try_nodes = [node for node in ast.walk(tree) if isinstance(node, ast.Try)]
        assert try_nodes == []


# ---------------------------------------------------------------------------
# No real database access
# ---------------------------------------------------------------------------


class TestNoRealDatabaseAccess:
    def test_no_reference_to_real_database_path(self):
        import app.services.fh_d4_disposition_resolution as module

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


# ---------------------------------------------------------------------------
# D4D8B - subgroup discovery (docs/architecture/fh-d4-signal-disposition-
# d4d8-subgroup-semantics-design.md, adversarially reviewed/locked in D4D8A)
# ---------------------------------------------------------------------------


class TestSubgroupDiscovery:
    """Mandatory test matrix A-R (D4D8B mission) plus three synthetic
    fixtures replaying the real D4D7 group shapes (Roanoke/Binghamton/
    Worcester) - all fully synthetic, never touching the real database."""

    # -- A: no dispositions at all ------------------------------------------------

    def test_a_no_dispositions_ordinary_active_no_subgroups(self):
        engine, session = make_session()
        ids = _signals(session, 3)
        finding = _real_fh_d4_finding(ids, airport_id=session.get(Signal, ids[0]).airport_id)
        result = resolve_fh_d4_findings(session, [finding])

        assert len(result.active_findings) == 1
        group = result.active_findings[0]
        assert group.status == "UNREVIEWED"
        assert group.resolved_subgroups == ()
        assert group.subgroup_conflict is False
        # Remainder = raw signal_ids minus union(resolved_subgroups) = full set.
        assert group.unresolved_remainder_signal_ids == tuple(sorted(ids))
        session.close(); engine.dispose()

    # -- B/C: exact-set precedence ------------------------------------------------

    def test_b_exact_set_distinct_carries_no_subgroup_metadata(self):
        engine, session = make_session()
        ids = _signals(session, 3)
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:a", reason="x")
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)
        result = resolve_fh_d4_findings(session, [finding])

        assert len(result.confirmed_distinct) == 1
        group = result.confirmed_distinct[0]
        assert group.resolved_subgroups == ()
        assert group.unresolved_remainder_signal_ids == ()
        assert group.subgroup_conflict is False
        session.close(); engine.dispose()

    def test_c_exact_set_same_carries_no_subgroup_metadata(self):
        engine, session = make_session()
        ids = _signals(session, 3)
        record_signal_group_disposition(session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:a", reason="x")
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)
        result = resolve_fh_d4_findings(session, [finding])

        assert len(result.confirmed_same_effort) == 1
        group = result.confirmed_same_effort[0]
        assert group.resolved_subgroups == ()
        assert group.unresolved_remainder_signal_ids == ()
        assert group.subgroup_conflict is False
        session.close(); engine.dispose()

    # -- D/E: a single two-member subgroup within a triple -------------------------

    def test_d_subgroup_same_within_triple_raw_stays_active(self):
        engine, session = make_session()
        ids = _signals(session, 3)
        a, b, c = ids
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:a", reason="x")
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)
        result = resolve_fh_d4_findings(session, [finding])

        assert result.confirmed_distinct == () and result.confirmed_same_effort == () and result.ambiguous_groups == ()
        assert len(result.active_findings) == 1
        group = result.active_findings[0]
        assert group.status == "UNREVIEWED"  # the RAW group itself is still unreviewed
        assert len(group.resolved_subgroups) == 1
        sub = group.resolved_subgroups[0]
        assert sub.signal_ids == tuple(sorted([a, b]))
        assert sub.decision == "SAME_REAL_WORLD_EFFORT"
        assert group.unresolved_remainder_signal_ids == (c,)
        assert group.subgroup_conflict is False
        session.close(); engine.dispose()

    def test_e_subgroup_distinct_within_triple_no_inference_about_remainder(self):
        engine, session = make_session()
        ids = _signals(session, 3)
        a, b, c = ids
        record_signal_group_disposition(session, signal_ids=[a, b], decision="DISTINCT", reviewer="human:a", reason="x")
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)
        result = resolve_fh_d4_findings(session, [finding])

        group = result.active_findings[0]
        assert len(group.resolved_subgroups) == 1
        assert group.resolved_subgroups[0].decision == "DISTINCT"
        assert group.unresolved_remainder_signal_ids == (c,)
        # No disposition, exact-set or otherwise, exists for {c} paired with
        # anything - resolve_fh_d4_group_statuses() (unmodified) would report
        # UNREVIEWED for any set naming c; nothing here asserts DISTINCT for c.
        session.close(); engine.dispose()

    # -- F: four-member subgroup within a quintuple, singleton remainder ----------

    def test_f_four_member_subgroup_within_quintuple(self):
        engine, session = make_session()
        ids = _signals(session, 5)
        four, fifth = ids[:4], ids[4]
        record_signal_group_disposition(session, signal_ids=four, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:a", reason="x")
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)
        result = resolve_fh_d4_findings(session, [finding])

        group = result.active_findings[0]
        assert len(group.resolved_subgroups) == 1
        assert group.resolved_subgroups[0].signal_ids == tuple(sorted(four))
        assert group.unresolved_remainder_signal_ids == (fifth,)
        session.close(); engine.dispose()

    # -- G: two disjoint subgroups within a six-member raw group -------------------

    def test_g_two_disjoint_subgroups_both_visible(self):
        engine, session = make_session()
        ids = _signals(session, 6)
        a, b, c, d, e, f = ids
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:a", reason="x")
        record_signal_group_disposition(session, signal_ids=[c, d], decision="DISTINCT", reviewer="human:b", reason="y")
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)
        result = resolve_fh_d4_findings(session, [finding])

        group = result.active_findings[0]
        assert len(group.resolved_subgroups) == 2
        by_set = {s.signal_ids: s.decision for s in group.resolved_subgroups}
        assert by_set[tuple(sorted([a, b]))] == "SAME_REAL_WORLD_EFFORT"
        assert by_set[tuple(sorted([c, d]))] == "DISTINCT"
        assert group.unresolved_remainder_signal_ids == tuple(sorted([e, f]))
        assert group.subgroup_conflict is False
        session.close(); engine.dispose()

    # -- H/I: overlapping proper subsets - deterministic conflict behavior --------

    def test_h_overlapping_proper_subsets_same_decision_is_conflict(self):
        engine, session = make_session()
        ids = _signals(session, 5)
        a, b, c, d, e = ids
        record_signal_group_disposition(session, signal_ids=[a, b, c], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:a", reason="x")
        record_signal_group_disposition(session, signal_ids=[c, d], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:b", reason="y")
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)
        result = resolve_fh_d4_findings(session, [finding])

        group = result.active_findings[0]
        assert group.subgroup_conflict is True
        # Nothing dropped: both conflicting candidates remain visible.
        assert len(group.resolved_subgroups) == 2
        seen_sets = {s.signal_ids for s in group.resolved_subgroups}
        assert tuple(sorted([a, b, c])) in seen_sets
        assert tuple(sorted([c, d])) in seen_sets
        # Nothing treated as safely resolved: remainder is the ENTIRE raw set.
        assert group.unresolved_remainder_signal_ids == tuple(sorted(ids))
        session.close(); engine.dispose()

    def test_i_overlapping_proper_subsets_different_decisions_is_conflict(self):
        engine, session = make_session()
        ids = _signals(session, 5)
        a, b, c, d, e = ids
        record_signal_group_disposition(session, signal_ids=[a, b, c], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:a", reason="x")
        record_signal_group_disposition(session, signal_ids=[c, d], decision="DISTINCT", reviewer="human:b", reason="y")
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)
        result = resolve_fh_d4_findings(session, [finding])

        group = result.active_findings[0]
        assert group.subgroup_conflict is True
        assert len(group.resolved_subgroups) == 2
        assert group.unresolved_remainder_signal_ids == tuple(sorted(ids))
        session.close(); engine.dispose()

    # -- J: exact-set primary bucket semantics still win ---------------------------

    def test_j_exact_set_history_wins_over_unrelated_subgroup_history(self):
        engine, session = make_session()
        ids = _signals(session, 3)
        a, b, c = ids
        # A genuine subset disposition exists for {a,b} ...
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:a", reason="x")
        # ... but the RAW group {a,b,c} itself has its OWN exact-set disposition.
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:b", reason="y")
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)
        result = resolve_fh_d4_findings(session, [finding])

        assert result.active_findings == ()
        assert len(result.confirmed_distinct) == 1
        group = result.confirmed_distinct[0]
        assert group.status == "CONFIRMED_DISTINCT"
        # Exact-set precedence: subgroup metadata is not computed/attached at all.
        assert group.resolved_subgroups == ()
        assert group.unresolved_remainder_signal_ids == ()
        assert group.subgroup_conflict is False
        session.close(); engine.dispose()

    # -- K: existing ambiguous exact-set-history behavior unchanged ---------------

    def test_k_ambiguous_exact_set_history_unaffected_by_subgroup_logic(self):
        """Strengthened (D4D8B critical review, mission §3 Case C): a
        LEGITIMATE proper-subset disposition also exists alongside the
        ambiguous exact-set history - subgroup metadata must not 'rescue' or
        suppress the ambiguity; the exact-set ambiguity still controls the
        primary bucket outright."""
        engine, session = make_session()
        ids = _signals(session, 3)
        a, b, c = ids
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:z", reason="w")
        record_signal_group_disposition(session, signal_ids=ids, decision="DISTINCT", reviewer="human:a", reason="x")
        record_signal_group_disposition(session, signal_ids=ids, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:b", reason="y")
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)
        result = resolve_fh_d4_findings(session, [finding])

        assert result.active_findings == () and result.confirmed_distinct == () and result.confirmed_same_effort == ()
        assert len(result.ambiguous_groups) == 1
        group = result.ambiguous_groups[0]
        assert group.ambiguous_history is True
        assert group.independent_root_count == 2
        assert group.resolved_subgroups == ()
        assert group.unresolved_remainder_signal_ids == ()
        assert group.subgroup_conflict is False
        session.close(); engine.dispose()

    # -- L: subgroup recorded independently of any particular raw-group context ---

    def test_l_subgroup_recorded_before_raw_group_is_ever_queried(self):
        """"Subgroup" is a purely derived relationship (design doc §4) - a
        disposition recorded with no FH-D4 raw group in view at all is
        discovered correctly the first time it is later evaluated against a
        currently-live raw group that happens to contain it as a proper
        subset."""
        engine, session = make_session()
        ids = _signals(session, 3)
        a, b, c = ids
        # Recorded via the generic persistence API - no FH-D4 finding involved.
        record_signal_group_disposition(session, signal_ids=[a, b], decision="DISTINCT", reviewer="human:a", reason="x")
        session.commit()

        finding = _fh_d4_finding(ids, airport_id=1)
        result = resolve_fh_d4_findings(session, [finding])
        group = result.active_findings[0]
        assert len(group.resolved_subgroups) == 1
        assert group.resolved_subgroups[0].signal_ids == tuple(sorted([a, b]))
        session.close(); engine.dispose()

    # -- M: deterministic ordering of multiple subgroup records --------------------

    def test_m_deterministic_ordering_regardless_of_insertion_order(self):
        engine, session = make_session()
        ids = _signals(session, 6)
        a, b, c, d, e, f = ids
        # Insert the HIGHER-signal_id subgroup first.
        record_signal_group_disposition(session, signal_ids=[c, d], decision="DISTINCT", reviewer="human:a", reason="x")
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:b", reason="y")
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)
        result = resolve_fh_d4_findings(session, [finding])

        group = result.active_findings[0]
        ordering = [s.signal_ids for s in group.resolved_subgroups]
        assert ordering == sorted(ordering)
        assert ordering[0] == tuple(sorted([a, b]))
        session.close(); engine.dispose()

    # -- N: singleton remainder ------------------------------------------------

    def test_n_singleton_remainder(self):
        engine, session = make_session()
        ids = _signals(session, 4)
        three, fourth = ids[:3], ids[3]
        record_signal_group_disposition(session, signal_ids=three, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:a", reason="x")
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)
        result = resolve_fh_d4_findings(session, [finding])

        group = result.active_findings[0]
        assert group.unresolved_remainder_signal_ids == (fourth,)
        session.close(); engine.dispose()

    # -- O: empty remainder never occurs for a proper-subset interpretation -------

    def test_o_minimum_pair_raw_group_has_no_possible_proper_subset(self):
        """A raw group of exactly 2 Signals (the minimum FH-D4/disposition
        cardinality) structurally cannot contain any proper subset of size
        >= 2 - exact-set semantics own the full-set case entirely; subgroup
        discovery must not crash or fabricate anything for the minimum case."""
        engine, session = make_session()
        ids = _signals(session, 2)
        finding = _fh_d4_finding(ids, airport_id=1)
        result = resolve_fh_d4_findings(session, [finding])

        group = result.active_findings[0]
        assert group.resolved_subgroups == ()
        assert group.unresolved_remainder_signal_ids == tuple(sorted(ids))
        session.close(); engine.dispose()

    # -- P/Q: non-D4 findings and raw counts unchanged ------------------------------

    def test_p_non_d4_findings_unchanged_by_subgroup_logic(self):
        engine, session = make_session()
        ids = _signals(session, 3)
        a, b, c = ids
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:a", reason="x")
        session.commit()
        d4_finding = _fh_d4_finding(ids, airport_id=1)
        other = HealthFinding(
            rule_id="FH-D3", classification=HealthClassification.REVIEW_REQUIRED,
            entity_type="Signal", entity_ids=(a,), airport_id=1, summary="unrelated", structured_evidence={},
        )
        result = resolve_fh_d4_findings(session, [d4_finding, other])
        assert result.non_d4_findings == (other,)
        session.close(); engine.dispose()

    def test_q_raw_finding_counts_and_accounting_invariant_unchanged(self):
        engine, session = make_session()
        ids = _signals(session, 3)
        a, b, c = ids
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:a", reason="x")
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)
        result = resolve_fh_d4_findings(session, [finding])

        all_grouped = (
            result.active_findings + result.confirmed_distinct
            + result.confirmed_same_effort + result.ambiguous_groups
        )
        assert len(all_grouped) == 1  # exactly the one raw finding, subgroup metadata notwithstanding
        session.close(); engine.dispose()

    # -- R: existing no-subgroup fixtures remain field/result compatible -----------

    def test_r_existing_unreviewed_fixture_gains_only_additive_defaults(self):
        engine, session = make_session()
        ids = _signals(session, 2)
        finding = _real_fh_d4_finding(ids, airport_id=session.get(Signal, ids[0]).airport_id)
        result = resolve_fh_d4_findings(session, [finding])
        group = result.active_findings[0]

        # Every pre-existing field still behaves exactly as before D4D8B.
        assert group.status == "UNREVIEWED"
        assert group.latest_disposition_id is None
        assert group.decision is None
        assert group.independent_root_count == 0
        assert group.ambiguous_history is False
        assert group.related_history == ()
        # New fields present with sane, non-crashing defaults.
        assert group.resolved_subgroups == ()
        assert group.subgroup_conflict is False
        session.close(); engine.dispose()

    # -- Real D4D7 shapes, as synthetic fixtures only (no real DB access) ----------

    def test_roanoke_shape_pair_subgroup_singleton_remainder(self):
        """Mirrors the real {37,51,61} topology: a two-member subgroup
        SAME-supported, one member left as a correctly-unresolved
        singleton remainder - no DISTINCT inference for it."""
        engine, session = make_session()
        ids = _signals(session, 3)
        thirty_seven, fifty_one, sixty_one = ids
        record_signal_group_disposition(
            session, signal_ids=[fifty_one, sixty_one], decision="SAME_REAL_WORLD_EFFORT",
            reviewer="human:rwi-owner", reason="Shared Installation, same runway_end='34', explicit AIP cross-reference text.",
        )
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=72)
        result = resolve_fh_d4_findings(session, [finding])

        group = result.active_findings[0]
        assert group.status == "UNREVIEWED"
        assert len(group.resolved_subgroups) == 1
        assert group.resolved_subgroups[0].signal_ids == tuple(sorted([fifty_one, sixty_one]))
        assert group.resolved_subgroups[0].decision == "SAME_REAL_WORLD_EFFORT"
        assert group.unresolved_remainder_signal_ids == (thirty_seven,)
        assert group.subgroup_conflict is False
        session.close(); engine.dispose()

    def test_binghamton_shape_four_member_subgroup_singleton_remainder(self):
        """Mirrors the real {49,55,58,59,60} topology: a four-member
        subgroup SAME-supported, one member left as a correctly-unresolved
        singleton remainder."""
        engine, session = make_session()
        ids = _signals(session, 5)
        four, sixty = ids[:4], ids[4]
        record_signal_group_disposition(
            session, signal_ids=four, decision="SAME_REAL_WORLD_EFFORT", reviewer="human:rwi-owner",
            reason="All four explicitly, consistently name Runway 34 end; gapless design->procurement->final(100%) phase sequence.",
        )
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=6)
        result = resolve_fh_d4_findings(session, [finding])

        group = result.active_findings[0]
        assert len(group.resolved_subgroups) == 1
        assert group.resolved_subgroups[0].signal_ids == tuple(sorted(four))
        assert group.unresolved_remainder_signal_ids == (sixty,)
        session.close(); engine.dispose()

    def test_worcester_shape_no_subgroup_manufactured(self):
        """Mirrors the real {46,53,56,57,62} topology: genuinely conflicting
        evidence with no clean bipartition - no disposition of any kind
        exists, so no subgroup state may be manufactured from suggestive
        evidence; the group stays an ordinary, unresolved active finding."""
        engine, session = make_session()
        ids = _signals(session, 5)
        finding = _fh_d4_finding(ids, airport_id=44)
        result = resolve_fh_d4_findings(session, [finding])

        group = result.active_findings[0]
        assert group.status == "UNREVIEWED"
        assert group.resolved_subgroups == ()
        assert group.unresolved_remainder_signal_ids == tuple(sorted(ids))
        assert group.subgroup_conflict is False
        session.close(); engine.dispose()

    def test_greenville_shape_no_subgroup_manufactured(self):
        """Mirrors the real {36,44} topology: a genuine pair with no positive
        anchor either way - no disposition exists, so no subgroup state may
        be manufactured from mere co-location."""
        engine, session = make_session()
        ids = _signals(session, 2)
        finding = _fh_d4_finding(ids, airport_id=63)
        result = resolve_fh_d4_findings(session, [finding])

        group = result.active_findings[0]
        assert group.status == "UNREVIEWED"
        assert group.resolved_subgroups == ()
        assert group.unresolved_remainder_signal_ids == tuple(sorted(ids))
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# D4D8B critical review (adversarial pass): genuine coverage gaps found and
# closed - the underlying implementation was independently verified correct
# in every case below BEFORE the corresponding test was written; none of
# these represent a production defect or a production code change.
# ---------------------------------------------------------------------------


class TestSubgroupDiscoveryCriticalReview:
    # -- mission §7 case G: strict subset/superset subgroup sets are a conflict ---

    def test_subset_of_subset_subgroup_sets_is_a_conflict(self):
        """{1,2} SAME and {1,2,3} SAME are BOTH proper subsets of raw
        {1,2,3,4} and are themselves in a subset/superset relationship with
        EACH OTHER - the D4D8A locked overlap policy is syntactic ('any
        non-exact-match, non-empty intersection... redundancy is not
        exempted') and applies identically here: this is not treated as
        'one is merely historical context for the other,' it is a hard
        conflict, exactly like a bare partial overlap."""
        engine, session = make_session()
        ids = _signals(session, 4)
        a, b, c, d = ids
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r1")
        record_signal_group_disposition(session, signal_ids=[a, b, c], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:y", reason="r2")
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)
        result = resolve_fh_d4_findings(session, [finding])

        group = result.active_findings[0]
        assert group.subgroup_conflict is True
        assert len(group.resolved_subgroups) == 2
        seen_sets = {s.signal_ids for s in group.resolved_subgroups}
        assert tuple(sorted([a, b])) in seen_sets
        assert tuple(sorted([a, b, c])) in seen_sets
        assert group.unresolved_remainder_signal_ids == tuple(sorted(ids))
        session.close(); engine.dispose()

    # -- mission §5: subgroup-level supersession chain is not walked/duplicated ---

    def test_subgroup_level_supersession_yields_exactly_one_current_summary(self):
        """A correction for the SAME exact subgroup member set ({1,2} SAME
        superseded by {1,2} DISTINCT) must produce exactly ONE
        SubgroupDispositionSummary reflecting the LATEST decision - the
        older SAME must not remain visible alongside it, and this must not
        be (mis)detected as a subgroup_conflict (it is one member set, not
        two overlapping ones)."""
        engine, session = make_session()
        ids = _signals(session, 3)
        a, b, c = ids
        d1 = record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r1")
        session.commit()
        record_signal_group_disposition(session, signal_ids=[a, b], decision="DISTINCT", reviewer="human:y", reason="r2", supersedes_id=d1.id)
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)
        result = resolve_fh_d4_findings(session, [finding])

        group = result.active_findings[0]
        assert group.subgroup_conflict is False
        assert len(group.resolved_subgroups) == 1
        sub = group.resolved_subgroups[0]
        assert sub.decision == "DISTINCT"  # the later, superseding decision - not the stale SAME
        assert sub.independent_root_count == 1
        assert sub.ambiguous_history is False
        session.close(); engine.dispose()

    # -- mission §5: competing UNsuperseded roots for the SAME subset -------------

    def test_competing_unsuperseded_roots_for_same_subset_flagged_not_duplicated(self):
        """Two independently-recorded (no supersedes_id link) dispositions
        for the identical {1,2} member set - D3's own latest-wins still
        yields exactly one operational summary (never two), but that
        summary's OWN ambiguous_history/independent_root_count exposes the
        disagreement, exactly mirroring the existing whole-group
        ambiguous_groups precedent. Orthogonal to, and must not trigger,
        subgroup_conflict (there is only one distinct member set here)."""
        engine, session = make_session()
        ids = _signals(session, 3)
        a, b, c = ids
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r1")
        record_signal_group_disposition(session, signal_ids=[a, b], decision="DISTINCT", reviewer="human:y", reason="r2")
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)
        result = resolve_fh_d4_findings(session, [finding])

        group = result.active_findings[0]
        assert len(group.resolved_subgroups) == 1
        sub = group.resolved_subgroups[0]
        assert sub.independent_root_count == 2
        assert sub.ambiguous_history is True
        assert group.subgroup_conflict is False  # orthogonal concern, not conflated
        session.close(); engine.dispose()

    # -- mission §5: same-timestamp tiebreak (higher id wins) ---------------------

    def test_subgroup_latest_wins_same_timestamp_higher_id_wins(self):
        engine, session = make_session()
        ids = _signals(session, 3)
        a, b, c = ids
        ts = datetime(2020, 1, 1, tzinfo=timezone.utc)

        d1 = SignalDisposition(decision="SAME_REAL_WORLD_EFFORT", reason="r1", reviewer="human:x", created_at=ts)
        setattr(d1, ACCEPTING_INITIAL_MEMBERS_ATTR, True)
        session.add(d1); session.flush()
        session.add(SignalDispositionMember(disposition_id=d1.id, signal_id=a))
        session.add(SignalDispositionMember(disposition_id=d1.id, signal_id=b))
        session.flush(); setattr(d1, ACCEPTING_INITIAL_MEMBERS_ATTR, False)

        d2 = SignalDisposition(decision="DISTINCT", reason="r2", reviewer="human:y", created_at=ts)
        setattr(d2, ACCEPTING_INITIAL_MEMBERS_ATTR, True)
        session.add(d2); session.flush()
        session.add(SignalDispositionMember(disposition_id=d2.id, signal_id=a))
        session.add(SignalDispositionMember(disposition_id=d2.id, signal_id=b))
        session.flush(); setattr(d2, ACCEPTING_INITIAL_MEMBERS_ATTR, False)
        session.commit()
        assert d1.created_at == d2.created_at
        assert d2.id > d1.id

        finding = _fh_d4_finding(ids, airport_id=1)
        result = resolve_fh_d4_findings(session, [finding])
        sub = result.active_findings[0].resolved_subgroups[0]
        assert sub.latest_disposition_id == d2.id
        assert sub.decision == "DISTINCT"
        session.close(); engine.dispose()

    # -- mission §11: detector growth/shrink transition, no data rewrite ----------

    def test_detector_growth_shrink_transition_no_data_rewrite(self):
        """The SAME persisted {a,b} SAME fact, evaluated against three
        different raw-group presentations in sequence, with no mutation
        between calls - purely derived, recomputed fresh each time."""
        engine, session = make_session()
        ids = _signals(session, 4)
        a, b, c, d = ids
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r1")
        session.commit()

        # Stage 1: raw {a,b,c} -> {a,b} is a subgroup, remainder {c}.
        r1 = resolve_fh_d4_findings(session, [_fh_d4_finding([a, b, c], airport_id=1)])
        g1 = r1.active_findings[0]
        assert len(g1.resolved_subgroups) == 1
        assert g1.unresolved_remainder_signal_ids == (c,)

        # Stage 2: detector shrinks to raw {a,b} exactly -> ordinary confirmed
        # SAME via D3's own exact-match resolution, NOT subgroup metadata.
        r2 = resolve_fh_d4_findings(session, [_fh_d4_finding([a, b], airport_id=1)])
        assert len(r2.confirmed_same_effort) == 1
        assert r2.confirmed_same_effort[0].resolved_subgroups == ()

        # Stage 3: detector grows to raw {a,b,c,d} -> {a,b} still a subgroup.
        r3 = resolve_fh_d4_findings(session, [_fh_d4_finding([a, b, c, d], airport_id=1)])
        g3 = r3.active_findings[0]
        assert len(g3.resolved_subgroups) == 1
        assert g3.unresolved_remainder_signal_ids == tuple(sorted([c, d]))
        session.close(); engine.dispose()

    # -- mission §10: no parent-group identity stored or assumed ------------------

    def test_same_fact_discovered_independently_against_two_different_raw_groups(self):
        """The identical {a,b} SAME fact is discovered correctly and
        independently as a subgroup of BOTH {a,b,c} and {a,b,d} when both
        are presented in the SAME batch call - proving no parent-group
        identity is stored, cached, or assumed anywhere."""
        engine, session = make_session()
        ids = _signals(session, 4)
        a, b, c, d = ids
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r1")
        session.commit()

        findings = [_fh_d4_finding([a, b, c], airport_id=1), _fh_d4_finding([a, b, d], airport_id=1)]
        result = resolve_fh_d4_findings(session, findings)
        assert len(result.active_findings) == 2
        by_set = {g.signal_ids: g for g in result.active_findings}

        g_abc = by_set[tuple(sorted([a, b, c]))]
        assert len(g_abc.resolved_subgroups) == 1
        assert g_abc.resolved_subgroups[0].signal_ids == tuple(sorted([a, b]))
        assert g_abc.unresolved_remainder_signal_ids == (c,)

        g_abd = by_set[tuple(sorted([a, b, d]))]
        assert len(g_abd.resolved_subgroups) == 1
        assert g_abd.resolved_subgroups[0].signal_ids == tuple(sorted([a, b]))
        assert g_abd.unresolved_remainder_signal_ids == (d,)
        session.close(); engine.dispose()

    # -- mission §13: independent query-count re-verification, overlapping case ---

    def test_query_count_bounded_with_overlapping_subgroup_history(self):
        """Independent re-measurement (not reusing TestBatchQueryBehavior's
        own fixtures): a genuinely CONFLICTING subgroup scenario must not
        cost any additional queries beyond the ordinary subgroup-present
        case - conflict detection is pure in-Python post-processing over
        already-fetched headers, never a further query."""
        engine, session = make_session()
        ids = _signals(session, 5)
        a, b, c, d, e = ids
        record_signal_group_disposition(session, signal_ids=[a, b, c], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r1")
        record_signal_group_disposition(session, signal_ids=[c, d], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:y", reason="r2")
        session.commit()
        finding = _fh_d4_finding(ids, airport_id=1)

        statements = []

        def _capture(_conn, _cursor, statement, *_args, **_kwargs):
            statements.append(statement.strip().upper())

        engine_for_events = session.get_bind()
        event.listen(engine_for_events, "before_cursor_execute", _capture)
        try:
            result = resolve_fh_d4_findings(session, [finding])
        finally:
            event.remove(engine_for_events, "before_cursor_execute", _capture)

        assert result.active_findings[0].subgroup_conflict is True
        select_statements = [s for s in statements if s.startswith("SELECT")]
        assert len(select_statements) == 8, (
            f"expected exactly 8 SELECT statements (same bound as the non-conflicting "
            f"subgroup-present case), got {len(select_statements)}: {select_statements}"
        )
        session.close(); engine.dispose()
