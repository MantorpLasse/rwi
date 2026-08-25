"""Tests for scripts/migrate_unknown_airport_candidate_relevance_reviews_erg3.py
(docs/architecture/rwi-erg3-human-relevance-review-recording-report.md).

Every test uses an isolated temp-file SQLite database (tmp_path). Nothing
in this file ever opens data/runway_safe.db - the real migration is
explicitly deferred to a separate, later, explicitly-authorized operational
step. Modeled directly on
tests/test_unknown_airport_candidate_relevance_assessment_migration.py
(ERG2), this repository's own strongest, most recent precedent for a
single-table additive migration test suite.
"""
from __future__ import annotations

import ast
import inspect as inspect_module
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateIndex, CreateTable

import app.models  # noqa: F401
from app.database import Base
from app.models import Source, SourceAssertion
from app.models.unknown_airport_candidate import UnknownAirportCandidate
from app.models.unknown_airport_candidate_relevance_assessment import UnknownAirportCandidateRelevanceAssessment
from app.models.unknown_airport_candidate_relevance_review import UnknownAirportCandidateRelevanceReview
from app.services.emas_relevance_evaluation import EmasEvidenceObservation, EvidenceClass
from app.services.unknown_airport_candidate_persistence import find_or_create_unknown_airport_candidate
from app.services.unknown_airport_candidate_relevance_persistence import (
    persist_unknown_airport_candidate_relevance_assessment,
)
from app.services.unknown_airport_candidate_relevance_review_persistence import (
    record_unknown_airport_candidate_relevance_review,
)
import scripts.migrate_unknown_airport_candidate_relevance_reviews_erg3 as migration

TABLE_NAME = "unknown_airport_candidate_relevance_reviews"


