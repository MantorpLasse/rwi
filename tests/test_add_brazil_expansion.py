from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Airport, Installation, Signal, Source
from scripts.add_brazil_expansion import AGENCIA_INFRA_URL, PODER360_URL, seed


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_seed_creates_both_airports_with_country_brazil():
    Session = session_factory()
    with Session() as session:
        seed(session)

        cgh = session.scalar(select(Airport).where(Airport.iata_code == "CGH"))
        sdu = session.scalar(select(Airport).where(Airport.iata_code == "SDU"))

        assert cgh is not None and cgh.country == "Brazil" and cgh.icao_code == "SBSP"
        assert sdu is not None and sdu.country == "Brazil" and sdu.icao_code == "SBRJ"


def test_seed_creates_cgh_installation_with_source():
    Session = session_factory()
    with Session() as session:
        seed(session)

        cgh = session.scalar(select(Airport).where(Airport.iata_code == "CGH"))
        installation = session.scalar(select(Installation).where(Installation.airport_id == cgh.id))

        assert installation is not None
        assert installation.install_year == 2022
        assert installation.status == "active"
        assert installation.source is not None
        assert installation.source.url == PODER360_URL
        assert installation.source.source_type == "news"


def test_seed_creates_sdu_signal_with_medium_confidence_and_both_sources_cited():
    Session = session_factory()
    with Session() as session:
        seed(session)

        sdu = session.scalar(select(Airport).where(Airport.iata_code == "SDU"))
        signal = session.scalar(select(Signal).where(Signal.airport_id == sdu.id))

        assert signal is not None
        assert signal.category == "new_installation"
        assert signal.confidence == "medium"
        assert signal.source is not None
        assert signal.source.url == AGENCIA_INFRA_URL
        assert "OBEKRÄFTAT" in signal.notes
        assert PODER360_URL in signal.notes


def test_seed_only_creates_two_sources_total():
    Session = session_factory()
    with Session() as session:
        seed(session)

        sources = session.scalars(select(Source)).all()
        assert len(sources) == 2
        assert {s.url for s in sources} == {PODER360_URL, AGENCIA_INFRA_URL}


def test_seed_is_idempotent():
    Session = session_factory()
    with Session() as session:
        seed(session)
        stats_second_run = seed(session)

        assert stats_second_run == {
            "airports_created": 0,
            "sources_created": 0,
            "installations_created": 0,
            "signals_created": 0,
        }
        assert len(session.scalars(select(Airport)).all()) == 2
        assert len(session.scalars(select(Source)).all()) == 2
        assert len(session.scalars(select(Installation)).all()) == 1
        assert len(session.scalars(select(Signal)).all()) == 1
