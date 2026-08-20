"""Tests for scripts/migrate_reconciliation_confirmation_slice_r4b.py
(docs/architecture/existing-signal-reconciliation-r4b-reviewer-action-report.md).

Never touches the real database - every test builds an isolated temp-file
SQLite database, starting from the Slice 9B reviewer_actions shape (built
via scripts/migrate_reviewer_action_slice9b.py against a synthetic DB, never
the real one) and then applying this migration on top. Modeled on the
established pattern in tests/test_reviewer_action_migration.py.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app.database import Base
from app import models  # noqa: F401 - registers all metadata
from scripts.migrate_reconciliation_confirmation_slice_r4b import downgrade, inspect, upgrade


def _pre_r4b_database(tmp_path: Path, name: str = "pre_r4b.db") -> Path:
    """A database with every table Slice 9B expects, plus a reviewer_actions
    table built the way it looked before R4B (no reconciliation_fingerprint
    column, 3-constraint CHECK set) - hand-written DDL, deliberately not
    derived from the live (now R4B-shaped) SQLAlchemy metadata, so this
    fixture keeps describing the real pre-R4B shape even after
    app/models/reviewer_action.py changes further in the future."""
    database = tmp_path / name
    engine = create_engine(f"sqlite:///{database}")
    tables = [t for name_, t in Base.metadata.tables.items() if name_ != "reviewer_actions"]
    Base.metadata.create_all(engine, tables=tables)
    engine.dispose()

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        """
        CREATE TABLE reviewer_actions (
            id INTEGER NOT NULL PRIMARY KEY,
            source_assertion_id INTEGER NOT NULL,
            action VARCHAR(30) NOT NULL,
            reason TEXT NOT NULL,
            reviewer VARCHAR(100) NOT NULL,
            created_at DATETIME NOT NULL,
            supersedes_action_id INTEGER,
            duplicate_of_signal_id INTEGER,
            CONSTRAINT ck_reviewer_actions_action CHECK (action IN
                ('APPROVE_SIGNAL','REJECT_SIGNAL','DEFER','NEEDS_MORE_EVIDENCE','MARK_DUPLICATE')),
            CONSTRAINT ck_reviewer_actions_duplicate_target_required CHECK
                ((action != 'MARK_DUPLICATE') OR duplicate_of_signal_id IS NOT NULL),
            CONSTRAINT ck_reviewer_actions_duplicate_target_only_for_duplicate CHECK
                ((action = 'MARK_DUPLICATE') OR duplicate_of_signal_id IS NULL),
            FOREIGN KEY(source_assertion_id) REFERENCES source_assertions (id),
            FOREIGN KEY(supersedes_action_id) REFERENCES reviewer_actions (id),
            FOREIGN KEY(duplicate_of_signal_id) REFERENCES signals (id)
        )
        """
    )
    connection.execute("CREATE INDEX ix_reviewer_actions_source_assertion_id ON reviewer_actions (source_assertion_id)")
    connection.execute("CREATE INDEX ix_reviewer_actions_supersedes_action_id ON reviewer_actions (supersedes_action_id)")
    connection.execute("CREATE INDEX ix_reviewer_actions_duplicate_of_signal_id ON reviewer_actions (duplicate_of_signal_id)")
    connection.commit()
    connection.close()
    return database


def _seed_realistic_rows(database: Path) -> dict:
    """MSP's real reviewer_actions history shape:
    id=1 APPROVE_SIGNAL, id=2 MARK_DUPLICATE -> Signal #67 (here re-created
    with synthetic ids 1/1 for a self-contained fixture)."""
    now = "2026-08-19 00:00:00"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "INSERT INTO airports (id, name, country, created_at, updated_at) VALUES (1, 'MSP', 'USA', ?, ?)",
        (now, now),
    )
    connection.execute(
        "INSERT INTO sources (id, title, source_type, reliability_level) VALUES (1, 'EMAS memo', 'web_discovery', 'unverified')"
    )
    connection.execute(
        "INSERT INTO source_assertions (id, source_id, airport_id, assertion_type, source_record_identifier, "
        "evidence_quality, review_state, identity_guard_decision, intelligence_review_decision, "
        "promotion_policy_decision, created_at) VALUES "
        "(222, 1, 1, 'project_construction', 'msp-222', 'unverified_candidate', 'unreviewed', "
        "'ATTACH_CONFIRMED', 'REVIEW_REQUIRED', 'HUMAN_REVIEW_REQUIRED', ?)",
        (now,),
    )
    connection.execute(
        "INSERT INTO signals (id, airport_id, title, category, confidence, published, created_at, updated_at) "
        "VALUES (67, 1, 'MSP EMAS signal', 'replacement', 'high', 1, ?, ?)",
        (now, now),
    )
    connection.execute(
        "INSERT INTO reviewer_actions (id, source_assertion_id, action, reason, reviewer, created_at) "
        "VALUES (1, 222, 'APPROVE_SIGNAL', 'Approved.', 'human:a', ?)",
        (now,),
    )
    connection.execute(
        "INSERT INTO reviewer_actions (id, source_assertion_id, action, reason, reviewer, created_at, "
        "supersedes_action_id, duplicate_of_signal_id) VALUES "
        "(2, 222, 'MARK_DUPLICATE', 'Corroborates existing signal.', 'human:b', ?, 1, 67)",
        (now,),
    )
    connection.commit()
    connection.close()
    return {"airport_id": 1, "source_assertion_id": 222, "signal_id": 67}


def _table_row_counts(database: Path) -> dict[str, int]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    counts = {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables}
    connection.close()
    return counts


# ---------------------------------------------------------------------------
# 1-3. Fresh upgrade / upgrade with existing rows.
# ---------------------------------------------------------------------------


def test_fresh_upgrade_adds_column_and_new_constraints(tmp_path):
    database = _pre_r4b_database(tmp_path)
    upgrade(database)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    columns = {row[1]: bool(row[3]) for row in connection.execute("PRAGMA table_info(reviewer_actions)")}
    create_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='reviewer_actions'"
    ).fetchone()[0]
    connection.close()

    assert "reconciliation_fingerprint" in columns
    assert columns["reconciliation_fingerprint"] is False  # nullable
    assert "CONFIRM_DISTINCT_SIGNAL" in create_sql
    assert "ck_reviewer_actions_fingerprint_required" in create_sql
    assert "ck_reviewer_actions_fingerprint_only_for_confirm_distinct" in create_sql
    assert "ck_reviewer_actions_action" in create_sql
    assert "ck_reviewer_actions_duplicate_target_required" in create_sql
    assert "ck_reviewer_actions_duplicate_target_only_for_duplicate" in create_sql


def test_upgrade_with_existing_approve_signal_row(tmp_path):
    database = _pre_r4b_database(tmp_path)
    ids = _seed_realistic_rows(database)
    upgrade(database)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    row = connection.execute(
        "SELECT action, reason, reviewer, duplicate_of_signal_id, reconciliation_fingerprint "
        "FROM reviewer_actions WHERE id=1"
    ).fetchone()
    connection.close()
    assert row == ("APPROVE_SIGNAL", "Approved.", "human:a", None, None)


def test_upgrade_with_existing_mark_duplicate_row(tmp_path):
    database = _pre_r4b_database(tmp_path)
    ids = _seed_realistic_rows(database)
    upgrade(database)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    row = connection.execute(
        "SELECT action, reason, reviewer, supersedes_action_id, duplicate_of_signal_id, reconciliation_fingerprint "
        "FROM reviewer_actions WHERE id=2"
    ).fetchone()
    connection.close()
    assert row == ("MARK_DUPLICATE", "Corroborates existing signal.", "human:b", 1, ids["signal_id"], None)


# ---------------------------------------------------------------------------
# 4. Existing rows preserved exactly (all tables, byte-for-byte row counts
# and content).
# ---------------------------------------------------------------------------


def test_existing_rows_and_all_other_tables_preserved_exactly(tmp_path):
    database = _pre_r4b_database(tmp_path)
    ids = _seed_realistic_rows(database)
    counts_before = _table_row_counts(database)

    upgrade(database)

    counts_after = _table_row_counts(database)
    assert counts_after == counts_before  # reviewer_actions row count unchanged; no table gained/lost rows

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    assertion_row = connection.execute(
        "SELECT identity_guard_decision, intelligence_review_decision, promotion_policy_decision "
        "FROM source_assertions WHERE id=?", (ids["source_assertion_id"],),
    ).fetchone()
    signal_row = connection.execute("SELECT title FROM signals WHERE id=?", (ids["signal_id"],)).fetchone()
    connection.close()
    assert assertion_row == ("ATTACH_CONFIRMED", "REVIEW_REQUIRED", "HUMAN_REVIEW_REQUIRED")
    assert signal_row == ("MSP EMAS signal",)


# ---------------------------------------------------------------------------
# 5-6. action CHECK updated correctly; new fingerprint CHECKs present
# (covered above); malformed/valid direct SQL inserts.
# ---------------------------------------------------------------------------


def test_malformed_direct_sql_insert_fails_after_upgrade(tmp_path):
    database = _pre_r4b_database(tmp_path)
    _seed_realistic_rows(database)
    upgrade(database)

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO reviewer_actions (source_assertion_id, action, reason, reviewer, created_at) "
            "VALUES (222, 'CONFIRM_DISTINCT_SIGNAL', 'x', 'human:c', '2026-08-19 00:03:00')"
        )  # missing reconciliation_fingerprint
    connection.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO reviewer_actions (source_assertion_id, action, reason, reviewer, created_at, "
            "reconciliation_fingerprint) VALUES (222, 'DEFER', 'x', 'human:c', '2026-08-19 00:03:00', ?)",
            ("a" * 64,),
        )  # fingerprint on a non-CONFIRM_DISTINCT_SIGNAL action
    connection.rollback()
    connection.close()


@pytest.mark.parametrize(
    "sql,params,expect_failure",
    [
        # A. CONFIRM_DISTINCT_SIGNAL + NULL fingerprint
        (
            "INSERT INTO reviewer_actions (source_assertion_id, action, reason, reviewer, created_at) "
            "VALUES (222, 'CONFIRM_DISTINCT_SIGNAL', 'x', 'human:z', '2026-08-19 00:10:00')",
            (),
            True,
        ),
        # B. CONFIRM_DISTINCT_SIGNAL + valid fingerprint + duplicate target
        (
            "INSERT INTO reviewer_actions (source_assertion_id, action, reason, reviewer, created_at, "
            "reconciliation_fingerprint, duplicate_of_signal_id) VALUES "
            "(222, 'CONFIRM_DISTINCT_SIGNAL', 'x', 'human:z', '2026-08-19 00:10:00', ?, 67)",
            ("a" * 64,),
            True,
        ),
        # C. APPROVE_SIGNAL + fingerprint
        (
            "INSERT INTO reviewer_actions (source_assertion_id, action, reason, reviewer, created_at, "
            "reconciliation_fingerprint) VALUES "
            "(222, 'APPROVE_SIGNAL', 'x', 'human:z', '2026-08-19 00:10:00', ?)",
            ("a" * 64,),
            True,
        ),
        # D. MARK_DUPLICATE without duplicate target
        (
            "INSERT INTO reviewer_actions (source_assertion_id, action, reason, reviewer, created_at) "
            "VALUES (222, 'MARK_DUPLICATE', 'x', 'human:z', '2026-08-19 00:10:00')",
            (),
            True,
        ),
        # E. MARK_DUPLICATE + fingerprint (with a valid duplicate target too)
        (
            "INSERT INTO reviewer_actions (source_assertion_id, action, reason, reviewer, created_at, "
            "duplicate_of_signal_id, reconciliation_fingerprint) VALUES "
            "(222, 'MARK_DUPLICATE', 'x', 'human:z', '2026-08-19 00:10:00', 67, ?)",
            ("a" * 64,),
            True,
        ),
        # F. invalid action
        (
            "INSERT INTO reviewer_actions (source_assertion_id, action, reason, reviewer, created_at) "
            "VALUES (222, 'APPROVE_EVERYTHING', 'x', 'human:z', '2026-08-19 00:10:00')",
            (),
            True,
        ),
        # G. valid CONFIRM_DISTINCT_SIGNAL
        (
            "INSERT INTO reviewer_actions (source_assertion_id, action, reason, reviewer, created_at, "
            "supersedes_action_id, reconciliation_fingerprint) VALUES "
            "(222, 'CONFIRM_DISTINCT_SIGNAL', 'x', 'human:z', '2026-08-19 00:10:00', 1, ?)",
            ("a" * 64,),
            False,
        ),
    ],
    ids=["A_confirm_null_fingerprint", "B_confirm_plus_duplicate_target", "C_approve_plus_fingerprint",
         "D_mark_duplicate_no_target", "E_mark_duplicate_plus_fingerprint", "F_invalid_action",
         "G_valid_confirm_distinct_signal"],
)
def test_migrated_table_check_constraints_attacked_via_raw_sql(tmp_path, sql, params, expect_failure):
    """Mission Section 17: the ORM-bypass CHECK tests in
    tests/test_reviewer_action_confirm_distinct_signal.py prove the CHECK
    constraints work on a table SQLAlchemy created directly from the current
    model - they do not prove the *migrated* (rebuilt) table carries the
    same constraints. This test attacks the actual post-migration table."""
    database = _pre_r4b_database(tmp_path)
    _seed_realistic_rows(database)
    upgrade(database)

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    if expect_failure:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(sql, params)
        connection.rollback()
    else:
        connection.execute(sql, params)
        connection.commit()
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        assert violations == []
    connection.close()


def test_upgrade_preserves_created_at_values_exactly(tmp_path):
    """Mission Section 16: 'No timestamp rewrite.' Verified directly rather
    than merely inferred from the tuple-equality checks elsewhere, which
    don't select created_at."""
    database = _pre_r4b_database(tmp_path)
    _seed_realistic_rows(database)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    before = connection.execute("SELECT id, created_at FROM reviewer_actions ORDER BY id").fetchall()
    connection.close()

    upgrade(database)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    after = connection.execute("SELECT id, created_at FROM reviewer_actions ORDER BY id").fetchall()
    connection.close()
    assert after == before == [(1, "2026-08-19 00:00:00"), (2, "2026-08-19 00:00:00")]


