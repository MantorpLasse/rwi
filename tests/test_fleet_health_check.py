"""Tests for app.services.fleet_health_check (FHC2 read-only DB adapter).

Every test builds an isolated, disposable SQLite database - the real
data/runway_safe.db is never opened here."""
from __future__ import annotations

import ast
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    Airport,
    Installation,
    InstallationAssertionLink,
    PhysicalInstallationIdentity,
    ReviewerAction,
    Runway,
    RunwayEnd,
    Signal,
    Source,
    SourceAssertion,
)
from app.services import fleet_health_check as fhc
from app.services.fleet_health_rules import RULE_IDS
from app.services.reviewer_action_persistence import get_latest_reviewer_action

MODULE_PATH = Path(fhc.__file__)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def session(tmp_path):
    db = tmp_path / "fhc2.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def _airport(session, id, name="Test Airport", country="XX", iata=None, icao=None, faa=None):
    a = Airport(id=id, name=name, country=country, iata_code=iata, icao_code=icao, faa_code=faa)
    session.add(a)
    return a


def _runway(session, id, airport_id, designation="01/19"):
    r = Runway(id=id, airport_id=airport_id, designation=designation)
    session.add(r)
    return r


def _runway_end(session, id, runway_id, designation="1"):
    e = RunwayEnd(id=id, runway_id=runway_id, designation=designation)
    session.add(e)
    return e


def _installation(session, id, airport_id, runway_id=None, install_year=None, replacement_year=None):
    i = Installation(
        id=id, airport_id=airport_id, runway_id=runway_id,
        install_year=install_year, replacement_year=replacement_year,
    )
    session.add(i)
    return i


def _signal(
    session, id, airport_id, runway_id=None, title="Signal", category="new_installation",
    confidence="high", source_id=None, construction_start=None, completion_date=None,
    published=True,
):
    s = Signal(
        id=id, airport_id=airport_id, runway_id=runway_id, title=title, category=category,
        confidence=confidence, source_id=source_id, construction_start=construction_start,
        completion_date=completion_date, published=published,
    )
    session.add(s)
    return s


def _source(session, id, title="Source", source_type="report"):
    s = Source(id=id, title=title, source_type=source_type)
    session.add(s)
    return s


def _source_assertion(
    session, id, source_id, airport_id=None, signal_id=None,
    assertion_type="project_construction", source_record_identifier=None,
):
    sa = SourceAssertion(
        id=id, source_id=source_id, airport_id=airport_id, signal_id=signal_id,
        assertion_type=assertion_type,
        source_record_identifier=source_record_identifier or f"rec-{id}",
    )
    session.add(sa)
    return sa


def _reviewer_action(
    session, id, source_assertion_id, action, duplicate_of_signal_id=None,
    reconciliation_fingerprint=None,
):
    ra = ReviewerAction(
        id=id, source_assertion_id=source_assertion_id, action=action, reason="r",
        reviewer="tester", duplicate_of_signal_id=duplicate_of_signal_id,
        reconciliation_fingerprint=reconciliation_fingerprint,
    )
    session.add(ra)
    return ra


def _physical_installation_identity(session, id, airport_id, runway_id=None, runway_end_id=None):
    p = PhysicalInstallationIdentity(
        id=id, airport_id=airport_id, runway_id=runway_id, runway_end_id=runway_end_id,
    )
    session.add(p)
    return p


def _row_counts(session) -> "dict[str, int]":
    return {
        "airports": session.query(Airport).count(),
        "runways": session.query(Runway).count(),
        "runway_ends": session.query(RunwayEnd).count(),
        "installations": session.query(Installation).count(),
        "signals": session.query(Signal).count(),
        "sources": session.query(Source).count(),
        "source_assertions": session.query(SourceAssertion).count(),
        "reviewer_actions": session.query(ReviewerAction).count(),
        "physical_installation_identities": session.query(PhysicalInstallationIdentity).count(),
    }


def _all_identifiers(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name)
    return names


# ---------------------------------------------------------------------------
# Purity / read-only guarantee
# ---------------------------------------------------------------------------


