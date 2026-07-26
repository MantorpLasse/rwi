from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Airport, Installation, Source
from scripts.import_faa_fact_sheets_2011_2016 import (
    AIRPORT_UPDATES,
    FACT_SHEET_2011_URL,
    FACT_SHEET_2016_URL,
    seed,
)

EXISTING_CODES = {
    "ORD": "Chicago O'Hare International", "BGM": "Greater Binghamton Airport",
    "HYA": "Cape Cod Gateway Airport", "MHT": "Manchester-Boston Regional Airport",
    "CLT": "Charlotte Douglas International", "SUA": "Martin County Witham Field",
    "DJT": "President Donald J. Trump International", "ROC": "Greater Rochester International",
    "BTR": "Baton Rouge Metropolitan", "LRD": "Laredo International",
    "SAN": "San Diego International", "INT": "Smith Reynolds", "ILG": "New Castle County",
    "FRG": "Republic", "AUG": "Augusta State", "GON": "Groton-New London",
}


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_prerequisites(session: Session) -> dict:
    faa_source = Source(title="FAA map", source_type="faa_tableau", url="https://old.test/faa-map")
    session.add(faa_source)
    session.flush()

    ids = {}
    for code, name in EXISTING_CODES.items():
        airport = Airport(iata_code=code, icao_code=f"K{code}"[:4], faa_code=code, name=name, country="USA")
        session.add(airport)
        session.flush()
        installation = Installation(
            airport=airport, source=faa_source, type="EMASMAX", status="active",
            notes="FAA map region: Map - Main",
        )
        session.add(installation)
        session.flush()
        ids[code] = {"airport_id": airport.id, "installation_id": installation.id}

    elm = Airport(iata_code="ELM", icao_code="KELM", faa_code="ELM", name="Elmira-Corning", country="USA")
    session.add(elm)
    session.flush()
    elm_installation = Installation(
        airport=elm, source=faa_source, type="EMASMAX", runway_end="06", status="active",
        notes="FAA map region: Map - Main",
    )
    session.add(elm_installation)
    session.flush()
    ids["ELM"] = {"airport_id": elm.id, "installation_id": elm_installation.id}

    session.commit()
    return ids


def test_seed_adds_new_installation_row_per_airport_leaving_old_one_untouched():
    Session = session_factory()
    with Session() as session:
        ids = _seed_prerequisites(session)

        stats = seed(session)

        assert stats["installations_created"] == len(AIRPORT_UPDATES) + 1  # +1 for Dutchess County
        for code, install_year, _source_key, _notes in AIRPORT_UPDATES:
            airport_id = ids[code]["airport_id"]
            installations = session.scalars(
                select(Installation).where(Installation.airport_id == airport_id)
            ).all()
            assert len(installations) == 2
            old = session.get(Installation, ids[code]["installation_id"])
            assert old.install_year is None  # untouched
            new = next(i for i in installations if i.id != old.id)
            assert new.install_year == install_year


def test_bgm_new_row_cites_2016_and_notes_replacement_and_retrofit_detail():
    Session = session_factory()
    with Session() as session:
        ids = _seed_prerequisites(session)

        seed(session)

        installations = session.scalars(
            select(Installation).where(Installation.airport_id == ids["BGM"]["airport_id"])
        ).all()
        new = next(i for i in installations if i.install_year == 2002)
        assert new.source.url == FACT_SHEET_2016_URL
        assert "2012" in new.notes
        assert "2009" in new.notes
        assert "retrofitted bed" in new.notes


def test_groton_new_row_cites_2011_as_primary_source():
    Session = session_factory()
    with Session() as session:
        ids = _seed_prerequisites(session)

        seed(session)

        installations = session.scalars(
            select(Installation).where(Installation.airport_id == ids["GON"]["airport_id"])
        ).all()
        new = next(i for i in installations if i.install_year == 2011)
        assert new.source.url == FACT_SHEET_2011_URL  # more granular there, not 2016
        assert "2012" in new.notes


def test_dutchess_county_created_as_new_airport():
    Session = session_factory()
    with Session() as session:
        _seed_prerequisites(session)

        stats = seed(session)

        assert stats["airports_created"] == 1
        dutchess = session.scalar(select(Airport).where(Airport.iata_code == "POU"))
        assert dutchess is not None
        assert dutchess.country == "USA"
        installation = session.scalar(select(Installation).where(Installation.airport_id == dutchess.id))
        assert installation.install_year == 2004


def test_elm_gets_install_year_set_without_changing_source():
    Session = session_factory()
    with Session() as session:
        ids = _seed_prerequisites(session)
        elm_installation_before = session.get(Installation, ids["ELM"]["installation_id"])
        original_source_id = elm_installation_before.source_id

        stats = seed(session)

        assert stats["elm_updated"] is True
        elm_installation = session.get(Installation, ids["ELM"]["installation_id"])
        assert elm_installation.install_year == 2012
        assert elm_installation.source_id == original_source_id  # unchanged (still FAA map)
        assert "2012" in elm_installation.notes


def test_seed_only_creates_two_new_sources():
    Session = session_factory()
    with Session() as session:
        _seed_prerequisites(session)

        seed(session)

        sources_2011 = session.scalars(select(Source).where(Source.url == FACT_SHEET_2011_URL)).all()
        sources_2016 = session.scalars(select(Source).where(Source.url == FACT_SHEET_2016_URL)).all()
        assert len(sources_2011) == 1
        assert len(sources_2016) == 1


def test_seed_is_idempotent():
    Session = session_factory()
    with Session() as session:
        _seed_prerequisites(session)

        seed(session)
        stats_second_run = seed(session)

        assert stats_second_run == {
            "airports_created": 0,
            "sources_created": 0,
            "installations_created": 0,
            "elm_updated": False,
        }
