from argparse import Namespace

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.migration_metadata import target_metadata


PREVIOUS_REVISION = "b5d82a7c1e40"


def config(database_url: str) -> Config:
    alembic_config = Config("alembic.ini")
    alembic_config.attributes["database_url"] = database_url
    alembic_config.cmd_opts = Namespace(x=["allow_database_write=true"])
    return alembic_config


def test_fact_migration_matches_metadata_and_downgrades(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'facts.db').as_posix()}"
    alembic_config = config(database_url)

    command.upgrade(alembic_config, "head")
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert {column["name"] for column in inspector.get_columns("facts")} == {
            "id",
            "fact_type_key",
            "subject_type",
            "subject_identifier",
            "accepted_value",
            "valid_from",
            "valid_to",
            "status",
            "created_at",
            "supersedes_fact_id",
        }
        assert {index["name"] for index in inspector.get_indexes("facts")} == {
            "ix_facts_fact_type_key",
            "ix_facts_subject",
            "ix_facts_supersedes_fact_id",
        }
        assert {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("facts")
        } == {("supersedes_fact_id",)}
        assert {
            tuple(column["name"] for column in constraint["constrained_columns"])
            if isinstance(constraint["constrained_columns"][0], dict)
            else tuple(constraint["constrained_columns"])
            for constraint in inspector.get_foreign_keys("fact_verifications")
        } == {("fact_id",), ("verification_id",)}
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
        assert "facts" not in tables
        assert "fact_verifications" not in tables
        assert "verifications" in tables
    finally:
        engine.dispose()