def _pre_erg3_db(path):
    """Full current schema (including the already-committed ERG2 tables)
    minus the ERG3 review table itself - the realistic "not yet migrated"
    starting state."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")
    conn.commit()
    conn.close()


def _create_table_raw(db_path, table_name):
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    table = Base.metadata.tables[table_name]
    conn.execute(str(CreateTable(table).compile(dialect=sqlite_dialect.dialect())))
    for index in table.indexes:
        conn.execute(str(CreateIndex(index).compile(dialect=sqlite_dialect.dialect())))
    conn.commit()
    conn.close()


def _seed_candidate_with_assessment(db_path):
    """Seeds one UnknownAirportCandidate, one linked SourceAssertion, and
    one automatic UnknownAirportCandidateRelevanceAssessment - the minimum
    real starting state for exercising the ERG3 service against a
    genuinely migrated (not create_all'd) database."""
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        candidate = find_or_create_unknown_airport_candidate(s, raw_name="Foo Regional Airport", raw_country="XX").candidate
        s.commit()
        source = Source(title="Test Source", source_type="official")
        s.add(source)
        s.commit()
        assertion = SourceAssertion(
            source_id=source.id, assertion_type="project_construction",
            source_record_identifier="erg3-migration-test-1",
            unknown_airport_candidate_id=candidate.id,
        )
        s.add(assertion)
        s.commit()
        result = persist_unknown_airport_candidate_relevance_assessment(
            s, candidate,
            observations=(EmasEvidenceObservation(EvidenceClass.G_GENERIC_RUNWAY_WORK, basis="x"),),
            source_assertion_ids=(assertion.id,),
        )
        s.commit()
        candidate_id, assessment_id = candidate.id, result.assessment.id
    engine.dispose()
    return candidate_id, assessment_id


# ---------------------------------------------------------------------------
# Clean upgrade / exact schema parity
# ---------------------------------------------------------------------------


class TestCleanUpgrade:
    def test_upgrade_creates_table(self, tmp_path):
        db = tmp_path / "clean.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["table_exists"] is True
        assert result["ready"] is True

    def test_exact_columns(self, tmp_path):
        db = tmp_path / "cols.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        cols = {row[1]: (row[2], bool(row[3]), bool(row[5])) for row in conn.execute(f"PRAGMA table_info({TABLE_NAME})")}
        conn.close()
        assert cols == {
            "id": ("INTEGER", True, True),
            "candidate_id": ("INTEGER", True, False),
            "basis_assessment_id": ("INTEGER", True, False),
            "action": ("VARCHAR(30)", True, False),
            "reviewer": ("VARCHAR(100)", True, False),
            "reason": ("TEXT", True, False),
            "created_at": ("DATETIME", True, False),
            "supersedes_review_id": ("INTEGER", False, False),
        }

    def test_exact_foreign_keys_via_pragma(self, tmp_path):
        db = tmp_path / "fks.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        fks = {(row[3], row[2], row[4]) for row in conn.execute(f"PRAGMA foreign_key_list({TABLE_NAME})")}
        conn.close()
        assert fks == {
            ("candidate_id", "unknown_airport_candidates", "id"),
            ("basis_assessment_id", "unknown_airport_candidate_relevance_assessments", "id"),
            ("supersedes_review_id", TABLE_NAME, "id"),
        }

    def test_no_on_delete_cascade(self, tmp_path):
        db = tmp_path / "nocascade.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        rows = conn.execute(f"PRAGMA foreign_key_list({TABLE_NAME})").fetchall()
        conn.close()
        for row in rows:
            assert row[5] == "NO ACTION"
            assert row[6] == "NO ACTION"

    def test_row_count_zero_after_upgrade(self, tmp_path):
        db = tmp_path / "zero.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        assert migration.inspect(db)["count"] == 0

    def test_no_trigger_objects_created_by_migration(self, tmp_path):
        db = tmp_path / "notrigger.db"
        _pre_erg3_db(db)
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
        _pre_erg3_db(db)
        migration.upgrade(db)
        before = migration.inspect(db)
        migration.upgrade(db)
        after = migration.inspect(db)
        assert before == after

    def test_second_upgrade_preserves_rows(self, tmp_path):
        db = tmp_path / "idem2.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        candidate_id, assessment_id = _seed_candidate_with_assessment(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = s.get(UnknownAirportCandidate, candidate_id)
            record_unknown_airport_candidate_relevance_review(
                s, candidate, basis_assessment_id=assessment_id,
                action="DEFER_RELEVANCE_REVIEW", reviewer="human:x", reason="x",
            )
            s.commit()
        engine.dispose()

        migration.upgrade(db)
        assert migration.inspect(db)["count"] == 1


# ---------------------------------------------------------------------------
# Partial-schema / incompatible-schema safety
# ---------------------------------------------------------------------------


class TestIncompatibleSchema:
    def test_wrong_columns_fails_closed(self, tmp_path):
        db = tmp_path / "wrongcols.db"
        _pre_erg3_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(f"CREATE TABLE {TABLE_NAME} (id INTEGER PRIMARY KEY, wrong_column TEXT)")
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError, match="do not match the expected ERG3 schema"):
            migration.upgrade(db)

    def test_missing_named_check_constraint_fails_closed(self, tmp_path):
        db = tmp_path / "noconstraint.db"
        _pre_erg3_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            f"CREATE TABLE {TABLE_NAME} ("
            "id INTEGER NOT NULL PRIMARY KEY, candidate_id INTEGER NOT NULL, "
            "basis_assessment_id INTEGER NOT NULL, action VARCHAR(30) NOT NULL, "
            "reviewer VARCHAR(100) NOT NULL, reason TEXT NOT NULL, created_at DATETIME NOT NULL, "
            "supersedes_review_id INTEGER, "
            "FOREIGN KEY(candidate_id) REFERENCES unknown_airport_candidates (id), "
            "FOREIGN KEY(basis_assessment_id) REFERENCES unknown_airport_candidate_relevance_assessments (id), "
            f"FOREIGN KEY(supersedes_review_id) REFERENCES {TABLE_NAME} (id))"
        )
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError, match="missing expected named constraint"):
            migration.upgrade(db)

    def test_missing_expected_index_fails_closed(self, tmp_path):
        db = tmp_path / "missingindex.db"
        _pre_erg3_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            f"CREATE TABLE {TABLE_NAME} ("
            "id INTEGER NOT NULL PRIMARY KEY, candidate_id INTEGER NOT NULL, "
            "basis_assessment_id INTEGER NOT NULL, action VARCHAR(30) NOT NULL, "
            "reviewer VARCHAR(100) NOT NULL, reason TEXT NOT NULL, created_at DATETIME NOT NULL, "
            "supersedes_review_id INTEGER, "
            "CONSTRAINT ck_unknown_airport_candidate_relevance_reviews_action CHECK "
            "(action IN ('CONFIRM_EMAS_RELEVANT','MARK_NOT_EMAS_RELEVANT','DEFER_RELEVANCE_REVIEW')), "
            "FOREIGN KEY(candidate_id) REFERENCES unknown_airport_candidates (id), "
            "FOREIGN KEY(basis_assessment_id) REFERENCES unknown_airport_candidate_relevance_assessments (id), "
            f"FOREIGN KEY(supersedes_review_id) REFERENCES {TABLE_NAME} (id))"
            # Deliberately no CREATE INDEX statements.
        )
        conn.commit()
        conn.close()
        result = migration.inspect(db)
        assert result["matches_expected_schema"] is False
        with pytest.raises(migration.IncompatibleExistingSchemaError, match="missing expected index"):
            migration.upgrade(db)


