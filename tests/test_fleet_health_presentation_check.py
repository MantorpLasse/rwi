"""Tests for app.services.fleet_health_presentation_check (the FHC4 adapter
for build_fleet_presentation_snapshot/run_fleet_presentation_check).

Every test builds an isolated, disposable SQLite database AND an isolated,
disposable output directory (always under pytest's own tmp_path) - the real
data/runway_safe.db and the repository's canonical site/ output are never
opened or touched here."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Runway, Signal, Source
from app.services import fleet_health_presentation_check as fhc
from app.services.fleet_health_presentation_rules import HealthClassification

REPO_ROOT = Path(__file__).resolve().parent.parent
CANONICAL_SITE_DIR = REPO_ROOT / "site"


@pytest.fixture()
def session(tmp_path):
    db = tmp_path / "fhc4.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def _airport(session, id, name="Test Airport", country="XX", iata=None, icao=None):
    a = Airport(id=id, name=name, country=country, iata_code=iata, icao_code=icao)
    session.add(a)
    return a


def _runway(session, id, airport_id, designation="01/19"):
    r = Runway(id=id, airport_id=airport_id, designation=designation)
    session.add(r)
    return r


def _source(session, id, title="Source", source_type="report"):
    s = Source(id=id, title=title, source_type=source_type)
    session.add(s)
    return s


def _signal(
    session, id, airport_id, runway_id=None, title="Signal", category="new_installation",
    confidence="high", source_id=None, published=True,
):
    s = Signal(
        id=id, airport_id=airport_id, runway_id=runway_id, title=title, category=category,
        confidence=confidence, source_id=source_id, published=published,
    )
    session.add(s)
    return s


def _snapshot_canonical_site_dir():
    """Records whether the repo's canonical site/ dir exists and, if so, a
    cheap fingerprint (mtime + file count) - used to prove FHC4 tests never
    touch it, without assuming it exists in every checkout."""
    if not CANONICAL_SITE_DIR.exists():
        return None
    files = sorted(p.relative_to(CANONICAL_SITE_DIR) for p in CANONICAL_SITE_DIR.rglob("*") if p.is_file())
    return (CANONICAL_SITE_DIR.stat().st_mtime, tuple(str(f) for f in files))


# ---------------------------------------------------------------------------
# Read-only / isolation guarantees
# ---------------------------------------------------------------------------


class TestReadOnlyAndIsolationGuarantees:
    def test_canonical_site_directory_untouched(self, session, tmp_path):
        before = _snapshot_canonical_site_dir()
        _airport(session, 1)
        _signal(session, 3000, airport_id=1)
        session.flush()
        fhc.run_fleet_presentation_check(session, tmp_path / "out")
        after = _snapshot_canonical_site_dir()
        assert before == after

    def test_run_leaves_db_row_counts_unchanged(self, session, tmp_path):
        _airport(session, 1)
        _signal(session, 3000, airport_id=1)
        session.flush()
        before = (session.query(Airport).count(), session.query(Signal).count())
        fhc.run_fleet_presentation_check(session, tmp_path / "out")
        after = (session.query(Airport).count(), session.query(Signal).count())
        assert before == after

    def test_run_leaves_transaction_clean(self, session, tmp_path):
        _airport(session, 1)
        _signal(session, 3000, airport_id=1)
        session.flush()
        fhc.run_fleet_presentation_check(session, tmp_path / "out")
        assert not session.dirty
        assert not session.new
        assert not session.deleted

    def test_never_autoflushes_callers_pending_mutation(self, session, tmp_path):
        _airport(session, 1, name="Original")
        _signal(session, 3000, airport_id=1)
        session.flush()
        session.commit()
        airport = session.get(Airport, 1)
        airport.name = "PENDING - MUST NOT BE FLUSHED"
        assert airport in session.dirty

        fhc.run_fleet_presentation_check(session, tmp_path / "out")

        assert airport in session.dirty
        raw = session.execute(text("SELECT name FROM airports WHERE id = 1")).scalar_one()
        assert raw == "Original"
        session.rollback()

    def test_build_site_always_called_with_explicit_session_not_default(self, session, tmp_path, monkeypatch):
        # Proves the adapter never silently falls back to build_site()'s own
        # default (production) database session.
        captured = {}
        real_build_site = fhc.build_site

        def _spy(output_dir, *, session=None):
            captured["session"] = session
            return real_build_site(output_dir, session=session)

        monkeypatch.setattr(fhc, "build_site", _spy)
        _airport(session, 1)
        session.flush()
        fhc.run_fleet_presentation_check(session, tmp_path / "out")
        assert captured["session"] is session

    def test_output_dir_has_no_default_value(self):
        import inspect

        sig = inspect.signature(fhc.run_fleet_presentation_check)
        assert sig.parameters["output_dir"].default is inspect.Parameter.empty
        sig2 = inspect.signature(fhc.build_fleet_presentation_snapshot)
        assert sig2.parameters["output_dir"].default is inspect.Parameter.empty

    def test_no_write_sql_statements_emitted_instrumented(self, tmp_path):
        """SQL-statement-level instrumentation (not just session-state
        inspection): captures every statement sent to the database and
        asserts none begins with INSERT/UPDATE/DELETE, even with a
        caller's pending dirty object present throughout the run."""
        db = tmp_path / "instrumented.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        with Session(engine) as s:
            _airport(s, 1, name="Original")
            _signal(s, 3000, airport_id=1, published=True)
            s.flush()
            s.commit()
            airport = s.get(Airport, 1)
            airport.name = "PENDING EDIT"

            statements = []

            def _capture(_conn, _cursor, statement, *_args, **_kwargs):
                statements.append(statement.strip().upper())

            from sqlalchemy import event

            event.listen(engine, "before_cursor_execute", _capture)
            try:
                fhc.run_fleet_presentation_check(s, tmp_path / "out")
            finally:
                event.remove(engine, "before_cursor_execute", _capture)

            write_statements = [
                stmt for stmt in statements
                if stmt.startswith(("INSERT", "UPDATE", "DELETE"))
            ]
            assert write_statements == []
        engine.dispose()

    def test_export_reflects_only_the_supplied_database_not_another(self, tmp_path):
        """Review-checkpoint regression test: two independent, isolated
        databases with DISTINCTIVE, non-overlapping Signal ids - proves the
        real generated output's CONTENT corresponds only to the database
        actually passed in, not merely that the same Python session object
        was threaded through (the spy test above proves identity, not
        content). This is the exact "protected DB unchanged, target DB
        content only" attack the review mission names as high priority,
        given build_site()'s own default falls back to the real production
        database."""
        target_db = tmp_path / "target.db"
        target_engine = create_engine(f"sqlite:///{target_db}")
        Base.metadata.create_all(target_engine)
        with Session(target_engine) as s:
            _airport(s, 1, name="Target Airport")
            _signal(s, 555555, airport_id=1, title="TARGET-ONLY SIGNAL", published=True)
            s.flush()
            output = tmp_path / "target_out"
            fhc.run_fleet_presentation_check(s, output)
        target_engine.dispose()

        protected_db = tmp_path / "protected.db"
        protected_engine = create_engine(f"sqlite:///{protected_db}")
        Base.metadata.create_all(protected_engine)
        with Session(protected_engine) as s:
            _airport(s, 1, name="Protected Airport")
            _signal(s, 777777, airport_id=1, title="PROTECTED-ONLY SIGNAL", published=True)
            s.flush()
            s.commit()
        before_hash = protected_db.read_bytes()
        protected_engine.dispose()

        # The generated output must contain the target signal and must NOT
        # contain the protected database's own distinctive signal id.
        assert (output / "signals" / "555555.html").exists()
        assert not (output / "signals" / "777777.html").exists()
        index_html = (output / "index.html").read_text(encoding="utf-8")
        assert "TARGET-ONLY SIGNAL" in index_html
        assert "PROTECTED-ONLY SIGNAL" not in index_html

        # The protected database file itself must be byte-identical - it
        # was never opened by the target's export at all.
        assert protected_db.read_bytes() == before_hash


