"""Tests for scripts/migrate_airport_identifier.py (docs/architecture,
"RWI - Governed Canonical Airport Identifiers - Architecture Design"
mission).

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
from app.services.airport_identifier import record_airport_identifier
import scripts.migrate_airport_identifier as migration

TABLE_NAME = "airport_identifiers"


def _pre_migration_db(path):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")
    conn.commit()
    conn.close()


def _seed_admission_fixture(db_path):
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        airport = Airport(name="Test Airport", country="Testland")
        s.add(airport)
        s.commit()
        source = Source(title="Official registry", source_type="government", reliability_level="official")
        s.add(source)
        s.commit()
        excerpt = "Test Airport (TST) is the code. TST(IATA)"
        assertion = SourceAssertion(
            source_id=source.id, airport_id=airport.id, assertion_type="airport_inventory",
            raw_relevant_text=excerpt, source_record_identifier="rec-1", evidence_quality="direct_strong",
        )
        s.add(assertion)
        s.commit()
        return source.id, airport.id, assertion.id, excerpt


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


def test_upgrade_is_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    _pre_migration_db(db_path)
    migration.upgrade(db_path)
    migration.upgrade(db_path)
    assert migration.inspect(db_path)["ready"] is True


def test_upgrade_preserves_pre_existing_data(tmp_path):
    db_path = tmp_path / "test.db"
    _pre_migration_db(db_path)
    _source_id, airport_id, assertion_id, _excerpt = _seed_admission_fixture(db_path)

    migration.upgrade(db_path)

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        assert s.get(Airport, airport_id) is not None
        assert s.get(SourceAssertion, assertion_id) is not None


def test_upgrade_never_touches_airport_code_columns(tmp_path):
    db_path = tmp_path / "test.db"
    _pre_migration_db(db_path)
    _source_id, airport_id, _assertion_id, _excerpt = _seed_admission_fixture(db_path)

    migration.upgrade(db_path)

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        airport = s.get(Airport, airport_id)
        assert airport.iata_code is None
        assert airport.icao_code is None
        assert airport.faa_code is None


def test_upgrade_rejects_incompatible_existing_table(tmp_path):
    db_path = tmp_path / "test.db"
    _pre_migration_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute(f"CREATE TABLE {TABLE_NAME} (id INTEGER PRIMARY KEY, wrong_column TEXT)")
    conn.commit()
    conn.close()

    with pytest.raises(migration.IncompatibleExistingSchemaError):
        migration.upgrade(db_path)

    conn = sqlite3.connect(str(db_path))
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({TABLE_NAME})")}
    conn.close()
    assert cols == {"id", "wrong_column"}


def test_downgrade_drops_empty_table(tmp_path):
    db_path = tmp_path / "test.db"
    _pre_migration_db(db_path)
    migration.upgrade(db_path)
    migration.downgrade(db_path)
    assert migration.inspect(db_path)["table_exists"] is False


def test_downgrade_refuses_when_rows_exist(tmp_path):
    db_path = tmp_path / "test.db"
    _pre_migration_db(db_path)
    source_id, airport_id, assertion_id, excerpt = _seed_admission_fixture(db_path)
    migration.upgrade(db_path)

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        record_airport_identifier(
            s, airport_id=airport_id, source_id=source_id, source_assertion_id=assertion_id,
            identifier_type="IATA", identifier_value="TST", evidence_excerpt=excerpt,
            analyst="human:tester", type_evidence_token="TST(IATA)",
        )
        s.commit()

    with pytest.raises(RuntimeError, match="refused"):
        migration.downgrade(db_path)
    assert migration.inspect(db_path)["table_exists"] is True
    assert migration.inspect(db_path)["count"] == 1


def test_upgrade_downgrade_upgrade_cycle(tmp_path):
    db_path = tmp_path / "test.db"
    _pre_migration_db(db_path)

    migration.upgrade(db_path)
    first = migration.inspect(db_path)
    assert first["ready"] is True
    assert first["foreign_key_check"] == []

    migration.downgrade(db_path)
    assert migration.inspect(db_path)["table_exists"] is False

    migration.upgrade(db_path)
    second = migration.inspect(db_path)
    assert second["ready"] is True
    assert second["foreign_key_check"] == []

    conn = sqlite3.connect(str(db_path))
    integrity = conn.execute("PRAGMA integrity_check").fetchall()
    conn.close()
    assert integrity == [("ok",)]


def test_backup_created_and_verified(tmp_path):
    db_path = tmp_path / "test.db"
    _pre_migration_db(db_path)
    backup_dir = tmp_path / "backups"

    backup_path = migration.backup_database(db_path, backup_directory=backup_dir)

    assert backup_path.exists()
    assert backup_path.stat().st_size == db_path.stat().st_size


def test_main_requires_allow_database_write(tmp_path):
    db_path = tmp_path / "test.db"
    _pre_migration_db(db_path)
    with pytest.raises(SystemExit):
        migration.main(["--database", str(db_path)])
    assert migration.inspect(db_path)["table_exists"] is False
