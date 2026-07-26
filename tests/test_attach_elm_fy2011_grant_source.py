from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Airport, Installation, Source
from scripts.attach_elm_fy2011_grant_source import FAA_FY2011_GRANTS_URL, attach_grant_source


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_elm_installation(session):
    faa_source = Source(title="FAA map", source_type="faa_tableau", url="https://old.test/faa-map")
    session.add(faa_source)
    session.flush()
    airport = Airport(iata_code="ELM", icao_code="KELM", name="Elmira-Corning", country="USA")
    installation = Installation(
        id=43, airport=airport, source=faa_source, type="EMASMAX", runway_end="06",
        status="active", notes="FAA map region: Map - Main",
    )
    session.add(installation)
    session.commit()
    return {"installation_id": installation.id, "faa_source_id": faa_source.id}


def test_attach_adds_grant_source_without_replacing_existing_one():
    Session = session_factory()
    with Session() as session:
        ids = _seed_elm_installation(session)

        installation, updated = attach_grant_source(session, today=date(2026, 7, 27))

        assert updated is True
        assert installation.source_id == ids["faa_source_id"]  # unchanged - FAA map stays primary
        assert FAA_FY2011_GRANTS_URL in installation.notes
        assert "FAA map region: Map - Main" in installation.notes  # old note preserved
        assert installation.install_year is None  # deliberately not set

        grant_sources = session.scalars(select(Source).where(Source.url == FAA_FY2011_GRANTS_URL)).all()
        assert len(grant_sources) == 1
        assert grant_sources[0].source_type == "aip_grant"


def test_attach_is_idempotent_and_does_not_duplicate_the_source():
    Session = session_factory()
    with Session() as session:
        _seed_elm_installation(session)

        attach_grant_source(session, today=date(2026, 7, 27))
        _, updated_second_run = attach_grant_source(session, today=date(2026, 7, 28))

        assert updated_second_run is False
        grant_sources = session.scalars(select(Source).where(Source.url == FAA_FY2011_GRANTS_URL)).all()
        assert len(grant_sources) == 1


def test_attach_raises_if_installation_is_missing():
    Session = session_factory()
    with Session() as session:
        try:
            attach_grant_source(session)
            assert False, "expected SystemExit"
        except SystemExit:
            pass