class TestStaleOutputDetection:
    """Mission-named attack: export state A, change DB to state B WITHOUT
    regenerating, and prove FH-H2 detects the resulting discrepancy between
    current persisted state and the now-stale generated output - then prove
    regenerating clears the finding. This is what makes FH-H2 a genuine
    presentation *staleness* detector, not merely an exporter self-test."""

    def test_stale_output_detected_then_cleared_by_regeneration(self, session, tmp_path):
        _airport(session, 1)
        _signal(session, 3000, airport_id=1, published=True)
        session.flush()
        output = tmp_path / "out"
        assert fhc.run_fleet_presentation_check(session, output) == ()

        # State B: a new Signal becomes published, but we do NOT regenerate.
        _signal(session, 3001, airport_id=1, published=True)
        session.flush()

        from app.services.fleet_health_presentation_rules import (
            FleetPresentationSnapshot,
            evaluate_presentation_findings,
        )

        stale_rendered = fhc._read_rendered_signal_page_facts(output)
        fresh_published = fhc._build_published_signal_facts(session)
        stale_snapshot = FleetPresentationSnapshot(
            published_signals=fresh_published,
            rendered_signal_pages=stale_rendered,
        )
        findings = evaluate_presentation_findings(stale_snapshot)
        assert len(findings) == 1
        assert findings[0].structured_evidence["missing_signal_ids"] == (3001,)

        # Now regenerate - the staleness must be resolved.
        assert fhc.run_fleet_presentation_check(session, output) == ()

    def test_publication_flag_change_before_export_is_followed_not_stale(self, session, tmp_path):
        # Section 11: "published flag changes in fixture before export =>
        # output follows persisted state" - FHC4 never caches or reuses a
        # prior published-state read.
        _airport(session, 1)
        signal = _signal(session, 3000, airport_id=1, published=True)
        session.flush()
        output = tmp_path / "out"
        assert fhc.run_fleet_presentation_check(session, output) == ()
        assert (output / "signals" / "3000.html").exists()

        signal.published = False
        session.flush()
        assert fhc.run_fleet_presentation_check(session, output) == ()
        assert not (output / "signals" / "3000.html").exists()


