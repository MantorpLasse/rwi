from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Airport, Runway, Signal
from scripts.rename_mdw_runway_13c31c_to_13l31r import (
    NEW_DESIGNATION,
    OLD_DESIGNATION,
    rename_mdw_runway,
)


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_mdw(session: Session) -> dict:
    airport = Airport(iata_code="MDW", icao_code="KMDW", faa_code="MDW", name="Chicago Midway International Airport", country="USA")
    session.add(airport)
    session.flush()
    runway = Runway(airport=airport, designation=OLD_DESIGNATION, length_m=1988, width_m=46)
    session.add(runway)
    session.flush()
    signal = Signal(
        airport=airport, runway=runway, title="Future EMAS lifecycle watch",
        category="replacement_watch", confidence="speculative",
    )
    session.add(signal)
    session.commit()
    return {"airport_id": airport.id, "runway_id": runway.id, "signal_id": signal.id}


def test_rename_updates_designation_and_adds_history_note():
    Session = session_factory()
    with Session() as session:
        ids = _seed_mdw(session)

        stats = rename_mdw_runway(session)

        assert stats == {"runway_renamed": True}
        runway = session.get(Runway, ids["runway_id"])
        assert runway.designation == NEW_DESIGNATION
        assert "13C/31C" in runway.notes  # old name preserved as history
        assert "2025-06-12" in runway.notes


def test_rename_is_idempotent():
    Session = session_factory()
    with Session() as session:
        _seed_mdw(session)

        rename_mdw_runway(session)
        stats_second_run = rename_mdw_runway(session)

        assert stats_second_run == {"runway_renamed": False}


def test_linked_signal_picks_up_the_new_designation_automatically():
    Session = session_factory()
    with Session() as session:
        ids = _seed_mdw(session)

        rename_mdw_runway(session)

        signal = session.get(Signal, ids["signal_id"])
        assert signal.runway.designation == NEW_DESIGNATION


def test_rename_raises_if_mdw_airport_is_missing():
    Session = session_factory()
    with Session() as session:
        try:
            rename_mdw_runway(session)
            assert False, "expected SystemExit"
        except SystemExit:
            pass


def test_rename_is_a_noop_if_runway_already_uses_new_designation():
    Session = session_factory()
    with Session() as session:
        airport = Airport(iata_code="MDW", icao_code="KMDW", name="Chicago Midway International Airport", country="USA")
        session.add(airport)
        session.flush()
        session.add(Runway(airport=airport, designation=NEW_DESIGNATION))
        session.commit()

        stats = rename_mdw_runway(session)

        assert stats == {"runway_renamed": False}
        runway = session.scalar(select(Runway).where(Runway.airport_id == airport.id))
        assert runway.notes is None