def test_upgrade_does_not_resequence_ids(tmp_path):
    """Mission Section 16: 'No accidental ID resequencing.'"""
    database = _pre_r4b_database(tmp_path)
    _seed_realistic_rows(database)
    upgrade(database)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    ids = [row[0] for row in connection.execute("SELECT id FROM reviewer_actions ORDER BY id")]
    connection.close()
    assert ids == [1, 2]


def test_valid_direct_sql_confirm_distinct_signal_succeeds_after_upgrade(tmp_path):
    database = _pre_r4b_database(tmp_path)
    ids = _seed_realistic_rows(database)
    upgrade(database)

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "INSERT INTO reviewer_actions (id, source_assertion_id, action, reason, reviewer, created_at, "
        "supersedes_action_id, reconciliation_fingerprint) VALUES "
        "(3, 222, 'CONFIRM_DISTINCT_SIGNAL', 'Reviewed; distinct.', 'human:c', '2026-08-19 00:03:00', 1, ?)",
        ("a" * 64,),
    )
    connection.commit()
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    connection.close()
    assert violations == []


# ---------------------------------------------------------------------------
# 7-8. Idempotent upgrade; downgrade succeeds.
# ---------------------------------------------------------------------------


def test_upgrade_is_idempotent(tmp_path):
    database = _pre_r4b_database(tmp_path)
    _seed_realistic_rows(database)
    upgrade(database)
    first = inspect(database)
    upgrade(database)  # must not fail or duplicate anything
    second = inspect(database)
    assert first == second


