import pytest
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Airport, Runway, RunwayEnd


@pytest.fixture
def engine():
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(test_engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(test_engine)
    try:
        yield test_engine
    finally:
        test_engine.dispose()


def _airport(name: str, code: str) -> Airport:
    return Airport(name=name, iata_code=code, country="USA")


def test_runway_end_table_contract():
    table = RunwayEnd.__table__

    assert table.name == "runway_ends"
    assert list(table.columns) == [
        table.c.id,
        table.c.runway_id,
        table.c.designation,
        table.c.heading,
        table.c.resa_length_m,
        table.c.notes,
    ]
    assert table.c.runway_id.nullable is False
    assert {foreign_key.target_fullname for foreign_key in table.c.runway_id.foreign_keys} == {"runways.id"}


def test_runway_end_schema_is_created_with_unique_constraint(engine):
    inspector = inspect(engine)
    constraints = inspector.get_unique_constraints("runway_ends")

    assert any(constraint["column_names"] == ["runway_id", "designation"] for constraint in constraints)


def test_duplicate_designation_on_same_runway_fails(engine):
    with Session(engine) as session:
        runway = Runway(airport=_airport("Alpha Airport", "AAA"), designation="09/27")
        runway.runway_ends.extend(
            [RunwayEnd(designation="09"), RunwayEnd(designation="09")]
        )
        session.add(runway)

        with pytest.raises(IntegrityError):
            session.commit()


def test_same_designation_on_different_runways_is_allowed(engine):
    with Session(engine) as session:
        first = Runway(airport=_airport("Alpha Airport", "AAA"), designation="09/27")
        second = Runway(airport=_airport("Bravo Airport", "BBB"), designation="09/27")
        first.runway_ends.append(RunwayEnd(designation="09"))
        second.runway_ends.append(RunwayEnd(designation="09"))
        session.add_all([first, second])
        session.commit()

        assert session.scalar(select(RunwayEnd).where(RunwayEnd.runway_id == first.id)).designation == "09"
        assert session.scalar(select(RunwayEnd).where(RunwayEnd.runway_id == second.id)).designation == "09"


def test_runway_and_runway_end_relationships_work(engine):
    with Session(engine) as session:
        runway = Runway(airport=_airport("Alpha Airport", "AAA"), designation="09/27")
        first_end = RunwayEnd(designation="09", heading=90)
        second_end = RunwayEnd(designation="27", heading=270)
        runway.runway_ends.extend([first_end, second_end])
        session.add(runway)
        session.commit()
        session.expire_all()

        loaded = session.scalar(select(Runway).where(Runway.id == runway.id))
        assert [runway_end.designation for runway_end in loaded.runway_ends] == ["09", "27"]
        assert first_end.runway is loaded
        assert second_end.runway is loaded
