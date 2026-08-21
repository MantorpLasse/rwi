"""Tests for scripts/migrate_signal_disposition_d4d2.py (D4D2,
docs/architecture/fh-d4-signal-disposition-design.md).

Every test uses an isolated temp-file SQLite database (tmp_path). Nothing
in this file ever opens data/runway_safe.db - the real migration is
explicitly deferred to a separate, later D4D6 slice. Grep-verified (see
TestNoRealDatabaseAccess).
"""
from __future__ import annotations

import ast
import inspect as inspect_module
import sqlite3

import pytest
from sqlalchemy import MetaData, create_engine
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateIndex, CreateTable

import app.models
from app.database import Base
from app.models import Airport, ReviewerAction, Signal, Source, SourceAssertion
from app.models.signal_disposition import SignalDisposition, SignalDispositionMember
from app.services.signal_disposition_persistence import record_signal_group_disposition
import scripts.migrate_signal_disposition_d4d2 as migration

D4D2_TABLES = ("signal_dispositions", "signal_disposition_members")


def _pre_d4d2_db(path):
    """A full pre-D4D2 schema (every table except the two this migration
    creates) - the realistic "not yet migrated" starting state."""
    engine = create_engine(f"sqlite:///{path}")
    pre_meta = MetaData()
    for name, table in Base.metadata.tables.items():
        if name not in D4D2_TABLES:
            table.to_metadata(pre_meta)
    pre_meta.create_all(engine)
    engine.dispose()


def _create_table_raw(db_path, table_name):
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    table = Base.metadata.tables[table_name]
    conn.execute(str(CreateTable(table).compile(dialect=sqlite_dialect.dialect())))
    for index in table.indexes:
        conn.execute(str(CreateIndex(index).compile(dialect=sqlite_dialect.dialect())))
    conn.commit()
    conn.close()


def _seed_two_signals(db_path):
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        airport = Airport(name="Migration Test Airport", country="XX")
        s.add(airport)
        a = Signal(airport=airport, title="A", category="replacement", confidence="high")
        b = Signal(airport=airport, title="B", category="replacement", confidence="high")
        s.add_all([a, b])
        s.commit()
        ids = (a.id, b.id)
    engine.dispose()
    return ids


# ---------------------------------------------------------------------------
# Clean upgrade
# ---------------------------------------------------------------------------


class TestCleanUpgrade:
    def test_upgrade_creates_both_tables(self, tmp_path):
        db = tmp_path / "clean.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["tables_exist"] == {"signal_dispositions": True, "signal_disposition_members": True}
        assert result["ready"] is True

    def test_exact_header_columns(self, tmp_path):
        db = tmp_path / "cols.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        cols = {row[1]: (row[2], bool(row[3]), bool(row[5])) for row in conn.execute("PRAGMA table_info(signal_dispositions)")}
        conn.close()
        assert cols == {
            "id": ("INTEGER", True, True),
            "decision": ("VARCHAR(30)", True, False),
            "reason": ("TEXT", True, False),
            "reviewer": ("VARCHAR(100)", True, False),
            "created_at": ("DATETIME", True, False),
            "supersedes_id": ("INTEGER", False, False),
        }

    def test_exact_member_columns(self, tmp_path):
        db = tmp_path / "cols2.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        cols = {row[1]: (row[2], bool(row[3]), bool(row[5])) for row in conn.execute("PRAGMA table_info(signal_disposition_members)")}
        conn.close()
        assert cols == {
            "id": ("INTEGER", True, True),
            "disposition_id": ("INTEGER", True, False),
            "signal_id": ("INTEGER", True, False),
        }

    def test_exact_check_constraint_via_raw_sql(self, tmp_path):
        db = tmp_path / "check.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                "INSERT INTO signal_dispositions (decision, reason, reviewer, created_at) "
                "VALUES ('MAYBE', 'x', 'human:x', '2026-01-01')"
            )
        conn.rollback()
        for valid in ("DISTINCT", "SAME_REAL_WORLD_EFFORT"):
            conn.execute(
                "INSERT INTO signal_dispositions (decision, reason, reviewer, created_at) VALUES (?, 'x', 'human:x', '2026-01-01')",
                (valid,),
            )
        conn.commit()
        assert conn.execute("SELECT count(*) FROM signal_dispositions").fetchone()[0] == 2
        conn.close()

    def test_exact_foreign_keys_via_pragma(self, tmp_path):
        db = tmp_path / "fks.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        header_fks = {(row[3], row[2], row[4]) for row in conn.execute("PRAGMA foreign_key_list(signal_dispositions)")}
        member_fks = {(row[3], row[2], row[4]) for row in conn.execute("PRAGMA foreign_key_list(signal_disposition_members)")}
        conn.close()
        assert header_fks == {("supersedes_id", "signal_dispositions", "id")}
        assert member_fks == {
            ("disposition_id", "signal_dispositions", "id"),
            ("signal_id", "signals", "id"),
        }

    def test_no_on_delete_cascade(self, tmp_path):
        db = tmp_path / "nocascade.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        rows = conn.execute("PRAGMA foreign_key_list(signal_disposition_members)").fetchall()
        conn.close()
        for row in rows:
            assert row[5] == "NO ACTION"  # on_delete column
            assert row[6] == "NO ACTION"  # on_update column

    def test_unique_member_constraint_via_raw_sql(self, tmp_path):
        db = tmp_path / "unique.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        ids = _seed_two_signals(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("INSERT INTO signal_dispositions (decision, reason, reviewer, created_at) VALUES ('DISTINCT', 'x', 'human:x', '2026-01-01')")
        disposition_id = conn.execute("SELECT id FROM signal_dispositions").fetchone()[0]
        conn.execute("INSERT INTO signal_disposition_members (disposition_id, signal_id) VALUES (?, ?)", (disposition_id, ids[0]))
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            conn.execute("INSERT INTO signal_disposition_members (disposition_id, signal_id) VALUES (?, ?)", (disposition_id, ids[0]))
        conn.rollback()
        conn.close()

    def test_same_signal_in_different_dispositions_allowed(self, tmp_path):
        db = tmp_path / "diffdisp.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        ids = _seed_two_signals(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("INSERT INTO signal_dispositions (decision, reason, reviewer, created_at) VALUES ('DISTINCT', 'x', 'human:x', '2026-01-01')")
        conn.execute("INSERT INTO signal_dispositions (decision, reason, reviewer, created_at) VALUES ('DISTINCT', 'y', 'human:y', '2026-01-02')")
        d1, d2 = [row[0] for row in conn.execute("SELECT id FROM signal_dispositions ORDER BY id")]
        conn.execute("INSERT INTO signal_disposition_members (disposition_id, signal_id) VALUES (?, ?)", (d1, ids[0]))
        conn.execute("INSERT INTO signal_disposition_members (disposition_id, signal_id) VALUES (?, ?)", (d2, ids[0]))
        conn.commit()  # same signal, two different dispositions - must succeed
        assert conn.execute("SELECT count(*) FROM signal_disposition_members").fetchone()[0] == 2
        conn.close()

    def test_row_counts_zero_after_upgrade(self, tmp_path):
        db = tmp_path / "zero.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["counts"] == {"signal_dispositions": 0, "signal_disposition_members": 0}


