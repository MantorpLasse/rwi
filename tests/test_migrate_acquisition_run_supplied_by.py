"""Tests for scripts/migrate_acquisition_run_supplied_by.py (RWI HQ
"Manual File Acquisition Provenance - supplied_by" mission).

Every test uses an isolated temp-file SQLite database (tmp_path). Nothing
in this file ever opens data/runway_safe.db - production migration
application is explicitly deferred to a separate, later, manual step.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import AcquisitionRun, AcquisitionRunStatus, AcquisitionSource, PublishingSource
import scripts.migrate_acquisition_run_supplied_by as migration


def _seed_pre_migration_db(db_path):
    """Full CURRENT schema (create_all already includes supplied_by, since
    it is now part of the live ORM model), then the supplied_by column is
    dropped via native SQLite DROP COLUMN to reconstruct the genuine
    "not yet migrated" starting shape - the realistic state
    data/runway_safe.db is actually in today."""
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        publisher = PublishingSource(name="Example Publisher", reliability_level="unverified")
        session.add(publisher)
        session.flush()
        source = AcquisitionSource(
            publishing_source=publisher, key="example:pre-migration", display_name="Example Source",
            acquisition_type="http", canonical_url="https://example.test/doc.pdf", active=True,
        )
        session.add(source)
        session.flush()
        # A real permission_failure row, matching AcquisitionRun #47's own
        # shape (no snapshot, a terminal failure status) - the historical
        # record this migration must never disturb.
        failed_run = AcquisitionRun(
            source=source, started_at=datetime.now(UTC), completed_at=datetime.now(UTC),
            status=AcquisitionRunStatus.PERMISSION_FAILURE, request_url=source.canonical_url,
            provider_version="generic-web-http/1", duration_seconds=0.4,
            error_category="HTTPStatusError", error_detail="403 Forbidden", is_new_snapshot=False,
        )
        session.add(failed_run)
        session.commit()
        failed_run_id = failed_run.id
    engine.dispose()

    conn = sqlite3.connect(str(db_path))
    conn.execute("ALTER TABLE acquisition_runs DROP COLUMN supplied_by")
    conn.commit()
    conn.close()
    return failed_run_id


def test_upgrade_adds_nullable_column_without_changing_existing_rows(tmp_path):
    db_path = tmp_path / "test.db"
    failed_run_id = _seed_pre_migration_db(db_path)

    before = migration.inspect(db_path)
    assert before["supplied_by_column_exists"] is False
    assert before["acquisition_runs_count"] == 1

    migration.upgrade(db_path)

    after = migration.inspect(db_path)
    assert after["supplied_by_column_exists"] is True
    assert after["acquisition_runs_count"] == 1
    assert after["acquisition_runs_with_supplied_by"] == 0
    assert after["foreign_key_check"] == []

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        run = session.get(AcquisitionRun, failed_run_id)
        assert run.supplied_by is None
        assert run.status == AcquisitionRunStatus.PERMISSION_FAILURE
        assert run.error_detail == "403 Forbidden"
        assert run.request_url == "https://example.test/doc.pdf"


def test_upgrade_is_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    _seed_pre_migration_db(db_path)

    migration.upgrade(db_path)
    first = migration.inspect(db_path)
    migration.upgrade(db_path)  # second call must be a safe no-op
    second = migration.inspect(db_path)

    assert first == second


def test_backup_matches_source_exactly(tmp_path):
    db_path = tmp_path / "test.db"
    _seed_pre_migration_db(db_path)

    backup_dir = tmp_path / "backups"
    backup_path = migration.backup_database(db_path, backup_directory=backup_dir)

    assert backup_path.read_bytes() == db_path.read_bytes()


def test_downgrade_removes_column_when_all_null(tmp_path):
    db_path = tmp_path / "test.db"
    _seed_pre_migration_db(db_path)
    migration.upgrade(db_path)

    migration.downgrade(db_path)

    after = migration.inspect(db_path)
    assert after["supplied_by_column_exists"] is False
    assert after["acquisition_runs_count"] == 1
    assert after["foreign_key_check"] == []


def test_downgrade_refuses_when_supplied_by_populated(tmp_path):
    db_path = tmp_path / "test.db"
    _seed_pre_migration_db(db_path)
    migration.upgrade(db_path)

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        publisher = PublishingSource(name="Manual Publisher", reliability_level="unverified")
        session.add(publisher)
        session.flush()
        source = AcquisitionSource(
            publishing_source=publisher, key="example:manual", display_name="Manual Source",
            acquisition_type="manual_file", canonical_url="https://example.test/manual.pdf", active=True,
        )
        session.add(source)
        session.flush()
        manual_run = AcquisitionRun(
            source=source, started_at=datetime.now(UTC), completed_at=datetime.now(UTC),
            status=AcquisitionRunStatus.SUCCESS, request_url=source.canonical_url,
            provider_version="manual-file/1", duration_seconds=0.0, is_new_snapshot=False,
            supplied_by="human:rwi-owner",
        )
        session.add(manual_run)
        session.commit()
    engine.dispose()

    with pytest.raises(RuntimeError, match="supplied_by"):
        migration.downgrade(db_path)

    after = migration.inspect(db_path)
    assert after["supplied_by_column_exists"] is True
    assert after["acquisition_runs_with_supplied_by"] == 1


def test_upgrade_requires_allow_database_write_flag(tmp_path):
    db_path = tmp_path / "test.db"
    _seed_pre_migration_db(db_path)

    with pytest.raises(SystemExit):
        migration.main(["--database", str(db_path)])