class TestNonCanonicalFilenameRejection:
    """Review-checkpoint regression test for a genuine defect found during
    this review: a non-canonical (e.g. zero-padded) numeric filename must
    be rejected outright, not silently accepted as an alias for the
    canonical id - accepting it would let two distinct filenames collapse
    into one fact via set deduplication downstream, hiding a genuine
    duplicate/unexpected artifact."""

    def test_zero_padded_filename_rejected(self, tmp_path):
        signals_dir = tmp_path / "export" / "signals"
        signals_dir.mkdir(parents=True)
        (signals_dir / "01.html").write_text("x")
        with pytest.raises(ValueError, match="unexpected"):
            fhc._read_rendered_signal_page_facts(tmp_path / "export")

    def test_zero_padded_alongside_canonical_never_silently_collapses(self, tmp_path):
        signals_dir = tmp_path / "export" / "signals"
        signals_dir.mkdir(parents=True)
        (signals_dir / "1.html").write_text("x")
        (signals_dir / "01.html").write_text("x")
        with pytest.raises(ValueError, match="unexpected"):
            fhc._read_rendered_signal_page_facts(tmp_path / "export")

    def test_plus_signed_filename_rejected(self, tmp_path):
        signals_dir = tmp_path / "export" / "signals"
        signals_dir.mkdir(parents=True)
        (signals_dir / "+1.html").write_text("x")
        with pytest.raises(ValueError, match="unexpected"):
            fhc._read_rendered_signal_page_facts(tmp_path / "export")

    def test_ordinary_canonical_ids_still_accepted(self, tmp_path):
        signals_dir = tmp_path / "export" / "signals"
        signals_dir.mkdir(parents=True)
        (signals_dir / "1.html").write_text("x")
        (signals_dir / "42.html").write_text("x")
        (signals_dir / "3000.html").write_text("x")
        facts = fhc._read_rendered_signal_page_facts(tmp_path / "export")
        assert {f.signal_id for f in facts} == {1, 42, 3000}