class TestReadOnlyGuarantee:
    def test_no_write_identifiers_via_ast(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        identifiers = _all_identifiers(tree)
        for banned in ("add", "flush", "commit", "delete", "add_all", "merge", "bulk_save_objects"):
            assert banned not in identifiers

    def test_no_forbidden_imports_via_ast(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.append(node.module)
        forbidden_prefixes = ("os", "pathlib", "socket", "random", "uuid", "requests", "httpx")
        for name in found:
            for bad in forbidden_prefixes:
                assert not name.startswith(bad)

    def test_run_leaves_row_counts_unchanged(self, session):
        _airport(session, 1)
        session.flush()
        before = _row_counts(session)
        fhc.run_fleet_hard_invariant_check(session)
        after = _row_counts(session)
        assert before == after

    def test_run_leaves_transaction_clean_no_pending_changes(self, session):
        _airport(session, 1)
        session.flush()
        fhc.run_fleet_hard_invariant_check(session)
        assert not session.dirty
        assert not session.new
        assert not session.deleted

    def test_run_never_autoflushes_callers_pending_mutation(self, session):
        """Review-checkpoint regression test for a real defect: SQLAlchemy's
        default autoflush=True would otherwise silently push a caller's own
        pending, uncommitted edit to the database the instant this adapter's
        first SELECT ran - a hidden write side effect of a call that is
        supposed to be strictly read-only. Fixed via `session.no_autoflush`
        in build_fleet_hard_invariant_snapshot(); this test proves the fix
        holds, not just that queries "work"."""
        _airport(session, 1, name="Original")
        session.flush()
        session.commit()

        airport = session.get(Airport, 1)
        airport.name = "PENDING EDIT - MUST NOT BE FLUSHED BY A READ-ONLY CALL"
        assert airport in session.dirty

        fhc.run_fleet_hard_invariant_check(session)

        # The pending edit must remain exactly pending - never flushed.
        assert airport in session.dirty
        raw_value = session.execute(text("SELECT name FROM airports WHERE id = 1")).scalar_one()
        assert raw_value == "Original"

        session.rollback()
        raw_value_after_rollback = session.execute(
            text("SELECT name FROM airports WHERE id = 1")
        ).scalar_one()
        assert raw_value_after_rollback == "Original"

    def test_run_reflects_callers_own_pending_in_session_state(self, session):
        """The no_autoflush fix must not make the health check blind to the
        caller's own current in-session view - it should see the caller's
        pending edit (the normal Session identity-map/unit-of-work
        semantic), it just must never WRITE it out as a side effect."""
        _airport(session, 1, name="Original", icao="AAAA")
        session.flush()
        session.commit()

        airport = session.get(Airport, 1)
        airport.icao_code = "BBBB"

        snapshot = fhc.build_fleet_hard_invariant_snapshot(session)
        fact = next(f for f in snapshot.airport_codes if f.airport_id == 1)
        assert fact.icao_code == "BBBB"  # reflects current in-session state
        session.rollback()

    def test_query_failure_raises_clear_exception_not_empty_result(self, tmp_path):
        """A broken/partial schema must fail loud, never silently become a
        falsely-healthy empty finding tuple."""
        db = tmp_path / "missing_table.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE physical_installation_identities"))
        with Session(engine) as s:
            with pytest.raises(Exception):
                fhc.run_fleet_hard_invariant_check(s)
            # No mutation occurred despite the failed read.
            assert s.query(Airport).count() == 0
        engine.dispose()


# ---------------------------------------------------------------------------
# Healthy DB fixture -> zero findings
# ---------------------------------------------------------------------------


def _healthy_fleet(session):
    _airport(session, 1, name="Arlanda", country="Sweden", iata="ARN", icao="ESSA")
    _airport(session, 2, name="Narita", country="Japan", iata="NRT", icao="RJAA")
    _airport(session, 3, name="Reference Only", country="XX")  # zero-runway airport
    _runway(session, 10, airport_id=1, designation="01/19")
    _runway_end(session, 100, runway_id=10, designation="1")
    _runway_end(session, 101, runway_id=10, designation="19")
    _installation(session, 1000, airport_id=1, runway_id=10, install_year=2015, replacement_year=2020)
    _installation(session, 1001, airport_id=2)  # airport-only, no runway link
    _physical_installation_identity(session, 2000, airport_id=1, runway_id=10)
    _source(session, 500, title="Src A")
    _signal(session, 3000, airport_id=1, runway_id=10, construction_start=date(2024, 1, 1), completion_date=date(2024, 6, 1), source_id=500)
    _signal(session, 3001, airport_id=2, source_id=500)  # legacy, airport-only
    _source_assertion(session, 4000, source_id=500, airport_id=1, signal_id=3000)
    _source_assertion(session, 4001, source_id=500, airport_id=None, signal_id=None)  # unreviewed, no airport
    _reviewer_action(session, 5000, source_assertion_id=4000, action="APPROVE_SIGNAL")
    session.flush()