# ---------------------------------------------------------------------------
# inspect()/upgrade() parity
# ---------------------------------------------------------------------------


class TestInspectTrustworthiness:
    def test_inspect_ready_true_only_for_genuinely_matching_schema(self, tmp_path):
        db = tmp_path / "healthy.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["ready"] is True
        assert result["matches_expected_schema"] is True


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


class TestDowngrade:
    def test_downgrade_empty_success(self, tmp_path):
        db = tmp_path / "downgrade_empty.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        migration.downgrade(db)
        assert migration.inspect(db)["table_exists"] is False

    def test_downgrade_review_rows_refused(self, tmp_path):
        db = tmp_path / "review_rows.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        candidate_id, assessment_id = _seed_candidate_with_assessment(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = s.get(UnknownAirportCandidate, candidate_id)
            record_unknown_airport_candidate_relevance_review(
                s, candidate, basis_assessment_id=assessment_id,
                action="CONFIRM_EMAS_RELEVANT", reviewer="human:x", reason="x",
            )
            s.commit()
        engine.dispose()
        with pytest.raises(RuntimeError, match=r"downgrade\(\) refused"):
            migration.downgrade(db)
        assert migration.inspect(db)["table_exists"] is True

    def test_downgrade_refusal_atomicity(self, tmp_path):
        db = tmp_path / "atomic.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        candidate_id, assessment_id = _seed_candidate_with_assessment(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = s.get(UnknownAirportCandidate, candidate_id)
            record_unknown_airport_candidate_relevance_review(
                s, candidate, basis_assessment_id=assessment_id,
                action="DEFER_RELEVANCE_REVIEW", reviewer="human:x", reason="x",
            )
            s.commit()
        engine.dispose()

        before = migration.inspect(db)
        with pytest.raises(RuntimeError):
            migration.downgrade(db)
        after = migration.inspect(db)
        assert before == after

    def test_round_trip_upgrade_downgrade_upgrade(self, tmp_path):
        db = tmp_path / "roundtrip.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        migration.downgrade(db)
        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["ready"] is True
        assert result["count"] == 0


class TestDdlAtomicity:
    def test_upgrade_failure_leaves_no_partial_table(self, tmp_path, monkeypatch):
        db = tmp_path / "upgrade_atomic.db"
        _pre_erg3_db(db)

        def _crash_on_check(*args, **kwargs):
            raise RuntimeError("simulated crash during schema check")

        monkeypatch.setattr(migration, "_table_exists", _crash_on_check)
        with pytest.raises(RuntimeError, match="simulated crash"):
            migration.upgrade(db)
        monkeypatch.undo()

        assert migration.inspect(db)["table_exists"] is False


