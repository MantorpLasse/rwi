"""RunwayEnd model contract and PhysicalInstallationIdentity.runway_end_id
behavior (docs/domain/canonical-runway-runway-end-design.md)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, PhysicalInstallationIdentity, Runway, RunwayEnd
from app.services.physical_installation_reconciliation import create_physical_installation_identity


@pytest.fixture
def engine():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.commit()
    return engine


def test_runway_end_belongs_to_a_runway_and_has_a_designation(engine):
    with Session(engine) as session:
        airport = Airport(name="Cuyahoga", country="USA")
        runway = Runway(airport=airport, designation="6/24")
        session.add(runway)
        session.flush()
        end = RunwayEnd(runway_id=runway.id, designation="6")
        session.add(end)
        session.commit()

        assert end.runway.designation == "6/24"
        assert runway.runway_ends[0].designation == "6"


def test_runway_end_designation_is_unique_per_runway(engine):
    with Session(engine) as session:
        airport = Airport(name="Cuyahoga", country="USA")
        runway = Runway(airport=airport, designation="6/24")
        session.add(runway)
        session.flush()
        session.add(RunwayEnd(runway_id=runway.id, designation="6"))
        session.commit()

        session.add(RunwayEnd(runway_id=runway.id, designation="6"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_same_designation_is_allowed_on_a_different_runway(engine):
    """Uniqueness is scoped to (runway_id, designation), not designation alone."""
    with Session(engine) as session:
        airport = Airport(name="MDW", country="USA")
        runway_a = Runway(airport=airport, designation="4L/22R")
        runway_b = Runway(airport=airport, designation="4R/22L")
        session.add_all([runway_a, runway_b])
        session.flush()
        session.add(RunwayEnd(runway_id=runway_a.id, designation="4L"))
        session.add(RunwayEnd(runway_id=runway_b.id, designation="4R"))
        session.commit()  # no conflict - different runways


def test_existing_physical_installation_identities_remain_valid_with_null_runway_end_id(engine):
    with Session(engine) as session:
        airport = Airport(name="MDW", country="USA")
        session.add(airport)
        session.flush()
        identity = PhysicalInstallationIdentity(airport_id=airport.id, runway_end="04R")
        session.add(identity)
        session.commit()

        fetched = session.get(PhysicalInstallationIdentity, identity.id)
        assert fetched.runway_end_id is None
        assert fetched.runway_end == "04R"  # untouched


def test_create_physical_installation_identity_accepts_runway_end_id(engine):
    with Session(engine) as session:
        airport = Airport(name="MDW", country="USA")
        runway = Runway(airport=airport, designation="4R/22L")
        session.add(runway)
        session.flush()
        end = RunwayEnd(runway_id=runway.id, designation="4R")
        session.add(end)
        session.flush()

        identity = create_physical_installation_identity(
            session, airport_id=airport.id, runway_end="04R", runway_end_id=end.id
        )
        session.commit()
        assert identity.runway_end_id == end.id
        assert identity.canonical_runway_end.designation == "4R"


def test_create_physical_installation_identity_rejects_a_runway_end_from_another_airport(engine):
    with Session(engine) as session:
        airport_a = Airport(name="A", country="USA")
        airport_b = Airport(name="B", country="USA")
        session.add_all([airport_a, airport_b])
        session.flush()
        runway_b = Runway(airport=airport_b, designation="6/24")
        session.add(runway_b)
        session.flush()
        end_b = RunwayEnd(runway_id=runway_b.id, designation="6")
        session.add(end_b)
        session.flush()

        with pytest.raises(ValueError, match="must belong to the physical identity airport"):
            create_physical_installation_identity(session, airport_id=airport_a.id, runway_end_id=end_b.id)


def test_deleting_a_runway_cascades_its_ends(engine):
    with Session(engine) as session:
        airport = Airport(name="Cuyahoga", country="USA")
        runway = Runway(airport=airport, designation="6/24")
        session.add(runway)
        session.flush()
        session.add_all([RunwayEnd(runway_id=runway.id, designation="6"), RunwayEnd(runway_id=runway.id, designation="24")])
        session.commit()

        session.delete(runway)
        session.commit()

        assert session.query(RunwayEnd).count() == 0
