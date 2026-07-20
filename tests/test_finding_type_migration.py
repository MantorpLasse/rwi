from argparse import Namespace

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.migration_metadata import target_metadata


FINDING_TYPE_REVISION = "f2a7c84e9d10"
PREVIOUS_REVISION = "d9e4b72a1c63"
EXPECTED_KEYS = {
    "CURRENT_EMAS",
    "NO_VERIFIED_EMAS",
    "CONFLICTING_EMAS",
    "MISSING_INSTALLATION_YEAR",
    "MISSING_MANUFACTURER",
    "MISSING_LENGTH",
    "MULTIPLE_CURRENT_FACTS",
    "UNVERIFIED_INSTALLATION",
}


def config(database_url: str) -> Config:
    alembic_config = Config("alembic.ini")
    alembic_config.attributes["database_url"] = database_url
    alembic_config.cmd_opts = Namespace(x=["allow_database_write=true"])
    return alembic_config


def test_finding_type_migration_seeds_matches_metadata_and_downgrades(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'finding-types.db').as_posix()}"
    alembic_config = config(database_url)

    command.upgrade(alembic_config, "head")
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert {column["name"] for column in inspector.get_columns("finding_types")} == {
            "id",
            "key",
            "name",
            "description",
            "category",
            "is_active",
            "created_at",
        }
        assert {column["name"] for column in inspector.get_columns("intelligence")} == {
            "id",
            "created_at",
            "finding_type_id",
            "title",
            "summary",
            "status",
            "derived_at",
            "supersedes_intelligence_id",
        }
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT key, category, is_active FROM finding_types")
            ).all()
            assert {row.key for row in rows} == EXPECTED_KEYS
            assert {row.category for row in rows} == {
                "STATUS",
                "COMPLETENESS",
                "CONFLICT",
                "QUALITY",
            }
            assert all(row.is_active for row in rows)
            assert compare_metadata(
                MigrationContext.configure(connection), target_metadata
            ) == []
    finally:
        engine.dispose()

    command.downgrade(alembic_config, PREVIOUS_REVISION)
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert "finding_types" not in inspector.get_table_names()
        assert "finding_type" in {
            column["name"] for column in inspector.get_columns("intelligence")
        }
        assert "finding_type_id" not in {
            column["name"] for column in inspector.get_columns("intelligence")
        }
    finally:
        engine.dispose()
