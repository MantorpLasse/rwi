from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Airport, Installation, Source
from scripts.update_cgh_emas_details import GOV_BR_FEB_2021_URL, update_cgh_installation


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_cgh_with_placeholder_installation(session):
    old_source = Source(title="Old Poder360 mention", source_type="news", url="https://old.test/poder360")
    session.add(old_source)
    session.flush()
    airport = Airport(iata_code="CGH", icao_code="SBSP", name="Congonhas Airport", country="Brazil")
    installation = Installation(
        airport=airport,
        source=old_source,
        type="EMAS",
        install_year=2022,
        status="active",
        notes="placeholder note",
    )
    session.add(installation)
    session.commit()
    return old_source.id


def test_update_repoints_source_and_replaces_notes():
    Session = session_factory()
    with Session() as session:
        old_source_id = _seed_cgh_with_placeholder_installation(session)

        installation = update_cgh_installation(session)

        assert installation.source is not None
        assert installation.source.url == GOV_BR_FEB_2021_URL
        assert installation.source_id != old_source_id
        assert "OS undertecknad 2021-02-11" in installation.notes
        assert "R$122,5M" in installation.notes
        assert "aeroflap.com.br" in installation.notes
        # The old source row is left in place, just unlinked.
        assert session.get(Source, old_source_id) is not None


def test_update_is_idempotent_and_does_not_duplicate_the_gov_br_source():
    Session = session_factory()
    with Session() as session:
        _seed_cgh_with_placeholder_installation(session)

        update_cgh_installation(session)
        update_cgh_installation(session)

        gov_br_sources = session.scalars(select(Source).where(Source.url == GOV_BR_FEB_2021_URL)).all()
        assert len(gov_br_sources) == 1


def test_update_raises_if_cgh_airport_is_missing():
    Session = session_factory()
    with Session() as session:
        try:
            update_cgh_installation(session)
            assert False, "expected SystemExit"
        except SystemExit:
            pass


def test_update_raises_if_installation_is_missing():
    Session = session_factory()
    with Session() as session:
        session.add(Airport(iata_code="CGH", icao_code="SBSP", name="Congonhas Airport", country="Brazil"))
        session.commit()
        try:
            update_cgh_installation(session)
            assert False, "expected SystemExit"
        except SystemExit:
            pass
