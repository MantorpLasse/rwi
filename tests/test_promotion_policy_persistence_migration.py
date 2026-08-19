"""Tests for scripts/migrate_promotion_policy_persistence_slice7.py
(docs/architecture/promotion-policy-persistence-slice7-report.md).

Never touches the real database - every test builds an isolated temp-file
SQLite database. Modeled directly on the already-proven pattern in
tests/test_intelligence_review_persistence_migration.py, including the
realistic-full-schema downgrade regression
(docs/domain/canonical-runway-migration-downgrade-fix-report.md)."""
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine

from app.database import Base
from app import models  # noqa: F401 - registers all metadata
from scripts.migrate_promotion_policy_persistence_slice7 import downgrade, inspect, upgrade


def _pre_migration_database(tmp_path: Path) -> Path:
    """Every current table, with source_assertions hand-built in its
    pre-slice7 shape - i.e. including identity_guard_decision/
    identity_guard_reason (Slice 1) and intelligence_review_decision/
    intelligence_review_reason (Slice 4), both already part of the current
    model, but NOT promotion_policy_decision/promotion_policy_reason (this
    slice's own addition)."""
    database = tmp_path / "pre_slice7.db"
    engine = create_engine(f"sqlite:///{database}")
    tables = [t for name, t in Base.metadata.tables.items() if name != "source_assertions"]
    Base.metadata.create_all(engine, tables=tables)
    engine.dispose()

    # Built under a temporary name and RENAMEd into place - matches
    # tests/test_intelligence_review_persistence_migration.py's own
    # technique, so the byte-for-byte schema-text comparison in
    # test_downgrade_is_exactly_reversible is meaningful rather than an
    # artifact of two different construction paths.
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


def _seed_pre_migration_assertion(database: Path) -> None:
    now = "2026-08-19 00:00:00"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "INSERT INTO sources (id, title, source_type, reliability_level) VALUES (1, 'Test Source', 'test', 'official')"
    )
    connection.execute(
        "INSERT INTO source_assertions "
        "(id, source_id, assertion_type, source_locator, raw_fragment_hash, artifact_identity, "
        "parser_identifier, evidence_quality, review_state, identity_guard_decision, identity_guard_reason, "
        "intelligence_review_decision, intelligence_review_reason, created_at) "
        "VALUES (1, 1, 'project_construction', 'loc-1', 'hash-1', 'artifact-1', 'test-parser', "
        "'unverified_candidate', 'unreviewed', 'ATTACH_CONFIRMED', 'test identity reason', "
        "'REVIEW_REQUIRED', 'test intelligence reason', ?)",
        (now,),
    )
    connection.commit()
    connection.close()


def test_upgrade_adds_only_the_expected_columns(tmp_path):
    database = _pre_migration_database(tmp_path)
    _seed_pre_migration_assertion(database)

    connection = sqlite3.connect(database)
    schema_before = sorted(row[0] for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
    ).fetchall())
    connection.close()

    upgrade(database)

    after = inspect(database)
    assert after["promotion_policy_decision_column_exists"] is True
    assert after["promotion_policy_reason_column_exists"] is True
    assert after["source_assertions_count"] == 1
    assert after["source_assertions_with_promotion_policy_decision"] == 0
    assert after["foreign_key_check"] == []

    connection = sqlite3.connect(database)
    schema_after = sorted(row[0] for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
    ).fetchall())
    row = connection.execute(
        "SELECT id, source_id, assertion_type, identity_guard_decision, intelligence_review_decision, "
        "promotion_policy_decision, promotion_policy_reason, created_at "
        "FROM source_assertions WHERE id=1"
    ).fetchone()
    connection.close()

    assert row == (1, 1, "project_construction", "ATTACH_CONFIRMED", "REVIEW_REQUIRED", None, None, "2026-08-19 00:00:00")

    # Only source_assertions' own CREATE TABLE text may differ; nothing else.
    added_or_changed = set(schema_after) - set(schema_before)
    removed = set(schema_before) - set(schema_after)
    touched_names = set()
    for statement in added_or_changed | removed:
        if "source_assertions" in statement:
            touched_names.add("source_assertions")
    assert touched_names <= {"source_assertions"}
    assert len(removed) == 1  # only the old source_assertions CREATE TABLE text


def test_upgrade_is_idempotent_when_run_twice(tmp_path):
    database = _pre_migration_database(tmp_path)
    upgrade(database)
    first = inspect(database)
    upgrade(database)  # must not fail or duplicate anything
    second = inspect(database)
    assert first == second


def test_downgrade_is_exactly_reversible(tmp_path):
    database = _pre_migration_database(tmp_path)
    _seed_pre_migration_assertion(database)

    connection = sqlite3.connect(database)
    schema_before = sorted(row[0] for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
    ).fetchall())
    row_before = connection.execute(
        "SELECT id, source_id, assertion_type, source_locator, raw_fragment_hash, artifact_identity, "
        "parser_identifier, evidence_quality, review_state, identity_guard_decision, identity_guard_reason, "
        "intelligence_review_decision, intelligence_review_reason, created_at FROM source_assertions WHERE id=1"
    ).fetchone()
    connection.close()

    upgrade(database)
    downgrade(database)

    after = inspect(database)
    assert after["promotion_policy_decision_column_exists"] is False
    assert after["promotion_policy_reason_column_exists"] is False
    assert after["source_assertions_count"] == 1

    connection = sqlite3.connect(database)
    schema_after = sorted(row[0] for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
    ).fetchall())
    row_after = connection.execute(
        "SELECT id, source_id, assertion_type, source_locator, raw_fragment_hash, artifact_identity, "
        "parser_identifier, evidence_quality, review_state, identity_guard_decision, identity_guard_reason, "
        "intelligence_review_decision, intelligence_review_reason, created_at FROM source_assertions WHERE id=1"
    ).fetchone()
    connection.close()

    assert schema_after == schema_before
    assert row_after == row_before


