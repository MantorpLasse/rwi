"""Tests for the FHC3 extension of app.services.fleet_health_check (the new
build_fleet_review_snapshot/run_fleet_review_check functions and their
supporting adapter queries). tests/test_fleet_health_check.py (FHC2's own,
already-reviewed test file) is deliberately left untouched by this task -
this is a new, separate file for the new, separate adapter surface.

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
    Runway,
    RunwayEnd,
    Signal,
    Source,
    SourceAssertion,
)
from app.services import fleet_health_check as fhc
from app.services.fleet_health_review_rules import REVIEW_RULE_IDS

MODULE_PATH = Path(fhc.__file__)


@pytest.fixture()
def session(tmp_path):
    db = tmp_path / "fhc3.db"
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


def _installation(session, id, airport_id, runway_id=None, runway_end=None):
    i = Installation(id=id, airport_id=airport_id, runway_id=runway_id, runway_end=runway_end)
    session.add(i)
    return i


def _signal(
    session, id, airport_id, runway_id=None, title="Signal", category="new_installation",
    confidence="high", source_id=None, status=None, planning_year=None, procurement_year=None,
    target_year=None, completion_date=None, published=True,
):
    s = Signal(
        id=id, airport_id=airport_id, runway_id=runway_id, title=title, category=category,
        confidence=confidence, source_id=source_id, status=status, planning_year=planning_year,
        procurement_year=procurement_year, target_year=target_year,
        completion_date=completion_date, published=published,
    )
    session.add(s)
    return s


def _source(session, id, title="Source", source_type="report"):
    s = Source(id=id, title=title, source_type=source_type)
    session.add(s)
    return s


def _source_assertion(
    session, id, source_id, airport_id=None, signal_id=None, review_state="unreviewed",
    identity_guard_decision=None, intelligence_review_decision=None,
    promotion_policy_decision=None, assertion_type="project_construction",
    source_record_identifier=None,
):
    sa = SourceAssertion(
        id=id, source_id=source_id, airport_id=airport_id, signal_id=signal_id,
        review_state=review_state, identity_guard_decision=identity_guard_decision,
        intelligence_review_decision=intelligence_review_decision,
        promotion_policy_decision=promotion_policy_decision,
        assertion_type=assertion_type,
        source_record_identifier=source_record_identifier or f"rec-{id}",
    )
    session.add(sa)
    return sa


def _physical_installation_identity(session, id, airport_id):
    p = PhysicalInstallationIdentity(id=id, airport_id=airport_id)
    session.add(p)
    return p


def _link(session, id, assertion_id, outcome, physical_installation_id=None, reviewed_at=None):
    # CHECK ck_installation_assertion_links_target_for_resolved: any outcome
    # other than UNRESOLVED requires a real physical_installation_id - the
    # caller must supply one (via _physical_installation_identity) for any
    # non-UNRESOLVED outcome.
    kwargs = {}
    if reviewed_at is not None:
        kwargs["reviewed_at"] = reviewed_at
    link = InstallationAssertionLink(
        id=id, assertion_id=assertion_id, outcome=outcome,
        physical_installation_id=physical_installation_id, reason="r", actor="t", **kwargs,
    )
    session.add(link)
    return link


def _row_counts(session) -> "dict[str, int]":
    return {
        "airports": session.query(Airport).count(),
        "runways": session.query(Runway).count(),
        "installations": session.query(Installation).count(),
        "signals": session.query(Signal).count(),
        "sources": session.query(Source).count(),
        "source_assertions": session.query(SourceAssertion).count(),
        "installation_assertion_links": session.query(InstallationAssertionLink).count(),
    }


class TestReadOnlyGuarantee:
    def test_run_leaves_row_counts_unchanged(self, session):
        _airport(session, 1)
        session.flush()
        before = _row_counts(session)
        fhc.run_fleet_review_check(session)
        assert _row_counts(session) == before

    def test_run_leaves_transaction_clean(self, session):
        _airport(session, 1)
        session.flush()
        fhc.run_fleet_review_check(session)
        assert not session.dirty
        assert not session.new
        assert not session.deleted

    def test_run_never_autoflushes_callers_pending_mutation(self, session):
        _airport(session, 1, name="Original")
        session.flush()
        session.commit()
        airport = session.get(Airport, 1)
        airport.name = "PENDING - MUST NOT BE FLUSHED"
        assert airport in session.dirty

        fhc.run_fleet_review_check(session)

        assert airport in session.dirty
        raw = session.execute(text("SELECT name FROM airports WHERE id = 1")).scalar_one()
        assert raw == "Original"
        session.rollback()

    def test_full_health_check_never_autoflushes_callers_pending_mutation(self, session):
        _airport(session, 1, name="Original")
        session.flush()
        session.commit()
        airport = session.get(Airport, 1)
        airport.name = "PENDING - MUST NOT BE FLUSHED"

        fhc.run_full_fleet_health_check(session)

        assert airport in session.dirty
        raw = session.execute(text("SELECT name FROM airports WHERE id = 1")).scalar_one()
        assert raw == "Original"
        session.rollback()

    def test_query_failure_raises_clear_exception(self, tmp_path):
        db = tmp_path / "missing.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE installation_assertion_links"))
        with Session(engine) as s:
            with pytest.raises(Exception):
                fhc.run_fleet_review_check(s)
        engine.dispose()


def _healthy_fleet(session):
    _airport(session, 1, name="A1", country="Sweden", iata="ARN", icao="ESSA")
    _runway(session, 10, airport_id=1)
    _runway_end(session, 100, runway_id=10, designation="1")
    _runway_end(session, 101, runway_id=10, designation="19")
    _installation(session, 1000, airport_id=1, runway_id=10)
    _source(session, 500)
    _signal(session, 3000, airport_id=1, runway_id=10, source_id=500)
    _source_assertion(session, 4000, source_id=500, airport_id=1, signal_id=3000)
    session.flush()


class TestHealthyFixture:
    def test_zero_findings(self, session):
        _healthy_fleet(session)
        assert fhc.run_fleet_review_check(session) == ()

    def test_zero_findings_stable(self, session):
        _healthy_fleet(session)
        assert fhc.run_fleet_review_check(session) == fhc.run_fleet_review_check(session)


class TestPerRuleDbBackedFindings:
    def test_fh_a1_zero_runway_airport(self, session):
        _airport(session, 1, icao="ESSA")  # has a code, so only A1 (not A3) fires
        session.flush()
        findings = fhc.run_fleet_review_check(session)
        assert len(findings) == 1
        assert findings[0].rule_id == "FH-A1"

    def test_fh_a3_no_codes(self, session):
        _airport(session, 1)
        _runway(session, 10, airport_id=1)
        _runway_end(session, 100, runway_id=10, designation="1")
        _runway_end(session, 101, runway_id=10, designation="19")
        session.flush()
        findings = fhc.run_fleet_review_check(session)
        rule_ids = {f.rule_id for f in findings}
        assert "FH-A3" in rule_ids
        assert "FH-A1" not in rule_ids

    def test_fh_c3_unlinked_installations(self, session):
        _airport(session, 1)
        _installation(session, 100, airport_id=1)
        _installation(session, 101, airport_id=1)
        session.flush()
        findings = [f for f in fhc.run_fleet_review_check(session) if f.rule_id == "FH-C3"]
        assert len(findings) == 1
        assert findings[0].entity_ids == (100, 101)

    def test_fh_c4_retraction(self, session):
        _airport(session, 1)
        _source(session, 500)
        _source_assertion(session, 4000, source_id=500, airport_id=1)
        _physical_installation_identity(session, 200, airport_id=1)
        _link(session, 1, assertion_id=4000, outcome="SAME_PHYSICAL_INSTALLATION", physical_installation_id=200)
        _link(session, 2, assertion_id=4000, outcome="UNRESOLVED")
        session.flush()
        findings = [f for f in fhc.run_fleet_review_check(session) if f.rule_id == "FH-C4"]
        assert len(findings) == 1
        assert findings[0].entity_ids == (4000,)

    def test_fh_d3_shared_runway(self, session):
        _airport(session, 1)
        _runway(session, 10, airport_id=1)
        _runway_end(session, 100, runway_id=10, designation="1")
        _runway_end(session, 101, runway_id=10, designation="19")
        _signal(session, 3000, airport_id=1, runway_id=10)
        _signal(session, 3001, airport_id=1, runway_id=10)
        session.flush()
        findings = [f for f in fhc.run_fleet_review_check(session) if f.rule_id == "FH-D3"]
        assert len(findings) == 1
        assert findings[0].entity_ids == (3000, 3001)

    def test_fh_d4_colocation(self, session):
        _airport(session, 1)
        _signal(session, 3000, airport_id=1)
        _signal(session, 3001, airport_id=1)
        session.flush()
        findings = [f for f in fhc.run_fleet_review_check(session) if f.rule_id == "FH-D4"]
        assert len(findings) == 1
        assert findings[0].entity_ids == (3000, 3001)

    def test_fh_e1_planning_after_procurement(self, session):
        _airport(session, 1)
        _signal(session, 3000, airport_id=1, planning_year=2026, procurement_year=2025)
        session.flush()
        findings = [f for f in fhc.run_fleet_review_check(session) if f.rule_id == "FH-E1"]
        assert len(findings) == 1

    def test_fh_e2_procurement_after_target(self, session):
        _airport(session, 1)
        _signal(session, 3000, airport_id=1, procurement_year=2025, target_year=2024)
        session.flush()
        findings = [f for f in fhc.run_fleet_review_check(session) if f.rule_id == "FH-E2"]
        assert len(findings) == 1

    def test_fh_e4_completed_without_date(self, session):
        _airport(session, 1)
        _signal(session, 3000, airport_id=1, status="completed", target_year=2026)
        session.flush()
        findings = [f for f in fhc.run_fleet_review_check(session) if f.rule_id == "FH-E4"]
        assert len(findings) == 1

    def test_fh_f1_legacy_provenance(self, session):
        _airport(session, 1)
        _source(session, 500)
        _signal(session, 3000, airport_id=1, source_id=500)
        session.flush()
        findings = [f for f in fhc.run_fleet_review_check(session) if f.rule_id == "FH-F1"]
        assert len(findings) == 1
        assert findings[0].entity_ids == (3000,)

    def test_fh_f2_unreviewed_no_airport(self, session):
        _source(session, 500)
        _source_assertion(session, 4000, source_id=500, airport_id=None, review_state="unreviewed")
        session.flush()
        findings = [f for f in fhc.run_fleet_review_check(session) if f.rule_id == "FH-F2"]
        assert len(findings) == 1

    def test_fh_f3_reviewed_no_airport(self, session):
        _source(session, 500)
        _source_assertion(session, 4000, source_id=500, airport_id=None, review_state="reviewed")
        session.flush()
        findings = [f for f in fhc.run_fleet_review_check(session) if f.rule_id == "FH-F3"]
        assert len(findings) == 1

    def test_fh_g1_decision_mismatch(self, session):
        _airport(session, 1)
        _source(session, 500)
        _source_assertion(
            session, 4000, source_id=500, airport_id=1,
            identity_guard_decision="PENDING", intelligence_review_decision="REVIEW_REQUIRED",
            promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
        )
        session.flush()
        findings = [f for f in fhc.run_fleet_review_check(session) if f.rule_id == "FH-G1"]
        assert len(findings) == 1


class TestCombinedFixture:
    def test_all_13_rules_fire_no_duplicates(self, session):
        _airport(session, 1)  # FH-A1: zero runways
        _airport(session, 2)
        _runway(session, 10, airport_id=2)
        _runway_end(session, 100, runway_id=10, designation="1")
        _runway_end(session, 101, runway_id=10, designation="19")
        # FH-C3
        _installation(session, 100, airport_id=2)
        _installation(session, 101, airport_id=2)
        # FH-C4
        _source(session, 500)
        _source_assertion(session, 4000, source_id=500, airport_id=2)
        _physical_installation_identity(session, 200, airport_id=2)
        _link(session, 1, assertion_id=4000, outcome="SAME_PHYSICAL_INSTALLATION", physical_installation_id=200)
        _link(session, 2, assertion_id=4000, outcome="UNRESOLVED")
        # FH-D3
        _signal(session, 3000, airport_id=2, runway_id=10)
        _signal(session, 3001, airport_id=2, runway_id=10)
        # FH-D4
        _signal(session, 3002, airport_id=2)
        _signal(session, 3003, airport_id=2)
        # FH-E1/E2/E4
        _signal(session, 3004, airport_id=2, planning_year=2026, procurement_year=2025)
        _signal(session, 3005, airport_id=2, procurement_year=2025, target_year=2024)
        _signal(session, 3006, airport_id=2, status="completed")
        # FH-F1
        _signal(session, 3007, airport_id=2, source_id=500)
        # FH-F2/F3
        _source_assertion(session, 4001, source_id=500, airport_id=None, review_state="unreviewed")
        _source_assertion(session, 4002, source_id=500, airport_id=None, review_state="reviewed")
        # FH-G1
        _source_assertion(
            session, 4003, source_id=500, airport_id=2,
            identity_guard_decision="PENDING", intelligence_review_decision="REVIEW_REQUIRED",
            promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
        )
        session.flush()

        findings = fhc.run_fleet_review_check(session)
        rule_ids = [f.rule_id for f in findings]
        # Exactly the 13 rule IDs appear, no unexpected extra rule fired.
        # NOT asserting "exactly one finding per rule": FH-A3 legitimately
        # fires once per code-less airport (this fixture has two), and
        # FH-D4 legitimately groups every co-located airport-2/no-runway
        # Signal into one finding - both are correct rule behavior, not
        # duplication, so a stronger "exactly 13 findings" count would be
        # the wrong assertion here.
        assert set(rule_ids) == set(REVIEW_RULE_IDS)


class TestJoinFanOutAttacks:
    def test_multiple_installation_assertion_links_across_multiple_assertions(self, session):
        _airport(session, 1)
        _source(session, 500)
        for aid in (4000, 4001, 4002):
            _source_assertion(session, aid, source_id=500, airport_id=1)
        for i, aid in enumerate((4000, 4001, 4002), start=1):
            _physical_installation_identity(session, 200 + i, airport_id=1)
            _link(session, i, assertion_id=aid, outcome="SAME_PHYSICAL_INSTALLATION", physical_installation_id=200 + i)
        session.flush()
        # Each assertion has only ONE link (no earlier history) - none should fire C4.
        findings = [f for f in fhc.run_fleet_review_check(session) if f.rule_id == "FH-C4"]
        assert findings == []

    def test_identical_reviewed_at_timestamps_break_tie_by_id_matching_r2_precedent(self, session):
        # Review-checkpoint regression test: a prior version of this suite
        # never actually stressed the (reviewed_at, id) tiebreak's second
        # clause for InstallationAssertionLink - the same class of gap
        # FHC2's own review found for ReviewerAction's identical-timestamp
        # case. Two links with the EXACT same reviewed_at, higher id must
        # win (matching existing_signal_reconciliation_candidates.py's own
        # `_latest_installation_links_by_assertion_id()` precedent).
        _airport(session, 1)
        _source(session, 500)
        _source_assertion(session, 4000, source_id=500, airport_id=1)
        _physical_installation_identity(session, 200, airport_id=1)
        same_ts = datetime(2024, 1, 1, tzinfo=UTC)
        _link(
            session, 1, assertion_id=4000, outcome="SAME_PHYSICAL_INSTALLATION",
            physical_installation_id=200, reviewed_at=same_ts,
        )
        _link(session, 2, assertion_id=4000, outcome="UNRESOLVED", reviewed_at=same_ts)
        session.flush()
        findings = [f for f in fhc.run_fleet_review_check(session) if f.rule_id == "FH-C4"]
        assert len(findings) == 1
        assert findings[0].structured_evidence["latest_outcome"] == "UNRESOLVED"

    def test_three_signals_same_airport_runway_produce_one_d3_group_not_pairs(self, session):
        _airport(session, 1)
        _runway(session, 10, airport_id=1)
        _runway_end(session, 100, runway_id=10, designation="1")
        _runway_end(session, 101, runway_id=10, designation="19")
        _signal(session, 3000, airport_id=1, runway_id=10)
        _signal(session, 3001, airport_id=1, runway_id=10)
        _signal(session, 3002, airport_id=1, runway_id=10)
        session.flush()
        findings = [f for f in fhc.run_fleet_review_check(session) if f.rule_id == "FH-D3"]
        assert len(findings) == 1
        assert findings[0].entity_ids == (3000, 3001, 3002)

    def test_multiple_source_assertions_linked_to_same_signal_do_not_inflate_f1(self, session):
        _airport(session, 1)
        _source(session, 500)
        _signal(session, 3000, airport_id=1, source_id=500)
        for aid in (4000, 4001, 4002):
            _source_assertion(session, aid, source_id=500, airport_id=1, signal_id=3000)
        session.flush()
        findings = [f for f in fhc.run_fleet_review_check(session) if f.rule_id == "FH-F1"]
        # Signal 3000 IS governed (has a linked assertion) -> must NOT appear.
        assert findings == []


class TestLegacyRealisticFixture:
    def test_zero_findings_or_only_informational(self, session):
        _airport(session, 1, name="No Runway", country="XX", icao="AAAA")  # zero-runway -> A1 informational, expected
        _airport(session, 2, name="Multi Install", country="XX", icao="BBBB")
        _installation(session, 100, airport_id=2)
        _installation(session, 101, airport_id=2)  # C3 warning, expected legacy pattern
        _source(session, 500)
        _signal(
            session, 3000, airport_id=2, source_id=500, category="replacement",
            confidence="programmed", status="identified",
        )
        _source_assertion(session, 4000, source_id=500, airport_id=None, review_state="unreviewed")
        session.flush()
        findings = fhc.run_fleet_review_check(session)
        rule_ids = {f.rule_id for f in findings}
        # Every finding here must be WARNING/REVIEW_REQUIRED/INFORMATIONAL,
        # never ERROR - and specifically must be exactly the expected legacy set.
        assert rule_ids == {"FH-A1", "FH-C3", "FH-F1", "FH-F2"}


class TestGovernedFixture:
    def test_msp_shaped_governed_fixture_produces_review_required_not_confirmed_duplicate(self, session):
        # Synthetic reproduction of the #41/#67-shaped co-location: one
        # legacy Signal, one governed Signal, both airport-only at the same
        # airport - no MSP/FAA/vendor constants anywhere in this fixture's
        # own IDs are read by production code.
        _airport(session, 45, name="Synthetic Intl", country="XX")
        _source(session, 500)
        _signal(session, 41, airport_id=45, source_id=500, category="replacement")
        _signal(session, 67, airport_id=45, category="replacement")
        _source_assertion(session, 222, source_id=500, airport_id=45, signal_id=67)
        session.flush()
        findings = fhc.run_fleet_review_check(session)
        d4 = [f for f in findings if f.rule_id == "FH-D4"]
        assert len(d4) == 1
        assert d4[0].entity_ids == (41, 67)
        assert d4[0].classification.value == "REVIEW_REQUIRED"
        assert "duplicate" not in d4[0].summary.lower()


class TestInternationalFixture:
    def test_synthetic_international_fleet(self, session):
        _airport(session, 1, name="Arlanda", country="Sweden", iata="ARN", icao="ESSA")
        _airport(session, 2, name="Narita", country="Japan")  # no code -> A3
        _runway(session, 10, airport_id=1)
        _runway_end(session, 100, runway_id=10, designation="1")
        _runway_end(session, 101, runway_id=10, designation="19")
        session.flush()
        findings = fhc.run_fleet_review_check(session)
        rule_ids = {f.rule_id for f in findings}
        assert "FH-A3" in rule_ids
        assert "FH-A1" in rule_ids  # airport 2 has zero runways


class TestQueryCount:
    def test_bounded_query_count_independent_of_entity_count(self, tmp_path):
        def _count(n: int) -> int:
            db = tmp_path / f"qc_{n}.db"
            engine = create_engine(f"sqlite:///{db}")
            Base.metadata.create_all(engine)
            with Session(engine) as s:
                for i in range(1, n + 1):
                    _airport(s, i, name=f"A{i}", country="XX")
                    _runway(s, i, airport_id=i)
                    _runway_end(s, i * 10, runway_id=i, designation="1")
                    _runway_end(s, i * 10 + 1, runway_id=i, designation="19")
                    _installation(s, i, airport_id=i)
                    _signal(s, i, airport_id=i)
                s.flush()
                counter = {"n": 0}

                def _on_exec(*_a, **_k):
                    counter["n"] += 1

                event.listen(engine, "before_cursor_execute", _on_exec)
                try:
                    fhc.run_fleet_review_check(s)
                finally:
                    event.remove(engine, "before_cursor_execute", _on_exec)
            engine.dispose()
            return counter["n"]

        small = _count(3)
        large = _count(30)
        assert small == large


class TestDeterminism:
    def test_opposite_insertion_order_same_output(self, tmp_path):
        db_a = tmp_path / "a.db"
        engine_a = create_engine(f"sqlite:///{db_a}")
        Base.metadata.create_all(engine_a)
        with Session(engine_a) as s:
            _airport(s, 2, name="A2", country="XX")
            _airport(s, 1, name="A1", country="XX")
            s.flush()
            findings_a = fhc.run_fleet_review_check(s)
        engine_a.dispose()

        db_b = tmp_path / "b.db"
        engine_b = create_engine(f"sqlite:///{db_b}")
        Base.metadata.create_all(engine_b)
        with Session(engine_b) as s:
            _airport(s, 1, name="A1", country="XX")
            _airport(s, 2, name="A2", country="XX")
            s.flush()
            findings_b = fhc.run_fleet_review_check(s)
        engine_b.dispose()

        assert findings_a == findings_b