class TestHealthyFixture:
    def test_zero_findings(self, session):
        _healthy_fleet(session)
        findings = fhc.run_fleet_hard_invariant_check(session)
        assert findings == ()

    def test_zero_findings_stable_across_repeated_calls(self, session):
        _healthy_fleet(session)
        first = fhc.run_fleet_hard_invariant_check(session)
        second = fhc.run_fleet_hard_invariant_check(session)
        assert first == second == ()


# ---------------------------------------------------------------------------
# Per-rule DB-backed violations - exactly one true violation per rule
# ---------------------------------------------------------------------------


class TestPerRuleDbBackedViolations:
    def test_fh_a2_duplicate_icao(self, session):
        _airport(session, 1, name="A1", country="Sweden", icao="ESSA")
        _airport(session, 2, name="A2", country="Norway", icao="ESSA")
        session.flush()
        findings = fhc.run_fleet_hard_invariant_check(session)
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == "FH-A2"
        assert f.entity_ids == (1, 2)
        assert f.structured_evidence["code_value"] == "ESSA"

    def test_fh_b1_wrong_end_count(self, session):
        _airport(session, 1)
        _runway(session, 10, airport_id=1)
        _runway_end(session, 100, runway_id=10, designation="1")
        session.flush()
        findings = fhc.run_fleet_hard_invariant_check(session)
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == "FH-B1"
        assert f.entity_ids == (10,)
        assert f.structured_evidence["runway_end_count"] == 1

    def test_fh_b2_duplicate_designation(self, session):
        _airport(session, 1)
        _runway(session, 10, airport_id=1, designation="01/19")
        _runway_end(session, 100, runway_id=10, designation="1")
        _runway_end(session, 101, runway_id=10, designation="19")
        _runway(session, 11, airport_id=1, designation="01/19")
        _runway_end(session, 110, runway_id=11, designation="1")
        _runway_end(session, 111, runway_id=11, designation="19")
        session.flush()
        findings = fhc.run_fleet_hard_invariant_check(session)
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == "FH-B2"
        assert f.entity_ids == (10, 11)

    def test_fh_c1_replacement_before_install(self, session):
        _airport(session, 1)
        _installation(session, 100, airport_id=1, install_year=2020, replacement_year=2019)
        session.flush()
        findings = fhc.run_fleet_hard_invariant_check(session)
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == "FH-C1"
        assert f.entity_ids == (100,)

    def test_fh_c2_installation_runway_airport_mismatch(self, session):
        _airport(session, 1)
        _airport(session, 2)
        _runway(session, 10, airport_id=2)
        _runway_end(session, 100, runway_id=10, designation="1")
        _runway_end(session, 101, runway_id=10, designation="19")
        _installation(session, 100, airport_id=1, runway_id=10)
        session.flush()
        findings = fhc.run_fleet_hard_invariant_check(session)
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == "FH-C2"
        assert f.entity_ids == (100,)
        assert f.structured_evidence["runway_airport_id"] == 2

    def test_fh_c5_identity_runway_airport_mismatch(self, session):
        _airport(session, 1)
        _airport(session, 2)
        _runway(session, 10, airport_id=2)
        _runway_end(session, 100, runway_id=10, designation="1")
        _runway_end(session, 101, runway_id=10, designation="19")
        _physical_installation_identity(session, 200, airport_id=1, runway_id=10)
        session.flush()
        findings = fhc.run_fleet_hard_invariant_check(session)
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == "FH-C5"
        assert f.entity_ids == (200,)

    def test_fh_c5_identity_runway_end_airport_mismatch(self, session):
        _airport(session, 1)
        _airport(session, 2)
        _runway(session, 10, airport_id=2)
        _runway_end(session, 100, runway_id=10, designation="1")
        _runway_end(session, 101, runway_id=10, designation="19")
        _physical_installation_identity(session, 200, airport_id=1, runway_end_id=100)
        session.flush()
        findings = fhc.run_fleet_hard_invariant_check(session)
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == "FH-C5"
        assert f.structured_evidence["runway_end_id"] == 100

    def test_fh_d1_signal_runway_airport_mismatch(self, session):
        _airport(session, 1)
        _airport(session, 2)
        _runway(session, 10, airport_id=2)
        _runway_end(session, 100, runway_id=10, designation="1")
        _runway_end(session, 101, runway_id=10, designation="19")
        _signal(session, 3000, airport_id=1, runway_id=10)
        session.flush()
        findings = fhc.run_fleet_hard_invariant_check(session)
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == "FH-D1"
        assert f.entity_ids == (3000,)

    def test_fh_d2_assertion_signal_airport_mismatch(self, session):
        _airport(session, 1)
        _airport(session, 2)
        _source(session, 500)
        _signal(session, 3000, airport_id=2)
        _source_assertion(session, 4000, source_id=500, airport_id=1, signal_id=3000)
        session.flush()
        findings = fhc.run_fleet_hard_invariant_check(session)
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == "FH-D2"
        assert f.entity_ids == (4000,)

    def test_fh_e3_impossible_chronology(self, session):
        _airport(session, 1)
        _signal(session, 3000, airport_id=1, construction_start=date(2024, 6, 1), completion_date=date(2024, 1, 1))
        session.flush()
        findings = fhc.run_fleet_hard_invariant_check(session)
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == "FH-E3"
        assert f.entity_ids == (3000,)

    def test_fh_g2_mark_duplicate_target_mismatch(self, session):
        _airport(session, 1)
        _source(session, 500)
        _signal(session, 3000, airport_id=1)
        _signal(session, 3001, airport_id=1)
        _source_assertion(session, 4000, source_id=500, airport_id=1, signal_id=3000)
        _reviewer_action(session, 5000, source_assertion_id=4000, action="MARK_DUPLICATE", duplicate_of_signal_id=3001)
        session.flush()
        findings = fhc.run_fleet_hard_invariant_check(session)
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == "FH-G2"
        assert f.entity_ids == (4000,)

    def test_fh_g3_defer_with_signal_set(self, session):
        _airport(session, 1)
        _source(session, 500)
        _signal(session, 3000, airport_id=1)
        _source_assertion(session, 4000, source_id=500, airport_id=1, signal_id=3000)
        _reviewer_action(session, 5000, source_assertion_id=4000, action="DEFER")
        session.flush()
        findings = fhc.run_fleet_hard_invariant_check(session)
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == "FH-G3"
        assert f.entity_ids == (4000,)

    def test_all_11_rules_fire_together_when_all_violated(self, session):
        # Airport identity collision
        _airport(session, 1, icao="ESSA")
        _airport(session, 2, icao="ESSA")
        # Runway structure
        _runway(session, 10, airport_id=1, designation="01/19")
        _runway_end(session, 100, runway_id=10, designation="1")
        _runway(session, 11, airport_id=1, designation="10/28")
        _runway(session, 12, airport_id=1, designation="10/28")
        # Installation
        _installation(session, 100, airport_id=1, install_year=2020, replacement_year=2019)
        _runway(session, 13, airport_id=2, designation="05/23")
        _installation(session, 101, airport_id=1, runway_id=13)
        # PhysicalInstallationIdentity
        _physical_installation_identity(session, 200, airport_id=1, runway_id=13)
        # Signal
        _signal(session, 3000, airport_id=1, runway_id=13)
        _signal(session, 3001, airport_id=1, construction_start=date(2024, 6, 1), completion_date=date(2024, 1, 1))
        # SourceAssertion/Signal mismatch
        _source(session, 500)
        _signal(session, 3002, airport_id=2)
        _source_assertion(session, 4000, source_id=500, airport_id=1, signal_id=3002)
        # Governance
        _signal(session, 3003, airport_id=1)
        _signal(session, 3004, airport_id=1)
        _source_assertion(session, 4001, source_id=500, airport_id=1, signal_id=3003)
        _reviewer_action(session, 5000, source_assertion_id=4001, action="MARK_DUPLICATE", duplicate_of_signal_id=3004)
        _source_assertion(session, 4002, source_id=500, airport_id=1, signal_id=3003)
        _reviewer_action(session, 5001, source_assertion_id=4002, action="DEFER")
        session.flush()
        findings = fhc.run_fleet_hard_invariant_check(session)
        assert {f.rule_id for f in findings} == set(RULE_IDS)


