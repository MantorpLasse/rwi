"""Tests for scripts/migrate_unknown_airport_candidate_relevance_assessments_erg2.py
(docs/architecture/rwi-erg2-relevance-assessment-persistence-report.md).

Every test uses an isolated temp-file SQLite database (tmp_path). Nothing
in this file ever opens data/runway_safe.db - the real migration is
explicitly deferred to a separate, later, explicitly-authorized operational
step. Modeled directly on tests/test_unknown_airport_candidate_migration.py
(UAC2A), this repository's own strongest, most recent precedent for a
two-table parent+child additive migration test suite.
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
from app.models import Airport, Source, SourceAssertion
from app.models.unknown_airport_candidate import UnknownAirportCandidate
from app.models.unknown_airport_candidate_relevance_assessment import (
    UnknownAirportCandidateRelevanceAssessment,
    UnknownAirportCandidateRelevanceAssessmentEvidenceLink,
)
from app.services.emas_relevance_evaluation import EvidenceClass, EmasEvidenceObservation
from app.services.unknown_airport_candidate_persistence import find_or_create_unknown_airport_candidate
from app.services.unknown_airport_candidate_relevance_persistence import (
    persist_unknown_airport_candidate_relevance_assessment,
)
import scripts.migrate_unknown_airport_candidate_relevance_assessments_erg2 as migration

ERG2_TABLES = (
    "unknown_airport_candidate_relevance_assessments",
    "unknown_airport_candidate_relevance_assessment_evidence_links",
)


def _pre_erg2_db(path):
    """Full current schema minus the two tables this migration creates -
    the realistic "not yet migrated" starting state. Unlike UAC2A's own
    fixture, no downstream table forward-references either new table, so a
    plain create_all() + DROP TABLE suffices."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=OFF")
    for table_name in reversed(ERG2_TABLES):
        conn.execute(f"DROP TABLE IF EXISTS {table_name}")
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


def _seed_candidate_with_assertion(db_path):
    """Seeds one UnknownAirportCandidate and one SourceAssertion linked to
    it (unknown_airport_candidate_id) - the minimum real starting state
    for exercising the persistence service against a genuinely migrated
    (not create_all'd) database."""
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        candidate = find_or_create_unknown_airport_candidate(s, raw_name="Foo Regional Airport", raw_country="XX").candidate
        s.commit()
        source = Source(title="Test Source", source_type="official")
        s.add(source)
        s.commit()
        assertion = SourceAssertion(
            source_id=source.id, assertion_type="project_construction",
            source_record_identifier="erg2-migration-test-1",
            unknown_airport_candidate_id=candidate.id,
        )
        s.add(assertion)
        s.commit()
        candidate_id, assertion_id = candidate.id, assertion.id
    engine.dispose()
    return candidate_id, assertion_id


# ---------------------------------------------------------------------------
# Clean upgrade / exact schema parity
# ---------------------------------------------------------------------------


