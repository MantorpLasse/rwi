"""Tests for app/services/signal_disposition_conflicts.py (D4D8C,
docs/architecture/fh-d4-signal-disposition-d4d8-subgroup-semantics-design.md
§7, adversarially reviewed and locked in D4D8A).

Every test uses an isolated in-memory SQLite database - nothing here ever
opens data/runway_safe.db (see TestNoRealDatabaseAccess).
"""
from __future__ import annotations

import ast
import inspect as inspect_module
from datetime import datetime, timezone

import pytest
import sqlalchemy.exc
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Signal
from app.models.signal_disposition import (
    ACCEPTING_INITIAL_MEMBERS_ATTR,
    SignalDisposition,
    SignalDispositionMember,
)
from app.services.signal_disposition_conflicts import (
    EXACT_MATCH,
    PARTIAL_OVERLAP,
    STRICT_SUBSET,
    STRICT_SUPERSET,
    find_signal_disposition_conflicts,
)
from app.services.signal_disposition_persistence import (
    MINIMUM_GROUP_CARDINALITY,
    record_signal_group_disposition,
)


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def _signals(session, n, airport=None):
    if airport is None:
        airport = Airport(name="D4D8C Test Airport", country="XX")
        session.add(airport)
    sigs = [Signal(airport=airport, title=f"S{i}", category="replacement", confidence="high") for i in range(n)]
    session.add_all(sigs)
    session.commit()
    return tuple(s.id for s in sigs)


# ---------------------------------------------------------------------------
# Required case matrix (mission §7, CASE 1-12)
# ---------------------------------------------------------------------------