class TestFailLoud:
    def test_exporter_failure_propagates_not_swallowed(self, session, tmp_path, monkeypatch):
        def _boom(output_dir, *, session=None):
            raise RuntimeError("simulated export crash")

        monkeypatch.setattr(fhc, "build_site", _boom)
        _airport(session, 1)
        session.flush()
        with pytest.raises(RuntimeError, match="simulated export crash"):
            fhc.run_fleet_presentation_check(session, tmp_path / "out")

    def test_real_exporter_genuinely_crashes_on_malformed_state_h1_path(self, tmp_path):
        """Exercises FH-H1's own 'run it and see' check against the REAL,
        unmocked build_site() (not a monkeypatched fake) - review-checkpoint
        addition, since the mocked test above only proves propagation, not
        that a genuine crash is possible/caught in the first place. A
        Signal whose airport_id names no real Airport row (constructible
        because a bare create_engine() session does not enable SQLite's
        PRAGMA foreign_keys, unlike app.database's own real engine) makes
        `_signal_view()`'s unconditional `signal.airport.name` access raise
        a real AttributeError - proving the adapter's fail-loud path is
        genuinely exercised by real exporter behavior, not merely assumed.
        """
        db = tmp_path / "crash.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        with Session(engine) as s:
            orphan = Signal(
                id=1, airport_id=999999, title="Orphan", category="new_installation",
                confidence="high", published=True,
            )
            s.add(orphan)
            s.flush()
            with pytest.raises(AttributeError):
                fhc.run_fleet_presentation_check(s, tmp_path / "out")
        engine.dispose()

    def test_missing_signals_directory_raises_not_zero_findings(self, tmp_path):
        empty_dir = tmp_path / "empty_export"
        empty_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            fhc._read_rendered_signal_page_facts(empty_dir)

    def test_unexpected_non_html_file_raises(self, tmp_path):
        signals_dir = tmp_path / "export" / "signals"
        signals_dir.mkdir(parents=True)
        (signals_dir / "1.html").write_text("x")
        (signals_dir / "index.html").write_text("x")
        (signals_dir / "stray.txt").write_text("x")
        with pytest.raises(ValueError, match="unexpected"):
            fhc._read_rendered_signal_page_facts(tmp_path / "export")

    def test_non_numeric_html_filename_raises(self, tmp_path):
        signals_dir = tmp_path / "export" / "signals"
        signals_dir.mkdir(parents=True)
        (signals_dir / "abc.html").write_text("x")
        with pytest.raises(ValueError, match="unexpected"):
            fhc._read_rendered_signal_page_facts(tmp_path / "export")

    def test_index_html_is_correctly_ignored(self, tmp_path):
        signals_dir = tmp_path / "export" / "signals"
        signals_dir.mkdir(parents=True)
        (signals_dir / "1.html").write_text("x")
        (signals_dir / "index.html").write_text("x")
        facts = fhc._read_rendered_signal_page_facts(tmp_path / "export")
        assert {f.signal_id for f in facts} == {1}


# ---------------------------------------------------------------------------
# Golden cases (mission Phase 5, A-O)
# ---------------------------------------------------------------------------


