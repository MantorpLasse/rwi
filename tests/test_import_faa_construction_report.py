from datetime import date
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.acquisition.faa_construction_report import ConstructionReportMatch
from app.database import Base
from app.models import Airport, Signal, Source
from scripts.import_faa_construction_report import (
    classify_category,
    find_airport,
    import_all,
    resolve_signal_to_update,
)

FIXTURE_PDF = (Path(__file__).parent / "fixtures" / "faa_construction_report_sample.pdf").read_bytes()
INDEX_HTML = (
    '<a href="/x/Q3_2026_508_Airport_Construction_Impact_Report.pdf">Q3 2026</a>'
)

BOS_MATCH = ConstructionReportMatch(
    airport_code="BOS",
    project_id="D",
    project_name="RWY 27 RSA (Phase 2)",
    description="Phase 2 of the 27 RSA project.",
    estimated_dates_raw="08/31/2026 to 11/15/2026",
    start_date=date(2026, 8, 31),
    end_date=date(2026, 11, 15),
    status="Upcoming",
    impact="R",
    notes="This is Phase 2 of the RWY 9 EMAS installation.",
    matched_keyword="EMAS",
)


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_classify_category():
    assert classify_category("RECONSTRUCT ENGINEERED MATERIAL ARRESTING SYSTEM") == "replacement"
    assert classify_category("REPLACE THE EMAS") == "replacement"
    assert classify_category("CONSTRUCT A NEW EMAS") == "new_installation"


def test_find_airport_matches_by_faa_code(session_factory):
    with session_factory() as session:
        airport = Airport(name="Boston Logan International Airport", faa_code="BOS", country="USA")
        session.add(airport)
        session.commit()

        assert find_airport(session, "BOS").id == airport.id
        assert find_airport(session, "ZZZ") is None


def _bos_candidates(session, airport):
    """Mirrors the real production data: one seed signal + three USAspending
    signals at BOS, all mentioning EMAS, describing overlapping project phases."""
    signals = [
        Signal(
            airport=airport,
            title="Runway 9/27 RSA and EMAS phase 2",
            category="new_installation",
            confidence="confirmed",
            status="construction",
            planning_year=2026,
            notes="Continuation of runway safety area and EMAS construction.",
        ),
        Signal(
            airport=airport,
            title="USAspending grant: Massachusetts Port Authority EMAS",
            category="new_installation",
            confidence="high",
            status="identified",
            planning_year=2025,
            notes=(
                "CONSTRUCTS A NEW ENGINEERED MATERIAL ARRESTING SYSTEM FOR RUNWAY 9/27, "
                "AT THE 27 END. GRANT FUNDS PHASE 2, BUILDING A PIER."
            ),
        ),
        Signal(
            airport=airport,
            title="USAspending grant: Massachusetts Port Authority EMAS",
            category="new_installation",
            confidence="high",
            status="identified",
            planning_year=2026,
            notes=(
                "CONSTRUCTS A NEW ENGINEERED MATERIAL ARRESTING SYSTEM FOR RUNWAY 9/27. "
                "GRANT FUNDS THE FINAL PHASE, INSTALLATION OF EMAS BLOCKS."
            ),
        ),
        Signal(
            airport=airport,
            title="USAspending grant: Massachusetts Port Authority EMAS",
            category="new_installation",
            confidence="high",
            status="identified",
            planning_year=2023,
            notes=(
                "CONSTRUCTS AN ENGINEERED MATERIAL ARRESTING SYSTEM AT RUNWAY 27 END. "
                "GRANT FUNDS THE FIRST PHASE, ENVIRONMENTAL ASSESSMENT."
            ),
        ),
    ]
    session.add_all(signals)
    session.commit()
    return signals


def test_resolve_signal_to_update_picks_the_seed_signal_among_four_bos_candidates(session_factory):
    with session_factory() as session:
        airport = Airport(name="Boston Logan International Airport", faa_code="BOS", country="USA")
        session.add(airport)
        session.flush()
        signals = _bos_candidates(session, airport)

        resolved = resolve_signal_to_update(session, airport, BOS_MATCH)

        assert resolved is not None
        assert resolved.id == signals[0].id  # the original seed signal, not a USAspending one
        assert resolved.title == "Runway 9/27 RSA and EMAS phase 2"