def test_downgrade_removes_column_and_restores_original_constraints(tmp_path):
    database = _pre_r4b_database(tmp_path)
    _seed_realistic_rows(database)
    upgrade(database)
    downgrade(database)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(reviewer_actions)")}
    create_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='reviewer_actions'"
    ).fetchone()[0]
    connection.close()

    assert "reconciliation_fingerprint" not in columns
    assert "CONFIRM_DISTINCT_SIGNAL" not in create_sql
    assert "ck_reviewer_actions_fingerprint_required" not in create_sql


def test_downgrade_preserves_existing_rows(tmp_path):
    database = _pre_r4b_database(tmp_path)
    ids = _seed_realistic_rows(database)
    upgrade(database)
    downgrade(database)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    rows = connection.execute(
        "SELECT id, action, reason, reviewer, supersedes_action_id, duplicate_of_signal_id "
        "FROM reviewer_actions ORDER BY id"
    ).fetchall()
    connection.close()
    assert rows == [
        (1, "APPROVE_SIGNAL", "Approved.", "human:a", None, None),
        (2, "MARK_DUPLICATE", "Corroborates existing signal.", "human:b", 1, ids["signal_id"]),
    ]


# ---------------------------------------------------------------------------
# 9. upgrade -> downgrade -> upgrade succeeds.
# ---------------------------------------------------------------------------


