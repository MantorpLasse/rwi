import sqlite3
from pathlib import Path

from sqlalchemy import create_engine

from app.database import Base
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