class TestCaseMatrix:
    def test_case_1_partial_overlap_hard_block(self):
        engine, session = make_session()
        a, b, c, d = _signals(session, 4)
        record_signal_group_disposition(session, signal_ids=[a, b, c], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r")
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[b, c, d])
        assert len(conflicts) == 1
        assert conflicts[0].relation == PARTIAL_OVERLAP
        assert conflicts[0].conflicting_signal_ids == tuple(sorted([a, b, c]))
        assert conflicts[0].overlap_signal_ids == tuple(sorted([b, c]))
        session.close(); engine.dispose()

    def test_case_2_partial_overlap_no_transitive_inference(self):
        engine, session = make_session()
        a, b, c = _signals(session, 3)
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r")
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[b, c])
        assert len(conflicts) == 1
        assert conflicts[0].relation == PARTIAL_OVERLAP
        session.close(); engine.dispose()

    def test_case_3_exact_match_is_not_an_overlap_conflict(self):
        engine, session = make_session()
        a, b = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r")
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[a, b])
        assert conflicts == ()
        session.close(); engine.dispose()

    def test_case_4_strict_subset_differing_decision_hard_block(self):
        engine, session = make_session()
        a, b, c = _signals(session, 3)
        record_signal_group_disposition(session, signal_ids=[a, b, c], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r")
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[a, b])
        assert len(conflicts) == 1
        assert conflicts[0].relation == STRICT_SUPERSET  # existing {a,b,c} is a strict SUPERSET of proposed {a,b}
        session.close(); engine.dispose()

    def test_case_5_larger_partial_overlap_hard_block(self):
        engine, session = make_session()
        a, b, c, d, e = _signals(session, 5)
        record_signal_group_disposition(session, signal_ids=[a, b, c, d], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r")
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[c, d, e])
        assert len(conflicts) == 1
        assert conflicts[0].relation == PARTIAL_OVERLAP
        session.close(); engine.dispose()

    def test_case_6_disjoint_is_allowed(self):
        engine, session = make_session()
        a, b, c, d = _signals(session, 4)
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r")
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[c, d])
        assert conflicts == ()
        session.close(); engine.dispose()

    def test_case_7_strict_subset_of_proposed_hard_block(self):
        engine, session = make_session()
        a, b, c = _signals(session, 3)
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r")
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[a, b, c])
        assert len(conflicts) == 1
        assert conflicts[0].relation == STRICT_SUBSET  # existing {a,b} is a strict SUBSET of proposed {a,b,c}
        session.close(); engine.dispose()

    def test_case_8_strict_superset_matching_decision_still_hard_block(self):
        """Syntactic, not semantic (design doc §7): redundancy is not
        exempted even when the existing and proposed decisions would agree."""
        engine, session = make_session()
        a, b, c = _signals(session, 3)
        record_signal_group_disposition(session, signal_ids=[a, b, c], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r")
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[a, b])
        assert len(conflicts) == 1
        assert conflicts[0].relation == STRICT_SUPERSET
        assert conflicts[0].conflicting_decision == "SAME_REAL_WORLD_EFFORT"
        session.close(); engine.dispose()

    def test_case_9_multiple_disjoint_existing_sets_zero_conflicts(self):
        engine, session = make_session()
        a, b, c, d, e, f = _signals(session, 6)
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r")
        record_signal_group_disposition(session, signal_ids=[c, d], decision="DISTINCT", reviewer="human:y", reason="r2")
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[e, f])
        assert conflicts == ()
        session.close(); engine.dispose()

    def test_case_10_exact_set_history_owned_by_supersession_path(self):
        engine, session = make_session()
        a, b, c = _signals(session, 3)
        d1 = record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r1")
        session.commit()
        record_signal_group_disposition(session, signal_ids=[a, b], decision="DISTINCT", reviewer="human:y", reason="r2", supersedes_id=d1.id)
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[a, b])
        assert conflicts == ()
        session.close(); engine.dispose()

    def test_case_11_only_current_fact_conflicts_not_stale_superseded_one(self):
        """The critical historical/latest-set policy test: {a,b} SAME
        superseded by {a,b} DISTINCT. Proposing {b,c} must yield EXACTLY ONE
        conflict, against the CURRENT (DISTINCT) fact - never two conflicts,
        and never a conflict against the stale, superseded SAME row alone."""
        engine, session = make_session()
        a, b, c = _signals(session, 3)
        d1 = record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r1")
        session.commit()
        d2 = record_signal_group_disposition(session, signal_ids=[a, b], decision="DISTINCT", reviewer="human:y", reason="r2", supersedes_id=d1.id)
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[b, c])
        assert len(conflicts) == 1
        assert conflicts[0].conflicting_disposition_id == d2.id
        assert conflicts[0].conflicting_decision == "DISTINCT"
        session.close(); engine.dispose()

    def test_case_12a_zero_member_disposition_silently_ignored(self):
        """A malformed, memberless disposition (constructible only via raw
        SQL, bypassing MINIMUM_GROUP_CARDINALITY) can never appear as a
        candidate at all - it owns no SignalDispositionMember row, so the
        signal_id-scoped candidate query never finds it."""
        engine, session = make_session()
        a, b, c = _signals(session, 3)
        record_signal_group_disposition(session, signal_ids=[a, b], decision="DISTINCT", reviewer="human:x", reason="r1")
        session.commit()
        session.execute(text(
            "INSERT INTO signal_dispositions (id, decision, reason, reviewer, created_at) "
            "VALUES (999, 'DISTINCT', 'malformed', 'human:z', '2020-01-01')"
        ))
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[b, c])
        assert len(conflicts) == 1  # only the real {a,b} DISTINCT conflict - the memberless row is invisible
        assert conflicts[0].conflicting_disposition_id != 999
        session.close(); engine.dispose()

    def test_case_12b_one_member_disposition_surfaced_deterministically(self):
        """A malformed one-member disposition (also only constructible via
        raw SQL) must NOT crash and must NOT be silently hidden - it is
        deterministically surfaced as a genuine (if malformed) STRICT_SUBSET
        conflict, exactly like any other subset would be. Fail visible, not
        fabricate clean state."""
        engine, session = make_session()
        a, b, c = _signals(session, 3)
        session.execute(text(
            "INSERT INTO signal_dispositions (id, decision, reason, reviewer, created_at) "
            "VALUES (998, 'SAME_REAL_WORLD_EFFORT', 'malformed-one-member', 'human:z', '2021-01-01')"
        ))
        session.execute(text(f"INSERT INTO signal_disposition_members (id, disposition_id, signal_id) VALUES (9998, 998, {a})"))
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[a, c])
        assert len(conflicts) == 1
        assert conflicts[0].conflicting_disposition_id == 998
        assert conflicts[0].conflicting_signal_ids == (a,)
        assert conflicts[0].relation == STRICT_SUBSET
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# exclude_exact_set toggle
# ---------------------------------------------------------------------------


