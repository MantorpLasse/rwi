from argparse import Namespace

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


EXPECTED_TABLES = {
    "airports",
    "runways",
    "runway_ends",
    "emas_beds",
    "emas_installations",
    "projects",
    "sources",
    "incidents",
}

BASELINE_REVISION = "8edd52d34c76"


def _config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.attributes["database_url"] = database_url
    config.cmd_opts = Namespace(x=["allow_database_write=true"])
    return config


def _application_tables(database_url: str) -> set[str]:
    engine = create_engine(database_url)
    try:
        return set(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


def test_baseline_upgrade_downgrade_and_reupgrade(tmp_path):
    database_path = tmp_path / "baseline.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = _config(database_url)

    command.upgrade(config, BASELINE_REVISION)
    assert _application_tables(database_url) == EXPECTED_TABLES

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []

        current_index = next(
            index
            for index in inspect(engine).get_indexes("emas_beds")
            if index["name"] == "uq_emas_beds_current_runway_end"
        )
        assert current_index["unique"] == 1
        assert str(current_index["dialect_options"]["sqlite_where"]) == "is_current = 1"
    finally:
        engine.dispose()

    command.downgrade(config, "base")
    assert _application_tables(database_url) == set()

    command.upgrade(config, BASELINE_REVISION)
    assert _application_tables(database_url) == EXPECTED_TABLES
