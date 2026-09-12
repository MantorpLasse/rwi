"""Tests for scripts/migrate_signal_amendment_action.py (RWI HQ "Signal
Amendment Audit Trail — Append-Only Governance" mission).

Every test uses an isolated temp-file SQLite database (tmp_path). Nothing
in this file ever opens data/runway_safe.db - production migration
application is explicitly deferred to a separate, later, manual step (see
this mission's own Part 13/15). Grep/AST-verified (see
TestNoRealDatabaseAccess), mirroring
tests/test_signal_disposition_migration.py's own established, proven
template for this exact class of two-table migration.
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
from app.models.signal_amendment import SignalAmendmentAction, SignalAmendmentFieldChange
from app.services.signal_amendment import FieldChange
from app.services.signal_amendment_history import record_signal_amendment
import scripts.migrate_signal_amendment_action as migration

NEW_TABLES = ("signal_amendment_actions", "signal_amendment_field_changes")


def _pre_migration_db(path):
    """A full pre-migration schema (every table except the two this
    migration creates) - the realistic "not yet migrated" starting state."""
    engine = create_engine(f"sqlite:///{path}")
    pre_meta = MetaData()
    for name, table in Base.metadata.tables.items():
        if name not in NEW_TABLES:
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


def _seed_signal(db_path):
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        airport = Airport(name="Migration Test Airport", country="XX")
        s.add(airport)
        signal = Signal(airport=airport, title="A", category="replacement", confidence="high", target_year=2028)
        s.add(signal)
        s.commit()
        signal_id = signal.id
    engine.dispose()
    return signal_id


# ---------------------------------------------------------------------------
# Clean upgrade
# ---------------------------------------------------------------------------


class TestCleanUpgrade:
    def test_upgrade_creates_both_tables(self, tmp_path):
        db = tmp_path / "clean.db"
        _pre_migration_db(db)
        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["tables_exist"] == {"signal_amendment_actions": True, "signal_amendment_field_changes": True}
        assert result["ready"] is True

    def test_exact_action_columns(self, tmp_path):
        db = tmp_path / "cols.db"
        _pre_migration_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        cols = {row[1]: (row[2], bool(row[3]), bool(row[5])) for row in conn.execute("PRAGMA table_info(signal_amendment_actions)")}
        conn.close()
        assert cols == {
            "id": ("INTEGER", True, True),
            "signal_id": ("INTEGER", True, False),
            "reason": ("TEXT", True, False),
            "reviewer": ("VARCHAR(100)", True, False),
            "source_assertion_id": ("INTEGER", False, False),
            "reviewer_action_id": ("INTEGER", False, False),
            "created_at": ("DATETIME", True, False),
        }

    def test_exact_field_change_columns(self, tmp_path):
        db = tmp_path / "cols2.db"
        _pre_migration_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        cols = {row[1]: (row[2], bool(row[3]), bool(row[5])) for row in conn.execute("PRAGMA table_info(signal_amendment_field_changes)")}
        conn.close()
        assert cols == {
            "id": ("INTEGER", True, True),
            "action_id": ("INTEGER", True, False),
            "field_name": ("VARCHAR(60)", True, False),
            "old_value": ("TEXT", False, False),
            "new_value": ("TEXT", False, False),
        }

    def test_exact_foreign_keys_via_pragma(self, tmp_path):
        db = tmp_path / "fks.db"
        _pre_migration_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        action_fks = {(row[3], row[2], row[4]) for row in conn.execute("PRAGMA foreign_key_list(signal_amendment_actions)")}
        field_change_fks = {(row[3], row[2], row[4]) for row in conn.execute("PRAGMA foreign_key_list(signal_amendment_field_changes)")}
        conn.close()
        assert action_fks == {
            ("signal_id", "signals", "id"),
            ("source_assertion_id", "source_assertions", "id"),
            ("reviewer_action_id", "reviewer_actions", "id"),
        }
        assert field_change_fks == {("action_id", "signal_amendment_actions", "id")}

    def test_no_on_delete_cascade(self, tmp_path):
        db = tmp_path / "nocascade.db"
        _pre_migration_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        rows = conn.execute("PRAGMA foreign_key_list(signal_amendment_field_changes)").fetchall()
        conn.close()
        for row in rows:
            assert row[5] == "NO ACTION"
            assert row[6] == "NO ACTION"

    def test_unique_field_name_per_action_constraint_via_raw_sql(self, tmp_path):
        db = tmp_path / "unique.db"
        _pre_migration_db(db)
        migration.upgrade(db)
        signal_id = _seed_signal(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO signal_amendment_actions (signal_id, reason, reviewer, created_at) VALUES (?, 'x', 'human:x', '2026-01-01')",
            (signal_id,),
        )
        action_id = conn.execute("SELECT id FROM signal_amendment_actions").fetchone()[0]
        conn.execute(
            "INSERT INTO signal_amendment_field_changes (action_id, field_name, old_value, new_value) VALUES (?, 'target_year', '2028', '2026')",
            (action_id,),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            conn.execute(
                "INSERT INTO signal_amendment_field_changes (action_id, field_name, old_value, new_value) VALUES (?, 'target_year', '2028', '2027')",
                (action_id,),
            )
        conn.rollback()
        conn.close()

    def test_no_check_constraint_on_field_name_by_design(self, tmp_path):
        """Deliberate: unlike ReviewerAction.action/SignalDisposition.decision,
        field_name is NOT a DB-level closed vocabulary - see the model's
        own docstring for why. Any string is accepted at the schema level;
        enforcement of ALLOWED_AMENDMENT_FIELDS happens in
        app.services.signal_amendment, not here."""
        db = tmp_path / "nocheck.db"
        _pre_migration_db(db)
        migration.upgrade(db)
        signal_id = _seed_signal(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO signal_amendment_actions (signal_id, reason, reviewer, created_at) VALUES (?, 'x', 'human:x', '2026-01-01')",
            (signal_id,),
        )
        action_id = conn.execute("SELECT id FROM signal_amendment_actions").fetchone()[0]
        conn.execute(
            "INSERT INTO signal_amendment_field_changes (action_id, field_name, old_value, new_value) VALUES (?, 'anything_at_all', NULL, NULL)",
            (action_id,),
        )
        conn.commit()
        assert conn.execute("SELECT count(*) FROM signal_amendment_field_changes").fetchone()[0] == 1
        conn.close()

    def test_row_counts_zero_after_upgrade(self, tmp_path):
        db = tmp_path / "zero.db"
        _pre_migration_db(db)
        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["counts"] == {"signal_amendment_actions": 0, "signal_amendment_field_changes": 0}

    def test_no_trigger_objects_created_by_migration(self, tmp_path):
        db = tmp_path / "notrigger.db"
        _pre_migration_db(db)
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
        _pre_migration_db(db)
        migration.upgrade(db)
        before = migration.inspect(db)
        migration.upgrade(db)  # must not raise
        after = migration.inspect(db)
        assert before == after

    def test_second_upgrade_preserves_rows(self, tmp_path):
        db = tmp_path / "idem2.db"
        _pre_migration_db(db)
        migration.upgrade(db)
        signal_id = _seed_signal(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            record_signal_amendment(
                s, signal_id=signal_id, changes=(FieldChange(field="target_year", old_value=2028, new_value=2026),),
                reason="x", reviewer="human:x",
            )
            s.commit()
        engine.dispose()

        migration.upgrade(db)  # idempotent re-run after real data exists
        result = migration.inspect(db)
        assert result["counts"] == {"signal_amendment_actions": 1, "signal_amendment_field_changes": 1}


# ---------------------------------------------------------------------------
# Partial-schema / incompatible-schema safety
# ---------------------------------------------------------------------------


class TestPartialAndIncompatibleSchema:
    def test_action_exists_correctly_field_change_absent_safe_completion(self, tmp_path):
        db = tmp_path / "partial1.db"
        _pre_migration_db(db)
        _create_table_raw(db, "signal_amendment_actions")
        migration.upgrade(db)
        assert migration.inspect(db)["ready"] is True

    def test_field_change_exists_correctly_action_absent_safe_completion(self, tmp_path):
        db = tmp_path / "partial2.db"
        _pre_migration_db(db)
        # signal_amendment_field_changes FKs to signal_amendment_actions,
        # so create the referenced table first at the raw-SQLite level
        # (foreign_keys is OFF for this raw setup step) purely to allow the
        # child table's own CREATE TABLE to succeed, then drop the parent
        # again so upgrade() sees the real partial state under test.
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.close()
        _create_table_raw(db, "signal_amendment_actions")
        _create_table_raw(db, "signal_amendment_field_changes")
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP TABLE signal_amendment_actions")
        conn.commit()
        conn.close()
        migration.upgrade(db)
        assert migration.inspect(db)["ready"] is True

    def test_wrong_columns_fails_closed(self, tmp_path):
        db = tmp_path / "wrongcols.db"
        _pre_migration_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE signal_amendment_actions (id INTEGER PRIMARY KEY, wrong_column TEXT)")
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError):
            migration.upgrade(db)
        # Untouched - the other table must not have been created either.
        assert migration.inspect(db)["tables_exist"]["signal_amendment_field_changes"] is False

    def test_missing_named_constraint_fails_closed(self, tmp_path):
        db = tmp_path / "missingconstraint.db"
        _pre_migration_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE signal_amendment_field_changes ("
            "id INTEGER PRIMARY KEY, action_id INTEGER NOT NULL, field_name VARCHAR(60) NOT NULL, "
            "old_value TEXT, new_value TEXT)"
        )  # missing the UNIQUE(action_id, field_name) constraint
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError):
            migration.upgrade(db)

    def test_misleading_same_name_incompatible_table_never_dropped_or_rebuilt(self, tmp_path):
        db = tmp_path / "misleading.db"
        _pre_migration_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE signal_amendment_actions (id INTEGER PRIMARY KEY, totally_different TEXT)")
        conn.execute("INSERT INTO signal_amendment_actions (totally_different) VALUES ('do not touch me')")
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError):
            migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        row = conn.execute("SELECT totally_different FROM signal_amendment_actions").fetchone()
        conn.close()
        assert row == ("do not touch me",)


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


class TestDowngrade:
    def test_downgrade_empty_success(self, tmp_path):
        db = tmp_path / "downgrade_empty.db"
        _pre_migration_db(db)
        migration.upgrade(db)
        migration.downgrade(db)
        result = migration.inspect(db)
        assert result["tables_exist"] == {"signal_amendment_actions": False, "signal_amendment_field_changes": False}

    def test_downgrade_nonempty_refused(self, tmp_path):
        db = tmp_path / "downgrade_nonempty.db"
        _pre_migration_db(db)
        migration.upgrade(db)
        signal_id = _seed_signal(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            record_signal_amendment(
                s, signal_id=signal_id, changes=(FieldChange(field="target_year", old_value=2028, new_value=2026),),
                reason="x", reviewer="human:x",
            )
            s.commit()
        engine.dispose()

        with pytest.raises(RuntimeError, match="refused"):
            migration.downgrade(db)
        assert migration.inspect(db)["tables_exist"] == {"signal_amendment_actions": True, "signal_amendment_field_changes": True}
        assert migration.inspect(db)["counts"] == {"signal_amendment_actions": 1, "signal_amendment_field_changes": 1}

    def test_round_trip_upgrade_downgrade_upgrade(self, tmp_path):
        db = tmp_path / "roundtrip.db"
        _pre_migration_db(db)
        migration.upgrade(db)
        migration.downgrade(db)
        migration.upgrade(db)
        assert migration.inspect(db)["ready"] is True


# ---------------------------------------------------------------------------
# Write gate / backup
# ---------------------------------------------------------------------------


class TestWriteGate:
    def test_main_requires_allow_database_write(self, tmp_path):
        db = tmp_path / "gate.db"
        _pre_migration_db(db)
        with pytest.raises(SystemExit):
            migration.main(["--database", str(db)])
        assert migration.inspect(db)["tables_exist"]["signal_amendment_actions"] is False

    def test_write_gate_is_the_only_thing_preventing_default_path_mutation(self, tmp_path, monkeypatch):
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


class TestBackup:
    def test_backup_created_before_write(self, tmp_path):
        db = tmp_path / "backup_src.db"
        _pre_migration_db(db)
        backup_dir = tmp_path / "backups"
        before_bytes = db.read_bytes()

        result = migration.backup_database(db, backup_directory=backup_dir)
        assert result.exists()
        assert result.read_bytes() == before_bytes

    def test_backup_is_independently_readable_pre_migration_schema(self, tmp_path):
        db = tmp_path / "backup_read.db"
        _pre_migration_db(db)
        backup_dir = tmp_path / "backups"
        backup_path = migration.backup_database(db, backup_directory=backup_dir)

        conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "signal_amendment_actions" not in tables
        assert "signal_amendment_field_changes" not in tables
        assert "signals" in tables  # pre-existing domain table present

    def test_main_creates_backup_on_write(self, tmp_path, capsys):
        db = tmp_path / "main_backup.db"
        _pre_migration_db(db)
        code = migration.main(["--database", str(db), "--allow-database-write"])
        assert code == 0
        captured = capsys.readouterr()
        assert "Backup created:" in captured.out


# ---------------------------------------------------------------------------
# Existing-data preservation
# ---------------------------------------------------------------------------


class TestExistingDataPreservation:
    def test_representative_domain_rows_unchanged_after_upgrade(self, tmp_path):
        db = tmp_path / "preserve.db"
        _pre_migration_db(db)
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
            action = ReviewerAction(source_assertion_id=assertion.id, action="APPROVE_SIGNAL", reason="x", reviewer="human:x")
            s.add(action)
            s.commit()
            snapshot = {
                "airports": [(r.id, r.name, r.country, r.iata_code) for r in s.query(Airport).all()],
                "sources": [(r.id, r.title, r.source_type) for r in s.query(Source).all()],
                "signals": [(r.id, r.title, r.category, r.confidence, r.source_id, r.published) for r in s.query(Signal).all()],
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
                "signals": [(r.id, r.title, r.category, r.confidence, r.source_id, r.published) for r in s.query(Signal).all()],
                "source_assertions": [(r.id, r.signal_id, r.identity_guard_decision) for r in s.query(SourceAssertion).all()],
                "reviewer_actions": [(r.id, r.action, r.reason, r.reviewer) for r in s.query(ReviewerAction).all()],
            }
        engine2.dispose()
        assert after == snapshot

    def test_only_the_two_new_tables_appear(self, tmp_path):
        db = tmp_path / "onlytwo.db"
        _pre_migration_db(db)
        before = set(sqlite3.connect(str(db)).execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        migration.upgrade(db)
        after = set(sqlite3.connect(str(db)).execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        new_tables = {t[0] for t in after - before}
        assert new_tables == {"signal_amendment_actions", "signal_amendment_field_changes"}


# ---------------------------------------------------------------------------
# Zero backfill
# ---------------------------------------------------------------------------


class TestZeroBackfill:
    def test_upgrade_never_inserts_a_row_even_with_legacy_correction_shaped_data_present(self, tmp_path):
        """A DB with a real legacy-shaped Signal that has already been
        corrected by a one-off script (source_notes carries an "Amended
        by..." style line, no SourceAssertion) must still end an upgrade
        with zero amendment rows - this migration never infers/backfills
        history from source_notes text or anything else (mission's own
        explicit "NO BACKFILL" instruction; design doc §19)."""
        db = tmp_path / "nobackfill.db"
        _pre_migration_db(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = Airport(name="Legacy Airport", country="XX")
            s.add(airport)
            signal = Signal(
                airport=airport, title="Legacy Signal", category="replacement", confidence="high",
                source_notes="[2026-01-01] Amended by human:x: legacy correction (target_year: 2028 -> 2026)",
            )
            s.add(signal)
            s.commit()
        engine.dispose()

        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["counts"] == {"signal_amendment_actions": 0, "signal_amendment_field_changes": 0}

    def test_no_ast_reference_to_bgm_phl_fty_lex_ase_correction_scripts(self):
        """The migration module's own docstring legitimately EXPLAINS why
        no backfill happens by naming the five historical corrections
        (design rationale, matching this mission's own "narrowly necessary
        documentation" allowance) - what must never appear is an actual
        dependency on one of those one-off scripts themselves (an import,
        or a literal reference to one of their filenames)."""
        tree = ast.parse(inspect_module.getsource(migration))
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
            elif isinstance(node, ast.Import):
                imported_modules |= {alias.name for alias in node.names}
        assert not any("scripts.correct_" in m or "scripts.confirm_" in m or "scripts.update_" in m for m in imported_modules)

        literals = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        forbidden_script_filenames = (
            "correct_bgm_signal6_target_year.py", "confirm_phl_emas_completion.py",
            "update_fty_emas_details.py", "update_lex_emas_details.py", "update_ase_runway_relocation_note.py",
        )
        for forbidden in forbidden_script_filenames:
            assert not any(forbidden in lit for lit in literals)


# ---------------------------------------------------------------------------
# inspect() itself
# ---------------------------------------------------------------------------


class TestInspect:
    def test_inspect_never_mutates(self, tmp_path):
        db = tmp_path / "inspectonly.db"
        _pre_migration_db(db)
        before = db.read_bytes()
        migration.inspect(db)
        migration.inspect(db)
        assert db.read_bytes() == before

    def test_inspect_reports_foreign_key_check_clean(self, tmp_path):
        db = tmp_path / "fkcheck.db"
        _pre_migration_db(db)
        migration.upgrade(db)
        assert migration.inspect(db)["foreign_key_check"] == []


# ---------------------------------------------------------------------------
# No real database access
# ---------------------------------------------------------------------------


class TestNoRealDatabaseAccess:
    def test_no_reference_to_the_real_database_path_beyond_the_default_constant(self):
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
        assert matches == ["data/runway_safe.db"]

    def test_no_sessionlocal_or_create_engine_reference_ast(self):
        tree = ast.parse(inspect_module.getsource(migration))
        code_identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert "SessionLocal" not in code_identifiers
        assert "create_engine" not in code_identifiers  # raw sqlite3 only, never an ORM engine