# ---------------------------------------------------------------------------
# Join fan-out attacks (mandatory, per the FHC1 review defect)
# ---------------------------------------------------------------------------


class TestJoinFanOutAttacks:
    def test_multiple_runway_ends_collapse_to_one_b1_fact_not_multiple(self, session):
        _airport(session, 1)
        _runway(session, 10, airport_id=1, designation="01/19")
        _runway_end(session, 100, runway_id=10, designation="1")
        _runway_end(session, 101, runway_id=10, designation="19")
        _runway_end(session, 102, runway_id=10, designation="X")  # a 3rd end - genuine violation
        session.flush()
        snapshot = fhc.build_fleet_hard_invariant_snapshot(session)
        b1_facts = [f for f in snapshot.runway_end_counts if f.runway_id == 10]
        assert len(b1_facts) == 1
        assert b1_facts[0].runway_end_count == 3

    def test_multiple_source_assertions_on_same_signal_produce_independent_d2_facts_not_fanout_of_d1(self, session):
        # Multiple SourceAssertions pointing at the same Signal is a genuine,
        # independent D2 fact per assertion - NOT a fan-out bug. But it must
        # never multiply the Signal's own D1 fact.
        _airport(session, 1)
        _source(session, 500)
        _signal(session, 3000, airport_id=1)
        _source_assertion(session, 4000, source_id=500, airport_id=1, signal_id=3000)
        _source_assertion(session, 4001, source_id=500, airport_id=1, signal_id=3000)
        _source_assertion(session, 4002, source_id=500, airport_id=1, signal_id=3000)
        session.flush()
        snapshot = fhc.build_fleet_hard_invariant_snapshot(session)
        d1_facts = [f for f in snapshot.signal_runway_airports if f.signal_id == 3000]
        assert len(d1_facts) == 1
        d2_facts = [f for f in snapshot.source_assertion_signal_airports if f.signal_id == 3000]
        assert len(d2_facts) == 3  # genuinely 3 independent assertions, correct
        assert fhc.run_fleet_hard_invariant_check(session) == ()

    def test_multiple_reviewer_actions_reduce_to_single_latest_governance_fact(self, session):
        _airport(session, 1)
        _source(session, 500)
        _signal(session, 3000, airport_id=1)
        _source_assertion(session, 4000, source_id=500, airport_id=1, signal_id=None)
        _reviewer_action(session, 5000, source_assertion_id=4000, action="APPROVE_SIGNAL")
        _reviewer_action(session, 5001, source_assertion_id=4000, action="DEFER")
        _reviewer_action(session, 5002, source_assertion_id=4000, action="APPROVE_SIGNAL")
        session.flush()
        snapshot = fhc.build_fleet_hard_invariant_snapshot(session)
        gov_facts = [f for f in snapshot.source_assertion_governance if f.assertion_id == 4000]
        assert len(gov_facts) == 1
        assert gov_facts[0].latest_action == "APPROVE_SIGNAL"  # id=5002, highest id, wins

    def test_installation_with_no_runway_and_unrelated_multiple_installations_no_fanout(self, session):
        _airport(session, 1)
        _installation(session, 100, airport_id=1)
        _installation(session, 101, airport_id=1)
        _installation(session, 102, airport_id=1)
        session.flush()
        snapshot = fhc.build_fleet_hard_invariant_snapshot(session)
        assert len(snapshot.installation_years) == 3
        assert len(snapshot.installation_runway_airports) == 3
        assert fhc.run_fleet_hard_invariant_check(session) == ()

    def test_physical_installation_identity_with_both_links_absent_no_fanout(self, session):
        _airport(session, 1)
        _physical_installation_identity(session, 200, airport_id=1)
        session.flush()
        snapshot = fhc.build_fleet_hard_invariant_snapshot(session)
        assert len(snapshot.physical_installation_identity_airports) == 1
        assert fhc.run_fleet_hard_invariant_check(session) == ()

    def test_multiple_installation_assertion_links_do_not_fanout_c5(self, session):
        # C5's query never touches InstallationAssertionLink at all, but this
        # is exactly the mission-named attack: one PhysicalInstallationIdentity
        # targeted by THREE separate append-only InstallationAssertionLink
        # rows (a genuine one-to-many relationship in this schema) must still
        # produce exactly one FH-C5 fact.
        _airport(session, 1)
        _source(session, 500)
        _physical_installation_identity(session, 200, airport_id=1)
        for i, assertion_id in enumerate((4000, 4001, 4002), start=1):
            _source_assertion(session, assertion_id, source_id=500, airport_id=1)
        session.flush()
        for i, assertion_id in enumerate((4000, 4001, 4002), start=1):
            session.add(
                InstallationAssertionLink(
                    id=i, assertion_id=assertion_id, physical_installation_id=200,
                    outcome="SAME_PHYSICAL_INSTALLATION", reason="r", actor="t",
                )
            )
        session.flush()
        snapshot = fhc.build_fleet_hard_invariant_snapshot(session)
        c5_facts = [f for f in snapshot.physical_installation_identity_airports if f.identity_id == 200]
        assert len(c5_facts) == 1
        assert fhc.run_fleet_hard_invariant_check(session) == ()


