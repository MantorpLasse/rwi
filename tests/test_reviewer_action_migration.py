"""Tests for scripts/migrate_reviewer_action_slice9b.py
(docs/architecture/reviewer-action-persistence-slice9b-report.md).

Never touches the real database - every test builds an isolated temp-file
SQLite database. Modeled on the already-proven pattern in
tests/test_physical_installation_reconciliation.py::test_isolated_migration_upgrade_and_downgrade,
expanded to the fuller coverage this slice's own instructions require.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app.database import Base
from app import models  # noqa: F401 - registers all metadata
from scripts.migrate_reviewer_action_slice9b import downgrade, inspect, upgrade

_BASE_TABLES = (
    "airports", "runways", "sources", "source_assertions", "signals",
)


def _pre_migration_database(tmp_path: Path) -> Path:
    database = tmp_path / "pre_slice9b.db"
    engine = create_engine(f"sqlite:///{database}")
    tables = [t for name, t in Base.metadata.tables.items() if name != "reviewer_actions"]
    Base.metadata.create_all(engine, tables=tables)
    engine.dispose()
    return database


def _seed_realistic_rows(database: Path) -> dict:
    """One airport, source, SourceAssertion (MSP #222 governed shape) and
    one Signal - the exact incoming-FK-target shapes reviewer_actions
    points at."""
    now = "2026-08-19 00:00:00"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "INSERT INTO airports (id, name, country, created_at, updated_at) VALUES (1, 'MSP', 'USA', ?, ?)",
        (now, now),
    )
    connection.execute(
        "INSERT INTO sources (id, title, source_type, reliability_level) VALUES (1, 'EMAS memo', 'web_discovery', 'unverified')"
    )
    connection.execute(
        "INSERT INTO source_assertions (id, source_id, airport_id, assertion_type, source_record_identifier, "
        "evidence_quality, review_state, identity_guard_decision, intelligence_review_decision, "
        "promotion_policy_decision, created_at) VALUES "
        "(222, 1, 1, 'project_construction', 'msp-222', 'unverified_candidate', 'unreviewed', "
        "'ATTACH_CONFIRMED', 'REVIEW_REQUIRED', 'HUMAN_REVIEW_REQUIRED', ?)",
        (now,),
    )
    connection.execute(
        "INSERT INTO signals (id, airport_id, title, category, confidence, published, created_at, updated_at) "
        "VALUES (1, 1, 'Existing MSP signal', 'replacement', 'high', 1, ?, ?)",
        (now, now),
    )
    connection.commit()
    connection.close()
    return {"airport_id": 1, "source_assertion_id": 222, "signal_id": 1}


def _table_row_counts(database: Path) -> dict[str, int]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    counts = {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables}
    connection.close()
    return counts


# ---------------------------------------------------------------------------
# 1-6. upgrade creates the table with correct columns/nullability/FKs/indexes.
# ---------------------------------------------------------------------------


def test_upgrade_creates_table_with_expected_columns_and_nullability(tmp_path):
    database = _pre_migration_database(tmp_path)
    upgrade(database)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    columns = {
        row[1]: (row[2], bool(row[3]))  # (type, notnull)
        for row in connection.execute("PRAGMA table_info(reviewer_actions)")
    }
    connection.close()

    assert columns["id"][1] is True  # PRIMARY KEY column, declared NOT NULL by SQLAlchemy
    assert columns["source_assertion_id"] == ("INTEGER", True)
    assert columns["action"][1] is True
    assert columns["reason"][1] is True
    assert columns["reviewer"][1] is True
    assert columns["created_at"][1] is True
    assert columns["supersedes_action_id"] == ("INTEGER", False)
    assert columns["duplicate_of_signal_id"] == ("INTEGER", False)


def test_upgrade_creates_expected_foreign_keys_including_self_fk_and_signal_fk(tmp_path):
    database = _pre_migration_database(tmp_path)
    upgrade(database)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    fks = {
        (row[3], row[2]): row  # (from_column, target_table)
        for row in connection.execute("PRAGMA foreign_key_list(reviewer_actions)")
    }
    connection.close()

    assert ("source_assertion_id", "source_assertions") in fks
    assert ("supersedes_action_id", "reviewer_actions") in fks  # self-FK
    assert ("duplicate_of_signal_id", "signals") in fks  # Signal duplicate FK
    assert len(fks) == 3


def test_upgrade_creates_expected_indexes(tmp_path):
    database = _pre_migration_database(tmp_path)
    upgrade(database)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    index_names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='reviewer_actions'"
        ).fetchall()
    }
    connection.close()

    assert index_names == {
        "ix_reviewer_actions_source_assertion_id",
        "ix_reviewer_actions_supersedes_action_id",
        "ix_reviewer_actions_duplicate_of_signal_id",
    }


def test_upgrade_creates_expected_check_constraints(tmp_path):
    database = _pre_migration_database(tmp_path)
    upgrade(database)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    create_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='reviewer_actions'"
    ).fetchone()[0]
    connection.close()

    assert "ck_reviewer_actions_action" in create_sql
    assert "ck_reviewer_actions_duplicate_target_required" in create_sql
    assert "ck_reviewer_actions_duplicate_target_only_for_duplicate" in create_sql


# ---------------------------------------------------------------------------
# 7-9. idempotency, downgrade scope, existing-data preservation.
# ---------------------------------------------------------------------------


def test_upgrade_is_idempotent_when_run_twice(tmp_path):
    database = _pre_migration_database(tmp_path)
    _seed_realistic_rows(database)
    upgrade(database)
    first = inspect(database)
    upgrade(database)  # must not fail or duplicate anything
    second = inspect(database)
    assert first == second


def test_downgrade_removes_only_reviewer_actions_table(tmp_path):
    database = _pre_migration_database(tmp_path)
    ids = _seed_realistic_rows(database)
    counts_before_upgrade = _table_row_counts(database)

    upgrade(database)
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "INSERT INTO reviewer_actions (source_assertion_id, action, reason, reviewer, created_at) "
        "VALUES (?, 'DEFER', 'Need a second opinion.', 'human:reviewer', '2026-08-19 00:00:00')",
        (ids["source_assertion_id"],),
    )
    connection.commit()
    connection.close()

    downgrade(database)

    after = inspect(database)
    assert after["reviewer_actions_table_exists"] is False

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    remaining_tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    connection.close()
    assert "reviewer_actions" not in remaining_tables

    counts_after_downgrade = _table_row_counts(database)
    assert counts_after_downgrade == counts_before_upgrade  # every other table's data untouched


def test_existing_data_and_tables_preserved_across_upgrade(tmp_path):
    database = _pre_migration_database(tmp_path)
    ids = _seed_realistic_rows(database)
    counts_before = _table_row_counts(database)

    upgrade(database)

    counts_after = _table_row_counts(database)
    counts_after.pop("reviewer_actions")  # the only new table
    assert counts_after == counts_before

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    assertion_row = connection.execute(
        "SELECT identity_guard_decision, intelligence_review_decision, promotion_policy_decision "
        "FROM source_assertions WHERE id=?", (ids["source_assertion_id"],),
    ).fetchone()
    signal_row = connection.execute("SELECT title FROM signals WHERE id=?", (ids["signal_id"],)).fetchone()
    connection.close()
    assert assertion_row == ("ATTACH_CONFIRMED", "REVIEW_REQUIRED", "HUMAN_REVIEW_REQUIRED")
    assert signal_row == ("Existing MSP signal",)


# ---------------------------------------------------------------------------
# 10-11. Realistic incoming/outgoing FK behavior, foreign_key_check clean.
# ---------------------------------------------------------------------------


def test_realistic_row_insert_respects_all_three_foreign_keys(tmp_path):
    database = _pre_migration_database(tmp_path)
    ids = _seed_realistic_rows(database)
    upgrade(database)

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "INSERT INTO reviewer_actions (id, source_assertion_id, action, reason, reviewer, created_at) "
        "VALUES (1, ?, 'DEFER', 'Initial note.', 'human:a', '2026-08-19 00:00:00')",
        (ids["source_assertion_id"],),
    )
    connection.execute(
        "INSERT INTO reviewer_actions (id, source_assertion_id, action, reason, reviewer, created_at, "
        "supersedes_action_id) VALUES (2, ?, 'APPROVE_SIGNAL', 'Approved.', 'human:b', "
        "'2026-08-19 00:01:00', 1)",
        (ids["source_assertion_id"],),
    )
    connection.execute(
        "INSERT INTO reviewer_actions (id, source_assertion_id, action, reason, reviewer, created_at, "
        "duplicate_of_signal_id) VALUES (3, ?, 'MARK_DUPLICATE', 'Duplicate of existing signal.', "
        "'human:c', '2026-08-19 00:02:00', ?)",
        (ids["source_assertion_id"], ids["signal_id"]),
    )
    connection.commit()
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    connection.close()
    assert violations == []


def test_foreign_key_check_clean_after_upgrade_and_downgrade(tmp_path):
    database = _pre_migration_database(tmp_path)
    _seed_realistic_rows(database)
    upgrade(database)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    connection.close()

    downgrade(database)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    connection.close()

    upgrade(database)  # re-upgrade must also stay clean
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_invalid_foreign_key_reference_is_rejected(tmp_path):
    database = _pre_migration_database(tmp_path)
    _seed_realistic_rows(database)
    upgrade(database)

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO reviewer_actions (source_assertion_id, action, reason, reviewer, created_at) "
            "VALUES (999999, 'DEFER', 'x', 'human:a', '2026-08-19 00:00:00')"
        )
    connection.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# Wrong-DB / write-gate safety, matching Slice 9A's established migration
# safety tests.
# ---------------------------------------------------------------------------


def test_main_requires_allow_database_write_flag(tmp_path):
    database = _pre_migration_database(tmp_path)
    from scripts.migrate_reviewer_action_slice9b import main

    with pytest.raises(SystemExit):
        main(["--database", str(database)])

    assert inspect(database)["reviewer_actions_table_exists"] is False