class TestExcludeExactSetToggle:
    def test_exact_match_included_when_flag_false(self):
        engine, session = make_session()
        a, b = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r")
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[a, b], exclude_exact_set=False)
        assert len(conflicts) == 1
        assert conflicts[0].relation == EXACT_MATCH
        assert conflicts[0].overlap_signal_ids == tuple(sorted([a, b]))
        session.close(); engine.dispose()

    def test_exact_match_excluded_by_default(self):
        engine, session = make_session()
        a, b = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r")
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[a, b])
        assert conflicts == ()
        session.close(); engine.dispose()

    def test_flag_never_affects_non_exact_conflicts(self):
        engine, session = make_session()
        a, b, c = _signals(session, 3)
        record_signal_group_disposition(session, signal_ids=[a, b, c], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r")
        session.commit()
        c_true = find_signal_disposition_conflicts(session, signal_ids=[a, b], exclude_exact_set=True)
        c_false = find_signal_disposition_conflicts(session, signal_ids=[a, b], exclude_exact_set=False)
        assert c_true == c_false  # no exact match exists here either way
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Global scope (not parent-group/airport/detector-run scoped)
# ---------------------------------------------------------------------------


class TestGlobalScope:
    def test_conflict_found_across_unrelated_airports(self):
        """No parent-group foreign key, no airport scoping - the guard is
        purely a global scan over persisted exact-set member ids."""
        engine, session = make_session()
        airport1 = Airport(name="Airport1", country="XX")
        airport2 = Airport(name="Airport2", country="XX")
        session.add_all([airport1, airport2])
        session.commit()
        a, b = _signals(session, 2, airport=airport1)
        c, d = _signals(session, 2, airport=airport2)
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r")
        session.commit()
        # A proposed set spanning BOTH airports still finds the conflict -
        # this module never reads Signal.airport_id and never restricts scope.
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[b, c])
        assert len(conflicts) == 1
        assert conflicts[0].relation == PARTIAL_OVERLAP
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Transitivity - none, ever
# ---------------------------------------------------------------------------


class TestNoTransitiveInference:
    def test_no_third_fact_derived_from_two_independent_conflicts(self):
        """{a,b} SAME and {b,c} SAME both independently conflict with a
        proposed {b,d} - but nothing here ever combines {a,b} and {b,c}
        with each other to derive {a,c} or {a,b,c}; each existing set is
        compared to the proposed set independently."""
        engine, session = make_session()
        a, b, c, d = _signals(session, 4)
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r1")
        record_signal_group_disposition(session, signal_ids=[b, c], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:y", reason="r2")
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[b, d])
        assert len(conflicts) == 2
        seen_sets = {c.conflicting_signal_ids for c in conflicts}
        assert seen_sets == {tuple(sorted([a, b])), tuple(sorted([b, c]))}
        # No conflict entry ever names a, c, and d together, or any set not
        # actually persisted.
        for c_ in conflicts:
            assert set(c_.conflicting_signal_ids).issubset({a, b, c, d})
        session.close(); engine.dispose()

    def test_module_contains_no_union_find_or_graph_machinery_ast(self):
        import app.services.signal_disposition_conflicts as module

        tree = ast.parse(inspect_module.getsource(module))
        # No import of any graph/union-find-style library, no recursive
        # helper, no second pairwise-comparison loop beyond the single
        # proposed-vs-each-existing-set comparison.
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_names.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported_names.add(alias.name)
        forbidden = {"networkx", "scipy", "scipy.sparse.csgraph"}
        assert not (imported_names & forbidden)
        function_defs = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
        assert len(function_defs) == 2  # _normalize, find_signal_disposition_conflicts - no hidden helper


# ---------------------------------------------------------------------------
# Information firewall
# ---------------------------------------------------------------------------


