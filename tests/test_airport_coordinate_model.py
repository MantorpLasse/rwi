"""RWI Mission #26G - offline tests for app.models.airport_coordinate's
model-level shape: constraints, append-only enforcement, nullability.

Every test builds its own isolated temp-file SQLite database via
Base.metadata.create_all(); no network, no LLM, never touches
data/runway_safe.db."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, AirportCoordinate, Source, SourceAssertion


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'test.db'}")

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    return eng


def _seed_airport_source_assertion(session: Session) -> dict:
    airport = Airport(name="Test Airport", country="Testland")
    session.add(airport)
    session.commit()
    source = Source(title="Test Source", source_type="web_discovery", reliability_level="unverified")
    session.add(source)
    session.commit()
    assertion = SourceAssertion(
        source_id=source.id, airport_id=airport.id, assertion_type="airport_inventory",
        raw_relevant_text="Latitude 12.34 Longitude 56.78", source_record_identifier="coord-model-1",
    )
    session.add(assertion)
    session.commit()
    return {"airport_id": airport.id, "source_id": source.id, "assertion_id": assertion.id}


def _make_row(**overrides) -> AirportCoordinate:
    defaults = dict(
        latitude=12.34, longitude=56.78, evidence_excerpt="Latitude 12.34 Longitude 56.78",
        analyst="test-analyst", status="ADMITTED",
    )
    defaults.update(overrides)
    return AirportCoordinate(**defaults)


# --- 1: model table creation ------------------------------------------------


def test_table_created(engine):
    with Session(engine) as s:
        assert s.scalar(select(func.count(AirportCoordinate.id))) == 0


# --- 2-5: latitude/longitude range CHECK constraints -----------------------


def test_latitude_boundaries_accepted(engine):
    with Session(engine) as s:
        ids = _seed_airport_source_assertion(s)
        for lat in (-90.0, 90.0):
            row = _make_row(airport_id=ids["airport_id"], source_id=ids["source_id"], source_assertion_id=ids["assertion_id"], latitude=lat)
            s.add(row)
            s.commit()


def test_latitude_below_minus_90_rejected(engine):
    with Session(engine) as s:
        ids = _seed_airport_source_assertion(s)
        row = _make_row(airport_id=ids["airport_id"], source_id=ids["source_id"], source_assertion_id=ids["assertion_id"], latitude=-90.001)
        s.add(row)
        with pytest.raises(IntegrityError):
            s.commit()


def test_latitude_above_90_rejected(engine):
    with Session(engine) as s:
        ids = _seed_airport_source_assertion(s)
        row = _make_row(airport_id=ids["airport_id"], source_id=ids["source_id"], source_assertion_id=ids["assertion_id"], latitude=90.001)
        s.add(row)
        with pytest.raises(IntegrityError):
            s.commit()


def test_longitude_boundaries_accepted(engine):
    with Session(engine) as s:
        ids = _seed_airport_source_assertion(s)
        for lon in (-180.0, 180.0):
            row = _make_row(airport_id=ids["airport_id"], source_id=ids["source_id"], source_assertion_id=ids["assertion_id"], longitude=lon)
            s.add(row)
            s.commit()


def test_longitude_below_minus_180_rejected(engine):
    with Session(engine) as s:
        ids = _seed_airport_source_assertion(s)
        row = _make_row(airport_id=ids["airport_id"], source_id=ids["source_id"], source_assertion_id=ids["assertion_id"], longitude=-180.001)
        s.add(row)
        with pytest.raises(IntegrityError):
            s.commit()


def test_longitude_above_180_rejected(engine):
    with Session(engine) as s:
        ids = _seed_airport_source_assertion(s)
        row = _make_row(airport_id=ids["airport_id"], source_id=ids["source_id"], source_assertion_id=ids["assertion_id"], longitude=180.001)
        s.add(row)
        with pytest.raises(IntegrityError):
            s.commit()


# --- 6: status CHECK ---------------------------------------------------------


def test_invalid_status_rejected(engine):
    with Session(engine) as s:
        ids = _seed_airport_source_assertion(s)
        row = _make_row(airport_id=ids["airport_id"], source_id=ids["source_id"], source_assertion_id=ids["assertion_id"], status="BOGUS")
        s.add(row)
        with pytest.raises(IntegrityError):
            s.commit()


def test_valid_statuses_all_accepted(engine):
    with Session(engine) as s:
        ids = _seed_airport_source_assertion(s)
        for status in ("ADMITTED", "REJECTED", "RETIRED"):
            row = _make_row(airport_id=ids["airport_id"], source_id=ids["source_id"], source_assertion_id=ids["assertion_id"], status=status)
            s.add(row)
            s.commit()


# --- 7: required fields ------------------------------------------------------


def test_required_fields_enforced(engine):
    with Session(engine) as s:
        ids = _seed_airport_source_assertion(s)
        for missing_field in ("airport_id", "source_id", "source_assertion_id", "evidence_excerpt", "analyst"):
            kwargs = dict(
                airport_id=ids["airport_id"], source_id=ids["source_id"], source_assertion_id=ids["assertion_id"],
                latitude=1.0, longitude=1.0, evidence_excerpt="x", analyst="y", status="ADMITTED",
            )
            kwargs[missing_field] = None
            row = AirportCoordinate(**kwargs)
            s.add(row)
            with pytest.raises(IntegrityError):
                s.commit()
            s.rollback()


# --- 8: FK enforcement --------------------------------------------------------


def test_unknown_airport_id_fk_violation(engine):
    with Session(engine) as s:
        ids = _seed_airport_source_assertion(s)
        row = _make_row(airport_id=999999, source_id=ids["source_id"], source_assertion_id=ids["assertion_id"])
        s.add(row)
        with pytest.raises(IntegrityError):
            s.commit()


# --- 9-10: append-only (before_update / before_delete) ----------------------


def test_before_update_raises(engine):
    with Session(engine) as s:
        ids = _seed_airport_source_assertion(s)
        row = _make_row(airport_id=ids["airport_id"], source_id=ids["source_id"], source_assertion_id=ids["assertion_id"])
        s.add(row)
        s.commit()
        row.analyst = "changed-analyst"
        with pytest.raises(ValueError):
            s.commit()


def test_before_delete_raises(engine):
    with Session(engine) as s:
        ids = _seed_airport_source_assertion(s)
        row = _make_row(airport_id=ids["airport_id"], source_id=ids["source_id"], source_assertion_id=ids["assertion_id"])
        s.add(row)
        s.commit()
        s.delete(row)
        with pytest.raises(ValueError):
            s.commit()


# --- 11-12: nullable datum / coordinate_semantic_type ------------------------


def test_datum_and_semantic_type_nullable(engine):
    with Session(engine) as s:
        ids = _seed_airport_source_assertion(s)
        row = _make_row(
            airport_id=ids["airport_id"], source_id=ids["source_id"], source_assertion_id=ids["assertion_id"],
            datum=None, coordinate_semantic_type=None,
        )
        s.add(row)
        s.commit()
        assert row.datum is None
        assert row.coordinate_semantic_type is None

        row2 = _make_row(
            airport_id=ids["airport_id"], source_id=ids["source_id"], source_assertion_id=ids["assertion_id"],
            datum="WGS84", coordinate_semantic_type="ARP",
        )
        s.add(row2)
        s.commit()
        assert row2.datum == "WGS84"
        assert row2.coordinate_semantic_type == "ARP"


# --- 13: supersedes self-FK shape --------------------------------------------


def test_supersedes_self_fk(engine):
    with Session(engine) as s:
        ids = _seed_airport_source_assertion(s)
        row1 = _make_row(airport_id=ids["airport_id"], source_id=ids["source_id"], source_assertion_id=ids["assertion_id"])
        s.add(row1)
        s.commit()
        assert row1.supersedes_coordinate_id is None

        row2 = _make_row(
            airport_id=ids["airport_id"], source_id=ids["source_id"], source_assertion_id=ids["assertion_id"],
            latitude=1.0, longitude=1.0, supersedes_coordinate_id=row1.id,
        )
        s.add(row2)
        s.commit()
        assert row2.supersedes_coordinate_id == row1.id
