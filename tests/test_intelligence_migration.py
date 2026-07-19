from argparse import Namespace

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.migration_metadata import target_metadata


PREVIOUS_REVISION = "c8f1a34d6e72"


def config(database_url: str) -> Config:
    alembic_config = Config("alembic.ini")
    alembic_config.attributes["database_url"] = database_url
    alembic_config.cmd_opts = Namespace(x=["allow_database_write=true"])
    return alembic_config


def test_intelligence_migration_matches_metadata_and_downgrades(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'intelligence.db').as_posix()}"
    alembic_config = config(database_url)

    command.upgrade(alembic_config, "head")
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
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
        assert {index["name"] for index in inspector.get_indexes("intelligence")} == {
            "ix_intelligence_created_at",
            "ix_intelligence_finding_type_id",
            "ix_intelligence_status",
            "ix_intelligence_supersedes_intelligence_id",
        }
        assert {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("intelligence")
        } == {("supersedes_intelligence_id",)}
        assert {
            tuple(foreign_key["constrained_columns"])
            for foreign_key in inspector.get_foreign_keys("intelligence_facts")
        } == {("intelligence_id",), ("fact_id",)}
        with engine.connect() as connection:
            assert compare_metadata(
                MigrationContext.configure(connection), target_metadata
            ) == []
    finally:
        engine.dispose()

    command.downgrade(alembic_config, PREVIOUS_REVISION)
    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "intelligence" not in tables
        assert "intelligence_facts" not in tables
        assert "facts" in tables
    finally:
        engine.dispose()