# ---------------------------------------------------------------------------
# Legacy realistic fixture -> zero findings solely from legacy characteristics
# ---------------------------------------------------------------------------


class TestLegacyRealisticFixture:
    def test_zero_findings(self, session):
        # Airport with zero runways
        _airport(session, 1, name="No Runway Intl", country="XX")
        # Multiple installation rows, both airport-only (real 18-airport pattern)
        _airport(session, 2, name="Multi Install", country="XX")
        _installation(session, 100, airport_id=2)
        _installation(session, 101, airport_id=2, install_year=2014)
        # Legacy Signal: source_id set, no supporting SourceAssertion, free-text fields
        _source(session, 500, title="Legacy grant record")
        _signal(
            session, 3000, airport_id=2, source_id=500, title="USAspending grant - $9.2M, FY2025",
            category="replacement", confidence="programmed",
        )
        # SourceAssertion with signal_id NULL, airport_id NULL, unreviewed
        _source_assertion(session, 4000, source_id=500, airport_id=None, signal_id=None)
        # No physical-installation link at all
        session.flush()
        findings = fhc.run_fleet_hard_invariant_check(session)
        assert findings == ()


# ---------------------------------------------------------------------------
# Governed fixture (#222/#67-like, no provider-specific constants)
# ---------------------------------------------------------------------------