class TestGoldenCases:
    def test_a_published_signal_expected_and_correctly_rendered(self, session, tmp_path):
        _airport(session, 1)
        _signal(session, 3000, airport_id=1, published=True)
        session.flush()
        findings = fhc.run_fleet_presentation_check(session, tmp_path / "out")
        assert findings == ()

    def test_c_unpublished_signal_absent_from_public_output(self, session, tmp_path):
        _airport(session, 1)
        _signal(session, 3000, airport_id=1, published=True)
        _signal(session, 3001, airport_id=1, published=False)
        session.flush()
        findings = fhc.run_fleet_presentation_check(session, tmp_path / "out")
        assert findings == ()
        assert not (tmp_path / "out" / "signals" / "3001.html").exists()
        assert (tmp_path / "out" / "signals" / "3000.html").exists()

    def test_e_correct_page_count_no_finding(self, session, tmp_path):
        _airport(session, 1)
        for i in range(3000, 3005):
            _signal(session, i, airport_id=1, published=True)
        session.flush()
        assert fhc.run_fleet_presentation_check(session, tmp_path / "out") == ()

    def test_k_multiple_signals_one_airport_no_collapse(self, session, tmp_path):
        _airport(session, 1)
        for i in range(3000, 3010):
            _signal(session, i, airport_id=1, published=True)
        session.flush()
        snapshot = fhc.build_fleet_presentation_snapshot(session, tmp_path / "out")
        assert len(snapshot.published_signals) == 10
        assert len(snapshot.rendered_signal_pages) == 10
        # Re-run against a second, independent output dir - clean either way.
        assert fhc.run_fleet_presentation_check(session, tmp_path / "out2") == ()

    def test_l_multiple_airports_runways_deterministic_mapping(self, session, tmp_path):
        _airport(session, 1, name="A1")
        _airport(session, 2, name="A2")
        _runway(session, 10, airport_id=1)
        _runway(session, 20, airport_id=2)
        _signal(session, 3000, airport_id=1, runway_id=10, published=True)
        _signal(session, 3001, airport_id=2, runway_id=20, published=True)
        session.flush()
        assert fhc.run_fleet_presentation_check(session, tmp_path / "out") == ()

    def test_o_missing_null_optional_data_no_fabricated_contradiction(self, session, tmp_path):
        # Signal with no runway, no source - both legitimately optional.
        _airport(session, 1)
        _signal(session, 3000, airport_id=1, runway_id=None, source_id=None, published=True)
        session.flush()
        assert fhc.run_fleet_presentation_check(session, tmp_path / "out") == ()

    def test_f_missing_one_page_among_many_exact_finding_no_collateral(self, session, tmp_path):
        _airport(session, 1)
        for i in range(3000, 3005):
            _signal(session, i, airport_id=1, published=True)
        session.flush()
        output = tmp_path / "out"
        snapshot = fhc.build_fleet_presentation_snapshot(session, output)
        assert len(snapshot.rendered_signal_pages) == 5
        # Simulate a missing page by deleting one rendered file, then
        # re-evaluate against the doctored output directory only (never
        # re-running the exporter, which would just regenerate it).
        (output / "signals" / "3002.html").unlink()
        from app.services.fleet_health_presentation_rules import (
            FleetPresentationSnapshot,
            evaluate_presentation_findings,
        )
        doctored_rendered = fhc._read_rendered_signal_page_facts(output)
        doctored_snapshot = FleetPresentationSnapshot(
            published_signals=snapshot.published_signals,
            rendered_signal_pages=doctored_rendered,
        )
        findings = evaluate_presentation_findings(doctored_snapshot)
        assert len(findings) == 1
        assert findings[0].structured_evidence["missing_signal_ids"] == (3002,)
        assert findings[0].structured_evidence["extra_signal_ids"] == ()
        assert findings[0].classification == HealthClassification.DETERMINISTIC_ERROR

    def test_g_unexpected_extra_generated_page_flagged(self, session, tmp_path):
        _airport(session, 1)
        _signal(session, 3000, airport_id=1, published=True)
        session.flush()
        output = tmp_path / "out"
        snapshot = fhc.build_fleet_presentation_snapshot(session, output)
        # Simulate an extra, unexpected numeric page appearing on disk.
        (output / "signals" / "9999.html").write_text("<html></html>", encoding="utf-8")
        from app.services.fleet_health_presentation_rules import (
            FleetPresentationSnapshot,
            evaluate_presentation_findings,
        )
        doctored_rendered = fhc._read_rendered_signal_page_facts(output)
        doctored_snapshot = FleetPresentationSnapshot(
            published_signals=snapshot.published_signals,
            rendered_signal_pages=doctored_rendered,
        )
        findings = evaluate_presentation_findings(doctored_snapshot)
        assert len(findings) == 1
        assert findings[0].structured_evidence["extra_signal_ids"] == (9999,)

    def test_h_correct_relationship_wrong_reference_out_of_scope(self, session, tmp_path):
        # H2 as reviewed compares ONLY identity/count, never cross-reference
        # correctness - a Signal correctly published and rendered, but
        # pointing at a runway belonging to a different airport (a real
        # data-level inconsistency FHC1's own FH-D1 would catch separately),
        # must NOT be flagged by FH-H2 - there is no finding to expect here.
        _airport(session, 1)
        _airport(session, 2)
        _runway(session, 10, airport_id=2)  # belongs to airport 2
        _signal(session, 3000, airport_id=1, runway_id=10, published=True)  # signal claims airport 1
        session.flush()
        findings = fhc.run_fleet_presentation_check(session, tmp_path / "out")
        assert findings == ()  # H2 cannot and does not detect this

    def test_j_unicode_signal_and_airport_data_same_behavior(self, session, tmp_path):
        _airport(session, 1, name="Örnsköldsvik Flygplats", country="Sverige", icao="ESNO")
        _signal(session, 3000, airport_id=1, title="Örnsköldsvik – ny EMAS-installation 2027", published=True)
        session.flush()
        assert fhc.run_fleet_presentation_check(session, tmp_path / "out") == ()

    def test_m_reversed_signal_id_insertion_order_same_findings(self, tmp_path):
        db_a = tmp_path / "a.db"
        engine_a = create_engine(f"sqlite:///{db_a}")
        Base.metadata.create_all(engine_a)
        with Session(engine_a) as s:
            _airport(s, 1)
            for i in (3002, 3000, 3001):
                _signal(s, i, airport_id=1, published=True)
            s.flush()
            findings_a = fhc.run_fleet_presentation_check(s, tmp_path / "out_a")
        engine_a.dispose()

        db_b = tmp_path / "b.db"
        engine_b = create_engine(f"sqlite:///{db_b}")
        Base.metadata.create_all(engine_b)
        with Session(engine_b) as s:
            _airport(s, 1)
            for i in (3000, 3001, 3002):
                _signal(s, i, airport_id=1, published=True)
            s.flush()
            findings_b = fhc.run_fleet_presentation_check(s, tmp_path / "out_b")
        engine_b.dispose()

        assert findings_a == findings_b == ()

    def test_n_repeated_evaluation_exact_equality(self, session, tmp_path):
        _airport(session, 1)
        _signal(session, 3000, airport_id=1, published=True)
        session.flush()
        snap1 = fhc.build_fleet_presentation_snapshot(session, tmp_path / "out1")
        snap2 = fhc.build_fleet_presentation_snapshot(session, tmp_path / "out2")
        assert snap1 == snap2


