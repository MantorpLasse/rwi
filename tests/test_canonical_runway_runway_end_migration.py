import sqlite3
from pathlib import Path

from sqlalchemy import create_engine

from app.database import Base
from app import models  # noqa: F401 - registers all metadata, needed for the full-schema fixture below
from scripts.migrate_canonical_runway_runway_end_slice1 import downgrade, inspect, upgrade


def _pre_migration_database(tmp_path: Path) -> Path:
    """A temp DB shaped like the real one before this slice: runways and
    physical_installation_identities exist, runway_ends and
    physical_installation_identities.runway_end_id do not."""
    database = tmp_path / "pre_slice1.db"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(
        engine,
        tables=[
            Base.metadata.tables["airports"],
            Base.metadata.tables["runways"],
            Base.metadata.tables["physical_installation_identities"],
        ],
    )
    engine.dispose()
    # physical_installation_identities was created straight from the
    # CURRENT model, which already has runway_end_id - drop it back off so
    # this fixture genuinely represents the pre-migration shape.
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute(
        "CREATE TABLE physical_installation_identities_old ("
        "id INTEGER NOT NULL, airport_id INTEGER NOT NULL, runway_id INTEGER, "
        "runway_end VARCHAR(20), created_at DATETIME NOT NULL, PRIMARY KEY (id), "
        "FOREIGN KEY(airport_id) REFERENCES airports (id), "
        "FOREIGN KEY(runway_id) REFERENCES runways (id))"
    )
    connection.execute(
        "INSERT INTO physical_installation_identities_old "
        "SELECT id, airport_id, runway_id, runway_end, created_at FROM physical_installation_identities"
    )
    connection.execute("DROP TABLE physical_installation_identities")
    connection.execute(
        "ALTER TABLE physical_installation_identities_old RENAME TO physical_installation_identities"
    )
    connection.commit()
    connection.close()
    return database


def _seed_pre_migration_identity(database: Path) -> int:
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO airports (id, name, country, created_at, updated_at) "
        "VALUES (1, 'CGF', 'USA', '2026-08-16 07:00:00', '2026-08-16 07:00:00')"
    )
    connection.execute("INSERT INTO runways (id, airport_id, designation) VALUES (1, 1, '6/24')")
    connection.execute(
        "INSERT INTO physical_installation_identities (id, airport_id, runway_id, runway_end, created_at) "
        "VALUES (1, 1, NULL, '06', '2026-08-16 07:38:18')"
    )
    connection.commit()
    connection.close()
    return 1


def test_upgrade_adds_only_the_expected_table_and_column(tmp_path):
    database = _pre_migration_database(tmp_path)
    _seed_pre_migration_identity(database)

    connection = sqlite3.connect(database)
    schema_before = sorted(row[0] for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
    ).fetchall())
    connection.close()

    upgrade(database)

    after = inspect(database)
    assert after["runway_ends_table_exists"] is True
    assert after["runway_end_id_column_exists"] is True
    assert after["runway_ends_count"] == 0
    assert after["physical_installation_identities_count"] == 1
    assert after["physical_installation_identities_with_runway_end_id"] == 0
    assert after["foreign_key_check"] == []

    connection = sqlite3.connect(database)
    schema_after = sorted(row[0] for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
    ).fetchall())
    identity_row = connection.execute(
        "SELECT id, airport_id, runway_id, runway_end, runway_end_id, created_at "
        "FROM physical_installation_identities WHERE id=1"
    ).fetchone()
    connection.close()

    assert identity_row == (1, 1, None, "06", None, "2026-08-16 07:38:18")

    # Only runway_ends (new table + its index) and
    # physical_installation_identities (rewritten CREATE TABLE text because
    # of the added column + its new index) may differ; nothing else.
    added_or_changed = set(schema_after) - set(schema_before)
    removed = set(schema_before) - set(schema_after)
    touched_names = set()
    for statement in added_or_changed | removed:
        for name in ("runway_ends", "physical_installation_identities"):
            if name in statement:
                touched_names.add(name)
    assert touched_names <= {"runway_ends", "physical_installation_identities"}
    assert len(removed) == 1  # only the old physical_installation_identities CREATE TABLE text


def test_upgrade_is_idempotent_when_run_twice(tmp_path):
    database = _pre_migration_database(tmp_path)
    upgrade(database)
    first = inspect(database)
    upgrade(database)  # must not fail or duplicate anything
    second = inspect(database)
    assert first == second


def test_downgrade_is_exactly_reversible(tmp_path):
    database = _pre_migration_database(tmp_path)
    _seed_pre_migration_identity(database)

    connection = sqlite3.connect(database)
    schema_before = sorted(row[0] for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
    ).fetchall())
    identity_before = connection.execute(
        "SELECT id, airport_id, runway_id, runway_end, created_at FROM physical_installation_identities WHERE id=1"
    ).fetchone()
    connection.close()

    upgrade(database)
    downgrade(database)

    after = inspect(database)
    assert after["runway_ends_table_exists"] is False
    assert after["runway_end_id_column_exists"] is False
    assert after["physical_installation_identities_count"] == 1

    connection = sqlite3.connect(database)
    schema_after = sorted(row[0] for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
    ).fetchall())
    identity_after = connection.execute(
        "SELECT id, airport_id, runway_id, runway_end, created_at FROM physical_installation_identities WHERE id=1"
    ).fetchone()
    connection.close()

    assert schema_after == schema_before
    assert identity_after == identity_before


