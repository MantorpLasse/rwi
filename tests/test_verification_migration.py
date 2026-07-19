from argparse import Namespace

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.migration_metadata import target_metadata


VERIFICATION_REVISION = "b5d82a7c1e40"
PREVIOUS_REVISION = "a91e6c3f4b27"


def config(database_url: str) -> Config:
    alembic_config = Config("alembic.ini")
    alembic_config.attributes["database_url"] = database_url
    alembic_config.cmd_opts = Namespace(x=["allow_database_write=true"])
    return alembic_config


def test_verification_migration_matches_metadata_and_downgrades(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'verifications.db').as_posix()}"
    alembic_config = config(database_url)

    command.upgrade(alembic_config, VERIFICATION_REVISION)
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert {column["name"] for column in inspector.get_columns("verifications")} == {
            "id",
            "observation_id",
            "status",
            "reviewed_at",
            "reviewed_by",
            "comment",
            "confidence",
            "created_at",
        }
        assert {index["name"] for index in inspector.get_indexes("verifications")} == {
            "ix_verifications_observation_id"
        }
        assert {
            (foreign_key["constrained_columns"][0], foreign_key["referred_table"])
            for foreign_key in inspector.get_foreign_keys("verifications")
        } == {("observation_id", "observations")}
        with engine.connect() as connection:
            assert compare_metadata(
                MigrationContext.configure(connection), target_metadata
            ) == []
    finally:
        engine.dispose()

    command.downgrade(alembic_config, PREVIOUS_REVISION)
    engine = create_engine(database_url)
    try:
        assert "verifications" not in inspect(engine).get_table_names()
        assert "observations" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
