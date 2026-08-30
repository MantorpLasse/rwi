"""Tests for scripts/migrate_signal_publication_action.py ("RWI - Signal
Publication Governance - Design + Implementation" mission).

Every test uses an isolated temp-file SQLite database (tmp_path). Nothing in
this file ever opens data/runway_safe.db.
"""
from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.database import Base
from app.models import Airport, Signal
from app.services.signal_publication import record_signal_publication_action
import scripts.migrate_signal_publication_action as migration

TABLE_NAME = "signal_publication_actions"


def _pre_migration_db(path):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")
    conn.commit()
    conn.close()


def _seed_signal_fixture(db_path):
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        airport = Airport(name="Test Airport", country="Testland")
        s.add(airport); s.commit()
        signal = Signal(
            airport_id=airport.id, title="Legacy signal", category="replacement",
            confidence="medium", status="identified", published=False,
        )
        s.add(signal); s.commit()
        return signal.id


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
    signal_id = _seed_signal_fixture(db_path)

    migration.upgrade(db_path)

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        assert s.get(Signal, signal_id) is not None
        assert s.get(Signal, signal_id).published is False  # unchanged, not backfilled


def test_upgrade_does_not_touch_signals_published_column(tmp_path):
    """The signals.published column (Slice 9A) must be completely
    unaffected by this migration - only the new table is created."""
    db_path = tmp_path / "test.db"
    _pre_migration_db(db_path)
    signal_id = _seed_signal_fixture(db_path)

    conn = sqlite3.connect(str(db_path))
    before = conn.execute("SELECT published FROM signals WHERE id=?", (signal_id,)).fetchone()
    conn.close()

    migration.upgrade(db_path)

    conn = sqlite3.connect(str(db_path))
    after = conn.execute("SELECT published FROM signals WHERE id=?", (signal_id,)).fetchone()
    conn.close()
    assert before == after == (0,)


def test_upgrade_rejects_incompatible_existing_table(tmp_path):
    db_path = tmp_path / "test.db"
    _pre_migration_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute(f"CREATE TABLE {TABLE_NAME} (id INTEGER PRIMARY KEY, wrong_column TEXT)")
    conn.commit()
    conn.close()

    with pytest.raises(migration.IncompatibleExistingSchemaError):
        migration.upgrade(db_path)


def test_downgrade_drops_empty_table(tmp_path):
    db_path = tmp_path / "test.db"
    _pre_migration_db(db_path)
    migration.upgrade(db_path)
    migration.downgrade(db_path)
    assert migration.inspect(db_path)["table_exists"] is False


def test_downgrade_refuses_when_rows_exist(tmp_path):
    db_path = tmp_path / "test.db"
    _pre_migration_db(db_path)
    signal_id = _seed_signal_fixture(db_path)
    migration.upgrade(db_path)

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        signal = s.get(Signal, signal_id)
        # record_signal_publication_action() (the low-level, ungoverned
        # append) rather than publish_signal() - this legacy fixture Signal
        # has no linked SourceAssertion and would legitimately be refused by
        # the full governed gate; this test only exercises the migration's
        # own row-count-based downgrade refusal, not publication governance.
        record_signal_publication_action(s, signal, action="PUBLISH", reviewer="human:tester", reason="test")
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