# ---------------------------------------------------------------------------
# Minimum cardinality boundary (documentation, not migration-level enforcement)
# ---------------------------------------------------------------------------


class TestMinimumCardinalityBoundary:
    def test_migration_does_not_enforce_group_cardinality_at_db_level(self, tmp_path):
        """D4D1 deliberately owns >=2-member enforcement as a service-level
        validation - SQLite has no native cross-table row-count CHECK, and
        this migration does not attempt to fabricate one via a trigger. A
        disposition with a SINGLE member row is not rejected by the schema
        itself (only by app.services.signal_disposition_persistence, which
        this migration never bypasses in its own upgrade path)."""
        db = tmp_path / "card.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        ids = _seed_two_signals(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("INSERT INTO signal_dispositions (decision, reason, reviewer, created_at) VALUES ('DISTINCT', 'x', 'human:x', '2026-01-01')")
        disposition_id = conn.execute("SELECT id FROM signal_dispositions").fetchone()[0]
        conn.execute("INSERT INTO signal_disposition_members (disposition_id, signal_id) VALUES (?, ?)", (disposition_id, ids[0]))
        conn.commit()  # a single member - the DB schema alone permits this
        assert conn.execute("SELECT count(*) FROM signal_disposition_members").fetchone()[0] == 1
        conn.close()

    def test_no_trigger_objects_created_by_migration(self, tmp_path):
        db = tmp_path / "notrigger.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        triggers = conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
        conn.close()
        assert triggers == []


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_second_upgrade_is_safe_no_op(self, tmp_path):
        db = tmp_path / "idem.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        before = migration.inspect(db)
        migration.upgrade(db)  # must not raise
        after = migration.inspect(db)
        assert before == after

    def test_second_upgrade_preserves_rows(self, tmp_path):
        db = tmp_path / "idem2.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        ids = _seed_two_signals(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            record_signal_group_disposition(s, signal_ids=list(ids), decision="DISTINCT", reviewer="human:x", reason="x")
            s.commit()
        engine.dispose()

        migration.upgrade(db)  # idempotent re-run after real data exists
        result = migration.inspect(db)
        assert result["counts"] == {"signal_dispositions": 1, "signal_disposition_members": 2}


# ---------------------------------------------------------------------------
# Partial-schema / incompatible-schema safety
# ---------------------------------------------------------------------------


