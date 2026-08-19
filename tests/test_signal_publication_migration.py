"""Tests for Slice 9A: Signal.published and
scripts/migrate_signal_publication_slice9a.py
(docs/architecture/signal-publication-separation-slice9a-report.md).

Never touches the real database - every test builds an isolated in-memory or
temp-file SQLite database. Modeled on the already-proven pattern in
tests/test_promotion_policy_persistence_migration.py.
"""
from __future__ import annotations

import ast
import inspect
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app import models  # noqa: F401 - registers all metadata
from app.models import Airport, Signal, Source
from app.static_export.build import _is_public_signal
from scripts.migrate_signal_publication_slice9a import (
    LEGACY_EXCLUDED_SIGNAL_IDS,
    downgrade,
    inspect as migration_inspect,
    upgrade,
)


# ---------------------------------------------------------------------------
# 1. Signal model published field
# ---------------------------------------------------------------------------


def test_signal_model_defaults_published_true_when_unset():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = Airport(name="Test Airport", country="USA")
        signal = Signal(airport=airport, title="Test signal", category="unknown", confidence="low")
        session.add(signal)
        session.commit()
        session.refresh(signal)
        assert signal.published is True


def test_signal_model_accepts_explicit_published_false():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = Airport(name="Test Airport", country="USA")
        signal = Signal(
            airport=airport, title="Internal governed signal", category="unknown",
            confidence="low", published=False,
        )
        session.add(signal)
        session.commit()
        session.refresh(signal)
        assert signal.published is False


# ---------------------------------------------------------------------------
# Migration fixtures: hand-built pre-Slice-9A `signals` table (no `published`
# column), matching the shape tests/test_promotion_policy_persistence_migration.py
# uses for source_assertions before its own slice's columns existed.
# ---------------------------------------------------------------------------


def _pre_migration_database(tmp_path: Path) -> Path:
    database = tmp_path / "pre_slice9a.db"
    engine = create_engine(f"sqlite:///{database}")
    tables = [t for name, t in Base.metadata.tables.items() if name != "signals"]
    Base.metadata.create_all(engine, tables=tables)
    engine.dispose()

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute(
        "CREATE TABLE signals_old ("
        "id INTEGER NOT NULL, airport_id INTEGER NOT NULL, runway_id INTEGER, source_id INTEGER, "
        "installation_id INTEGER, title VARCHAR(250) NOT NULL, category VARCHAR(50) NOT NULL, "
        "confidence VARCHAR(30) NOT NULL, target_year INTEGER, notes TEXT, manual_year_estimate INTEGER, "
        "source_notes TEXT, status VARCHAR(50), planning_year INTEGER, procurement_year INTEGER, "
        "construction_start DATE, completion_date DATE, estimated_total_value_usd NUMERIC(14, 2), "
        "estimated_emas_value_usd NUMERIC(14, 2), probability_score FLOAT, supplier VARCHAR(150), "
        "likely_supplier VARCHAR(150), supplier_reason TEXT, confirmed_vendor VARCHAR(150), "
        "last_verified_at DATE, created_at DATETIME, updated_at DATETIME, "
        "PRIMARY KEY (id), "
        "FOREIGN KEY(airport_id) REFERENCES airports (id), "
        "FOREIGN KEY(runway_id) REFERENCES runways (id), "
        "FOREIGN KEY(source_id) REFERENCES sources (id), "
        "FOREIGN KEY(installation_id) REFERENCES installations (id))"
    )
    # Built under a temporary name and RENAMEd into place - matches
    # tests/test_promotion_policy_persistence_migration.py's own technique,
    # so the byte-for-byte schema-text comparison in
    # test_downgrade_is_exactly_reversible is meaningful rather than an
    # artifact of two different construction paths (SQLite's ALTER TABLE
    # RENAME rewrites the stored CREATE TABLE text the same way
    # _drop_column_via_rebuild()'s own rebuild-then-rename does).
    connection.execute("ALTER TABLE signals_old RENAME TO signals")
    connection.execute("CREATE INDEX ix_signals_airport_id ON signals(airport_id)")
    connection.execute("CREATE INDEX ix_signals_runway_id ON signals(runway_id)")
    connection.execute("CREATE INDEX ix_signals_source_id ON signals(source_id)")
    connection.execute("CREATE INDEX ix_signals_title ON signals(title)")
    connection.execute("CREATE INDEX ix_signals_category ON signals(category)")
    connection.execute("CREATE INDEX ix_signals_confidence ON signals(confidence)")
    connection.execute("CREATE INDEX ix_signals_target_year ON signals(target_year)")
    connection.execute("CREATE INDEX ix_signals_status ON signals(status)")
    connection.execute("CREATE INDEX ix_signals_planning_year ON signals(planning_year)")
    connection.execute("CREATE INDEX ix_signals_procurement_year ON signals(procurement_year)")
    connection.execute("CREATE INDEX ix_signals_installation_id ON signals(installation_id)")
    connection.commit()
    connection.close()
    return database


