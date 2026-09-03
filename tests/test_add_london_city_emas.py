"""Tests for "RWI - Mission #8D" (Governed London City Historical Baseline
Ingestion): scripts/add_london_city_emas.py.

Every test runs against a fresh, isolated, file-based SQLite database (the
script itself builds its own engine from a path, matching every other
governed CLI in this pipeline) - never the real data/runway_safe.db.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.static_export import build_site
from app.models import (
    Airport,
    Installation,
    Runway,
    Signal,
    Source,
    SourceAssertion,
    UnknownAirportCandidate,
    UnknownAirportCandidateRelevanceAssessment,
    UnknownAirportCandidateRelevanceReview,
    UnknownAirportCandidateReview,
)
from scripts.add_london_city_emas import run_ingestion


def _fresh_db(tmp_path):
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    return db_path


def _session(db_path):
    engine = create_engine(f"sqlite:///{db_path}")
    return Session(engine)


def test_dry_run_creates_nothing(tmp_path):
    db_path = _fresh_db(tmp_path)
    run_ingestion(db_path, allow_database_write=False)
    with _session(db_path) as session:
        assert session.scalars(select(Airport)).all() == []
        assert session.scalars(select(Source)).all() == []
        assert session.scalars(select(SourceAssertion)).all() == []
        assert session.scalars(select(UnknownAirportCandidate)).all() == []


def test_write_creates_exactly_one_lcy_airport(tmp_path):
    db_path = _fresh_db(tmp_path)
    run_ingestion(db_path, allow_database_write=True)
    with _session(db_path) as session:
        airports = session.scalars(select(Airport)).all()
        assert len(airports) == 1
        lcy = airports[0]
        assert lcy.name == "London City Airport"
        assert lcy.iata_code == "LCY"
        assert lcy.icao_code == "EGLC"
        assert lcy.country == "United Kingdom"
        assert lcy.city == "London"
        assert lcy.latitude is None
        assert lcy.longitude is None


def test_write_creates_exactly_one_runway_09_27(tmp_path):
    db_path = _fresh_db(tmp_path)
    run_ingestion(db_path, allow_database_write=True)
    with _session(db_path) as session:
        runways = session.scalars(select(Runway)).all()
        assert len(runways) == 1
        assert runways[0].designation == "09/27"


def test_write_creates_exactly_two_installations_with_expected_fields(tmp_path):
    db_path = _fresh_db(tmp_path)
    run_ingestion(db_path, allow_database_write=True)
    with _session(db_path) as session:
        installations = session.scalars(select(Installation)).all()
        assert len(installations) == 2
        ends = {i.runway_end for i in installations}
        assert ends == {"09", "27"}
        for installation in installations:
            assert installation.type == "EMASMAX"
            assert installation.install_year == 2023
            assert installation.replacement_year is None
            assert installation.confirmed_vendor == "Runway Safe"
            assert installation.runway_id is not None
            assert installation.source_id is not None


def test_installation_source_is_the_uk_caa_regulator_source(tmp_path):
    db_path = _fresh_db(tmp_path)
    run_ingestion(db_path, allow_database_write=True)
    with _session(db_path) as session:
        installations = session.scalars(select(Installation)).all()
        for installation in installations:
            assert installation.source.publisher == "UK Civil Aviation Authority"
            assert installation.source.url == "https://airspacechange.caa.co.uk/documents/download/5487"


def test_provenance_all_three_sources_and_assertions_preserved(tmp_path):
    db_path = _fresh_db(tmp_path)
    run_ingestion(db_path, allow_database_write=True)
    with _session(db_path) as session:
        sources = session.scalars(select(Source)).all()
        assert len(sources) == 3
        assert {s.publisher for s in sources} == {"UK Civil Aviation Authority", "Runway Safe", "blu-3"}
        assertions = session.scalars(select(SourceAssertion)).all()
        assert len(assertions) == 3
        for assertion in assertions:
            assert assertion.raw_relevant_text  # verbatim excerpt preserved
            assert assertion.raw_fragment_hash


def test_governed_evidence_records_preserved(tmp_path):
    db_path = _fresh_db(tmp_path)
    run_ingestion(db_path, allow_database_write=True)
    with _session(db_path) as session:
        candidates = session.scalars(select(UnknownAirportCandidate)).all()
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.raw_name == "London City Airport"

        reviews = session.scalars(select(UnknownAirportCandidateReview)).all()
        assert len(reviews) == 1
        assert reviews[0].action == "CREATE_NEW_AIRPORT"

        assessments = session.scalars(select(UnknownAirportCandidateRelevanceAssessment)).all()
        assert len(assessments) == 1
        assert assessments[0].outcome == "EMAS_CONFIRMED"

        relevance_reviews = session.scalars(select(UnknownAirportCandidateRelevanceReview)).all()
        assert len(relevance_reviews) == 1
        assert relevance_reviews[0].action == "CONFIRM_EMAS_RELEVANT"


def test_candidate_review_resolution_links_to_lcy(tmp_path):
    db_path = _fresh_db(tmp_path)
    run_ingestion(db_path, allow_database_write=True)
    with _session(db_path) as session:
        candidate = session.scalars(select(UnknownAirportCandidate)).one()
        lcy = session.scalars(select(Airport)).one()
        assert candidate.resolved_airport_id == lcy.id
        # every SourceAssertion originally candidate-linked is now
        # airport-linked, never both at once (mutual-exclusivity).
        for assertion in session.scalars(select(SourceAssertion)).all():
            assert assertion.airport_id == lcy.id
            assert assertion.unknown_airport_candidate_id is None


def test_zero_lcy_signals(tmp_path):
    db_path = _fresh_db(tmp_path)
    run_ingestion(db_path, allow_database_write=True)
    with _session(db_path) as session:
        lcy = session.scalars(select(Airport)).one()
        signals = session.scalars(select(Signal).where(Signal.airport_id == lcy.id)).all()
        assert signals == []
        assert session.scalars(select(Signal)).all() == []  # zero Signals anywhere in this fresh DB


def test_no_internal_language_leakage_in_notes(tmp_path):
    db_path = _fresh_db(tmp_path)
    run_ingestion(db_path, allow_database_write=True)
    with _session(db_path) as session:
        for installation in session.scalars(select(Installation)).all():
            notes = installation.notes or ""
            for forbidden in (
                "SourceAssertion", "source_assertion_id", "RWI found", "RWI:s egna",
                "reviewer", "confidence", "C:\\", "/tmp/", "traceback", "Traceback",
            ):
                assert forbidden not in notes, f"forbidden token {forbidden!r} found in notes: {notes!r}"


def test_idempotent_rerun_creates_no_duplicates(tmp_path):
    db_path = _fresh_db(tmp_path)
    run_ingestion(db_path, allow_database_write=True)
    run_ingestion(db_path, allow_database_write=True)  # second run
    with _session(db_path) as session:
        assert len(session.scalars(select(Airport)).all()) == 1
        assert len(session.scalars(select(Runway)).all()) == 1
        assert len(session.scalars(select(Installation)).all()) == 2
        assert len(session.scalars(select(Source)).all()) == 3
        assert len(session.scalars(select(SourceAssertion)).all()) == 3
        assert len(session.scalars(select(UnknownAirportCandidate)).all()) == 1
        # append-only layers: exactly one of each, not re-recorded on rerun
        assert len(session.scalars(select(UnknownAirportCandidateReview)).all()) == 1
        assert len(session.scalars(select(UnknownAirportCandidateRelevanceReview)).all()) == 1
        assert session.scalars(select(Signal)).all() == []


def test_identity_guard_stop_if_lcy_already_exists(tmp_path):
    """If an Airport already carries iata_code=LCY (however it got there),
    the script must refuse rather than create a duplicate or silently
    reuse it without the governed candidate/review path."""
    db_path = _fresh_db(tmp_path)
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        session.add(Airport(name="Pre-existing LCY", iata_code="LCY", country="United Kingdom"))
        session.commit()
    engine.dispose()

    run_ingestion(db_path, allow_database_write=True)
    with _session(db_path) as session:
        airports = session.scalars(select(Airport)).all()
        assert len(airports) == 1  # still just the pre-existing one, nothing added
        assert airports[0].name == "Pre-existing LCY"
        assert session.scalars(select(UnknownAirportCandidate)).all() == []


# ---------------------------------------------------------------------
# Static-export verification (Mission #8D Part K items 19-20 / Part L)
# ---------------------------------------------------------------------

def test_static_export_airport_detail_renders_both_installations(tmp_path):
    db_path = _fresh_db(tmp_path)
    run_ingestion(db_path, allow_database_write=True)
    with _session(db_path) as session:
        lcy = session.scalars(select(Airport)).one()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = (tmp_path / "site" / "airports" / f"{lcy.id}.html").read_text(encoding="utf-8")
    assert "London City Airport" in html
    assert html.count("EMASMAX") >= 2  # both installations render
    assert "09" in html and "27" in html


def test_static_export_produces_no_signal_no_attention_no_marknadslage_row(tmp_path):
    db_path = _fresh_db(tmp_path)
    run_ingestion(db_path, allow_database_write=True)
    with _session(db_path) as session:
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    signals_html = (tmp_path / "site" / "signals" / "index.html").read_text(encoding="utf-8")
    assert "London City" not in signals_html
    market_html = (tmp_path / "site" / "marknadslage.html").read_text(encoding="utf-8")
    assert "London City" not in market_html
    assert "attention-row" not in market_html or "London City" not in market_html


def test_static_export_uk_map_legend_behavior_unchanged(tmp_path):
    """United Kingdom must not newly appear in the SIGNAL-DRIVEN map/legend
    (the "Aktuell intelligens" mode) as a consequence of this Signal-less
    historical-baseline ingestion - Mission #8B/#8C's own verified finding,
    locked in as a regression test against the concrete LCY case.

    ("RWI - Mission #23C" mission) Scoped to the Current Intelligence panel
    specifically, rather than the whole page, because Mission #23B/#23C
    added a SECOND, independent, deliberately Installation-driven "Installerad
    bas" panel to the same index.html - whose entire purpose is to surface
    exactly this kind of Signal-less-but-Installation-documented country
    (this test's own LCY fixture is the textbook case). United Kingdom now
    legitimately appearing in THAT panel is the intended, HQ-approved #23B
    design, not a regression of this test's own, narrower, still-true
    claim about the Signal-driven map/legend."""
    db_path = _fresh_db(tmp_path)
    run_ingestion(db_path, allow_database_write=True)
    with _session(db_path) as session:
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    index_html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    current_panel = index_html.split('class="footprint-panel footprint-panel-current"')[1]
    assert "United Kingdom" not in current_panel


def test_static_export_no_internal_prose_leaks_from_lcy_records(tmp_path):
    db_path = _fresh_db(tmp_path)
    run_ingestion(db_path, allow_database_write=True)
    with _session(db_path) as session:
        lcy = session.scalars(select(Airport)).one()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = (tmp_path / "site" / "airports" / f"{lcy.id}.html").read_text(encoding="utf-8")
    for forbidden in ("SourceAssertion", "UnknownAirportCandidate", "IdentityGuard", "human:lkarlsson"):
        assert forbidden not in html
