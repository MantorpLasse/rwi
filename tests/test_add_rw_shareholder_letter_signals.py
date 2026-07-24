from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Airport, Installation, Signal, Source
from scripts.add_rw_shareholder_letter_signals import (
    MAY_2025_LETTER_TITLE,
    ensure_confirmed_vendor_columns,
    ensure_source_url_nullable,
    seed,
)


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_clt_msp_sdu(session):
    clt = Airport(iata_code="CLT", icao_code="KCLT", name="Charlotte Douglas International", country="USA")
    msp = Airport(iata_code="MSP", icao_code="KMSP", name="Minneapolis-St Paul International", country="USA")
    sdu = Airport(iata_code="SDU", icao_code="SBRJ", name="Santos Dumont Airport", country="Brazil")
    session.add_all([clt, msp, sdu])
    session.flush()
    sdu_signal = Signal(
        airport=sdu,
        title="Santos Dumont EMAS installation (R$400M investment package)",
        category="new_installation",
        confidence="medium",
        notes="Existing SDU note about the R$400M package.",
    )
    session.add(sdu_signal)
    session.commit()
    return sdu_signal.id


def test_ensure_confirmed_vendor_columns_adds_to_old_style_tables():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE signals (id INTEGER PRIMARY KEY, airport_id INTEGER NOT NULL, "
                "title VARCHAR(250) NOT NULL, category VARCHAR(50) NOT NULL, confidence VARCHAR(30) NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE installations (id INTEGER PRIMARY KEY, airport_id INTEGER NOT NULL)"
            )
        )

    ensure_confirmed_vendor_columns(engine)
    ensure_confirmed_vendor_columns(engine)  # must not raise the second time

    signals_cols = {c["name"] for c in inspect(engine).get_columns("signals")}
    installations_cols = {c["name"] for c in inspect(engine).get_columns("installations")}
    assert "confirmed_vendor" in signals_cols
    assert "confirmed_vendor" in installations_cols


def test_ensure_source_url_nullable_preserves_data_and_allows_null_afterwards():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE sources ("
                "id INTEGER NOT NULL, title VARCHAR(300) NOT NULL, "
                "source_type VARCHAR(50) NOT NULL, publisher VARCHAR(200), "
                "url VARCHAR(1000) NOT NULL, published_date DATE, retrieved_at DATE, "
                "document_reference VARCHAR(200), page_number VARCHAR(30), summary TEXT, "
                "reliability_level VARCHAR(30) NOT NULL, external_id VARCHAR(200), "
                "PRIMARY KEY (id)"
                ")"
            )
        )
        connection.execute(
            text(
                "INSERT INTO sources (title, source_type, url, reliability_level) "
                "VALUES ('Existing', 'news', 'https://example.test', 'official')"
            )
        )

    ensure_source_url_nullable(engine)
    ensure_source_url_nullable(engine)  # must not raise / re-rebuild the second time

    with Session(engine) as session:
        existing = session.execute(text("SELECT title, url FROM sources WHERE id = 1")).one()
        assert existing.title == "Existing"
        assert existing.url == "https://example.test"

        session.execute(
            text("INSERT INTO sources (title, source_type, url, reliability_level) VALUES ('No URL', 'shareholder_newsletter', NULL, 'official')")
        )
        session.commit()
        no_url = session.execute(text("SELECT url FROM sources WHERE title = 'No URL'")).scalar_one()
        assert no_url is None


def test_seed_creates_zqn_installation_and_wlg_signal():
    Session = session_factory()
    with Session() as session:
        _seed_clt_msp_sdu(session)
        seed(session)

        zqn = session.scalar(select(Airport).where(Airport.iata_code == "ZQN"))
        assert zqn is not None and zqn.country == "New Zealand"
        installation = session.scalar(select(Installation).where(Installation.airport_id == zqn.id))
        assert installation.install_year == 2025
        assert installation.confirmed_vendor == "Runway Safe"
        assert installation.source.url is None
        assert installation.source.source_type == "shareholder_newsletter"

        wlg = session.scalar(select(Airport).where(Airport.iata_code == "WLG"))
        assert wlg is not None
        signal = session.scalar(select(Signal).where(Signal.airport_id == wlg.id))
        assert signal.confirmed_vendor == "Runway Safe"
        assert signal.target_year == 2026
        assert "mars 2026" in signal.notes


def test_seed_creates_clt_and_msp_signals_without_touching_existing_rows():
    Session = session_factory()
    with Session() as session:
        _seed_clt_msp_sdu(session)
        clt = session.scalar(select(Airport).where(Airport.iata_code == "CLT"))
        old_installation = Installation(airport=clt, type="EMASMAX", runway_end="36R", status="active")
        session.add(old_installation)
        session.commit()
        old_installation_id = old_installation.id

        seed(session)

        clt_signals = session.scalars(select(Signal).where(Signal.airport_id == clt.id)).all()
        assert len(clt_signals) == 1
        assert clt_signals[0].category == "new_installation"
        assert clt_signals[0].confirmed_vendor == "Runway Safe"
        assert clt_signals[0].target_year is None
        # The pre-existing installation is untouched.
        untouched = session.get(Installation, old_installation_id)
        assert untouched.type == "EMASMAX" and untouched.confirmed_vendor is None

        msp = session.scalar(select(Airport).where(Airport.iata_code == "MSP"))
        msp_signals = session.scalars(select(Signal).where(Signal.airport_id == msp.id)).all()
        assert len(msp_signals) == 1
        assert msp_signals[0].category == "replacement"
        assert msp_signals[0].confirmed_vendor == "Runway Safe"


def test_seed_updates_sdu_signal_preserving_existing_notes_and_source():
    Session = session_factory()
    with Session() as session:
        sdu_signal_id = _seed_clt_msp_sdu(session)

        seed(session)

        signal = session.get(Signal, sdu_signal_id)
        assert signal.confirmed_vendor == "Runway Safe (med Atlantis Consulting)"
        assert "Existing SDU note about the R$400M package." in signal.notes
        assert "EMASMax" in signal.notes
        assert "mUSD 36" in signal.notes


def test_seed_is_idempotent():
    Session = session_factory()
    with Session() as session:
        _seed_clt_msp_sdu(session)

        seed(session)
        stats_second_run = seed(session)

        assert stats_second_run == {"installations_created": 0, "signals_created": 0, "sdu_updated": False}
        assert len(session.scalars(select(Source).where(Source.title == MAY_2025_LETTER_TITLE)).all()) == 1

        wlg = session.scalar(select(Airport).where(Airport.iata_code == "WLG"))
        assert len(session.scalars(select(Signal).where(Signal.airport_id == wlg.id)).all()) == 1


def test_seed_raises_if_clt_is_missing():
    Session = session_factory()
    with Session() as session:
        msp = Airport(iata_code="MSP", icao_code="KMSP", name="MSP", country="USA")
        sdu = Airport(iata_code="SDU", icao_code="SBRJ", name="SDU", country="Brazil")
        session.add_all([msp, sdu])
        session.flush()
        session.add(Signal(airport=sdu, title="x", category="new_installation", confidence="medium", notes="n"))
        session.commit()
        try:
            seed(session)
            assert False, "expected SystemExit"
        except SystemExit:
            pass