class TestInformationFirewall:
    _FORBIDDEN_SIGNAL_ATTRS = (
        "title", "notes", "source_notes", "estimated_total_value_usd",
        "estimated_emas_value_usd", "supplier", "likely_supplier", "supplier_reason",
        "confirmed_vendor", "category", "confidence", "manual_year_estimate",
        "published", "runway_id", "installation_id", "airport_id", "status",
    )

    def test_module_never_references_forbidden_signal_attributes_ast(self):
        import app.services.signal_disposition_conflicts as module

        tree = ast.parse(inspect_module.getsource(module))
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert not (attrs & set(self._FORBIDDEN_SIGNAL_ATTRS))

    def test_module_never_imports_signal_model(self):
        import app.services.signal_disposition_conflicts as module

        tree = ast.parse(inspect_module.getsource(module))
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_names.add(node.module)
                for alias in node.names:
                    imported_names.add(alias.name)
        assert "app.models" not in imported_names or "Signal" not in imported_names

    def test_behavioral_no_signal_content_leak(self):
        engine, session = make_session()
        airport = Airport(name="Firewall Airport", country="XX")
        session.add(airport)
        a = Signal(airport=airport, title="SECRET TITLE TEXT", category="replacement", confidence="high", estimated_total_value_usd=123456789)
        b = Signal(airport=airport, title="OTHER SECRET TITLE", category="replacement", confidence="high")
        c = Signal(airport=airport, title="THIRD SECRET", category="replacement", confidence="high")
        session.add_all([a, b, c])
        session.commit()
        record_signal_group_disposition(session, signal_ids=[a.id, b.id], decision="DISTINCT", reviewer="human:x", reason="x")
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[b.id, c.id])
        result_repr = repr(conflicts)
        assert "SECRET" not in result_repr
        assert "123456789" not in result_repr
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Query count / batching
# ---------------------------------------------------------------------------


class TestQueryCount:
    def _count_selects(self, session, engine, **kwargs):
        statements = []

        def _capture(_conn, _cursor, statement, *_args, **_kwargs):
            statements.append(statement.strip().upper())

        event.listen(engine, "before_cursor_execute", _capture)
        try:
            find_signal_disposition_conflicts(session, **kwargs)
        finally:
            event.remove(engine, "before_cursor_execute", _capture)
        return len([s for s in statements if s.startswith("SELECT")])

    def test_one_query_when_zero_dispositions_exist(self):
        engine, session = make_session()
        a, b = _signals(session, 2)
        n = self._count_selects(session, engine, signal_ids=[a, b])
        assert n == 1
        session.close(); engine.dispose()

    @pytest.mark.parametrize("n_other_dispositions", [1, 10, 100])
    def test_three_queries_regardless_of_total_disposition_count(self, n_other_dispositions):
        """Bounded, non-N+1: exactly 3 queries (candidate ids -> full member
        rows -> headers) whenever at least one candidate exists, REGARDLESS
        of how many OTHER, unrelated dispositions exist in the table."""
        engine, session = make_session()
        airport = Airport(name="Scale Airport", country="XX")
        session.add(airport); session.commit()
        for i in range(n_other_dispositions):
            sigs = [Signal(airport=airport, title=f"Other{i}_{j}", category="replacement", confidence="high") for j in range(2)]
            session.add_all(sigs); session.commit()
            record_signal_group_disposition(session, signal_ids=[s.id for s in sigs], decision="DISTINCT", reviewer="human:x", reason="r")
            session.commit()
        a, b, c = _signals(session, 3, airport=airport)
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:y", reason="r2")
        session.commit()

        n = self._count_selects(session, engine, signal_ids=[b, c])
        assert n == 3, f"expected exactly 3 SELECT statements for {n_other_dispositions} unrelated dispositions, got {n}"
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Read-only / no-autoflush
# ---------------------------------------------------------------------------


