"""Tests for scripts/migrate_signal_lifecycle_assessment.py (RWI HQ "SLT2 -
Governed Signal Lifecycle Assessment" mission).

Every test uses an isolated temp-file SQLite database (tmp_path). Nothing in
this file ever opens data/runway_safe.db - production migration application
is explicitly deferred to a separate, later, manual step, mirroring
tests/test_migrate_signal_amendment_action.py's own established, proven
template for this exact class of single-table additive migration.
"""
from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Signal
from app.services.signal_lifecycle_assessment import record_signal_lifecycle_assessment
import scripts.migrate_signal_lifecycle_assessment as migration

NEW_TABLE = "signal_lifecycle_assessments"


def _pre_migration_db(path):
    """A full pre-migration schema (every table except the one this
    migration creates) - the realistic "not yet migrated" starting state."""
    engine = create_engine(f"sqlite:///{path}")
    pre_meta = MetaData()
    for name, table in Base.metadata.tables.items():
        if name != NEW_TABLE:
            table.to_metadata(pre_meta)
    pre_meta.create_all(engine)
    engine.dispose()


def _seed_signal(db_path):
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        airport = Airport(name="Migration Test Airport", country="XX")
        s.add(airport)
        signal = Signal(airport=airport, title="A", category="replacement", confidence="high")
        s.add(signal)
        s.commit()
        signal_id = signal.id
    engine.dispose()
    return signal_id


class TestCleanUpgrade:
    def test_upgrade_creates_the_table(self, tmp_path):
        db = tmp_path / "clean.db"
        _pre_migration_db(db)
        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["table_exists"] is True
        assert result["ready"] is True
        assert result["count"] == 0

    def test_exact_columns(self, tmp_path):
        db = tmp_path / "cols.db"
        _pre_migration_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        cols = {row[1]: (row[2], bool(row[3]), bool(row[5])) for row in conn.execute(f"PRAGMA table_info({NEW_TABLE})")}
        conn.close()
        assert set(cols) == {"id", "signal_id", "state", "reason", "reviewer", "created_at"}
        assert cols["id"][2] is True  # primary key
        assert cols["signal_id"][1] is True  # NOT NULL
        assert cols["state"][1] is True
        assert cols["reason"][1] is True
        assert cols["reviewer"][1] is True

    def test_check_constraint_present(self, tmp_path):
        db = tmp_path / "check.db"
        _pre_migration_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (NEW_TABLE,)
        ).fetchone()[0]
        conn.close()
        assert "ck_signal_lifecycle_assessments_state" in sql

    def test_upgrade_leaves_no_foreign_key_violations(self, tmp_path):
        db = tmp_path / "fk.db"
        _pre_migration_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        conn.close()
        assert violations == []

    def test_upgrade_does_not_touch_existing_signal_row_count(self, tmp_path):
        db = tmp_path / "signals.db"
        _pre_migration_db(db)
        _seed_signal(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            before = s.query(Signal).count()
        engine.dispose()
        migration.upgrade(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            after = s.query(Signal).count()
        engine.dispose()
        assert before == after


class TestIdempotentUpgrade:
    def test_upgrade_twice_is_a_no_op(self, tmp_path):
        db = tmp_path / "idempotent.db"
        _pre_migration_db(db)
        migration.upgrade(db)
        migration.upgrade(db)  # must not raise
        result = migration.inspect(db)
        assert result["ready"] is True
        assert result["count"] == 0


class TestBackup:
    def test_backup_matches_source_exactly(self, tmp_path):
        db = tmp_path / "data" / "runway_safe.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        _pre_migration_db(db)
        _seed_signal(db)
        backup_dir = tmp_path / "backups"
        backup_path = migration.backup_database(db, backup_directory=backup_dir)
        assert backup_path.exists()
        assert backup_path.stat().st_size == db.stat().st_size
        assert backup_path.read_bytes() == db.read_bytes()


class TestDowngrade:
    def test_downgrade_removes_empty_table(self, tmp_path):
        db = tmp_path / "downgrade_empty.db"
        _pre_migration_db(db)
        migration.upgrade(db)
        migration.downgrade(db)
        result = migration.inspect(db)
        assert result["table_exists"] is False

    def test_downgrade_refuses_when_rows_exist(self, tmp_path):
        db = tmp_path / "downgrade_populated.db"
        _pre_migration_db(db)
        signal_id = _seed_signal(db)
        migration.upgrade(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            record_signal_lifecycle_assessment(
                s, signal_id=signal_id, state="active_opportunity",
                reason="confirmed vendor/order; no stronger lifecycle state established.",
                reviewer="human:rwi-owner",
            )
            s.commit()
        engine.dispose()

        try:
            migration.downgrade(db)
            assert False, "downgrade() should have refused"
        except RuntimeError as exc:
            assert "recorded lifecycle" in str(exc)

        result = migration.inspect(db)
        assert result["table_exists"] is True
        assert result["count"] == 1


class TestCLIWriteGate:
    def test_main_requires_allow_database_write(self, tmp_path):
        db = tmp_path / "gate.db"
        _pre_migration_db(db)
        with pytest.raises(SystemExit):
            migration.main(["--database", str(db)])
        # No table created - the gate refused before any write was attempted.
        result = migration.inspect(db)
        assert result["table_exists"] is False