class TestGovernedFixture:
    def test_clean_governed_state_no_g2_g3_finding(self, session):
        _airport(session, 1, name="Governed Airport", country="XX")
        _source(session, 500)
        _signal(session, 3000, airport_id=1)  # the signal this assertion produced
        _source_assertion(session, 4000, source_id=500, airport_id=1, signal_id=3000)
        _reviewer_action(session, 5000, source_assertion_id=4000, action="APPROVE_SIGNAL")
        _reviewer_action(
            session, 5001, source_assertion_id=4000, action="MARK_DUPLICATE",
            duplicate_of_signal_id=3000,  # matches assertion.signal_id - consistent
        )
        session.flush()
        assert fhc.run_fleet_hard_invariant_check(session) == ()

    def test_broken_target_link_mismatch_fires_g2(self, session):
        _airport(session, 1)
        _source(session, 500)
        _signal(session, 3000, airport_id=1)
        _signal(session, 3001, airport_id=1)
        _source_assertion(session, 4000, source_id=500, airport_id=1, signal_id=3000)
        _reviewer_action(session, 5000, source_assertion_id=4000, action="APPROVE_SIGNAL")
        _reviewer_action(
            session, 5001, source_assertion_id=4000, action="MARK_DUPLICATE",
            duplicate_of_signal_id=3001,  # mismatch vs assertion.signal_id=3000
        )
        session.flush()
        findings = fhc.run_fleet_hard_invariant_check(session)
        assert len(findings) == 1
        assert findings[0].rule_id == "FH-G2"

    def test_terminal_action_link_inconsistency_fires_g3(self, session):
        _airport(session, 1)
        _source(session, 500)
        _signal(session, 3000, airport_id=1)
        _source_assertion(session, 4000, source_id=500, airport_id=1, signal_id=3000)
        _reviewer_action(session, 5000, source_assertion_id=4000, action="REJECT_SIGNAL")
        session.flush()
        findings = fhc.run_fleet_hard_invariant_check(session)
        assert len(findings) == 1
        assert findings[0].rule_id == "FH-G3"


