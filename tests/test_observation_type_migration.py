from argparse import Namespace

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.migration_metadata import target_metadata


OBSERVATION_TYPE_REVISION = "7c4f1d8e2a90"
PREVIOUS_REVISION = "3f2a1c9d7e6b"


def config(database_url: str) -> Config:
    alembic_config = Config("alembic.ini")
    alembic_config.attributes["database_url"] = database_url
    alembic_config.cmd_opts = Namespace(x=["allow_database_write=true"])
    return alembic_config


def test_observation_type_migration_creates_seeds_and_downgrades(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'observation-types.db').as_posix()}"
    alembic_config = config(database_url)

    command.upgrade(alembic_config, OBSERVATION_TYPE_REVISION)

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT key, value_type, active "
                    "FROM observation_types ORDER BY key"
                )
            ).all()
            assert rows == [
                ("airport.emas.installation_year_display", "raw_text", 1),
                ("airport.emas.product", "enumeration", 1),
                ("airport.emas.system_count", "integer", 1),
            ]
            assert compare_metadata(
                MigrationContext.configure(connection), target_metadata
            ) == []
    finally:
        engine.dispose()

    command.downgrade(alembic_config, PREVIOUS_REVISION)

    engine = create_engine(database_url)
    try:
        assert "observation_types" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()

