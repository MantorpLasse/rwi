"""Tests for scripts/migrate_airport_coordinate.py (RWI Mission #26G).

Every test uses an isolated temp-file SQLite database (tmp_path). Nothing
in this file ever opens data/runway_safe.db.
"""
from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.database import Base
from app.models import Airport, Source, SourceAssertion
import scripts.migrate_airport_coordinate as migration

TABLE_NAME = "airport_coordinates"


def _pre_migration_db(path):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")
    conn.commit()
    conn.close()


def _seed_airport_and_legacy_coordinate(db_path):
    """Simulates a pre-migration DB with an already-populated legacy
    Airport coordinate (e.g. an FAA/Tableau-backed row) - the migration
    must leave this completely untouched."""
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        airport = Airport(name="Legacy Airport", country="USA", latitude=42.36, longitude=-71.0)
        s.add(airport)
        s.commit()
        return airport.id, airport.latitude, airport.longitude


def test_upgrade_creates_table_matching_orm_model(tmp_path):
    db_path = tmp_path / "test.db"
    _pre_migration_db(db_path)

    inspected_before = migration.inspect(db_path)
    assert inspected_before["table_exists"] is False
    assert inspected_before["ready"] is False

    migration.upgrade(db_path)

    inspected_after = migration.inspect(db_path)
    assert inspected_after["table_exists"] is True
    assert inspected_after["matches_expected_schema"] is True
    assert inspected_after["ready"] is True
    assert inspected_after["count"] == 0
    assert inspected_after["foreign_key_check"] == []
    for constraint_name, present in inspected_after["named_constraints_present"].items():
        assert present, f"missing constraint {constraint_name}"


def test_upgrade_never_inserts_a_row(tmp_path):
    db_path = tmp_path / "test.db"
    _pre_migration_db(db_path)
    migration.upgrade(db_path)
    assert migration.inspect(db_path)["count"] == 0


def test_upgrade_never_touches_existing_airport_data(tmp_path):
    db_path = tmp_path / "test.db"
    _pre_migration_db(db_path)
    airport_id, lat_before, lon_before = _seed_airport_and_legacy_coordinate(db_path)

    migration.upgrade(db_path)

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        airport = s.get(Airport, airport_id)
        assert airport.latitude == lat_before
        assert airport.longitude == lon_before


def test_upgrade_idempotent_on_already_matching_table(tmp_path):
    db_path = tmp_path / "test.db"
    _pre_migration_db(db_path)
    migration.upgrade(db_path)
    migration.upgrade(db_path)  # second call: table already exists and matches - no error
    assert migration.inspect(db_path)["ready"] is True


def test_downgrade_refuses_when_rows_exist(tmp_path):
    db_path = tmp_path / "test.db"
    _pre_migration_db(db_path)
    migration.upgrade(db_path)

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        airport = Airport(name="Test Airport", country="Testland")
        s.add(airport)
        s.commit()
        source = Source(title="Test Source", source_type="web_discovery", reliability_level="unverified")
        s.add(source)
        s.commit()
        assertion = SourceAssertion(
            source_id=source.id, airport_id=airport.id, assertion_type="airport_inventory",
            raw_relevant_text="Latitude 1 Longitude 2", source_record_identifier="mig-test-1",
        )
        s.add(assertion)
        s.commit()
        from app.models import AirportCoordinate
        row = AirportCoordinate(
            airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
            latitude=1.0, longitude=2.0, evidence_excerpt="Latitude 1 Longitude 2",
            analyst="x", status="ADMITTED",
        )
        s.add(row)
        s.commit()

    with pytest.raises(RuntimeError):
        migration.downgrade(db_path)
    assert migration.inspect(db_path)["table_exists"] is True


def test_downgrade_succeeds_when_empty(tmp_path):
    db_path = tmp_path / "test.db"
    _pre_migration_db(db_path)
    migration.upgrade(db_path)
    migration.downgrade(db_path)
    assert migration.inspect(db_path)["table_exists"] is False