class TestCleanUpgrade:
    def test_upgrade_creates_both_tables(self, tmp_path):
        db = tmp_path / "clean.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["tables_exist"] == {t: True for t in ERG2_TABLES}
        assert result["ready"] is True

    def test_exact_assessment_columns(self, tmp_path):
        db = tmp_path / "cols.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        cols = {
            row[1]: (row[2], bool(row[3]), bool(row[5]))
            for row in conn.execute("PRAGMA table_info(unknown_airport_candidate_relevance_assessments)")
        }
        conn.close()
        assert cols == {
            "id": ("INTEGER", True, True),
            "candidate_id": ("INTEGER", True, False),
            "outcome": ("VARCHAR(30)", True, False),
            "reason": ("TEXT", True, False),
            "evidence_classes_matched_json": ("TEXT", True, False),
            "contradicting_evidence_classes_json": ("TEXT", True, False),
            "is_inventory_relevant": ("BOOLEAN", True, False),
            "is_watch_worthy": ("BOOLEAN", True, False),
            "evaluator_version": ("VARCHAR(20)", True, False),
            "created_at": ("DATETIME", True, False),
        }

    def test_exact_evidence_link_columns(self, tmp_path):
        db = tmp_path / "cols2.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        cols = {
            row[1]: (row[2], bool(row[3]), bool(row[5]))
            for row in conn.execute("PRAGMA table_info(unknown_airport_candidate_relevance_assessment_evidence_links)")
        }
        conn.close()
        assert cols == {
            "id": ("INTEGER", True, True),
            "assessment_id": ("INTEGER", True, False),
            "source_assertion_id": ("INTEGER", True, False),
        }

    def test_exact_foreign_keys_via_pragma(self, tmp_path):
        db = tmp_path / "fks.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        assessment_fks = {
            (row[3], row[2], row[4])
            for row in conn.execute("PRAGMA foreign_key_list(unknown_airport_candidate_relevance_assessments)")
        }
        link_fks = {
            (row[3], row[2], row[4])
            for row in conn.execute("PRAGMA foreign_key_list(unknown_airport_candidate_relevance_assessment_evidence_links)")
        }
        conn.close()
        assert assessment_fks == {("candidate_id", "unknown_airport_candidates", "id")}
        assert link_fks == {
            ("assessment_id", "unknown_airport_candidate_relevance_assessments", "id"),
            ("source_assertion_id", "source_assertions", "id"),
        }

    def test_no_on_delete_cascade(self, tmp_path):
        db = tmp_path / "nocascade.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "PRAGMA foreign_key_list(unknown_airport_candidate_relevance_assessment_evidence_links)"
        ).fetchall()
        conn.close()
        for row in rows:
            assert row[5] == "NO ACTION"
            assert row[6] == "NO ACTION"

    def test_evidence_link_unique_constraint_via_raw_sql(self, tmp_path):
        db = tmp_path / "unique.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        candidate_id, assertion_id = _seed_candidate_with_assertion(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO unknown_airport_candidate_relevance_assessments "
            "(candidate_id, outcome, reason, evidence_classes_matched_json, contradicting_evidence_classes_json, "
            "is_inventory_relevant, is_watch_worthy, evaluator_version, created_at) "
            "VALUES (?, 'RUNWAY_ONLY_NOT_EMAS_RELEVANT', 'x', '[]', '[]', 0, 0, '1', '2026-01-01')",
            (candidate_id,),
        )
        assessment_id = conn.execute("SELECT id FROM unknown_airport_candidate_relevance_assessments").fetchone()[0]
        conn.execute(
            "INSERT INTO unknown_airport_candidate_relevance_assessment_evidence_links "
            "(assessment_id, source_assertion_id) VALUES (?, ?)", (assessment_id, assertion_id),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            conn.execute(
                "INSERT INTO unknown_airport_candidate_relevance_assessment_evidence_links "
                "(assessment_id, source_assertion_id) VALUES (?, ?)", (assessment_id, assertion_id),
            )
        conn.rollback()
        conn.close()

    def test_row_counts_zero_after_upgrade(self, tmp_path):
        db = tmp_path / "zero.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        assert migration.inspect(db)["counts"] == {t: 0 for t in ERG2_TABLES}

    def test_no_trigger_objects_created_by_migration(self, tmp_path):
        db = tmp_path / "notrigger.db"
        _pre_erg2_db(db)
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
        _pre_erg2_db(db)
        migration.upgrade(db)
        before = migration.inspect(db)
        migration.upgrade(db)
        after = migration.inspect(db)
        assert before == after

    def test_second_upgrade_preserves_rows(self, tmp_path):
        db = tmp_path / "idem2.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        candidate_id, assertion_id = _seed_candidate_with_assertion(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = s.get(UnknownAirportCandidate, candidate_id)
            persist_unknown_airport_candidate_relevance_assessment(
                s, candidate,
                observations=(EmasEvidenceObservation(EvidenceClass.G_GENERIC_RUNWAY_WORK, basis="x"),),
                source_assertion_ids=(assertion_id,),
            )
            s.commit()
        engine.dispose()

        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["counts"] == {
            "unknown_airport_candidate_relevance_assessments": 1,
            "unknown_airport_candidate_relevance_assessment_evidence_links": 1,
        }


# ---------------------------------------------------------------------------
# Partial-schema / incompatible-schema safety
# ---------------------------------------------------------------------------


class TestPartialAndIncompatibleSchema:
    def test_assessment_exists_correctly_link_absent_safe_completion(self, tmp_path):
        db = tmp_path / "assessment_only.db"
        _pre_erg2_db(db)
        _create_table_raw(db, "unknown_airport_candidate_relevance_assessments")
        migration.upgrade(db)
        assert migration.inspect(db)["ready"] is True

    def test_link_exists_correctly_assessment_absent_safe_completion(self, tmp_path):
        db = tmp_path / "link_only.db"
        _pre_erg2_db(db)
        _create_table_raw(db, "unknown_airport_candidate_relevance_assessment_evidence_links")
        migration.upgrade(db)
        assert migration.inspect(db)["ready"] is True

    def test_wrong_columns_fails_closed(self, tmp_path):
        db = tmp_path / "wrongcols.db"
        _pre_erg2_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE unknown_airport_candidate_relevance_assessments (id INTEGER PRIMARY KEY, wrong_column TEXT)"
        )
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError, match="do not match the expected ERG2 schema"):
            migration.upgrade(db)
        assert migration.inspect(db)["tables_exist"][
            "unknown_airport_candidate_relevance_assessment_evidence_links"
        ] is False

    def test_missing_named_check_constraint_fails_closed(self, tmp_path):
        db = tmp_path / "noconstraint.db"
        _pre_erg2_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE unknown_airport_candidate_relevance_assessments ("
            "id INTEGER NOT NULL PRIMARY KEY, candidate_id INTEGER NOT NULL, outcome VARCHAR(30) NOT NULL, "
            "reason TEXT NOT NULL, evidence_classes_matched_json TEXT NOT NULL, "
            "contradicting_evidence_classes_json TEXT NOT NULL, is_inventory_relevant BOOLEAN NOT NULL, "
            "is_watch_worthy BOOLEAN NOT NULL, evaluator_version VARCHAR(20) NOT NULL, created_at DATETIME NOT NULL, "
            "FOREIGN KEY(candidate_id) REFERENCES unknown_airport_candidates (id))"
        )
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError, match="missing expected named constraint"):
            migration.upgrade(db)

    def test_missing_expected_index_fails_closed(self, tmp_path):
        db = tmp_path / "missingindex.db"
        _pre_erg2_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE unknown_airport_candidate_relevance_assessments ("
            "id INTEGER NOT NULL PRIMARY KEY, candidate_id INTEGER NOT NULL, outcome VARCHAR(30) NOT NULL, "
            "reason TEXT NOT NULL, evidence_classes_matched_json TEXT NOT NULL, "
            "contradicting_evidence_classes_json TEXT NOT NULL, is_inventory_relevant BOOLEAN NOT NULL, "
            "is_watch_worthy BOOLEAN NOT NULL, evaluator_version VARCHAR(20) NOT NULL, created_at DATETIME NOT NULL, "
            "CONSTRAINT ck_unknown_airport_candidate_relevance_assessments_outcome CHECK "
            "(outcome IN ('EMAS_CONFIRMED','EMAS_STRONG_SIGNAL','EMAS_PLAUSIBLE_SIGNAL',"
            "'RUNWAY_ONLY_NOT_EMAS_RELEVANT','INSUFFICIENT_EVIDENCE')), "
            "FOREIGN KEY(candidate_id) REFERENCES unknown_airport_candidates (id))"
            # Deliberately no CREATE INDEX for candidate_id.
        )
        conn.commit()
        conn.close()
        result = migration.inspect(db)
        assert result["matches_expected_schema"]["unknown_airport_candidate_relevance_assessments"] is False
        with pytest.raises(migration.IncompatibleExistingSchemaError, match="missing expected index"):
            migration.upgrade(db)


