import pytest
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Airport, EmasBed, Runway, RunwayEnd


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


def _runway(designation: str = "09/27") -> Runway:
    airport = Airport(name=f"Airport {designation}", country="USA")
    return Runway(airport=airport, designation=designation)


def test_emas_bed_table_contract():
    table = EmasBed.__table__

    assert table.name == "emas_beds"
    assert [(column.name, str(column.type), column.nullable) for column in table.columns] == [
        ("id", "INTEGER", False),
        ("runway_end_id", "INTEGER", False),
        ("manufacturer", "VARCHAR(150)", True),
        ("product_name", "VARCHAR(100)", True),
        ("installation_year", "INTEGER", True),
        ("replacement_year", "INTEGER", True),
        ("status", "VARCHAR(30)", False),
        ("length_m", "FLOAT", True),
        ("width_m", "FLOAT", True),
        ("faa_accepted", "BOOLEAN", True),
        ("notes", "TEXT", True),
        ("is_current", "BOOLEAN", False),
    ]
    assert {foreign_key.target_fullname for foreign_key in table.c.runway_end_id.foreign_keys} == {
        "runway_ends.id"
    }
    assert table.c.is_current.default.arg is True

    indexes = {
        (index.name, tuple(column.name for column in index.columns), index.unique)
        for index in table.indexes
    }
    assert indexes == {
        ("ix_emas_beds_installation_year", ("installation_year",), False),
        ("ix_emas_beds_runway_end_id", ("runway_end_id",), False),
        ("ix_emas_beds_status", ("status",), False),
        ("uq_emas_beds_current_runway_end", ("runway_end_id",), True),
    }


def test_sqlite_current_bed_index_is_partial(engine):
    indexes = inspect(engine).get_indexes("emas_beds")
    current_index = next(index for index in indexes if index["name"] == "uq_emas_beds_current_runway_end")

    assert current_index["unique"] == 1
    assert str(current_index["dialect_options"]["sqlite_where"]) == "is_current = 1"


def test_runway_end_and_emas_bed_relationships_work(engine):
    with Session(engine) as session:
        runway_end = RunwayEnd(runway=_runway(), designation="09")
        bed = EmasBed(status="existing")
        runway_end.emas_beds.append(bed)
        session.add(runway_end)
        session.commit()
        session.expire_all()

        loaded = session.scalar(select(RunwayEnd).where(RunwayEnd.id == runway_end.id))
        assert loaded.emas_beds == [bed]
        assert bed.runway_end is loaded


def test_one_current_bed_plus_historical_beds_is_allowed(engine):
    with Session(engine) as session:
        runway_end = RunwayEnd(runway=_runway(), designation="09")
        runway_end.emas_beds.extend(
            [
                EmasBed(status="removed", is_current=False),
                EmasBed(status="replaced", is_current=False),
                EmasBed(status="existing", is_current=True),
            ]
        )
        session.add(runway_end)
        session.commit()

        assert len(session.scalars(select(EmasBed)).all()) == 3


def test_two_current_beds_for_same_runway_end_fail(engine):
    with Session(engine) as session:
        runway_end = RunwayEnd(runway=_runway(), designation="09")
        runway_end.emas_beds.extend(
            [EmasBed(status="existing"), EmasBed(status="existing")]
        )
        session.add(runway_end)

        with pytest.raises(IntegrityError):
            session.commit()


def test_current_beds_on_different_runway_ends_are_allowed(engine):
    with Session(engine) as session:
        runway = _runway()
        first_end = RunwayEnd(runway=runway, designation="09")
        second_end = RunwayEnd(runway=runway, designation="27")
        first_end.emas_beds.append(EmasBed(status="existing"))
        second_end.emas_beds.append(EmasBed(status="existing"))
        session.add(runway)
        session.commit()

        assert len(session.scalars(select(EmasBed)).all()) == 2


def test_removing_historical_bed_from_relationship_does_not_delete_it(engine):
    with Session(engine) as session:
        runway_end = RunwayEnd(runway=_runway(), designation="09")
        historical_bed = EmasBed(status="removed", is_current=False)
        runway_end.emas_beds.append(historical_bed)
        session.add(runway_end)
        session.commit()
        historical_bed_id = historical_bed.id

        runway_end.emas_beds.remove(historical_bed)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        preserved = session.get(EmasBed, historical_bed_id)
        assert preserved is not None
        assert preserved.runway_end_id == runway_end.id