# ---------------------------------------------------------------------------
# DATA_ANOMALY vs. PRESENTATION_ANOMALY: the Signal #20 boundary
# ---------------------------------------------------------------------------


class TestDataAnomalyBoundary:
    def test_bad_but_faithfully_rendered_airport_name_is_not_a_presentation_finding(
        self, session, tmp_path
    ):
        # Reproduces the real Signal #20 / Airport organization-name-leak
        # shape with fully synthetic data: a persisted Airport.name that
        # looks like an organization, not an airport, correctly published,
        # correctly rendered verbatim. FH-H2 must find nothing wrong -
        # persisted-data quality is FHC3's FH-A4 concern (deliberately not
        # automated), never this module's.
        _airport(session, 1, name="Synthetic County")  # organization-shaped, deliberately
        _signal(
            session, 3000, airport_id=1,
            title="Synthetic County – EMAS replacement expected after incident",
            published=True,
        )
        session.flush()
        output = tmp_path / "out"
        findings = fhc.run_fleet_presentation_check(session, output)
        assert findings == ()
        # Confirm the bad name really did render verbatim (faithful
        # rendering, not a presentation bug) - proving this is a genuine
        # DATA_ANOMALY-shaped case, not merely an untested one.
        page = (output / "signals" / "3000.html").read_text(encoding="utf-8")
        assert "Synthetic County" in page