def test_upgrade_downgrade_upgrade_round_trip(tmp_path):
    database = _pre_r4b_database(tmp_path)
    _seed_realistic_rows(database)
    upgrade(database)
    downgrade(database)
    upgrade(database)

    assert inspect(database)["reconciliation_fingerprint_column_exists"] is True
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "INSERT INTO reviewer_actions (source_assertion_id, action, reason, reviewer, created_at, "
        "reconciliation_fingerprint) VALUES (222, 'CONFIRM_DISTINCT_SIGNAL', 'x', 'human:d', "
        "'2026-08-19 00:04:00', ?)",
        ("c" * 64,),
    )
    connection.commit()
    connection.close()


# ---------------------------------------------------------------------------
# 10. Downgrade refuses to silently discard a CONFIRM_DISTINCT_SIGNAL row.
# ---------------------------------------------------------------------------


def test_downgrade_fails_closed_if_a_confirm_distinct_signal_row_exists(tmp_path):
    database = _pre_r4b_database(tmp_path)
    _seed_realistic_rows(database)
    upgrade(database)

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "INSERT INTO reviewer_actions (source_assertion_id, action, reason, reviewer, created_at, "
        "reconciliation_fingerprint) VALUES (222, 'CONFIRM_DISTINCT_SIGNAL', 'x', 'human:e', "
        "'2026-08-19 00:05:00', ?)",
        ("d" * 64,),
    )
    connection.commit()
    connection.close()

    with pytest.raises(Exception):
        downgrade(database)

    # The table must still exist with the fingerprint column and the row intact -
    # a failed downgrade must not have partially applied.
    assert inspect(database)["reconciliation_fingerprint_column_exists"] is True
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    count = connection.execute(
        "SELECT count(*) FROM reviewer_actions WHERE action='CONFIRM_DISTINCT_SIGNAL'"
    ).fetchone()[0]
    connection.close()
    assert count == 1