class TestReadOnlyGuarantee:
    def test_never_autoflushes_callers_pending_mutation(self):
        engine, session = make_session()
        a, b, c = _signals(session, 3)
        record_signal_group_disposition(session, signal_ids=[a, b], decision="DISTINCT", reviewer="human:x", reason="r")
        session.commit()

        airport = session.get(Airport, session.get(Signal, a).airport_id)
        airport.name = "PENDING - MUST NOT BE FLUSHED"
        assert airport in session.dirty

        find_signal_disposition_conflicts(session, signal_ids=[b, c])

        assert airport in session.dirty
        session.rollback()
        session.close(); engine.dispose()

    def test_no_write_sql_emitted(self):
        engine, session = make_session()
        a, b, c = _signals(session, 3)
        record_signal_group_disposition(session, signal_ids=[a, b], decision="DISTINCT", reviewer="human:x", reason="r")
        session.commit()

        statements = []

        def _capture(_conn, _cursor, statement, *_args, **_kwargs):
            statements.append(statement.strip().upper())

        event.listen(engine, "before_cursor_execute", _capture)
        try:
            find_signal_disposition_conflicts(session, signal_ids=[b, c])
        finally:
            event.remove(engine, "before_cursor_execute", _capture)

        write_statements = [s for s in statements if s.startswith(("INSERT", "UPDATE", "DELETE"))]
        assert write_statements == []
        session.close(); engine.dispose()

    def test_session_stays_clean(self):
        engine, session = make_session()
        a, b, c = _signals(session, 3)
        find_signal_disposition_conflicts(session, signal_ids=[a, b])
        assert not session.new
        assert not session.dirty
        assert not session.deleted
        session.close(); engine.dispose()

    def test_query_failure_propagates_missing_schema_fails_loud(self):
        """Narrow, not a broad Exception catch-all: the module's own
        docstring promises specifically an uncaught
        sqlalchemy.exc.OperationalError, never a silently-converted 'no
        conflicts' result."""
        engine = create_engine("sqlite:///:memory:")
        # Deliberately do NOT create the schema.
        session = Session(engine)
        with pytest.raises(sqlalchemy.exc.OperationalError):
            find_signal_disposition_conflicts(session, signal_ids=[1, 2])
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Input normalization
# ---------------------------------------------------------------------------


class TestInputNormalization:
    def test_reversed_ids_produce_identical_result(self):
        engine, session = make_session()
        a, b, c = _signals(session, 3)
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r")
        session.commit()
        forward = find_signal_disposition_conflicts(session, signal_ids=[b, c])
        reversed_ = find_signal_disposition_conflicts(session, signal_ids=[c, b])
        assert forward == reversed_
        session.close(); engine.dispose()

    def test_duplicate_ids_deduplicated(self):
        engine, session = make_session()
        a, b, c = _signals(session, 3)
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r")
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[b, b, c, c, c])
        assert len(conflicts) == 1
        assert conflicts[0].proposed_signal_ids == tuple(sorted([b, c]))
        session.close(); engine.dispose()

    def test_minimum_cardinality_matches_persistence_constant(self):
        assert MINIMUM_GROUP_CARDINALITY == 2

    def test_singleton_rejected(self):
        engine, session = make_session()
        a, b = _signals(session, 2)
        with pytest.raises(ValueError, match="at least 2"):
            find_signal_disposition_conflicts(session, signal_ids=[a])
        session.close(); engine.dispose()

    def test_empty_rejected(self):
        engine, session = make_session()
        with pytest.raises(ValueError, match="at least 2"):
            find_signal_disposition_conflicts(session, signal_ids=[])
        session.close(); engine.dispose()

    def test_nonexistent_signal_ids_do_not_crash_no_existence_check(self):
        """Documented contract: this module never validates Signal
        existence - a proposed set naming ids that do not reference real
        Signals still produces a well-defined answer (based purely on
        whether any persisted disposition's member set overlaps)."""
        engine, session = make_session()
        a, b = _signals(session, 2)
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r")
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[b, 999999])
        assert len(conflicts) == 1
        assert conflicts[0].relation == PARTIAL_OVERLAP
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Backward compatibility - nothing else changes behavior
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_record_signal_group_disposition_unaffected(self):
        """The write path is completely untouched by this slice - recording
        an ordinary disposition still works exactly as before, with no
        conflict-scan side effect of any kind."""
        engine, session = make_session()
        a, b = _signals(session, 2)
        disposition = record_signal_group_disposition(session, signal_ids=[a, b], decision="DISTINCT", reviewer="human:x", reason="r")
        session.commit()
        assert disposition.id is not None
        assert disposition.decision == "DISTINCT"
        session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Deterministic ordering
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_multiple_conflicts_deterministically_ordered(self):
        engine, session = make_session()
        a, b, c, d, e = _signals(session, 5)
        record_signal_group_disposition(session, signal_ids=[c, d], decision="DISTINCT", reviewer="human:x", reason="r1")
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:y", reason="r2")
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[a, b, c, d, e])
        orderings = [c_.conflicting_signal_ids for c_ in conflicts]
        assert orderings == sorted(orderings)


# ---------------------------------------------------------------------------
# No real database access
# ---------------------------------------------------------------------------


class TestNoRealDatabaseAccess:
    def test_no_reference_to_real_database_path(self):
        import app.services.signal_disposition_conflicts as module

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
# No write-path imports / no try-except
# ---------------------------------------------------------------------------


