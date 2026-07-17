import importlib
import sys

from sqlalchemy import create_engine, inspect

from app.database import Base
from scripts.init_db import initialize_database


def test_importing_app_main_does_not_create_tables(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("app.main must not call Base.metadata.create_all()")

    monkeypatch.setattr(Base.metadata, "create_all", fail_if_called)
    sys.modules.pop("app.main", None)

    imported = importlib.import_module("app.main")

    assert imported.app.title == "Runway Safe Intelligence"


def test_explicit_initialization_creates_all_tables_in_temporary_sqlite(tmp_path):
    database_path = tmp_path / "initialized.db"
    test_engine = create_engine(f"sqlite:///{database_path}")
    try:
        initialize_database(test_engine)

        assert set(inspect(test_engine).get_table_names()) == set(Base.metadata.tables)
    finally:
        test_engine.dispose()
