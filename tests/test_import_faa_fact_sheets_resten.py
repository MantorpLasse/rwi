from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Airport, Installation, Source
from scripts.import_faa_fact_sheets_resten import (
    AIRPORT_UPDATES,
    UNDER_CONTRACT_ONLY_NOTES,
    seed,
)
from scripts.import_faa_fact_sheets_2011_2016 import FACT_SHEET_2016_URL

ALL_CODES = [code for code, _, _ in AIRPORT_UPDATES] + list(UNDER_CONTRACT_ONLY_NOTES)


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_prerequisites(session: Session) -> dict:
    faa_source = Source(title="FAA map", source_type="faa_tableau", url="https://old.test/faa-map")
    session.add(faa_source)
    session.flush()

    ids = {}
    for code in ALL_CODES:
        airport = Airport(iata_code=code, icao_code=f"K{code}"[:4], faa_code=code, name=f"{code} airport", country="USA")
        session.add(airport)
        session.flush()
        installation = Installation(
            airport=airport, source=faa_source, type="EMASMAX", status="active",
            notes="FAA map region: Map - Main",
        )
        session.add(installation)
        session.flush()
        ids[code] = {"airport_id": airport.id, "installation_id": installation.id}

    session.commit()
    return ids


def test_seed_adds_one_new_dated_row_per_airport_leaving_old_one_untouched():
    Session = session_factory()
    with Session() as session:
        ids = _seed_prerequisites(session)

        stats = seed(session)

        assert stats["installations_created"] == len(AIRPORT_UPDATES)
        for code, install_year, _notes in AIRPORT_UPDATES:
            installations = session.scalars(
                select(Installation).where(Installation.airport_id == ids[code]["airport_id"])
            ).all()
            assert len(installations) == 2
            old = session.get(Installation, ids[code]["installation_id"])
            assert old.install_year is None  # untouched
            new = next(i for i in installations if i.id != old.id)
            assert new.install_year == install_year
            assert new.source.url == FACT_SHEET_2016_URL


def test_under_contract_only_airports_get_a_note_but_no_new_row():
    Session = session_factory()
    with Session() as session:
        ids = _seed_prerequisites(session)

        stats = seed(session)

        assert stats["under_contract_notes_added"] == 3
        for code in UNDER_CONTRACT_ONLY_NOTES:
            installations = session.scalars(
                select(Installation).where(Installation.airport_id == ids[code]["airport_id"])
            ).all()
            assert len(installations) == 1  # no new row
            installation = installations[0]
            assert installation.install_year is None
            assert FACT_SHEET_2016_URL in installation.notes
            assert "FAA map region: Map - Main" in installation.notes  # old note preserved


def test_seed_does_not_duplicate_the_2016_source():
    Session = session_factory()
    with Session() as session:
        _seed_prerequisites(session)

        seed(session)

        sources = session.scalars(select(Source).where(Source.url == FACT_SHEET_2016_URL)).all()
        assert len(sources) == 1


def test_seed_is_idempotent():
    Session = session_factory()
    with Session() as session:
        _seed_prerequisites(session)

        seed(session)
        stats_second_run = seed(session)

        assert stats_second_run == {"installations_created": 0, "under_contract_notes_added": 0}