def _seed_realistic_signals(database: Path) -> None:
    """68 real Signals never fit in a unit test, but three rows spanning
    both legacy-excluded ids and a set of realistically varied ordinary
    rows exercise the same code paths: the two hardcoded exclusions (52,
    54) plus one representative ordinary public row, with every column
    populated the way real rows in this table actually look (financial
    fields, dates, free text)."""
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    now = "2026-08-19 00:00:00"
    connection.execute(
        "INSERT INTO airports (id, name, country, created_at, updated_at) VALUES (1, 'Test Airport', 'USA', ?, ?)",
        (now, now),
    )
    connection.execute("INSERT INTO sources (id, title, source_type, reliability_level) VALUES (1, 'Test Source', 'test', 'official')")
    rows = [
        (1, "Ordinary public signal", "replacement", "high"),
        (52, "Legacy excluded signal 52", "unknown", "low"),
        (54, "Legacy excluded signal 54", "unknown", "low"),
        (99, "Another ordinary public signal", "new_installation", "medium"),
    ]
    for signal_id, title, category, confidence in rows:
        connection.execute(
            "INSERT INTO signals (id, airport_id, source_id, title, category, confidence, "
            "estimated_total_value_usd, notes, source_notes, status, created_at, updated_at) "
            "VALUES (?, 1, 1, ?, ?, ?, 1590000.00, 'internal note', 'public research note', "
            "'identified', ?, ?)",
            (signal_id, title, category, confidence, now, now),
        )
    connection.commit()
    connection.close()


def _signal_rows(database: Path) -> list[tuple]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    rows = connection.execute(
        "SELECT id, airport_id, source_id, title, category, confidence, estimated_total_value_usd, "
        "notes, source_notes, status, created_at, updated_at FROM signals ORDER BY id"
    ).fetchall()
    connection.close()
    return rows


# ---------------------------------------------------------------------------
# 2-5. migration upgrade / idempotency / downgrade / realistic-row preservation
# ---------------------------------------------------------------------------


def test_upgrade_adds_published_column_and_backfills_true_by_default(tmp_path):
    database = _pre_migration_database(tmp_path)
    _seed_realistic_signals(database)
    rows_before = _signal_rows(database)

    upgrade(database)

    after = migration_inspect(database)
    assert after["published_column_exists"] is True
    assert after["signals_count"] == 4
    assert after["published_true_count"] == 2  # ids 1, 99
    assert after["published_false_count"] == 2  # ids 52, 54
    assert after["legacy_excluded_ids_published_false"] is True
    assert after["foreign_key_check"] == []

    # Every non-published column is untouched.
    rows_after = _signal_rows(database)
    assert rows_after == rows_before


def test_upgrade_is_idempotent_when_run_twice(tmp_path):
    database = _pre_migration_database(tmp_path)
    _seed_realistic_signals(database)
    upgrade(database)
    first = migration_inspect(database)
    upgrade(database)  # must not fail or duplicate anything
    second = migration_inspect(database)
    assert first == second


