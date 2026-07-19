from argparse import Namespace

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.migration_metadata import target_metadata


OBSERVATION_REVISION = "a91e6c3f4b27"
PREVIOUS_REVISION = "7c4f1d8e2a90"


def config(database_url: str) -> Config:
    alembic_config = Config("alembic.ini")
    alembic_config.attributes["database_url"] = database_url
    alembic_config.cmd_opts = Namespace(x=["allow_database_write=true"])
    return alembic_config


def test_observation_migration_matches_metadata_and_downgrades(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'observations.db').as_posix()}"
    alembic_config = config(database_url)

    command.upgrade(alembic_config, OBSERVATION_REVISION)
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert {column["name"] for column in inspector.get_columns("observations")} == {
            "id", "document_id", "observation_type_id", "raw_value",
            "normalized_value", "extraction_confidence", "evidence_locator",
            "extraction_method", "extractor_version", "created_at",
            "supersedes_observation_id",
        }
        assert {index["name"] for index in inspector.get_indexes("observations")} == {
            "ix_observations_document_id",
            "ix_observations_observation_type_id",
            "ix_observations_supersedes_observation_id",
        }
        with engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), target_metadata) == []
    finally:
        engine.dispose()

    command.downgrade(alembic_config, PREVIOUS_REVISION)
    engine = create_engine(database_url)
    try:
        assert "observations" not in inspect(engine).get_table_names()
        assert "observation_types" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
