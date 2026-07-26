from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Airport, Installation, Source
from scripts.update_lex_emas_details import AIRPORT_IMPROVEMENT_URL, update_lex_installation


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_lex_with_faa_installation(session):
    faa_source = Source(title="FAA map", source_type="faa_tableau", url="https://old.test/faa-map")
    session.add(faa_source)
    session.flush()
    airport = Airport(iata_code="LEX", icao_code="KLEX", name="Blue Grass", country="USA")
    installation = Installation(
        airport=airport,
        source=faa_source,
        type="EMASMAX",
        status="active",
        notes="FAA map region: Map - Main",
    )
    session.add(installation)
    session.commit()
    return {"installation_id": installation.id, "old_source_id": faa_source.id}


def test_update_sets_install_year_vendor_and_repoints_source():
    Session = session_factory()
    with Session() as session:
        ids = _seed_lex_with_faa_installation(session)

        installation, updated = update_lex_installation(session, today=date(2026, 7, 27))

        assert updated is True
        assert installation.install_year == 2022
        assert installation.confirmed_vendor == "Runway Safe"
        assert installation.source is not None
        assert installation.source.url == AIRPORT_IMPROVEMENT_URL
        assert installation.source_id != ids["old_source_id"]
        # old note preserved, new note appended
        assert "FAA map region" in installation.notes
        assert "$24,5M" in installation.notes
        assert "$4M" in installation.notes
        assert "$24M" in installation.notes
        # old source row left in place, just unlinked
        assert session.get(Source, ids["old_source_id"]) is not None


def test_update_is_idempotent_and_does_not_duplicate_the_source():
    Session = session_factory()
    with Session() as session:
        _seed_lex_with_faa_installation(session)

        update_lex_installation(session, today=date(2026, 7, 27))
        _, updated_second_run = update_lex_installation(session, today=date(2026, 7, 28))

        assert updated_second_run is False
        sources = session.scalars(select(Source).where(Source.url == AIRPORT_IMPROVEMENT_URL)).all()
        assert len(sources) == 1


def test_update_raises_if_lex_airport_is_missing():
    Session = session_factory()
    with Session() as session:
        try:
            update_lex_installation(session)
            assert False, "expected SystemExit"
        except SystemExit:
            pass


def test_update_raises_if_installation_is_missing():
    Session = session_factory()
    with Session() as session:
        session.add(Airport(iata_code="LEX", icao_code="KLEX", name="Blue Grass", country="USA"))
        session.commit()
        try:
            update_lex_installation(session)
            assert False, "expected SystemExit"
        except SystemExit:
            pass