def test_resolve_signal_to_update_returns_none_when_no_candidates(session_factory):
    with session_factory() as session:
        airport = Airport(name="Test", faa_code="ZZZ", country="USA")
        session.add(airport)
        session.commit()

        assert resolve_signal_to_update(session, airport, BOS_MATCH) is None


def test_resolve_signal_to_update_returns_none_on_an_unresolvable_tie(session_factory):
    with session_factory() as session:
        airport = Airport(name="Test", faa_code="ZZZ", country="USA")
        session.add(airport)
        session.flush()
        # Two equally-generic EMAS-mentioning signals, neither matching the
        # report's year/runway/phase any better than the other.
        session.add_all([
            Signal(airport=airport, title="EMAS project A", category="new_installation", confidence="high"),
            Signal(airport=airport, title="EMAS project B", category="new_installation", confidence="high"),
        ])
        session.commit()

        assert resolve_signal_to_update(session, airport, BOS_MATCH) is None


def _client_with_fixture():
    def handler(request):
        url = str(request.url)
        if url.endswith(".pdf"):
            return httpx.Response(200, content=FIXTURE_PDF, request=request)
        return httpx.Response(200, content=INDEX_HTML, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_import_all_updates_the_matching_bos_signal_and_creates_one_for_sfo(session_factory):
    with session_factory() as session:
        bos = Airport(name="Boston Logan International Airport", faa_code="BOS", country="USA")
        session.add(bos)
        session.flush()
        bos_signals = _bos_candidates(session, bos)
        seed_signal_id = bos_signals[0].id
        other_bos_signal_ids = [s.id for s in bos_signals[1:]]

        session.add(Airport(name="San Francisco International Airport", faa_code="SFO", country="USA"))
        session.commit()

    stats = import_all(session_factory=session_factory, client=_client_with_fixture())

    assert stats["matches_found"] == 2
    assert stats["signals_updated"] == 1
    assert stats["signals_created"] == 1
    assert stats["already_imported"] == 0
    assert stats["airports_not_found"] == []

    with session_factory() as session:
        bos_signal = session.get(Signal, seed_signal_id)
        assert bos_signal.status == "under construction"
        assert bos_signal.construction_start == date(2026, 8, 31)
        assert bos_signal.completion_date == date(2026, 11, 15)
        assert "Confirmed by FAA Construction Impact Report" in bos_signal.notes
        assert "Continuation of runway safety area" in bos_signal.notes  # original note preserved

        # The other 3 BOS candidates must be untouched.
        for signal_id in other_bos_signal_ids:
            refreshed = session.get(Signal, signal_id)
            assert refreshed.status == "identified"

        sfo_signal = session.scalar(
            select(Signal).join(Airport).where(Airport.faa_code == "SFO")
        )
        assert sfo_signal is not None
        assert sfo_signal.confidence == "high"
        assert sfo_signal.status == "under construction"
        assert sfo_signal.probability_score == 8.0
        assert sfo_signal.construction_start == date(2026, 3, 30)
        assert sfo_signal.completion_date == date(2026, 10, 3)


def test_import_all_skips_unmatched_airports(session_factory):
    stats = import_all(session_factory=session_factory, client=_client_with_fixture())

    assert set(stats["airports_not_found"]) == {"BOS", "SFO"}
    with session_factory() as session:
        assert session.scalars(select(Signal)).all() == []


def test_import_all_is_idempotent_on_rerun(session_factory):
    with session_factory() as session:
        session.add(Airport(name="Boston Logan International Airport", faa_code="BOS", country="USA"))
        session.add(Airport(name="San Francisco International Airport", faa_code="SFO", country="USA"))
        session.commit()

    first = import_all(session_factory=session_factory, client=_client_with_fixture())
    second = import_all(session_factory=session_factory, client=_client_with_fixture())

    assert first["signals_created"] == 2
    assert second["signals_created"] == 0
    assert second["signals_updated"] == 0
    assert second["already_imported"] == 2

    with session_factory() as session:
        assert len(session.scalars(select(Signal)).all()) == 2
        assert len(session.scalars(select(Source)).all()) == 2
