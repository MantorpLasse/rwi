from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_acquisition_migration_upgrade_and_downgrade(tmp_path: Path):
    database = tmp_path / "acquisition.db"
    url = f"sqlite:///{database.as_posix()}"
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    config.cmd_opts = type("Options", (), {"x": ["allow_database_write=true"]})()

    command.upgrade(config, "head")
    inspector = inspect(create_engine(url))
    assert {"acquisition_sources", "acquisition_runs", "snapshots"}.issubset(
        inspector.get_table_names()
    )
    snapshot_columns = {column["name"] for column in inspector.get_columns("snapshots")}
    assert {"payload", "sha256", "byte_size", "retrieved_at"}.issubset(snapshot_columns)

    command.downgrade(config, "f2a7c84e9d10")
    tables = inspect(create_engine(url)).get_table_names()
    assert "snapshots" not in tables
    assert "acquisition_runs" not in tables
    assert "acquisition_sources" not in tables