# ---------------------------------------------------------------------------
# Write authorization / backup
# ---------------------------------------------------------------------------


class TestWriteGate:
    def test_main_requires_allow_database_write(self, tmp_path):
        db = tmp_path / "gate.db"
        _pre_erg3_db(db)
        with pytest.raises(SystemExit):
            migration.main(["--database", str(db)])
        assert migration.inspect(db)["table_exists"] is False

    def test_main_no_sessionlocal_reference_ast(self):
        tree = ast.parse(inspect_module.getsource(migration))
        code_identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert "SessionLocal" not in code_identifiers
        assert "create_engine" not in code_identifiers

    def test_malformed_database_file_fails_closed(self, tmp_path):
        malformed = tmp_path / "not_a_real_database.db"
        malformed.write_bytes(b"this is not a sqlite file at all")
        with pytest.raises(sqlite3.DatabaseError):
            migration.upgrade(malformed)


class TestBackup:
    def test_backup_created_before_write(self, tmp_path):
        db = tmp_path / "backup_src.db"
        _pre_erg3_db(db)
        backup_dir = tmp_path / "backups"
        before_bytes = db.read_bytes()
        result = migration.backup_database(db, backup_directory=backup_dir)
        assert result.exists()
        assert result.read_bytes() == before_bytes

    def test_main_creates_backup_on_write(self, tmp_path, capsys):
        db = tmp_path / "main_backup.db"
        _pre_erg3_db(db)
        code = migration.main(["--database", str(db), "--allow-database-write"])
        assert code == 0
        captured = capsys.readouterr()
        assert "Backup created:" in captured.out


class TestWrongDatabaseIsolation:
    def test_migration_touches_only_the_named_database(self, tmp_path):
        target = tmp_path / "target.db"
        protected = tmp_path / "protected.db"
        _pre_erg3_db(target)
        _pre_erg3_db(protected)
        before_protected = protected.read_bytes()
        migration.upgrade(target)
        assert protected.read_bytes() == before_protected
        assert migration.inspect(target)["ready"] is True
        assert migration.inspect(protected)["table_exists"] is False


# ---------------------------------------------------------------------------
# ORM/migration parity
# ---------------------------------------------------------------------------


class TestModelMigrationParity:
    def test_fresh_session_can_read_and_write_via_erg3_service(self, tmp_path):
        db = tmp_path / "parity.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        candidate_id, assessment_id = _seed_candidate_with_assessment(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = s.get(UnknownAirportCandidate, candidate_id)
            review = record_unknown_airport_candidate_relevance_review(
                s, candidate, basis_assessment_id=assessment_id,
                action="CONFIRM_EMAS_RELEVANT", reviewer="human:x", reason="x",
            )
            s.commit()
            assert s.query(UnknownAirportCandidateRelevanceReview).count() == 1
            assert review.basis_assessment_id == assessment_id
        engine.dispose()

    def test_immutability_still_works_post_migration(self, tmp_path):
        db = tmp_path / "parity_immut.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        candidate_id, assessment_id = _seed_candidate_with_assessment(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = s.get(UnknownAirportCandidate, candidate_id)
            review = record_unknown_airport_candidate_relevance_review(
                s, candidate, basis_assessment_id=assessment_id,
                action="DEFER_RELEVANCE_REVIEW", reviewer="human:x", reason="x",
            )
            s.commit()
            review.reason = "changed"
            with pytest.raises(ValueError, match="immutable"):
                s.commit()
            s.rollback()
        engine.dispose()

    def test_action_check_vocabulary_still_enforced_post_migration_via_raw_sql(self, tmp_path):
        db = tmp_path / "parity_vocab.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        candidate_id, assessment_id = _seed_candidate_with_assessment(db)
        conn = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                f"INSERT INTO {TABLE_NAME} (candidate_id, basis_assessment_id, action, reviewer, reason, created_at) "
                "VALUES (?, ?, 'NOT_A_REAL_ACTION', 'human:x', 'x', '2026-01-01')",
                (candidate_id, assessment_id),
            )
        conn.rollback()
        conn.close()


