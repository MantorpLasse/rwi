"""Tests for scripts/migrate_governed_signal_creation_slice9c.py
(docs/architecture/human-approved-governed-signal-creation-slice9c-report.md).

Never touches the real database - every test builds an isolated temp-file
SQLite database. Modeled on the already-proven pattern in
tests/test_promotion_policy_persistence_migration.py, with the downgrade
rebuild helper's own index-preservation logic exercised specifically because
this is the first additive column in this family that is itself indexed.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import create_engine

from app.database import Base
from app import models  # noqa: F401 - registers all metadata
from scripts.migrate_governed_signal_creation_slice9c import downgrade, inspect, upgrade


def _pre_migration_database(tmp_path: Path) -> Path:
    """Every current table, with source_assertions hand-built in its
    pre-slice9c shape - i.e. including identity_guard_*/intelligence_review_*/
    promotion_policy_* (Slices 1/4/7) but NOT signal_id (this slice's own
    addition)."""
    database = tmp_path / "pre_slice9c.db"
    engine = create_engine(f"sqlite:///{database}")
    tables = [t for name, t in Base.metadata.tables.items() if name != "source_assertions"]
    Base.metadata.create_all(engine, tables=tables)
    engine.dispose()

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute(
        "CREATE TABLE source_assertions_old ("
        "id INTEGER NOT NULL, source_id INTEGER NOT NULL, airport_id INTEGER, runway_id INTEGER, "
        "assertion_type VARCHAR(30) NOT NULL, runway_end VARCHAR(20), "
        "raw_airport_identifier VARCHAR(100), raw_airport_name VARCHAR(300), "
        "raw_runway_value VARCHAR(100), raw_runway_end_value VARCHAR(100), "
        "raw_product_type VARCHAR(200), raw_year_date_wording VARCHAR(300), "
        "raw_vendor_manufacturer_wording VARCHAR(300), raw_count VARCHAR(100), raw_relevant_text TEXT, "
        "source_record_identifier VARCHAR(300), source_locator VARCHAR(500), "
        "raw_fragment_hash VARCHAR(128), artifact_identity VARCHAR(500), "
        "parser_identifier VARCHAR(200), extracted_at DATETIME, "
        "evidence_quality VARCHAR(30) NOT NULL, review_state VARCHAR(20) NOT NULL, "
        "identity_guard_decision VARCHAR(30), identity_guard_reason TEXT, "
        "intelligence_review_decision VARCHAR(30), intelligence_review_reason TEXT, "
        "promotion_policy_decision VARCHAR(30), promotion_policy_reason TEXT, "
        "created_at DATETIME NOT NULL, "
        "PRIMARY KEY (id), "
        "FOREIGN KEY(source_id) REFERENCES sources (id), "
        "FOREIGN KEY(airport_id) REFERENCES airports (id), "
        "FOREIGN KEY(runway_id) REFERENCES runways (id))"
    )
    connection.execute("ALTER TABLE source_assertions_old RENAME TO source_assertions")
    connection.execute("CREATE INDEX ix_source_assertions_source_id ON source_assertions(source_id)")
    connection.execute("CREATE INDEX ix_source_assertions_airport_id ON source_assertions(airport_id)")
    connection.execute("CREATE INDEX ix_source_assertions_runway_id ON source_assertions(runway_id)")
    connection.commit()
    connection.close()
    return database


def _seed_realistic_assertion_and_signal(database: Path) -> dict:
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
        "VALUES (1, 1, 'Existing MSP signal', 'replacement', 'high', 0, ?, ?)",
        (now, now),
    )
    connection.commit()
    connection.close()
    return {"airport_id": 1, "source_assertion_id": 222, "signal_id": 1}


def _source_assertion_row(database: Path) -> tuple:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    row = connection.execute(
        "SELECT id, source_id, assertion_type, identity_guard_decision, intelligence_review_decision, "
        "promotion_policy_decision, created_at FROM source_assertions WHERE id=222"
    ).fetchone()
    connection.close()
    return row


# ---------------------------------------------------------------------------
# 1-3. Column, nullability, FK.
# ---------------------------------------------------------------------------


def test_upgrade_adds_signal_id_column_nullable_with_fk(tmp_path):
    database = _pre_migration_database(tmp_path)
    _seed_realistic_assertion_and_signal(database)
    row_before = _source_assertion_row(database)

    upgrade(database)

    after = inspect(database)
    assert after["signal_id_column_exists"] is True
    assert after["source_assertions_count"] == 1
    assert after["source_assertions_with_signal_id"] == 0
    assert after["foreign_key_check"] == []

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    columns = {row[1]: (row[2], bool(row[3])) for row in connection.execute("PRAGMA table_info(source_assertions)")}
    fks = {(row[3], row[2]) for row in connection.execute("PRAGMA foreign_key_list(source_assertions)")}
    connection.close()
    assert columns["signal_id"] == ("INTEGER", False)  # nullable
    assert ("signal_id", "signals") in fks

    # Every other column untouched.
    row_after = _source_assertion_row(database)
    assert row_after == row_before


# ---------------------------------------------------------------------------
# 4. Index.
# ---------------------------------------------------------------------------


def test_upgrade_creates_signal_id_index(tmp_path):
    database = _pre_migration_database(tmp_path)
    upgrade(database)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    index_names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='source_assertions'"
        ).fetchall()
    }
    connection.close()
    assert "ix_source_assertions_signal_id" in index_names
    # Pre-existing indexes on the same table must survive too.
    assert {
        "ix_source_assertions_source_id", "ix_source_assertions_airport_id", "ix_source_assertions_runway_id",
    } <= index_names


# ---------------------------------------------------------------------------
# 5-6. Idempotent upgrade / existing rows NULL by default.
# ---------------------------------------------------------------------------


def test_upgrade_is_idempotent_when_run_twice(tmp_path):
    database = _pre_migration_database(tmp_path)
    _seed_realistic_assertion_and_signal(database)
    upgrade(database)
    first = inspect(database)
    upgrade(database)
    second = inspect(database)
    assert first == second


def test_existing_rows_get_signal_id_null_not_backfilled(tmp_path):
    database = _pre_migration_database(tmp_path)
    _seed_realistic_assertion_and_signal(database)
    upgrade(database)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    value = connection.execute("SELECT signal_id FROM source_assertions WHERE id=222").fetchone()[0]
    connection.close()
    assert value is None


# ---------------------------------------------------------------------------
# 7. Downgrade - and specifically its own index-preservation fix (this
# migration is the first additive column in this family that is itself
# indexed, which exposed a bug in the naive "replay every stored index SQL"
# rebuild technique during implementation - now fixed to filter by real
# column membership, PRAGMA index_info, not text matching).
# ---------------------------------------------------------------------------


def test_downgrade_is_exactly_reversible(tmp_path):
    database = _pre_migration_database(tmp_path)
    _seed_realistic_assertion_and_signal(database)
    row_before = _source_assertion_row(database)

    connection = sqlite3.connect(database)
    schema_before = sorted(row[0] for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
    ).fetchall())
    connection.close()

    upgrade(database)
    downgrade(database)

    after = inspect(database)
    assert after["signal_id_column_exists"] is False
    assert after["source_assertions_count"] == 1

    connection = sqlite3.connect(database)
    schema_after = sorted(row[0] for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
    ).fetchall())
    connection.close()
    row_after = _source_assertion_row(database)

    assert schema_after == schema_before
    assert row_after == row_before

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    surviving_indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='source_assertions'"
        ).fetchall()
    }
    connection.close()
    assert surviving_indexes == {
        "ix_source_assertions_source_id", "ix_source_assertions_airport_id", "ix_source_assertions_runway_id",
    }
    assert "ix_source_assertions_signal_id" not in surviving_indexes

    # Re-upgrade must succeed cleanly.
    upgrade(database)
    final = inspect(database)
    assert final["signal_id_column_exists"] is True
    assert final["foreign_key_check"] == []


# ---------------------------------------------------------------------------
# 8. Existing data / other tables preserved.
# ---------------------------------------------------------------------------


def _table_row_counts(database: Path) -> dict[str, int]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    counts = {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables}
    connection.close()
    return counts


def test_existing_signal_and_other_table_data_preserved_across_upgrade(tmp_path):
    database = _pre_migration_database(tmp_path)
    ids = _seed_realistic_assertion_and_signal(database)
    counts_before = _table_row_counts(database)

    upgrade(database)

    counts_after = _table_row_counts(database)
    assert counts_after == counts_before

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    signal_row = connection.execute("SELECT title, published FROM signals WHERE id=?", (ids["signal_id"],)).fetchone()
    connection.close()
    assert signal_row == ("Existing MSP signal", 0)


# ---------------------------------------------------------------------------
# 9. Realistic incoming/outgoing FK behavior, foreign_key_check clean.
# ---------------------------------------------------------------------------


def test_setting_signal_id_to_a_real_signal_succeeds_and_is_fk_clean(tmp_path):
    database = _pre_migration_database(tmp_path)
    ids = _seed_realistic_assertion_and_signal(database)
    upgrade(database)

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "UPDATE source_assertions SET signal_id=? WHERE id=?", (ids["signal_id"], ids["source_assertion_id"]),
    )
    connection.commit()
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    connection.close()
    assert violations == []


def test_signal_id_referencing_nonexistent_signal_is_rejected(tmp_path):
    database = _pre_migration_database(tmp_path)
    _seed_realistic_assertion_and_signal(database)
    upgrade(database)

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE source_assertions SET signal_id=999999 WHERE id=222")
    connection.rollback()
    connection.close()


def test_foreign_key_check_clean_after_upgrade_and_downgrade(tmp_path):
    database = _pre_migration_database(tmp_path)
    _seed_realistic_assertion_and_signal(database)
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


# ---------------------------------------------------------------------------
# Wrong-DB / write-gate safety, matching Slice 9A/9B's established migration
# safety tests.
# ---------------------------------------------------------------------------


def test_main_requires_allow_database_write_flag(tmp_path):
    database = _pre_migration_database(tmp_path)
    from scripts.migrate_governed_signal_creation_slice9c import main
    import pytest

    with pytest.raises(SystemExit):
        main(["--database", str(database)])

    assert inspect(database)["signal_id_column_exists"] is False