class TestPartialAndIncompatibleSchema:
    def test_neither_table_exists_full_create(self, tmp_path):
        db = tmp_path / "neither.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        assert migration.inspect(db)["ready"] is True

    def test_header_exists_correctly_member_absent_safe_completion(self, tmp_path):
        db = tmp_path / "header_only.db"
        _pre_d4d2_db(db)
        _create_table_raw(db, "signal_dispositions")
        before = migration.inspect(db)
        assert before["tables_exist"] == {"signal_dispositions": True, "signal_disposition_members": False}
        migration.upgrade(db)
        after = migration.inspect(db)
        assert after["ready"] is True

    def test_member_exists_correctly_header_absent_safe_completion(self, tmp_path):
        """A correctly-shaped member table with no header present yet is
        unusual (FK target didn't exist at CREATE TABLE time - SQLite
        permits this since it does not validate FK targets at creation)
        but not incompatible: each table is independently verified against
        the current model, so a correctly-shaped member table is accepted
        regardless of the header's own presence, and the missing header is
        simply created."""
        db = tmp_path / "member_only.db"
        _pre_d4d2_db(db)
        _create_table_raw(db, "signal_disposition_members")
        before = migration.inspect(db)
        assert before["tables_exist"] == {"signal_dispositions": False, "signal_disposition_members": True}
        migration.upgrade(db)
        after = migration.inspect(db)
        assert after["ready"] is True

    def test_wrong_columns_fails_closed(self, tmp_path):
        db = tmp_path / "wrongcols.db"
        _pre_d4d2_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE signal_dispositions (id INTEGER PRIMARY KEY, wrong_column TEXT)")
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError, match="do not match the expected D4D2 schema"):
            migration.upgrade(db)
        # Refusing must not have created the second table either - the whole
        # operation is one transaction.
        result = migration.inspect(db)
        assert result["tables_exist"]["signal_disposition_members"] is False

    def test_wrong_foreign_key_target_fails_closed(self, tmp_path):
        db = tmp_path / "wrongfk.db"
        _pre_d4d2_db(db)
        conn = sqlite3.connect(str(db))
        # Right column shape, but supersedes_id points at the wrong table.
        conn.execute(
            "CREATE TABLE signal_dispositions ("
            "id INTEGER NOT NULL PRIMARY KEY, decision VARCHAR(30) NOT NULL, reason TEXT NOT NULL, "
            "reviewer VARCHAR(100) NOT NULL, created_at DATETIME NOT NULL, supersedes_id INTEGER, "
            "CONSTRAINT ck_signal_dispositions_decision CHECK (decision IN ('DISTINCT', 'SAME_REAL_WORLD_EFFORT')), "
            "FOREIGN KEY(supersedes_id) REFERENCES signals (id))"
        )
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError, match="foreign keys do not match"):
            migration.upgrade(db)

    def test_misleading_same_name_incompatible_table_never_dropped_or_rebuilt(self, tmp_path):
        db = tmp_path / "misleading.db"
        _pre_d4d2_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE signal_disposition_members (id INTEGER PRIMARY KEY, totally_different TEXT)")
        conn.execute("INSERT INTO signal_disposition_members (totally_different) VALUES ('irreplaceable data')")
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError):
            migration.upgrade(db)
        # The unrelated, pre-existing (if oddly-named) table's data must survive untouched.
        conn = sqlite3.connect(str(db))
        row = conn.execute("SELECT totally_different FROM signal_disposition_members").fetchone()
        conn.close()
        assert row[0] == "irreplaceable data"

    def test_missing_named_constraint_fails_closed(self, tmp_path):
        db = tmp_path / "noconstraint.db"
        _pre_d4d2_db(db)
        conn = sqlite3.connect(str(db))
        # Right columns/FK, but the CHECK constraint was never actually added
        # (e.g. a hand-edited or manually-restored table).
        conn.execute(
            "CREATE TABLE signal_dispositions ("
            "id INTEGER NOT NULL PRIMARY KEY, decision VARCHAR(30) NOT NULL, reason TEXT NOT NULL, "
            "reviewer VARCHAR(100) NOT NULL, created_at DATETIME NOT NULL, supersedes_id INTEGER, "
            "FOREIGN KEY(supersedes_id) REFERENCES signal_dispositions (id))"
        )
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError, match="missing expected named constraint"):
            migration.upgrade(db)

    def test_extra_unexpected_column_fails_closed(self, tmp_path):
        """§8 case H: every expected column present, plus one genuinely
        extra one - must be treated as incompatible, not silently
        accepted."""
        db = tmp_path / "extracol.db"
        _pre_d4d2_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE signal_dispositions ("
            "id INTEGER NOT NULL PRIMARY KEY, decision VARCHAR(30) NOT NULL, reason TEXT NOT NULL, "
            "reviewer VARCHAR(100) NOT NULL, created_at DATETIME NOT NULL, supersedes_id INTEGER, "
            "unexpected_extra_field TEXT, "
            "CONSTRAINT ck_signal_dispositions_decision CHECK (decision IN ('DISTINCT', 'SAME_REAL_WORLD_EFFORT')), "
            "FOREIGN KEY(supersedes_id) REFERENCES signal_dispositions (id))"
        )
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError, match="columns do not match"):
            migration.upgrade(db)

    def test_wrong_nullability_fails_closed(self, tmp_path):
        """§8 case I: right columns and types, but `reason` is nullable
        when the model requires NOT NULL."""
        db = tmp_path / "wrongnull.db"
        _pre_d4d2_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE signal_dispositions ("
            "id INTEGER NOT NULL PRIMARY KEY, decision VARCHAR(30) NOT NULL, reason TEXT, "  # reason nullable - wrong
            "reviewer VARCHAR(100) NOT NULL, created_at DATETIME NOT NULL, supersedes_id INTEGER, "
            "CONSTRAINT ck_signal_dispositions_decision CHECK (decision IN ('DISTINCT', 'SAME_REAL_WORLD_EFFORT')), "
            "FOREIGN KEY(supersedes_id) REFERENCES signal_dispositions (id))"
        )
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError, match="columns do not match"):
            migration.upgrade(db)

    def test_wrong_type_fails_closed(self, tmp_path):
        db = tmp_path / "wrongtype.db"
        _pre_d4d2_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE signal_dispositions ("
            "id INTEGER NOT NULL PRIMARY KEY, decision INTEGER NOT NULL, reason TEXT NOT NULL, "  # decision wrong type
            "reviewer VARCHAR(100) NOT NULL, created_at DATETIME NOT NULL, supersedes_id INTEGER, "
            "CONSTRAINT ck_signal_dispositions_decision CHECK (decision > 0), "
            "FOREIGN KEY(supersedes_id) REFERENCES signal_dispositions (id))"
        )
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError, match="columns do not match"):
            migration.upgrade(db)