# ---------------------------------------------------------------------------
# Raw-SQL attacks
# ---------------------------------------------------------------------------


class TestRawSqlAttacks:
    def test_invalid_action_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "rawcheck.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        candidate_id, assessment_id = _seed_candidate_with_assessment(db)
        conn = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                f"INSERT INTO {TABLE_NAME} (candidate_id, basis_assessment_id, action, reviewer, reason, created_at) "
                "VALUES (?, ?, 'MAYBE', 'human:x', 'x', '2026-01-01')", (candidate_id, assessment_id),
            )
        conn.rollback()
        conn.close()

    def test_lowercase_action_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "lowercase.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        candidate_id, assessment_id = _seed_candidate_with_assessment(db)
        conn = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                f"INSERT INTO {TABLE_NAME} (candidate_id, basis_assessment_id, action, reviewer, reason, created_at) "
                "VALUES (?, ?, 'confirm_emas_relevant', 'human:x', 'x', '2026-01-01')", (candidate_id, assessment_id),
            )
        conn.rollback()
        conn.close()

    def test_invalid_candidate_fk_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "nocandidate.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        _candidate_id, assessment_id = _seed_candidate_with_assessment(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute(
                f"INSERT INTO {TABLE_NAME} (candidate_id, basis_assessment_id, action, reviewer, reason, created_at) "
                "VALUES (999999, ?, 'DEFER_RELEVANCE_REVIEW', 'human:x', 'x', '2026-01-01')", (assessment_id,),
            )
        conn.rollback()
        conn.close()

    def test_invalid_basis_assessment_fk_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "noassessment.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        candidate_id, _assessment_id = _seed_candidate_with_assessment(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute(
                f"INSERT INTO {TABLE_NAME} (candidate_id, basis_assessment_id, action, reviewer, reason, created_at) "
                "VALUES (?, 999999, 'DEFER_RELEVANCE_REVIEW', 'human:x', 'x', '2026-01-01')", (candidate_id,),
            )
        conn.rollback()
        conn.close()

    def test_invalid_supersession_fk_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "nosupersedes.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        candidate_id, assessment_id = _seed_candidate_with_assessment(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute(
                f"INSERT INTO {TABLE_NAME} "
                "(candidate_id, basis_assessment_id, action, reviewer, reason, supersedes_review_id, created_at) "
                "VALUES (?, ?, 'DEFER_RELEVANCE_REVIEW', 'human:x', 'x', 999999, '2026-01-01')",
                (candidate_id, assessment_id),
            )
        conn.rollback()
        conn.close()

    def test_null_reviewer_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "nullreviewer.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        candidate_id, assessment_id = _seed_candidate_with_assessment(db)
        conn = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL constraint failed"):
            conn.execute(
                f"INSERT INTO {TABLE_NAME} (candidate_id, basis_assessment_id, action, reviewer, reason, created_at) "
                "VALUES (?, ?, 'DEFER_RELEVANCE_REVIEW', NULL, 'x', '2026-01-01')", (candidate_id, assessment_id),
            )
        conn.rollback()
        conn.close()

    def test_null_reason_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "nullreason.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        candidate_id, assessment_id = _seed_candidate_with_assessment(db)
        conn = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL constraint failed"):
            conn.execute(
                f"INSERT INTO {TABLE_NAME} (candidate_id, basis_assessment_id, action, reviewer, reason, created_at) "
                "VALUES (?, ?, 'DEFER_RELEVANCE_REVIEW', 'human:x', NULL, '2026-01-01')", (candidate_id, assessment_id),
            )
        conn.rollback()
        conn.close()

    def test_candidate_deletion_blocked_when_review_exists(self, tmp_path):
        db = tmp_path / "candidatedelete.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        candidate_id, assessment_id = _seed_candidate_with_assessment(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = s.get(UnknownAirportCandidate, candidate_id)
            record_unknown_airport_candidate_relevance_review(
                s, candidate, basis_assessment_id=assessment_id,
                action="DEFER_RELEVANCE_REVIEW", reviewer="human:x", reason="x",
            )
            s.commit()
        engine.dispose()
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute("DELETE FROM unknown_airport_candidates WHERE id=?", (candidate_id,))
        conn.rollback()
        conn.close()

    def test_assessment_deletion_blocked_when_review_exists(self, tmp_path):
        db = tmp_path / "assessmentdelete.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        candidate_id, assessment_id = _seed_candidate_with_assessment(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = s.get(UnknownAirportCandidate, candidate_id)
            record_unknown_airport_candidate_relevance_review(
                s, candidate, basis_assessment_id=assessment_id,
                action="DEFER_RELEVANCE_REVIEW", reviewer="human:x", reason="x",
            )
            s.commit()
        engine.dispose()
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute("DELETE FROM unknown_airport_candidate_relevance_assessments WHERE id=?", (assessment_id,))
        conn.rollback()
        conn.close()