# ---------------------------------------------------------------------------
# inspect()/upgrade() parity
# ---------------------------------------------------------------------------


class TestInspectTrustworthiness:
    def test_inspect_ready_true_only_for_genuinely_matching_schema(self, tmp_path):
        db = tmp_path / "healthy.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["ready"] is True
        assert result["matches_expected_schema"] == {t: True for t in ERG2_TABLES}


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


class TestDowngrade:
    def test_downgrade_empty_success(self, tmp_path):
        db = tmp_path / "downgrade_empty.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        migration.downgrade(db)
        assert migration.inspect(db)["tables_exist"] == {t: False for t in ERG2_TABLES}

    def test_downgrade_assessment_rows_refused(self, tmp_path):
        db = tmp_path / "assessment_rows.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        candidate_id, assertion_id = _seed_candidate_with_assertion(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = s.get(UnknownAirportCandidate, candidate_id)
            persist_unknown_airport_candidate_relevance_assessment(
                s, candidate, observations=(), source_assertion_ids=(),
            )
            s.commit()
        engine.dispose()
        with pytest.raises(RuntimeError, match=r"downgrade\(\) refused"):
            migration.downgrade(db)
        assert migration.inspect(db)["tables_exist"]["unknown_airport_candidate_relevance_assessments"] is True

    def test_downgrade_link_rows_refused(self, tmp_path):
        db = tmp_path / "link_rows.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        candidate_id, assertion_id = _seed_candidate_with_assertion(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = s.get(UnknownAirportCandidate, candidate_id)
            persist_unknown_airport_candidate_relevance_assessment(
                s, candidate,
                observations=(EmasEvidenceObservation(EvidenceClass.G_GENERIC_RUNWAY_WORK, basis="x"),),
                source_assertion_ids=(assertion_id,),
            )
            s.commit()
        engine.dispose()
        with pytest.raises(RuntimeError, match=r"downgrade\(\) refused"):
            migration.downgrade(db)

    def test_downgrade_refusal_atomicity_schema_and_rows_intact(self, tmp_path):
        db = tmp_path / "atomic.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        candidate_id, assertion_id = _seed_candidate_with_assertion(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = s.get(UnknownAirportCandidate, candidate_id)
            persist_unknown_airport_candidate_relevance_assessment(
                s, candidate,
                observations=(EmasEvidenceObservation(EvidenceClass.G_GENERIC_RUNWAY_WORK, basis="x"),),
                source_assertion_ids=(assertion_id,),
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
        _pre_erg2_db(db)
        migration.upgrade(db)
        migration.downgrade(db)
        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["ready"] is True
        assert result["counts"] == {t: 0 for t in ERG2_TABLES}


class TestDdlAtomicity:
    def test_upgrade_failure_between_tables_leaves_neither_table_created(self, tmp_path, monkeypatch):
        db = tmp_path / "upgrade_atomic.db"
        _pre_erg2_db(db)

        real_table_exists = migration._table_exists

        def _crash_before_second_table(connection, name):
            result = real_table_exists(connection, name)
            if name == "unknown_airport_candidate_relevance_assessment_evidence_links" and not result:
                raise RuntimeError("simulated crash before creating the second table")
            return result

        monkeypatch.setattr(migration, "_table_exists", _crash_before_second_table)
        with pytest.raises(RuntimeError, match="simulated crash"):
            migration.upgrade(db)
        monkeypatch.undo()

        assert migration.inspect(db)["tables_exist"] == {t: False for t in ERG2_TABLES}


# ---------------------------------------------------------------------------
# Write authorization / backup / wrong-DB isolation
# ---------------------------------------------------------------------------


class TestWriteGate:
    def test_main_requires_allow_database_write(self, tmp_path):
        db = tmp_path / "gate.db"
        _pre_erg2_db(db)
        with pytest.raises(SystemExit):
            migration.main(["--database", str(db)])
        assert migration.inspect(db)["tables_exist"]["unknown_airport_candidate_relevance_assessments"] is False

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
        _pre_erg2_db(db)
        backup_dir = tmp_path / "backups"
        before_bytes = db.read_bytes()
        result = migration.backup_database(db, backup_directory=backup_dir)
        assert result.exists()
        assert result.read_bytes() == before_bytes

    def test_main_creates_backup_on_write(self, tmp_path, capsys):
        db = tmp_path / "main_backup.db"
        _pre_erg2_db(db)
        code = migration.main(["--database", str(db), "--allow-database-write"])
        assert code == 0
        captured = capsys.readouterr()
        assert "Backup created:" in captured.out


class TestWrongDatabaseIsolation:
    def test_migration_touches_only_the_named_database(self, tmp_path):
        target = tmp_path / "target.db"
        protected = tmp_path / "protected.db"
        _pre_erg2_db(target)
        _pre_erg2_db(protected)
        before_protected = protected.read_bytes()
        migration.upgrade(target)
        assert protected.read_bytes() == before_protected
        assert migration.inspect(target)["ready"] is True
        assert migration.inspect(protected)["tables_exist"]["unknown_airport_candidate_relevance_assessments"] is False


# ---------------------------------------------------------------------------
# ORM/migration (service) parity
# ---------------------------------------------------------------------------


class TestModelMigrationParity:
    def test_fresh_session_can_read_and_write_via_erg2_service(self, tmp_path):
        db = tmp_path / "parity.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        candidate_id, assertion_id = _seed_candidate_with_assertion(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = s.get(UnknownAirportCandidate, candidate_id)
            result = persist_unknown_airport_candidate_relevance_assessment(
                s, candidate,
                observations=(EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="EMAS procurement"),),
                source_assertion_ids=(assertion_id,),
            )
            s.commit()
            assert s.query(UnknownAirportCandidateRelevanceAssessment).count() == 1
            assert s.query(UnknownAirportCandidateRelevanceAssessmentEvidenceLink).count() == 1
            assert result.outcome.value == "EMAS_STRONG_SIGNAL"
        engine.dispose()

    def test_assessment_immutability_still_works_post_migration(self, tmp_path):
        db = tmp_path / "parity_immut.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        candidate_id, assertion_id = _seed_candidate_with_assertion(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = s.get(UnknownAirportCandidate, candidate_id)
            result = persist_unknown_airport_candidate_relevance_assessment(
                s, candidate,
                observations=(EmasEvidenceObservation(EvidenceClass.G_GENERIC_RUNWAY_WORK, basis="x"),),
                source_assertion_ids=(assertion_id,),
            )
            s.commit()
            result.assessment.reason = "changed"
            with pytest.raises(ValueError, match="immutable"):
                s.commit()
            s.rollback()
        engine.dispose()

    def test_outcome_check_vocabulary_still_enforced_post_migration_via_raw_sql(self, tmp_path):
        db = tmp_path / "parity_vocab.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        candidate_id, _ = _seed_candidate_with_assertion(db)
        conn = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                "INSERT INTO unknown_airport_candidate_relevance_assessments "
                "(candidate_id, outcome, reason, evidence_classes_matched_json, "
                "contradicting_evidence_classes_json, is_inventory_relevant, is_watch_worthy, "
                "evaluator_version, created_at) "
                "VALUES (?, 'NOT_A_REAL_OUTCOME', 'x', '[]', '[]', 0, 0, '1', '2026-01-01')", (candidate_id,),
            )
        conn.rollback()
        conn.close()