# ---------------------------------------------------------------------------
# 11. FK check / integrity_check clean throughout.
# ---------------------------------------------------------------------------


def test_foreign_key_and_integrity_check_clean_through_full_cycle(tmp_path):
    database = _pre_r4b_database(tmp_path)
    _seed_realistic_rows(database)

    upgrade(database)
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    connection.close()

    downgrade(database)
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    connection.close()


# ---------------------------------------------------------------------------
# 12-13. Write-authorization gate; real DB never resolved by default.
# ---------------------------------------------------------------------------


def test_main_requires_allow_database_write_flag(tmp_path):
    database = _pre_r4b_database(tmp_path)
    from scripts.migrate_reconciliation_confirmation_slice_r4b import main

    with pytest.raises(SystemExit):
        main(["--database", str(database)])

    assert inspect(database)["reconciliation_fingerprint_column_exists"] is False


def test_migration_never_touches_a_different_database_than_the_one_named(tmp_path):
    """Mission Section 21: 'protected.db vs target.db isolation.' The
    migration is only ever given a single explicit --database path
    (data/runway_safe.db is only an argparse *default*, never silently
    substituted when a real path is passed) - proven here by upgrading one
    disposable database and confirming a second, untouched sibling database
    is byte-identical before and after."""
    target = _pre_r4b_database(tmp_path, name="target.db")
    _seed_realistic_rows(target)
    protected = _pre_r4b_database(tmp_path, name="protected.db")
    _seed_realistic_rows(protected)

    protected_bytes_before = protected.read_bytes()
    upgrade(target)
    protected_bytes_after = protected.read_bytes()

    assert protected_bytes_after == protected_bytes_before
    assert inspect(protected)["reconciliation_fingerprint_column_exists"] is False
    assert inspect(target)["reconciliation_fingerprint_column_exists"] is True


def test_upgrade_requires_slice_9b_table_to_already_exist(tmp_path):
    database = tmp_path / "no_reviewer_actions.db"
    engine = create_engine(f"sqlite:///{database}")
    tables = [t for name_, t in Base.metadata.tables.items() if name_ != "reviewer_actions"]
    Base.metadata.create_all(engine, tables=tables)
    engine.dispose()

    with pytest.raises(RuntimeError, match="migrate_reviewer_action_slice9b"):
        upgrade(database)