# ---------------------------------------------------------------------------
# Zero backfill / no business logic
# ---------------------------------------------------------------------------


class TestZeroBackfill:
    def test_upgrade_never_inserts_a_row_even_with_real_data_present(self, tmp_path):
        db = tmp_path / "nobackfill.db"
        _pre_erg3_db(db)
        _seed_candidate_with_assessment(db)
        migration.upgrade(db)
        assert migration.inspect(db)["count"] == 0

    def test_no_ast_reference_to_business_logic_modules(self):
        tree = ast.parse(inspect_module.getsource(migration))
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
            elif isinstance(node, ast.Import):
                imported_modules |= {alias.name for alias in node.names}
        forbidden_substrings = (
            "discovery_candidate_fragment", "evidence_attachment_guard", "discovery_evidence_persistence",
            "unknown_airport_candidate_persistence", "unknown_airport_candidate_relevance_persistence",
            "unknown_airport_candidate_relevance_review_persistence", "emas_relevance_evaluation",
            "acquisition", "mac_granicus", "fleet_health",
            "unknown_airport_candidate_resolution", "unknown_airport_discovery_integration",
        )
        for module_name in imported_modules:
            for forbidden in forbidden_substrings:
                assert forbidden not in module_name, f"unexpected business-logic import: {module_name}"

    def test_no_construction_of_review_or_related_orm_objects_in_migration_source(self):
        tree = ast.parse(inspect_module.getsource(migration))
        forbidden = {
            "UnknownAirportCandidateRelevanceReview", "UnknownAirportCandidateRelevanceAssessment",
            "UnknownAirportCandidate", "SourceAssertion",
        }
        found = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden
        }
        assert found == set()


# ---------------------------------------------------------------------------
# Source-neutrality
# ---------------------------------------------------------------------------


class TestSourceNeutrality:
    def test_no_source_specific_terms_in_migration_source(self):
        source = inspect_module.getsource(migration)
        for term in ("MAC", "Granicus", "granicus", "USAspending", "usaspending", "n8n", "OpenAI", "Anthropic"):
            assert term not in source


# ---------------------------------------------------------------------------
# inspect() itself / no real database access
# ---------------------------------------------------------------------------


class TestInspect:
    def test_inspect_never_mutates(self, tmp_path):
        db = tmp_path / "inspectonly.db"
        _pre_erg3_db(db)
        before = db.read_bytes()
        migration.inspect(db)
        migration.inspect(db)
        assert db.read_bytes() == before

    def test_inspect_reports_foreign_key_check_clean(self, tmp_path):
        db = tmp_path / "fkcheck.db"
        _pre_erg3_db(db)
        migration.upgrade(db)
        assert migration.inspect(db)["foreign_key_check"] == []


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
        assert matches == ["data/runway_safe.db"]