def _raw_disposition(session, *, decision, reason, reviewer, created_at, member_signal_ids, supersedes_id=None):
    """Direct-ORM construction (bypassing record_signal_group_disposition())
    for tests that need explicit control over created_at (for tie-break
    tests) or a deliberately malformed supersedes_id - mirrors the exact
    pattern already established in tests/test_fh_d4_disposition_resolution
    .py for the same reason."""
    disposition = SignalDisposition(decision=decision, reason=reason, reviewer=reviewer, created_at=created_at, supersedes_id=supersedes_id)
    setattr(disposition, ACCEPTING_INITIAL_MEMBERS_ATTR, True)
    session.add(disposition)
    session.flush()
    for signal_id in member_signal_ids:
        session.add(SignalDispositionMember(disposition_id=disposition.id, signal_id=signal_id))
    session.flush()
    setattr(disposition, ACCEPTING_INITIAL_MEMBERS_ATTR, False)
    return disposition


# ---------------------------------------------------------------------------
# D4D8C critical review (adversarial pass): historical/latest-set policy
# attacks (mission §4, H1-H6) that were verified live but never locked in as
# permanent regression tests, plus the exact mission §7/§8 scenarios.
# ---------------------------------------------------------------------------


class TestHistoricalLatestPolicyAttacks:
    def test_h2_competing_unsuperseded_roots_latest_wins_and_is_flagged_ambiguous(self):
        """Two independently-recorded (no supersedes_id link) dispositions
        for the identical {a,b} set - latest-by-recency still yields exactly
        ONE conflict entry (never two), but that entry's own
        independent_root_count/ambiguous_history now exposes the
        disagreement - the exact gap found and closed during D4D8C critical
        review (the dataclass originally had no such fields)."""
        engine, session = make_session()
        a, b, c = _signals(session, 3)
        record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r1")
        record_signal_group_disposition(session, signal_ids=[a, b], decision="DISTINCT", reviewer="human:y", reason="r2")  # no supersedes_id
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[b, c])
        assert len(conflicts) == 1
        assert conflicts[0].independent_root_count == 2
        assert conflicts[0].ambiguous_history is True
        session.close(); engine.dispose()

    def test_h3_three_deep_supersession_chain_only_latest_matters(self):
        engine, session = make_session()
        a, b, c = _signals(session, 3)
        d1 = record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:x", reason="r1")
        session.commit()
        d2 = record_signal_group_disposition(session, signal_ids=[a, b], decision="DISTINCT", reviewer="human:y", reason="r2", supersedes_id=d1.id)
        session.commit()
        d3 = record_signal_group_disposition(session, signal_ids=[a, b], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:z", reason="r3", supersedes_id=d2.id)
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[b, c])
        assert len(conflicts) == 1
        assert conflicts[0].conflicting_disposition_id == d3.id
        assert conflicts[0].conflicting_decision == "SAME_REAL_WORLD_EFFORT"
        assert conflicts[0].independent_root_count == 1
        assert conflicts[0].ambiguous_history is False
        session.close(); engine.dispose()

    def test_h4_same_timestamp_tiebreak_higher_id_wins(self):
        engine, session = make_session()
        a, b, c = _signals(session, 3)
        ts = datetime(2020, 1, 1, tzinfo=timezone.utc)
        d1 = _raw_disposition(session, decision="SAME_REAL_WORLD_EFFORT", reason="r1", reviewer="human:x", created_at=ts, member_signal_ids=[a, b])
        d2 = _raw_disposition(session, decision="DISTINCT", reason="r2", reviewer="human:y", created_at=ts, member_signal_ids=[a, b])
        session.commit()
        assert d1.created_at == d2.created_at
        assert d2.id > d1.id
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[b, c])
        assert len(conflicts) == 1
        assert conflicts[0].conflicting_disposition_id == d2.id
        assert conflicts[0].conflicting_decision == "DISTINCT"
        session.close(); engine.dispose()

    def test_h5_h6_malformed_supersedes_id_across_different_member_sets_not_walked(self):
        """A malformed disposition (constructible only via raw ORM,
        bypassing the persistence layer's own supersession gate) claims
        supersedes_id pointing at a disposition with a DIFFERENT member set.
        Grouping is by ACTUAL member set, never by trusting/walking
        supersedes_id - the two rows are correctly treated as two
        independent, unrelated facts, not merged or chain-walked."""
        engine, session = make_session()
        a, b, c, d = _signals(session, 4)
        d1 = _raw_disposition(session, decision="SAME_REAL_WORLD_EFFORT", reason="r1", reviewer="human:x", created_at=datetime(2020, 1, 1, tzinfo=timezone.utc), member_signal_ids=[a, b])
        session.commit()
        _raw_disposition(session, decision="DISTINCT", reason="r2-malformed", reviewer="human:y", created_at=datetime(2020, 1, 2, tzinfo=timezone.utc), member_signal_ids=[a, c], supersedes_id=d1.id)
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[b, d])
        assert len(conflicts) == 1  # only {a,b} overlaps {b,d}; {a,c} does not
        assert conflicts[0].conflicting_signal_ids == tuple(sorted([a, b]))
        assert conflicts[0].conflicting_supersedes_id is None
        session.close(); engine.dispose()