# ---------------------------------------------------------------------------
# Raw-SQL attacks (CHECK/UNIQUE/FK bypassing the ORM entirely)
# ---------------------------------------------------------------------------


class TestRawSqlAttacks:
    def test_invalid_outcome_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "rawcheck.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        candidate_id, _ = _seed_candidate_with_assertion(db)
        conn = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                "INSERT INTO unknown_airport_candidate_relevance_assessments "
                "(candidate_id, outcome, reason, evidence_classes_matched_json, "
                "contradicting_evidence_classes_json, is_inventory_relevant, is_watch_worthy, "
                "evaluator_version, created_at) "
                "VALUES (?, 'MAYBE', 'x', '[]', '[]', 0, 0, '1', '2026-01-01')", (candidate_id,),
            )
        conn.rollback()
        conn.close()

    def test_lowercase_outcome_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "lowercase.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        candidate_id, _ = _seed_candidate_with_assertion(db)
        conn = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                "INSERT INTO unknown_airport_candidate_relevance_assessments "
                "(candidate_id, outcome, reason, evidence_classes_matched_json, "
                "contradicting_evidence_classes_json, is_inventory_relevant, is_watch_worthy, "
                "evaluator_version, created_at) "
                "VALUES (?, 'insufficient_evidence', 'x', '[]', '[]', 0, 0, '1', '2026-01-01')", (candidate_id,),
            )
        conn.rollback()
        conn.close()

    def test_assessment_referencing_nonexistent_candidate_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "nocandidate.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute(
                "INSERT INTO unknown_airport_candidate_relevance_assessments "
                "(candidate_id, outcome, reason, evidence_classes_matched_json, "
                "contradicting_evidence_classes_json, is_inventory_relevant, is_watch_worthy, "
                "evaluator_version, created_at) "
                "VALUES (999999, 'INSUFFICIENT_EVIDENCE', 'x', '[]', '[]', 0, 0, '1', '2026-01-01')"
            )
        conn.rollback()
        conn.close()

    def test_link_referencing_nonexistent_assessment_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "nolinkassess.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        _candidate_id, assertion_id = _seed_candidate_with_assertion(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute(
                "INSERT INTO unknown_airport_candidate_relevance_assessment_evidence_links "
                "(assessment_id, source_assertion_id) VALUES (999999, ?)", (assertion_id,),
            )
        conn.rollback()
        conn.close()

    def test_link_referencing_nonexistent_source_assertion_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "nolinksa.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        candidate_id, _ = _seed_candidate_with_assertion(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO unknown_airport_candidate_relevance_assessments "
            "(candidate_id, outcome, reason, evidence_classes_matched_json, "
            "contradicting_evidence_classes_json, is_inventory_relevant, is_watch_worthy, "
            "evaluator_version, created_at) "
            "VALUES (?, 'INSUFFICIENT_EVIDENCE', 'x', '[]', '[]', 0, 0, '1', '2026-01-01')", (candidate_id,),
        )
        assessment_id = conn.execute("SELECT id FROM unknown_airport_candidate_relevance_assessments").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute(
                "INSERT INTO unknown_airport_candidate_relevance_assessment_evidence_links "
                "(assessment_id, source_assertion_id) VALUES (?, 999999)", (assessment_id,),
            )
        conn.rollback()
        conn.close()

    def test_cross_candidate_link_not_rejected_by_schema_alone_documented_boundary(self, tmp_path):
        """HIGH-PRIORITY adversarial-review finding (mission's own S5/S26),
        documented honestly rather than silently accepted or falsely
        claimed as fixed: the DATABASE schema alone does NOT reject a link
        row pairing a real assessment with a real but UNRELATED candidate's
        SourceAssertion - both FKs (assessment_id -> assessments.id,
        source_assertion_id -> source_assertions.id) are satisfied
        independently, with no cross-column consistency check between them.
        A composite-FK schema-level fix was prototyped during this review
        and reverted after proving it would break upgrade() against the
        real, already-migrated production source_assertions table (see
        the model's own CROSS-CANDIDATE INTEGRITY docstring section).
        Cross-candidate integrity is therefore enforced at the SERVICE
        layer only - see
        tests/test_unknown_airport_candidate_relevance_persistence.py::
        TestEvidenceTraceability::test_source_assertion_linked_to_different_candidate_rejected
        for the proof that the GOVERNED persistence function refuses this
        exact case. This test proves the inverse: bypassing that service
        via raw SQL succeeds, which is why the service-level check remains
        load-bearing and must never be removed or weakened."""
        db = tmp_path / "crosscandidate.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        candidate_a, assertion_a = _seed_candidate_with_assertion(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO unknown_airport_candidates (candidate_fingerprint, raw_name, created_at) "
            "VALUES ('fp-other', 'Other Airport', '2026-01-01')"
        )
        candidate_b = conn.execute(
            "SELECT id FROM unknown_airport_candidates WHERE candidate_fingerprint='fp-other'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO unknown_airport_candidate_relevance_assessments "
            "(candidate_id, outcome, reason, evidence_classes_matched_json, "
            "contradicting_evidence_classes_json, is_inventory_relevant, is_watch_worthy, "
            "evaluator_version, created_at) "
            "VALUES (?, 'INSUFFICIENT_EVIDENCE', 'x', '[]', '[]', 0, 0, '1', '2026-01-01')", (candidate_b,),
        )
        assessment_for_b = conn.execute(
            "SELECT id FROM unknown_airport_candidate_relevance_assessments WHERE candidate_id=?", (candidate_b,)
        ).fetchone()[0]
        # assessment_for_b genuinely belongs to candidate_b; assertion_a
        # genuinely belongs to candidate_a. The schema alone permits this
        # cross-candidate pairing - it does NOT raise.
        conn.execute(
            "INSERT INTO unknown_airport_candidate_relevance_assessment_evidence_links "
            "(assessment_id, source_assertion_id) VALUES (?, ?)", (assessment_for_b, assertion_a),
        )
        conn.commit()
        linked = conn.execute(
            "SELECT source_assertion_id FROM unknown_airport_candidate_relevance_assessment_evidence_links "
            "WHERE assessment_id=?", (assessment_for_b,),
        ).fetchone()
        conn.close()
        assert linked == (assertion_a,)  # confirms the mismatched link was NOT rejected by schema alone

    def test_null_outcome_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "nulloutcome.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        candidate_id, _ = _seed_candidate_with_assertion(db)
        conn = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL constraint failed"):
            conn.execute(
                "INSERT INTO unknown_airport_candidate_relevance_assessments "
                "(candidate_id, outcome, reason, evidence_classes_matched_json, "
                "contradicting_evidence_classes_json, is_inventory_relevant, is_watch_worthy, "
                "evaluator_version, created_at) "
                "VALUES (?, NULL, 'x', '[]', '[]', 0, 0, '1', '2026-01-01')", (candidate_id,),
            )
        conn.rollback()
        conn.close()

    def test_null_evaluator_version_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "nullversion.db"
        _pre_erg2_db(db)
        migration.upgrade(db)
        candidate_id, _ = _seed_candidate_with_assertion(db)
        conn = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL constraint failed"):
            conn.execute(
                "INSERT INTO unknown_airport_candidate_relevance_assessments "
                "(candidate_id, outcome, reason, evidence_classes_matched_json, "
                "contradicting_evidence_classes_json, is_inventory_relevant, is_watch_worthy, "
                "evaluator_version, created_at) "
                "VALUES (?, 'INSUFFICIENT_EVIDENCE', 'x', '[]', '[]', 0, 0, NULL, '2026-01-01')", (candidate_id,),
            )
        conn.rollback()
        conn.close()


