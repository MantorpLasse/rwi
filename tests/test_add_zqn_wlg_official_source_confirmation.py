from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Airport, Installation, Signal, Source
from scripts.add_zqn_wlg_official_source_confirmation import (
    WLG_SOURCE_URL,
    ZQN_SOURCE_URL,
    seed,
)


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_zqn_wlg(session: Session) -> dict:
    zqn = Airport(iata_code="ZQN", icao_code="NZQN", name="Queenstown Airport", country="New Zealand")
    wlg = Airport(iata_code="WLG", icao_code="NZWN", name="Wellington International Airport", country="New Zealand")
    session.add_all([zqn, wlg])
    session.flush()

    old_zqn_source = Source(title="Runway Safe aktieagarbrev 2024-07-15", source_type="shareholder_newsletter", reliability_level="official")
    old_wlg_source = Source(title="Runway Safe aktieagarbrev, maj 2025", source_type="shareholder_newsletter", reliability_level="official")
    session.add_all([old_zqn_source, old_wlg_source])
    session.flush()

    installation = Installation(
        airport=zqn, source=old_zqn_source, type="EMAS", install_year=2025, status="active",
        confirmed_vendor="Runway Safe", notes="Kontrakt for tva EMAS. Fardigstallt H1 2025.",
    )
    signal = Signal(
        airport=wlg, source=old_wlg_source, title="Wellington EMAS-order",
        category="new_installation", confidence="high", confirmed_vendor="Runway Safe",
        notes="Order signerad enligt brevet (jan-apr 2025).",
    )
    session.add_all([installation, signal])
    session.commit()
    return {
        "installation_id": installation.id,
        "signal_id": signal.id,
        "old_zqn_source_id": old_zqn_source.id,
        "old_wlg_source_id": old_wlg_source.id,
    }


def test_seed_replaces_source_and_appends_notes_without_creating_new_rows():
    Session = session_factory()
    with Session() as session:
        ids = _seed_zqn_wlg(session)

        stats = seed(session, today=date(2026, 7, 26))

        assert stats == {"zqn_updated": True, "wlg_updated": True}

        installation = session.get(Installation, ids["installation_id"])
        assert installation.source.url == ZQN_SOURCE_URL
        assert installation.source.source_type == "news"
        assert installation.source_id != ids["old_zqn_source_id"]
        assert installation.install_year == 2025
        assert "Kontrakt for tva EMAS" in installation.notes  # old note preserved
        assert "23 MNZD" in installation.notes
        assert "4870 EMAS-block" in installation.notes
        assert "confirmed_vendor" in installation.notes  # explains why it wasn't changed
        assert installation.confirmed_vendor == "Runway Safe"  # unchanged, not "+ Downer"

        signal = session.get(Signal, ids["signal_id"])
        assert signal.source.url == WLG_SOURCE_URL
        assert signal.source.published_date == date(2026, 3, 25)
        assert signal.source_id != ids["old_wlg_source_id"]
        assert "Order signerad enligt brevet" in signal.notes  # old note preserved
        assert "+143m" in signal.notes
        assert "+37m" in signal.notes

        # old sources still exist, just unlinked
        assert session.get(Source, ids["old_zqn_source_id"]) is not None
        assert session.get(Source, ids["old_wlg_source_id"]) is not None


def test_seed_is_idempotent():
    Session = session_factory()
    with Session() as session:
        _seed_zqn_wlg(session)

        seed(session, today=date(2026, 7, 26))
        first_run_source_count = len(session.scalars(select(Source)).all())

        stats_second_run = seed(session, today=date(2026, 7, 27))

        assert stats_second_run == {"zqn_updated": False, "wlg_updated": False}
        assert len(session.scalars(select(Source)).all()) == first_run_source_count