class TestMissionExactScenarios:
    def test_section_7_three_existing_sets_two_overlap_one_disjoint(self):
        """Mission §7 exact scenario: {1,2} SAME, {3,4} DISTINCT, {5,6} SAME
        existing; propose {2,3,7}. Expected conflicts: {1,2} and {3,4} -
        NOT {5,6}. Both retained as independent peers, deterministically
        ordered, no ranking, no 'best conflict' selection, no merging."""
        engine, session = make_session()
        one, two, three, four, five, six, seven = _signals(session, 7)
        record_signal_group_disposition(session, signal_ids=[one, two], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:a", reason="r1")
        record_signal_group_disposition(session, signal_ids=[three, four], decision="DISTINCT", reviewer="human:b", reason="r2")
        record_signal_group_disposition(session, signal_ids=[five, six], decision="SAME_REAL_WORLD_EFFORT", reviewer="human:c", reason="r3")
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[two, three, seven])
        assert len(conflicts) == 2
        seen = {c.conflicting_signal_ids for c in conflicts}
        assert seen == {tuple(sorted([one, two])), tuple(sorted([three, four]))}
        assert tuple(sorted([five, six])) not in seen
        # Deterministic ordering, no ranking field of any kind.
        orderings = [c.conflicting_signal_ids for c in conflicts]
        assert orderings == sorted(orderings)
        session.close(); engine.dispose()

    def test_section_8_existing_distinct_disposition_also_conflicts(self):
        """All prior CASE 1-8 tests happen to use an existing SAME
        disposition as the conflicting fact - this explicitly proves an
        existing DISTINCT disposition conflicts identically via partial
        overlap, confirming decision-independence on the EXISTING side too
        (the API itself has no 'proposed decision' parameter at all - the
        guard is decision-agnostic on the proposed side by construction)."""
        engine, session = make_session()
        a, b, c = _signals(session, 3)
        record_signal_group_disposition(session, signal_ids=[a, b], decision="DISTINCT", reviewer="human:x", reason="r")
        session.commit()
        conflicts = find_signal_disposition_conflicts(session, signal_ids=[b, c])
        assert len(conflicts) == 1
        assert conflicts[0].relation == PARTIAL_OVERLAP
        assert conflicts[0].conflicting_decision == "DISTINCT"
        session.close(); engine.dispose()


class TestNoWritePathImports:
    def test_no_try_except_anywhere_in_module_ast(self):
        import app.services.signal_disposition_conflicts as module

        tree = ast.parse(inspect_module.getsource(module))
        try_nodes = [node for node in ast.walk(tree) if isinstance(node, ast.Try)]
        assert try_nodes == []

    def test_never_calls_session_write_methods_ast(self):
        """Precisely scoped to `session.<write-method>(...)` (checking the
        called object is literally the `session` parameter, not any
        attribute) - a naive attribute-name-only scan would false-positive
        on `set.add()` (used internally to build a plain Python member-set
        dict, nothing to do with the ORM session), exactly the same class
        of collision D4D4's own TestInformationFirewall already documented
        for `Signal.status` vs `SignalDispositionStatus.status`."""
        import app.services.signal_disposition_conflicts as module

        tree = ast.parse(inspect_module.getsource(module))
        forbidden_calls = {"add", "flush", "commit", "delete", "rollback"}
        session_write_calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "session"
            and node.func.attr in forbidden_calls
        }
        assert session_write_calls == set()
