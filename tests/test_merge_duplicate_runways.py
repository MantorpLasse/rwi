from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Airport, Installation, Runway, Signal
from scripts.merge_duplicate_runways import merge_duplicates


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_mht_style_duplicate(session: Session):
    airport = Airport(name="Manchester-Boston Regional Airport", faa_code="MHT", country="USA")
    session.add(airport)
    session.flush()
    seeded = Runway(airport=airport, designation="6/24", length_m=2926, width_m=46)
    duplicate = Runway(airport=airport, designation="06/24")
    session.add_all([seeded, duplicate])
    session.flush()
    installation = Installation(airport=airport, runway=duplicate, runway_end="06", type="EMASMAX", status="active")
    signal = Signal(airport=airport, runway=duplicate, title="watch", category="replacement", confidence="low")
    session.add_all([installation, signal])
    session.commit()
    return airport.id, seeded.id, duplicate.id, installation.id, signal.id


def test_merge_duplicates_repoints_installation_and_signal_and_deletes_duplicate():
    Session = session_factory()
    with Session() as session:
        airport_id, seeded_id, duplicate_id, installation_id, signal_id = _seed_mht_style_duplicate(session)

        stats = merge_duplicates(session)

        assert stats == {
            "groups_merged": 1,
            "runways_deleted": 1,
            "installations_repointed": 1,
            "signals_repointed": 1,
        }

        remaining = session.scalars(select(Runway).where(Runway.airport_id == airport_id)).all()
        assert len(remaining) == 1
        assert remaining[0].id == seeded_id
        assert remaining[0].length_m == 2926  # richer seed row kept, not overwritten

        assert session.get(Runway, duplicate_id) is None
        assert session.get(Installation, installation_id).runway_id == seeded_id
        assert session.get(Signal, signal_id).runway_id == seeded_id


def test_merge_duplicates_normalizes_canonical_designation_even_without_leading_zero():
    Session = session_factory()
    with Session() as session:
        _, seeded_id, _, _, _ = _seed_mht_style_duplicate(session)
        merge_duplicates(session)
        assert session.get(Runway, seeded_id).designation == "6/24"


def test_merge_duplicates_is_idempotent():
    Session = session_factory()
    with Session() as session:
        _seed_mht_style_duplicate(session)

        merge_duplicates(session)
        stats_second_run = merge_duplicates(session)

        assert stats_second_run == {
            "groups_merged": 0,
            "runways_deleted": 0,
            "installations_repointed": 0,
            "signals_repointed": 0,
        }


def test_merge_duplicates_leaves_unrelated_single_runways_untouched():
    Session = session_factory()
    with Session() as session:
        airport = Airport(name="Test", faa_code="ZZZ", country="USA")
        session.add(airport)
        session.flush()
        session.add(Runway(airport=airport, designation="9/27"))
        session.commit()

        stats = merge_duplicates(session)

        assert stats["groups_merged"] == 0
        assert len(session.scalars(select(Runway)).all()) == 1