# ---------------------------------------------------------------------------
# International fixture
# ---------------------------------------------------------------------------


class TestInternationalFixture:
    def test_synthetic_non_us_fleet_behaves_identically(self, session):
        _airport(session, 1, name="Arlanda", country="Sweden", iata="ARN", icao="ESSA")
        _airport(session, 2, name="Narita", country="Japan", iata="NRT", icao="RJAA")
        _airport(session, 3, name="Heathrow", country="United Kingdom", iata="LHR", icao="EGLL")
        _runway(session, 10, airport_id=1, designation="01L/19R")
        _runway_end(session, 100, runway_id=10, designation="01L")
        _runway_end(session, 101, runway_id=10, designation="19R")
        _installation(session, 1000, airport_id=2)
        session.flush()
        assert fhc.run_fleet_hard_invariant_check(session) == ()

    def test_no_faa_or_usd_identifiers_in_module_source(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for banned in ("FAA", "USAspending", "USD", "Runway Safe", "MSP", "MAC"):
            assert banned not in source


# ---------------------------------------------------------------------------
# Latest ReviewerAction equivalence
# ---------------------------------------------------------------------------


class TestLatestReviewerActionEquivalence:
    def test_batched_result_matches_get_latest_reviewer_action_per_assertion(self, session):
        _airport(session, 1)
        _source(session, 500)
        for assertion_id in (4000, 4001, 4002):
            _source_assertion(session, assertion_id, source_id=500, airport_id=1)
        _reviewer_action(session, 5000, source_assertion_id=4000, action="APPROVE_SIGNAL")
        _reviewer_action(session, 5001, source_assertion_id=4000, action="DEFER")
        _reviewer_action(session, 5002, source_assertion_id=4001, action="NEEDS_MORE_EVIDENCE")
        # 4002 has no ReviewerAction at all
        session.flush()

        snapshot = fhc.build_fleet_hard_invariant_snapshot(session)
        facts_by_assertion = {f.assertion_id: f for f in snapshot.source_assertion_governance}

        for assertion_id in (4000, 4001):
            expected = get_latest_reviewer_action(session, assertion_id)
            fact = facts_by_assertion[assertion_id]
            assert fact.latest_action == expected.action
            assert fact.latest_action_duplicate_of_signal_id == expected.duplicate_of_signal_id

        assert get_latest_reviewer_action(session, 4002) is None
        assert 4002 not in facts_by_assertion

    def test_identical_created_at_timestamps_break_tie_by_id_matching_helper_exactly(self, session):
        # A prior version of this test suite only ever exercised distinct,
        # implicit datetime.now(UTC) timestamps per action - never actually
        # stressing the (created_at DESC, id DESC) tiebreak's second clause.
        # Force two ReviewerActions to share the EXACT same created_at.
        _airport(session, 1)
        _source(session, 500)
        _source_assertion(session, 4000, source_id=500, airport_id=1)
        same_ts = datetime(2024, 1, 1, tzinfo=UTC)
        ra1 = ReviewerAction(
            id=5000, source_assertion_id=4000, action="APPROVE_SIGNAL",
            reason="r", reviewer="t", created_at=same_ts,
        )
        ra2 = ReviewerAction(
            id=5001, source_assertion_id=4000, action="DEFER",
            reason="r", reviewer="t", created_at=same_ts,
        )
        session.add(ra1)
        session.add(ra2)
        session.flush()

        expected = get_latest_reviewer_action(session, 4000)
        assert expected.id == 5001  # higher id wins the tie

        snapshot = fhc.build_fleet_hard_invariant_snapshot(session)
        fact = next(f for f in snapshot.source_assertion_governance if f.assertion_id == 4000)
        assert fact.latest_action == expected.action == "DEFER"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_repeated_calls_produce_equal_snapshot_and_findings(self, session):
        _airport(session, 1, icao="ESSA")
        _airport(session, 2, icao="ESSA")
        session.flush()
        snap1 = fhc.build_fleet_hard_invariant_snapshot(session)
        snap2 = fhc.build_fleet_hard_invariant_snapshot(session)
        assert snap1 == snap2
        assert fhc.run_fleet_hard_invariant_check(session) == fhc.run_fleet_hard_invariant_check(session)

    def test_insertion_order_does_not_affect_output(self, tmp_path):
        db_a = tmp_path / "order_a.db"
        engine_a = create_engine(f"sqlite:///{db_a}")
        Base.metadata.create_all(engine_a)
        with Session(engine_a) as s:
            _airport(s, 2, icao="ESSA")
            _airport(s, 1, icao="ESSA")
            s.flush()
            findings_a = fhc.run_fleet_hard_invariant_check(s)
        engine_a.dispose()

        db_b = tmp_path / "order_b.db"
        engine_b = create_engine(f"sqlite:///{db_b}")
        Base.metadata.create_all(engine_b)
        with Session(engine_b) as s:
            _airport(s, 1, icao="ESSA")
            _airport(s, 2, icao="ESSA")
            s.flush()
            findings_b = fhc.run_fleet_hard_invariant_check(s)
        engine_b.dispose()

        assert findings_a == findings_b


# ---------------------------------------------------------------------------
# Query-count / N+1 behavior
# ---------------------------------------------------------------------------


class TestQueryCount:
    """Review-checkpoint correction: the original implementation report
    claimed "exactly 10 SQL statements, always." That was only true for a
    fixture with zero ReviewerAction rows - _build_source_assertion_governance
    short-circuits to a single query when no governance data exists at all,
    but issues a SECOND bounded query (the signal_id lookup) whenever at
    least one ReviewerAction exists anywhere in the database, including the
    real one (which has 2). The real count is therefore 10 OR 11 depending
    on whether any ReviewerAction exists - never proportional to entity
    count either way. Both cases are tested here, at two different scales
    each, to prove the count is genuinely bounded, not merely small by
    coincidence in the original single-scenario test.
    """

    def _count_statements(self, tmp_path, n_airports: int, *, with_reviewer_action: bool) -> int:
        db = tmp_path / f"qc_{n_airports}_{with_reviewer_action}.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        with Session(engine) as s:
            for i in range(1, n_airports + 1):
                _airport(s, i, name=f"A{i}", country="XX")
                _runway(s, i, airport_id=i, designation="01/19")
                _runway_end(s, i * 10, runway_id=i, designation="1")
                _runway_end(s, i * 10 + 1, runway_id=i, designation="19")
                _installation(s, i, airport_id=i)
                _signal(s, i, airport_id=i)
            if with_reviewer_action:
                _source(s, 500)
                _source_assertion(s, 9000, source_id=500, airport_id=1)
                _reviewer_action(s, 9000, source_assertion_id=9000, action="APPROVE_SIGNAL")
            s.flush()

            counter = {"n": 0}

            def _on_execute(*_args, **_kwargs):
                counter["n"] += 1

            event.listen(engine, "before_cursor_execute", _on_execute)
            try:
                fhc.run_fleet_hard_invariant_check(s)
            finally:
                event.remove(engine, "before_cursor_execute", _on_execute)
        engine.dispose()
        return counter["n"]

    def test_bounded_query_count_without_any_reviewer_action(self, tmp_path):
        small = self._count_statements(tmp_path, 3, with_reviewer_action=False)
        large = self._count_statements(tmp_path, 30, with_reviewer_action=False)
        assert small == large == 10

    def test_bounded_query_count_with_reviewer_action_present(self, tmp_path):
        small = self._count_statements(tmp_path, 3, with_reviewer_action=True)
        large = self._count_statements(tmp_path, 30, with_reviewer_action=True)
        assert small == large == 11


# ---------------------------------------------------------------------------
# Snapshot contract
# ---------------------------------------------------------------------------


class TestSnapshotContract:
    def test_snapshot_contains_only_fhc1_fact_dataclasses(self, session):
        _healthy_fleet(session)
        snapshot = fhc.build_fleet_hard_invariant_snapshot(session)
        for field_name in snapshot.__dataclass_fields__:
            value = getattr(snapshot, field_name)
            assert isinstance(value, tuple)

    def test_null_vs_empty_string_airport_codes_preserved(self, session):
        _airport(session, 1, iata=None, icao="", faa=None)
        session.flush()
        snapshot = fhc.build_fleet_hard_invariant_snapshot(session)
        fact = next(f for f in snapshot.airport_codes if f.airport_id == 1)
        assert fact.iata_code is None
        assert fact.icao_code == ""
        assert fact.faa_code is None
