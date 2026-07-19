from io import StringIO

from alembic import command
from alembic.config import Config

from app.database import Base
from app.migration_metadata import target_metadata


EXPECTED_TABLES = {
    "airports",
    "runways",
    "runway_ends",
    "emas_beds",
    "emas_installations",
    "projects",
    "sources",
    "incidents",
    "publishing_sources",
    "documents",
    "project_documents",
    "observation_types",
    "observations",
    "verifications",
    "facts",
    "fact_verifications",
}


def test_alembic_config_loads():
    config = Config("alembic.ini")

    assert config.get_main_option("script_location").endswith("/alembic")


def test_alembic_uses_project_metadata_and_sees_all_models():
    assert target_metadata is Base.metadata
    assert set(target_metadata.tables) == EXPECTED_TABLES


def test_offline_sql_generation_does_not_require_database_access(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///this-file-must-not-be-created.db")
    output = StringIO()
    config = Config("alembic.ini", stdout=output)

    command.upgrade(config, "head", sql=True)

    assert not output.closed