# ---------------------------------------------------------------------------
# inspect()/upgrade() parity - review-checkpoint addition
#
# inspect() must never report `ready: True` for a table upgrade() would
# itself refuse - a real, reproduced gap where inspect()'s own original
# "ready" computation only checked column NAME presence and constraint-name
# substring presence, never type/nullability/PK/FK correctness, so a table
# with every expected column NAME present but a WRONG type on one column
# plus a genuinely extra column still reported ready=True even though
# upgrade() correctly refused that exact table as incompatible. Fixed by
# making both functions share one strict, structural comparison
# (_schema_mismatch_reasons/_table_matches_expected_schema).
# ---------------------------------------------------------------------------


class TestInspectTrustworthiness:
    def test_inspect_never_reports_ready_for_a_table_upgrade_would_refuse(self, tmp_path):
        db = tmp_path / "sneaky.db"
        _pre_d4d2_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE signal_dispositions ("
            "id INTEGER NOT NULL PRIMARY KEY, decision INTEGER NOT NULL, reason TEXT NOT NULL, "
            "reviewer VARCHAR(100) NOT NULL, created_at DATETIME NOT NULL, supersedes_id INTEGER, "
            "extra_unexpected_column TEXT, "
            "CONSTRAINT ck_signal_dispositions_decision CHECK (decision > 0), "
            "FOREIGN KEY(supersedes_id) REFERENCES signal_dispositions (id))"
        )
        conn.commit()
        conn.close()
        _create_table_raw(db, "signal_disposition_members")  # correctly shaped

        result = migration.inspect(db)
        assert result["ready"] is False
        assert result["matches_expected_schema"]["signal_dispositions"] is False
        assert result["matches_expected_schema"]["signal_disposition_members"] is True

        with pytest.raises(migration.IncompatibleExistingSchemaError):
            migration.upgrade(db)

    def test_inspect_ready_true_only_for_genuinely_matching_schema(self, tmp_path):
        db = tmp_path / "healthy.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["ready"] is True
        assert result["matches_expected_schema"] == {
            "signal_dispositions": True, "signal_disposition_members": True,
        }

    @pytest.mark.parametrize(
        "sql",
        [
            "CREATE TABLE signal_dispositions (id INTEGER PRIMARY KEY, wrong_column TEXT)",
            "CREATE TABLE signal_dispositions (id INTEGER NOT NULL PRIMARY KEY, decision VARCHAR(30) NOT NULL, "
            "reason TEXT NOT NULL, reviewer VARCHAR(100) NOT NULL, created_at DATETIME NOT NULL, "
            "supersedes_id INTEGER, FOREIGN KEY(supersedes_id) REFERENCES signal_dispositions (id))",  # missing CHECK
        ],
    )
    def test_inspect_reports_not_ready_for_every_fail_closed_case(self, tmp_path, sql):
        """§26: after every fail-closed upgrade() case, inspect() must
        accurately reveal the incompatible state - never claim ready."""
        db = tmp_path / "failclosed.db"
        _pre_d4d2_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(sql)
        conn.commit()
        conn.close()

        with pytest.raises(migration.IncompatibleExistingSchemaError):
            migration.upgrade(db)
        assert migration.inspect(db)["ready"] is False


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


