"""Tests for scripts/migrate_source_assertion_legacy_identity_attestation.py
(docs/architecture/rwi-legacy-attached-sourceassertion-identity-governance-
design.md).

Every test uses an isolated temp-file SQLite database (tmp_path). Nothing
in this file ever opens data/runway_safe.db. Modeled directly on
tests/test_source_assertion_identity_resolution_kar1_migration.py, this
repository's own strongest, most recent precedent for a single-table
additive migration test suite.
"""
from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.database import Base
from app.models import Airport, Source, SourceAssertion
from app.services.source_assertion_legacy_identity_attestation import record_legacy_identity_attestation
import scripts.migrate_source_assertion_legacy_identity_attestation as migration

TABLE_NAME = "source_assertion_legacy_identity_attestations"


def _pre_migration_db(path):
    """Full current schema minus the new table itself - the realistic
    "not yet migrated" starting state."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")
    conn.commit()
    conn.close()


def _seed_legacy_assertion(db_path):
    """Seeds one Airport and one SourceAssertion shaped exactly like the
    real legacy-attached class (airport_id set, identity_guard_decision
    NULL, no EvidenceBag) - the minimum realistic starting state."""
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        airport = Airport(name="Test Airport", iata_code="TST", icao_code="KTST", country="USA")
        s.add(airport)
        s.commit()
        source = Source(title="Test grant", source_type="usaspending_grant")
        s.add(source)
        s.commit()
        assertion = SourceAssertion(
            source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
            raw_relevant_text="TEST AIRPORT EMAS project", source_record_identifier="rec-1",
            evidence_quality="direct_strong", review_state="unreviewed",
        )
        s.add(assertion)
        s.commit()
        return airport.id, assertion.id


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
    migration.upgrade(db_path)  # second call must not raise or duplicate anything
    assert migration.inspect(db_path)["ready"] is True


def test_upgrade_preserves_pre_existing_data(tmp_path):
    db_path = tmp_path / "test.db"
    _pre_migration_db(db_path)
    airport_id, assertion_id = _seed_legacy_assertion(db_path)

    migration.upgrade(db_path)

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        assert s.get(Airport, airport_id) is not None
        assert s.get(SourceAssertion, assertion_id) is not None


def test_upgrade_rejects_incompatible_existing_table(tmp_path):
    db_path = tmp_path / "test.db"
    _pre_migration_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute(f"CREATE TABLE {TABLE_NAME} (id INTEGER PRIMARY KEY, wrong_column TEXT)")
    conn.commit()
    conn.close()

    with pytest.raises(migration.IncompatibleExistingSchemaError):
        migration.upgrade(db_path)

    # Left completely untouched - not silently rebuilt.
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
    _airport_id, assertion_id = _seed_legacy_assertion(db_path)
    migration.upgrade(db_path)

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        assertion = s.get(SourceAssertion, assertion_id)
        record_legacy_identity_attestation(
            s, source_assertion_id=assertion_id, action="DEFER_IDENTITY_REVIEW",
            reason="needs more research", reviewer="human:tester",
        )
        s.commit()

    with pytest.raises(RuntimeError, match="refused"):
        migration.downgrade(db_path)
    assert migration.inspect(db_path)["table_exists"] is True
    assert migration.inspect(db_path)["count"] == 1


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