# ---------------------------------------------------------------------------
# Realistic full-schema downgrade regression
# (docs/domain/canonical-runway-migration-downgrade-fix-report.md).
#
# _pre_migration_database() above only builds three tables (airports,
# runways, physical_installation_identities) - there is no other table
# with a foreign key pointing *into* physical_installation_identities, so
# it could never have caught the original bug: SQLite's native
# `ALTER TABLE ... DROP COLUMN` leaves a dangling table-level FOREIGN KEY
# clause when the dropped column's constraint was declared as part of the
# table's original CREATE TABLE (exactly what a fresh
# Base.metadata.create_all() produces) - but only once some *other*
# table's foreign key actually depends on the table graph. The real
# repository's `installation_assertion_links` table has exactly such a
# foreign key into physical_installation_identities. This fixture builds
# every table in the current schema (the full, realistic dependency
# graph) so this scenario is actually exercised.
# ---------------------------------------------------------------------------


def _pre_migration_database_full_schema(tmp_path: Path) -> Path:
    """Every current table except physical_installation_identities (built
    via create_all(), so each has whatever foreign-key style
    SQLAlchemy's own CreateTable naturally produces - not hand-picked),
    plus physical_installation_identities hand-built in its true
    pre-migration shape, matching _pre_migration_database() above."""
    database = tmp_path / "pre_slice1_full.db"
    engine = create_engine(f"sqlite:///{database}")
    tables = [t for name, t in Base.metadata.tables.items() if name != "physical_installation_identities"]
    Base.metadata.create_all(engine, tables=tables)
    engine.dispose()

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "CREATE TABLE physical_installation_identities ("
        "id INTEGER PRIMARY KEY, airport_id INTEGER NOT NULL REFERENCES airports(id), "
        "runway_id INTEGER REFERENCES runways(id), runway_end VARCHAR(20), created_at DATETIME NOT NULL)"
    )
    connection.execute(
        "CREATE INDEX ix_physical_installation_identities_airport_id ON physical_installation_identities(airport_id)"
    )
    connection.execute(
        "CREATE INDEX ix_physical_installation_identities_runway_id ON physical_installation_identities(runway_id)"
    )
    connection.commit()
    connection.close()
    return database


def _seed_full_schema_with_incoming_foreign_key(database: Path) -> None:
    """One PhysicalInstallationIdentity plus one InstallationAssertionLink
    that references it - the exact incoming-foreign-key shape that
    exposes the original bug."""
    now = "2026-08-16 07:38:18"
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
        "parser_identifier, evidence_quality, review_state, created_at) "
        "VALUES (1, 1, 'project_construction', 'loc', 'hash', 'artifact', 'parser', 'direct_strong', 'unreviewed', ?)",
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
    """Regression test for the bug found in the merge-readiness review:
    downgrade() previously raised OperationalError ("unknown column
    'runway_end_id' in foreign key definition") whenever some other
    table (here, installation_assertion_links) held a foreign key into
    physical_installation_identities and that table's own
    runway_end_id-carrying FK was declared as part of a single CREATE
    TABLE (as every fresh Base.metadata.create_all() database - i.e.
    every test fixture and any future clean deployment - naturally
    produces)."""
    database = _pre_migration_database_full_schema(tmp_path)
    _seed_full_schema_with_incoming_foreign_key(database)
    counts_before_upgrade = _table_row_counts(database)

    upgrade(database)

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("INSERT INTO runway_ends (id, runway_id, designation) VALUES (1, 1, '6')")
    connection.execute("UPDATE physical_installation_identities SET runway_end_id=1 WHERE id=1")
    connection.commit()
    fk_after_link = connection.execute("PRAGMA foreign_key_check").fetchall()
    connection.close()
    assert fk_after_link == []

    # This is the exact call that failed before the fix.
    downgrade(database)

    after = inspect(database)
    assert after["runway_ends_table_exists"] is False
    assert after["runway_end_id_column_exists"] is False
    assert after["physical_installation_identities_count"] == 1
    assert after["foreign_key_check"] == []

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    # Only physical_installation_identities/runway_ends were ever
    # supposed to change - every unrelated table's row count, and the
    # incoming-foreign-key row itself, must be exactly as seeded.
    identity_row = connection.execute(
        "SELECT id, airport_id, runway_id, runway_end, created_at FROM physical_installation_identities WHERE id=1"
    ).fetchone()
    link_row = connection.execute("SELECT * FROM installation_assertion_links WHERE id=1").fetchone()
    connection.close()
    assert identity_row == (1, 1, 1, "06", "2026-08-16 07:38:18")
    assert link_row == (1, 1, 1, "SAME_PHYSICAL_INSTALLATION", "test reason", "test actor", "2026-08-16 07:38:18", None)

    counts_after_downgrade = _table_row_counts(database)
    unrelated = set(counts_before_upgrade) - {"runway_ends"}
    assert {t: counts_after_downgrade.get(t) for t in unrelated} == {t: counts_before_upgrade[t] for t in unrelated}

    # Surviving (non-migration) indexes on the rebuilt table must remain.
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    surviving_indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='physical_installation_identities'"
        ).fetchall()
    }
    connection.close()
    assert surviving_indexes == {
        "ix_physical_installation_identities_airport_id",
        "ix_physical_installation_identities_runway_id",
    }

    # Re-upgrade must succeed again and leave everything clean.
    upgrade(database)
    final = inspect(database)
    assert final["runway_ends_table_exists"] is True
    assert final["runway_end_id_column_exists"] is True
    assert final["foreign_key_check"] == []

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    final_identity_row = connection.execute(
        "SELECT id, airport_id, runway_id, runway_end, created_at FROM physical_installation_identities WHERE id=1"
    ).fetchone()
    connection.close()
    assert final_identity_row == identity_row