# ---------------------------------------------------------------------------
# Realistic full-schema downgrade regression
# (docs/domain/canonical-runway-migration-downgrade-fix-report.md).
# ---------------------------------------------------------------------------


def _seed_full_schema_with_incoming_foreign_key(database: Path) -> None:
    """One SourceAssertion plus a PhysicalInstallationIdentity and an
    InstallationAssertionLink that references the assertion - the exact
    incoming-foreign-key shape that exposed the original bug."""
    now = "2026-08-19 00:00:00"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "INSERT INTO airports (id, name, country, created_at, updated_at) VALUES (1, 'CGF', 'USA', ?, ?)",
        (now, now),
    )
    connection.execute("INSERT INTO runways (id, airport_id, designation) VALUES (1, 1, '6/24')")
    connection.execute(
        "INSERT INTO physical_installation_identities (id, airport_id, runway_id, runway_end, created_at) "
        "VALUES (1, 1, 1, '06', ?)",
        (now,),
    )
    connection.execute(
        "INSERT INTO sources (id, title, source_type, reliability_level) VALUES (1, 'Test Source', 'test', 'official')"
    )
    connection.execute(
        "INSERT INTO source_assertions "
        "(id, source_id, assertion_type, source_locator, raw_fragment_hash, artifact_identity, "
        "parser_identifier, evidence_quality, review_state, identity_guard_decision, identity_guard_reason, "
        "intelligence_review_decision, intelligence_review_reason, created_at) "
        "VALUES (1, 1, 'project_construction', 'loc-1', 'hash-1', 'artifact-1', 'test-parser', "
        "'direct_strong', 'unreviewed', 'ATTACH_CONFIRMED', 'test identity reason', "
        "'REVIEW_REQUIRED', 'test intelligence reason', ?)",
        (now,),
    )
    connection.execute(
        "INSERT INTO installation_assertion_links "
        "(id, assertion_id, physical_installation_id, outcome, reason, actor, reviewed_at) "
        "VALUES (1, 1, 1, 'SAME_PHYSICAL_INSTALLATION', 'test reason', 'test actor', ?)",
        (now,),
    )
    connection.commit()
    connection.close()


def _table_row_counts(database: Path) -> dict[str, int]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    counts = {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables}
    connection.close()
    return counts


def test_downgrade_succeeds_against_the_full_realistic_schema_with_an_incoming_foreign_key(tmp_path):
    """Regression coverage for the exact bug class fixed in
    docs/domain/canonical-runway-migration-downgrade-fix-report.md, this
    time against source_assertions after it already carries both the
    Slice 1 identity_guard_* and Slice 4 intelligence_review_* columns."""
    database = _pre_migration_database(tmp_path)
    _seed_full_schema_with_incoming_foreign_key(database)
    counts_before_upgrade = _table_row_counts(database)

    upgrade(database)

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "UPDATE source_assertions SET promotion_policy_decision='HUMAN_REVIEW_REQUIRED', "
        "promotion_policy_reason='test promotion reason text' WHERE id=1"
    )
    connection.commit()
    fk_after_write = connection.execute("PRAGMA foreign_key_check").fetchall()
    connection.close()
    assert fk_after_write == []

    # This is the exact call shape that failed before the upstream fix.
    downgrade(database)

    after = inspect(database)
    assert after["promotion_policy_decision_column_exists"] is False
    assert after["promotion_policy_reason_column_exists"] is False
    assert after["source_assertions_count"] == 1
    assert after["foreign_key_check"] == []

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assertion_row = connection.execute(
        "SELECT id, source_id, assertion_type, source_locator, raw_fragment_hash, artifact_identity, "
        "parser_identifier, evidence_quality, review_state, identity_guard_decision, identity_guard_reason, "
        "intelligence_review_decision, intelligence_review_reason, created_at "
        "FROM source_assertions WHERE id=1"
    ).fetchone()
    link_row = connection.execute("SELECT * FROM installation_assertion_links WHERE id=1").fetchone()
    connection.close()
    assert assertion_row == (
        1, 1, "project_construction", "loc-1", "hash-1", "artifact-1", "test-parser",
        "direct_strong", "unreviewed", "ATTACH_CONFIRMED", "test identity reason",
        "REVIEW_REQUIRED", "test intelligence reason", "2026-08-19 00:00:00",
    )
    assert link_row == (1, 1, 1, "SAME_PHYSICAL_INSTALLATION", "test reason", "test actor", "2026-08-19 00:00:00", None)

    counts_after_downgrade = _table_row_counts(database)
    assert counts_after_downgrade == counts_before_upgrade

    # Surviving (non-migration) indexes on the rebuilt table must remain.
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    surviving_indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='source_assertions'"
        ).fetchall()
    }
    connection.close()
    assert surviving_indexes == {
        "ix_source_assertions_source_id",
        "ix_source_assertions_airport_id",
        "ix_source_assertions_runway_id",
    }

    # Re-upgrade must succeed again and leave everything clean.
    upgrade(database)
    final = inspect(database)
    assert final["promotion_policy_decision_column_exists"] is True
    assert final["promotion_policy_reason_column_exists"] is True
    assert final["foreign_key_check"] == []

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    final_row = connection.execute(
        "SELECT id, source_id, assertion_type, source_locator, raw_fragment_hash, artifact_identity, "
        "parser_identifier, evidence_quality, review_state, identity_guard_decision, identity_guard_reason, "
        "intelligence_review_decision, intelligence_review_reason, created_at "
        "FROM source_assertions WHERE id=1"
    ).fetchone()
    connection.close()
    assert final_row == assertion_row