# ---------------------------------------------------------------------------
# Zero backfill / no discovery business logic
# ---------------------------------------------------------------------------


class TestZeroBackfill:
    def test_upgrade_never_inserts_a_row_even_with_real_candidate_data_present(self, tmp_path):
        db = tmp_path / "nobackfill.db"
        _pre_erg2_db(db)
        _seed_candidate_with_assertion(db)
        migration.upgrade(db)
        assert migration.inspect(db)["counts"] == {t: 0 for t in ERG2_TABLES}

    def test_no_ast_reference_to_discovery_business_logic_modules(self):
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
            "emas_relevance_evaluation", "acquisition", "mac_granicus", "fleet_health",
            "unknown_airport_candidate_resolution", "unknown_airport_discovery_integration",
        )
        for module_name in imported_modules:
            for forbidden in forbidden_substrings:
                assert forbidden not in module_name, f"unexpected business-logic import: {module_name}"

    def test_no_construction_of_assessment_or_link_orm_objects_in_migration_source(self):
        tree = ast.parse(inspect_module.getsource(migration))
        forbidden = {
            "UnknownAirportCandidateRelevanceAssessment", "UnknownAirportCandidateRelevanceAssessmentEvidenceLink",
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
# inspect() itself
# ---------------------------------------------------------------------------


class TestInspect:
    def test_inspect_never_mutates(self, tmp_path):
        db = tmp_path / "inspectonly.db"
        _pre_erg2_db(db)
        before = db.read_bytes()
        migration.inspect(db)
        migration.inspect(db)
        assert db.read_bytes() == before

    def test_inspect_reports_foreign_key_check_clean(self, tmp_path):
        db = tmp_path / "fkcheck.db"
        _pre_erg2_db(db)
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
        assert matches == ["data/runway_safe.db"]