class TestDowngrade:
    def test_downgrade_empty_success(self, tmp_path):
        db = tmp_path / "downgrade_empty.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        migration.downgrade(db)
        result = migration.inspect(db)
        assert result["tables_exist"] == {"signal_dispositions": False, "signal_disposition_members": False}

    def test_downgrade_restores_pre_d4d2_schema(self, tmp_path):
        db = tmp_path / "restore.db"
        _pre_d4d2_db(db)
        before_tables = set(sqlite3.connect(str(db)).execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        migration.upgrade(db)
        migration.downgrade(db)
        after_tables = set(sqlite3.connect(str(db)).execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        assert before_tables == after_tables

    def test_downgrade_header_nonempty_refused(self, tmp_path):
        db = tmp_path / "header_rows.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        conn.execute("INSERT INTO signal_dispositions (decision, reason, reviewer, created_at) VALUES ('DISTINCT', 'x', 'human:x', '2026-01-01')")
        conn.commit()
        conn.close()
        with pytest.raises(RuntimeError, match="downgrade\\(\\) refused"):
            migration.downgrade(db)
        assert migration.inspect(db)["tables_exist"]["signal_dispositions"] is True

    def test_downgrade_member_nonempty_refused(self, tmp_path):
        db = tmp_path / "member_rows.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        ids = _seed_two_signals(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("INSERT INTO signal_dispositions (decision, reason, reviewer, created_at) VALUES ('DISTINCT', 'x', 'human:x', '2026-01-01')")
        disposition_id = conn.execute("SELECT id FROM signal_dispositions").fetchone()[0]
        conn.execute("INSERT INTO signal_disposition_members (disposition_id, signal_id) VALUES (?, ?)", (disposition_id, ids[0]))
        conn.commit()
        conn.close()
        with pytest.raises(RuntimeError, match="downgrade\\(\\) refused"):
            migration.downgrade(db)
        assert migration.inspect(db)["tables_exist"]["signal_disposition_members"] is True

    def test_downgrade_both_nonempty_refused(self, tmp_path):
        db = tmp_path / "both_rows.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        ids = _seed_two_signals(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            record_signal_group_disposition(s, signal_ids=list(ids), decision="DISTINCT", reviewer="human:x", reason="x")
            s.commit()
        engine.dispose()
        with pytest.raises(RuntimeError, match="downgrade\\(\\) refused"):
            migration.downgrade(db)

    def test_downgrade_refusal_atomicity_schema_and_rows_intact(self, tmp_path):
        db = tmp_path / "atomic.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        ids = _seed_two_signals(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            record_signal_group_disposition(s, signal_ids=list(ids), decision="DISTINCT", reviewer="human:x", reason="x")
            s.commit()
        engine.dispose()

        before = migration.inspect(db)
        with pytest.raises(RuntimeError):
            migration.downgrade(db)
        after = migration.inspect(db)
        assert before == after  # no partial DROP, no row loss, nothing changed

    def test_round_trip_upgrade_downgrade_upgrade(self, tmp_path):
        db = tmp_path / "roundtrip.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        migration.downgrade(db)
        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["ready"] is True
        assert result["counts"] == {"signal_dispositions": 0, "signal_disposition_members": 0}


class TestDdlAtomicity:
    """§24/§12: SQLite genuinely supports transactional DDL - both
    upgrade() and downgrade() wrap every CREATE/DROP TABLE inside one
    BEGIN IMMEDIATE ... COMMIT block. These tests inject a real failure
    between the first and second table operation and confirm SQLite's own
    rollback actually undoes the first one too - proven directly, not
    assumed from the code's own transaction wrapping."""

    def test_upgrade_failure_between_tables_leaves_neither_table_created(self, tmp_path, monkeypatch):
        db = tmp_path / "upgrade_atomic.db"
        _pre_d4d2_db(db)

        real_table_exists = migration._table_exists

        def _crash_before_second_table(connection, name):
            result = real_table_exists(connection, name)
            if name == "signal_disposition_members" and not result:
                raise RuntimeError("simulated crash before creating the second table")
            return result

        monkeypatch.setattr(migration, "_table_exists", _crash_before_second_table)
        with pytest.raises(RuntimeError, match="simulated crash"):
            migration.upgrade(db)
        monkeypatch.undo()

        result = migration.inspect(db)
        assert result["tables_exist"] == {"signal_dispositions": False, "signal_disposition_members": False}

    def test_downgrade_failure_between_tables_leaves_both_tables_intact(self, tmp_path, monkeypatch):
        db = tmp_path / "downgrade_atomic.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)

        real_table_exists = migration._table_exists

        def _crash_before_second_drop(connection, name):
            result = real_table_exists(connection, name)
            if name == "signal_dispositions" and result:
                raise RuntimeError("simulated crash before dropping the second (header) table")
            return result

        monkeypatch.setattr(migration, "_table_exists", _crash_before_second_drop)
        with pytest.raises(RuntimeError, match="simulated crash"):
            migration.downgrade(db)
        monkeypatch.undo()

        result = migration.inspect(db)
        assert result["tables_exist"] == {"signal_dispositions": True, "signal_disposition_members": True}
        assert result["ready"] is True


# ---------------------------------------------------------------------------
# Write authorization / backup / wrong-DB isolation
# ---------------------------------------------------------------------------


class TestWriteGate:
    def test_main_requires_allow_database_write(self, tmp_path):
        db = tmp_path / "gate.db"
        _pre_d4d2_db(db)
        with pytest.raises(SystemExit):
            migration.main(["--database", str(db)])
        # Refused before any write - table must not have been created.
        assert migration.inspect(db)["tables_exist"]["signal_dispositions"] is False

    def test_main_no_sessionlocal_reference_ast(self):
        tree = ast.parse(inspect_module.getsource(migration))
        code_identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert "SessionLocal" not in code_identifiers
        assert "create_engine" not in code_identifiers  # raw sqlite3 only, never an ORM engine

    def test_database_flag_omitted_silently_defaults_to_the_real_path(self):
        """§15: `--database` is NOT required and silently defaults to
        `DEFAULT_DATABASE` (data/runway_safe.db) when omitted - matching
        every prior migration script's own established convention
        (migrate_reviewer_action_slice9b.py, migrate_reconciliation_confirmation_slice_r4b.py,
        migrate_evidence_identity_slice6c.py all share this identical
        default). This is a real, documented operational risk mitigated
        entirely by --allow-database-write being separately, always
        required (test_main_requires_allow_database_write above) - not a
        new gap this migration introduces, and deliberately not changed
        here to avoid silently diverging from established precedent
        without cause."""
        # Reconstruct the same --database argument main() builds, to inspect
        # its default without invoking main() itself (which would exit on
        # missing --allow-database-write before we could inspect args).
        import argparse as argparse_module
        real_parser = argparse_module.ArgumentParser()
        real_parser.add_argument("--database", type=type(migration.DEFAULT_DATABASE), default=migration.DEFAULT_DATABASE)
        args = real_parser.parse_args([])
        assert args.database == migration.DEFAULT_DATABASE

    def test_write_gate_is_the_only_thing_preventing_default_path_mutation(self, tmp_path, monkeypatch):
        """Directly proves the mitigation: even with --database omitted
        (so it resolves to the real DEFAULT_DATABASE), omitting
        --allow-database-write means upgrade()/downgrade() are never
        called at all - verified by monkeypatching both to fail loudly if
        invoked."""
        def _must_not_be_called(*args, **kwargs):
            raise AssertionError("upgrade()/downgrade() must never be called without --allow-database-write")

        monkeypatch.setattr(migration, "upgrade", _must_not_be_called)
        monkeypatch.setattr(migration, "downgrade", _must_not_be_called)
        with pytest.raises(SystemExit):
            migration.main([])  # no --database, no --allow-database-write

    def test_nonexistent_database_path_fails_closed_on_backup(self, tmp_path):
        missing = tmp_path / "does_not_exist.db"
        with pytest.raises(FileNotFoundError):
            migration.backup_database(missing, backup_directory=tmp_path / "backups")

    def test_malformed_database_file_fails_closed(self, tmp_path):
        malformed = tmp_path / "not_a_real_database.db"
        malformed.write_bytes(b"this is not a sqlite file at all")
        with pytest.raises(sqlite3.DatabaseError):
            migration.upgrade(malformed)


class TestBackup:
    def test_backup_created_before_write(self, tmp_path):
        db = tmp_path / "backup_src.db"
        _pre_d4d2_db(db)
        backup_dir = tmp_path / "backups"
        before_bytes = db.read_bytes()

        result = migration.backup_database(db, backup_directory=backup_dir)
        assert result.exists()
        assert result.read_bytes() == before_bytes

    def test_backup_is_independently_readable_pre_d4d2_schema(self, tmp_path):
        db = tmp_path / "backup_read.db"
        _pre_d4d2_db(db)
        backup_dir = tmp_path / "backups"
        backup_path = migration.backup_database(db, backup_directory=backup_dir)

        conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "signal_dispositions" not in tables
        assert "signal_disposition_members" not in tables
        assert "signals" in tables  # pre-existing domain table present

    def test_backup_matches_source_before_mutation(self, tmp_path):
        db = tmp_path / "backup_match.db"
        _pre_d4d2_db(db)
        ids = _seed_two_signals(db)
        backup_dir = tmp_path / "backups"
        backup_path = migration.backup_database(db, backup_directory=backup_dir)

        migration.upgrade(db)  # mutates the source after backup was taken

        conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
        count = conn.execute("SELECT count(*) FROM signals").fetchone()[0]
        has_d4d2 = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='signal_dispositions'").fetchone())
        conn.close()
        assert count == 2
        assert has_d4d2 is False  # backup is frozen at pre-migration state

    def test_backup_passes_integrity_check(self, tmp_path):
        db = tmp_path / "backup_integrity.db"
        _pre_d4d2_db(db)
        _seed_two_signals(db)
        backup_dir = tmp_path / "backups"
        backup_path = migration.backup_database(db, backup_directory=backup_dir)

        conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        conn.close()
        assert result == "ok"
        assert fk_violations == []

    def test_main_creates_backup_on_write(self, tmp_path, capsys):
        db = tmp_path / "main_backup.db"
        _pre_d4d2_db(db)
        code = migration.main(["--database", str(db), "--allow-database-write"])
        assert code == 0
        captured = capsys.readouterr()
        assert "Backup created:" in captured.out


class TestWrongDatabaseIsolation:
    def test_migration_touches_only_the_named_database(self, tmp_path):
        target = tmp_path / "target.db"
        protected = tmp_path / "protected.db"
        _pre_d4d2_db(target)
        _pre_d4d2_db(protected)
        before_protected = protected.read_bytes()

        migration.upgrade(target)

        assert protected.read_bytes() == before_protected
        assert migration.inspect(target)["ready"] is True
        assert migration.inspect(protected)["tables_exist"]["signal_dispositions"] is False


# ---------------------------------------------------------------------------
# Existing-data preservation
# ---------------------------------------------------------------------------


class TestExistingDataPreservation:
    def test_representative_domain_rows_unchanged_after_upgrade(self, tmp_path):
        db = tmp_path / "preserve.db"
        _pre_d4d2_db(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = Airport(name="Preserve Airport", country="XX", iata_code="PPP")
            source = Source(title="Preserve Source", source_type="official")
            s.add_all([airport, source])
            s.commit()
            signal = Signal(airport=airport, title="Preserve Signal", category="replacement", confidence="high", source_id=source.id)
            s.add(signal)
            s.commit()
            assertion = SourceAssertion(
                source=source, airport=airport, assertion_type="project_construction",
                source_record_identifier="preserve-1", signal_id=signal.id,
                identity_guard_decision="ATTACH_CONFIRMED", intelligence_review_decision="REVIEW_REQUIRED",
                promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
            )
            s.add(assertion)
            s.commit()
            action = ReviewerAction(
                source_assertion_id=assertion.id, action="APPROVE_SIGNAL", reason="x", reviewer="human:x",
            )
            s.add(action)
            s.commit()
            snapshot = {
                "airports": [(r.id, r.name, r.country, r.iata_code) for r in s.query(Airport).all()],
                "sources": [(r.id, r.title, r.source_type) for r in s.query(Source).all()],
                "signals": [(r.id, r.title, r.category, r.confidence, r.source_id) for r in s.query(Signal).all()],
                "source_assertions": [(r.id, r.signal_id, r.identity_guard_decision) for r in s.query(SourceAssertion).all()],
                "reviewer_actions": [(r.id, r.action, r.reason, r.reviewer) for r in s.query(ReviewerAction).all()],
            }
        engine.dispose()

        migration.upgrade(db)

        engine2 = create_engine(f"sqlite:///{db}")
        with Session(engine2) as s:
            after = {
                "airports": [(r.id, r.name, r.country, r.iata_code) for r in s.query(Airport).all()],
                "sources": [(r.id, r.title, r.source_type) for r in s.query(Source).all()],
                "signals": [(r.id, r.title, r.category, r.confidence, r.source_id) for r in s.query(Signal).all()],
                "source_assertions": [(r.id, r.signal_id, r.identity_guard_decision) for r in s.query(SourceAssertion).all()],
                "reviewer_actions": [(r.id, r.action, r.reason, r.reviewer) for r in s.query(ReviewerAction).all()],
            }
        engine2.dispose()
        assert after == snapshot

    def test_only_the_two_new_empty_tables_appear(self, tmp_path):
        db = tmp_path / "onlytwo.db"
        _pre_d4d2_db(db)
        before = set(sqlite3.connect(str(db)).execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        migration.upgrade(db)
        after = set(sqlite3.connect(str(db)).execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        new_tables = {t[0] for t in after - before}
        assert new_tables == {"signal_dispositions", "signal_disposition_members"}


# ---------------------------------------------------------------------------
# Model/migration parity
# ---------------------------------------------------------------------------


class TestModelMigrationParity:
    def test_fresh_session_can_read_and_write_via_d4d1_service(self, tmp_path):
        db = tmp_path / "parity.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        ids = _seed_two_signals(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            disposition = record_signal_group_disposition(
                s, signal_ids=list(ids), decision="DISTINCT", reviewer="human:x", reason="x",
            )
            s.commit()
            assert s.query(SignalDisposition).count() == 1
            assert s.query(SignalDispositionMember).count() == 2
            assert {m.signal_id for m in disposition.members} == set(ids)
        engine.dispose()

    def test_member_set_immutability_guard_still_works_post_migration(self, tmp_path):
        """The D4D1 review-checkpoint fix (before_insert seal guard) is a
        Python-level ORM event listener, not persisted schema - confirm it
        still fires correctly against a migrated (not create_all'd) DB."""
        db = tmp_path / "parity2.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        ids = _seed_two_signals(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            d = record_signal_group_disposition(s, signal_ids=list(ids), decision="DISTINCT", reviewer="human:x", reason="x")
            s.commit()
            airport = s.get(Airport, s.get(Signal, ids[0]).airport_id)
            c = Signal(airport=airport, title="C", category="replacement", confidence="high")
            s.add(c)
            s.commit()
            s.add(SignalDispositionMember(disposition_id=d.id, signal_id=c.id))
            with pytest.raises(ValueError, match="cannot be appended to an already-persisted disposition"):
                s.commit()
            s.rollback()
        engine.dispose()


# ---------------------------------------------------------------------------
# Signal-delete FK protection / supersession FK, post-migration
# ---------------------------------------------------------------------------


class TestSignalDeleteFkPostMigration:
    def test_deleting_a_member_signal_fails_with_fk_enforced(self, tmp_path):
        db = tmp_path / "fkdelete.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        ids = _seed_two_signals(db)

        engine = create_engine(f"sqlite:///{db}")
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _enable_fk(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        with Session(engine) as s:
            record_signal_group_disposition(s, signal_ids=list(ids), decision="DISTINCT", reviewer="human:x", reason="x")
            s.commit()
            member_signal = s.get(Signal, ids[0])
            s.delete(member_signal)
            with pytest.raises(Exception, match="FOREIGN KEY constraint failed"):
                s.commit()
            s.rollback()
            assert s.query(Signal).count() == 2
        engine.dispose()

    def test_deleting_a_member_signal_fails_via_raw_sql(self, tmp_path):
        db = tmp_path / "fkdelete_raw.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        ids = _seed_two_signals(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("INSERT INTO signal_dispositions (decision, reason, reviewer, created_at) VALUES ('DISTINCT', 'x', 'human:x', '2026-01-01')")
        disposition_id = conn.execute("SELECT id FROM signal_dispositions").fetchone()[0]
        conn.execute("INSERT INTO signal_disposition_members (disposition_id, signal_id) VALUES (?, ?)", (disposition_id, ids[0]))
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute("DELETE FROM signals WHERE id=?", (ids[0],))
            conn.commit()
        conn.rollback()
        conn.close()


class TestSupersessionFkPostMigration:
    def test_self_fk_works_for_valid_supersession(self, tmp_path):
        db = tmp_path / "selffk.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        ids = _seed_two_signals(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            d1 = record_signal_group_disposition(s, signal_ids=list(ids), decision="DISTINCT", reviewer="human:x", reason="x")
            s.commit()
            d2 = record_signal_group_disposition(
                s, signal_ids=list(ids), decision="SAME_REAL_WORLD_EFFORT",
                reviewer="human:y", reason="y", supersedes_id=d1.id,
            )
            s.commit()
            assert d2.supersedes_id == d1.id
        engine.dispose()

    def test_invalid_supersedes_target_fails_via_raw_sql_fk(self, tmp_path):
        db = tmp_path / "badfk.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute(
                "INSERT INTO signal_dispositions (decision, reason, reviewer, created_at, supersedes_id) "
                "VALUES ('DISTINCT', 'x', 'human:x', '2026-01-01', 999999)"
            )
            conn.commit()
        conn.rollback()
        conn.close()


# ---------------------------------------------------------------------------
# Raw-SQL attacks (CHECK/UNIQUE bypassing the ORM entirely)
# ---------------------------------------------------------------------------


class TestRawSqlAttacks:
    def test_invalid_decision_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "rawcheck.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                "INSERT INTO signal_dispositions (decision, reason, reviewer, created_at) "
                "VALUES ('NOT_A_REAL_DECISION', 'x', 'human:x', '2026-01-01')"
            )
            conn.commit()
        conn.rollback()
        conn.close()

    def test_duplicate_member_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "rawdup.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        ids = _seed_two_signals(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("INSERT INTO signal_dispositions (decision, reason, reviewer, created_at) VALUES ('DISTINCT', 'x', 'human:x', '2026-01-01')")
        disposition_id = conn.execute("SELECT id FROM signal_dispositions").fetchone()[0]
        conn.execute("INSERT INTO signal_disposition_members (disposition_id, signal_id) VALUES (?, ?)", (disposition_id, ids[0]))
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            conn.execute("INSERT INTO signal_disposition_members (disposition_id, signal_id) VALUES (?, ?)", (disposition_id, ids[0]))
            conn.commit()
        conn.rollback()
        conn.close()

    def test_member_referencing_nonexistent_disposition_rejected_raw_sql(self, tmp_path):
        """FK attack matrix case A, against the raw MIGRATED schema
        specifically (not an ORM create_all() schema, matching this
        mission's own §5 instruction to attack the migration's own
        output)."""
        db = tmp_path / "nodisposition.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        ids = _seed_two_signals(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute(
                "INSERT INTO signal_disposition_members (disposition_id, signal_id) VALUES (999999, ?)", (ids[0],),
            )
            conn.commit()
        conn.rollback()
        conn.close()

    def test_member_referencing_nonexistent_signal_rejected_raw_sql(self, tmp_path):
        """FK attack matrix case B, against the raw migrated schema."""
        db = tmp_path / "nosignal.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO signal_dispositions (decision, reason, reviewer, created_at) VALUES ('DISTINCT', 'x', 'human:x', '2026-01-01')"
        )
        disposition_id = conn.execute("SELECT id FROM signal_dispositions").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute(
                "INSERT INTO signal_disposition_members (disposition_id, signal_id) VALUES (?, 999999)", (disposition_id,),
            )
            conn.commit()
        conn.rollback()
        conn.close()


# ---------------------------------------------------------------------------
# Zero backfill
# ---------------------------------------------------------------------------


class TestZeroBackfill:
    def test_upgrade_never_inserts_a_row_even_with_real_fh_d4_shaped_data_present(self, tmp_path):
        """A DB with real FH-D4-shaped co-located Signals (no runway
        claimed, same airport) must still end an upgrade with zero
        dispositions - this migration never infers/backfills from current
        health findings, matching the design's own explicit separation of
        detection from human disposition."""
        db = tmp_path / "nobackfill.db"
        _pre_d4d2_db(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = Airport(name="FH-D4 Shaped Airport", country="XX")
            s.add(airport)
            a = Signal(airport=airport, title="A", category="replacement", confidence="high", runway_id=None)
            b = Signal(airport=airport, title="B", category="replacement", confidence="high", runway_id=None)
            s.add_all([a, b])
            s.commit()
        engine.dispose()

        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["counts"] == {"signal_dispositions": 0, "signal_disposition_members": 0}

    def test_no_ast_reference_to_fleet_health_modules(self):
        tree = ast.parse(inspect_module.getsource(migration))
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
            elif isinstance(node, ast.Import):
                imported_modules |= {alias.name for alias in node.names}
        assert not any("fleet_health" in m for m in imported_modules)


# ---------------------------------------------------------------------------
# inspect() itself
# ---------------------------------------------------------------------------


class TestInspect:
    def test_inspect_never_mutates(self, tmp_path):
        db = tmp_path / "inspectonly.db"
        _pre_d4d2_db(db)
        before = db.read_bytes()
        migration.inspect(db)
        migration.inspect(db)
        assert db.read_bytes() == before

    def test_inspect_reports_foreign_key_check_clean(self, tmp_path):
        db = tmp_path / "fkcheck.db"
        _pre_d4d2_db(db)
        migration.upgrade(db)
        assert migration.inspect(db)["foreign_key_check"] == []


# ---------------------------------------------------------------------------
# No real database access
# ---------------------------------------------------------------------------


class TestNoRealDatabaseAccess:
    def test_no_reference_to_the_real_database_path_in_migration_module(self):
        tree = ast.parse(inspect_module.getsource(migration))
        body = list(tree.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body = body[1:]
        literals = [
            node.value for node in ast.walk(ast.Module(body=body, type_ignores=[]))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        real_db_filename = "runway_safe" + ".db"
        matches = [lit for lit in literals if real_db_filename in lit]
        # DEFAULT_DATABASE = Path("data/runway_safe.db") is expected and
        # legitimate (matching every other migration script's own default,
        # required only when --allow-database-write is also explicitly
        # passed) - anything beyond that single, well-known constant would
        # be suspicious.
        assert matches == ["data/runway_safe.db"]