def test_downgrade_is_exactly_reversible(tmp_path):
    database = _pre_migration_database(tmp_path)
    _seed_realistic_signals(database)
    rows_before = _signal_rows(database)

    connection = sqlite3.connect(database)
    schema_before = sorted(row[0] for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
    ).fetchall())
    connection.close()

    upgrade(database)
    downgrade(database)

    after = migration_inspect(database)
    assert after["published_column_exists"] is False
    assert after["signals_count"] == 4

    connection = sqlite3.connect(database)
    schema_after = sorted(row[0] for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
    ).fetchall())
    connection.close()
    rows_after = _signal_rows(database)

    assert schema_after == schema_before
    assert rows_after == rows_before

    # Re-upgrade must succeed cleanly after downgrade.
    upgrade(database)
    final = migration_inspect(database)
    assert final["published_column_exists"] is True
    assert final["foreign_key_check"] == []


def test_downgrade_leaves_no_foreign_key_or_integrity_violations(tmp_path):
    database = _pre_migration_database(tmp_path)
    _seed_realistic_signals(database)
    upgrade(database)
    downgrade(database)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    connection.close()


# ---------------------------------------------------------------------------
# 6-9. legacy public/excluded backfill correctness, exact before/after set
# equality, row-count invariance.
# ---------------------------------------------------------------------------


def _legacy_is_public(signal_id: int) -> bool:
    """The exact pre-Slice-9A rule, reproduced independently of the
    now-updated app/static_export/build.py::_is_public_signal() so this
    test does not just compare a function against itself."""
    return signal_id not in LEGACY_EXCLUDED_SIGNAL_IDS


def test_legacy_public_rows_backfill_to_published_true(tmp_path):
    database = _pre_migration_database(tmp_path)
    _seed_realistic_signals(database)
    upgrade(database)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    for signal_id in (1, 99):
        assert _legacy_is_public(signal_id) is True
        published = connection.execute("SELECT published FROM signals WHERE id=?", (signal_id,)).fetchone()[0]
        assert published == 1
    connection.close()


def test_legacy_excluded_ids_backfill_to_published_false(tmp_path):
    database = _pre_migration_database(tmp_path)
    _seed_realistic_signals(database)
    upgrade(database)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    for signal_id in LEGACY_EXCLUDED_SIGNAL_IDS:
        assert _legacy_is_public(signal_id) is False
        published = connection.execute("SELECT published FROM signals WHERE id=?", (signal_id,)).fetchone()[0]
        assert published == 0
    connection.close()


def test_public_signal_id_set_identical_before_and_after_migration(tmp_path):
    database = _pre_migration_database(tmp_path)
    _seed_realistic_signals(database)
    all_ids = [row[0] for row in _signal_rows(database)]

    public_before = {signal_id for signal_id in all_ids if _legacy_is_public(signal_id)}

    upgrade(database)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    public_after = {
        row[0] for row in connection.execute("SELECT id FROM signals WHERE published=1").fetchall()
    }
    signals_count_after = connection.execute("SELECT count(*) FROM signals").fetchone()[0]
    connection.close()

    assert public_after == public_before
    assert signals_count_after == len(all_ids)


# ---------------------------------------------------------------------------
# 10-13, 20. Internal (unpublished) Signal semantics + create != publish,
# exercised through the real ORM model and the real static-export filter,
# not just raw SQL.
# ---------------------------------------------------------------------------


def _seed_public_and_private_signal(session):
    airport = Airport(name="Test Airport", country="USA")
    source = Source(title="Test Source", source_type="test")
    public_signal = Signal(
        airport=airport, source=source, title="Public signal", category="replacement",
        confidence="high", published=True,
    )
    private_signal = Signal(
        airport=airport, source=source, title="Internal governed signal", category="replacement",
        confidence="medium", published=False,
    )
    session.add_all([public_signal, private_signal])
    session.commit()
    return public_signal, private_signal


