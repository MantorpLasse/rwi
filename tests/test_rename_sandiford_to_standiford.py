from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Airport, Installation
from scripts.rename_sandiford_to_standiford import (
    NEW_NAME,
    OLD_NAME,
    OLD_NOTE_FRAGMENT,
    rename,
)


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_sdf(session: Session) -> dict:
    airport = Airport(iata_code="SDF", icao_code="KSDF", faa_code="SDF", name=OLD_NAME, country="USA")
    session.add(airport)
    session.flush()
    generic = Installation(airport=airport, type="EMASMAX", status="active", notes="FAA map region: Map - Main")
    dated = Installation(
        airport=airport, type="EMASMAX", install_year=2015, status="active",
        notes=(
            "1 system, hosten 2015, enligt 2016 Fact Sheet ('Sandiford, Louisville, KY, 1, "
            f"fall 2015'). {OLD_NOTE_FRAGMENT}"
        ),
    )
    session.add_all([generic, dated])
    session.commit()
    return {"airport_id": airport.id, "generic_id": generic.id, "dated_id": dated.id}


def test_rename_updates_airport_name_and_installation_note():
    Session = session_factory()
    with Session() as session:
        ids = _seed_sdf(session)

        stats = rename(session)

        assert stats == {"airport_renamed": True, "installation_note_updated": True}
        airport = session.get(Airport, ids["airport_id"])
        assert airport.name == NEW_NAME

        dated = session.get(Installation, ids["dated_id"])
        assert "RATTAD" in dated.notes
        assert "Sandiford, Louisville, KY, 1, fall 2015" in dated.notes  # verbatim source quote preserved
        assert OLD_NOTE_FRAGMENT not in dated.notes

        generic = session.get(Installation, ids["generic_id"])
        assert generic.notes == "FAA map region: Map - Main"  # untouched, no name mention


def test_rename_is_idempotent():
    Session = session_factory()
    with Session() as session:
        _seed_sdf(session)

        rename(session)
        stats_second_run = rename(session)

        assert stats_second_run == {"airport_renamed": False, "installation_note_updated": False}


def test_rename_raises_if_sdf_airport_is_missing():
    Session = session_factory()
    with Session() as session:
        try:
            rename(session)
            assert False, "expected SystemExit"
        except SystemExit:
            pass