def test_unpublished_signal_stays_internally_queryable():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _public, private_signal = _seed_public_and_private_signal(session)
        private_id = private_signal.id
        session.expire_all()
        refetched = session.get(Signal, private_id)
        assert refetched is not None
        assert refetched.published is False
        assert refetched.title == "Internal governed signal"


def test_unpublished_signal_excluded_from_is_public_signal_filter():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        public_signal, private_signal = _seed_public_and_private_signal(session)
        assert _is_public_signal(public_signal) is True
        assert _is_public_signal(private_signal) is False


def test_creating_a_signal_does_not_imply_public_export():
    """The architectural point of this slice: persisting a Signal and
    making it publicly visible are two independent actions."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = Airport(name="Test Airport", country="USA")
        signal = Signal(
            airport=airport, title="Newly created governed signal", category="replacement",
            confidence="medium", published=False,
        )
        session.add(signal)
        session.commit()
        session.refresh(signal)

        # Persisted, has a real id, fully queryable...
        assert signal.id is not None
        assert session.get(Signal, signal.id) is not None
        # ...but not eligible for public/static export.
        assert _is_public_signal(signal) is False


# ---------------------------------------------------------------------------
# 14. Existing Signal creation paths retain behavior: a Signal constructed
# the way every current writer does (no `published` kwarg) is public by
# default, exactly as before this slice.
# ---------------------------------------------------------------------------


def test_signal_created_without_published_kwarg_matches_legacy_writer_shape():
    """Mirrors app/services/signal_rules.py::add_source_and_flag_keywords()'s
    own Signal(...) call shape - no `published` argument at all."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = Airport(name="Test Airport", country="USA")
        signal = Signal(
            airport=airport, title="Auto-flagged from source text", category="unknown",
            confidence="low", notes="Auto-flagged from source text (matched: emas).",
        )
        session.add(signal)
        session.commit()
        session.refresh(signal)
        assert signal.published is True
        assert _is_public_signal(signal) is True


# ---------------------------------------------------------------------------
# 15. Structural invariant: no discovery/review/promotion module constructs
# a Signal. AST-based, not substring search - a docstring mentioning
# "Signal" in prose must not produce a false positive.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_path",
    [
        "app.services.human_review_queue",
        "app.services.human_review_claim_enrichment",
        "app.services.promotion_policy_persistence",
        "app.services.promotion_policy_evaluation",
        "app.services.intelligence_review_persistence",
        "app.services.signal_candidate_evaluation",
        "app.services.discovery_evidence_persistence",
    ],
)
def test_no_discovery_or_review_module_constructs_a_signal(module_path):
    import importlib

    module = importlib.import_module(module_path)
    tree = ast.parse(inspect.getsource(module))
    signal_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Signal"
    ]
    assert signal_calls == []


# ---------------------------------------------------------------------------
# 16. Wrong-DB / unauthorized-write migration safety.
# ---------------------------------------------------------------------------


def test_main_requires_allow_database_write_flag(tmp_path, capsys):
    database = _pre_migration_database(tmp_path)
    from scripts.migrate_signal_publication_slice9a import main

    with pytest.raises(SystemExit):
        main(["--database", str(database)])

    published_column_exists = migration_inspect(database)["published_column_exists"]
    assert published_column_exists is False  # nothing was written


def test_upgrade_against_database_missing_signals_table_raises(tmp_path):
    database = tmp_path / "no_signals_table.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()

    with pytest.raises(sqlite3.OperationalError):
        upgrade(database)


# ---------------------------------------------------------------------------
# 17. FK/integrity after upgrade specifically (downgrade already covered
# above).
# ---------------------------------------------------------------------------


def test_upgrade_leaves_no_foreign_key_or_integrity_violations(tmp_path):
    database = _pre_migration_database(tmp_path)
    _seed_realistic_signals(database)
    upgrade(database)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    connection.close()
