"""Tests for scripts/migrate_source_assertion_identity_resolution_kar1.py
(docs/architecture/rwi-known-airport-ambiguity-resolution-design.md).

Every test uses an isolated temp-file SQLite database (tmp_path). Nothing
in this file ever opens data/runway_safe.db - the real migration is
explicitly deferred to a separate, later, explicitly-authorized operational
step. Modeled directly on
tests/test_unknown_airport_candidate_relevance_review_migration.py, this
repository's own strongest, most recent precedent for a single-table
additive migration test suite.
"""
from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.database import Base
from app.models import Airport, Source, SourceAssertion, SourceAssertionEvidenceBag
from app.services.source_assertion_identity_resolution import record_source_assertion_identity_resolution
import scripts.migrate_source_assertion_identity_resolution_kar1 as migration

TABLE_NAME = "source_assertion_identity_resolutions"


def _pre_kar1_db(path):
    """Full current schema (including the already-committed KAR1 table's
    own dependencies) minus the KAR1 table itself - the realistic "not yet
    migrated" starting state."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")
    conn.commit()
    conn.close()


def _seed_assertion_with_snapshot(db_path):
    """Seeds one Airport, one Source, one SourceAssertion, and one
    SourceAssertionEvidenceBag snapshot - the minimum real starting state
    for exercising the KAR2 service against a genuinely migrated (not
    create_all'd) database."""
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        airport = Airport(name="Test Airport", country="USA")
        s.add(airport)
        s.commit()
        source = Source(title="Test Source", source_type="web_discovery")
        s.add(source)
        s.commit()
        assertion = SourceAssertion(
            source_id=source.id, assertion_type="project_construction",
            source_locator="item-1", artifact_identity="doc-1", raw_fragment_hash="hash-1",
            identity_guard_decision="REVIEW_REQUIRED", identity_guard_reason="ambiguous",
            evidence_quality="unverified_candidate", review_state="unreviewed",
        )
        s.add(assertion)
        s.commit()
        snapshot = SourceAssertionEvidenceBag(
            source_assertion_id=assertion.id, evidence_bag_json="{}", evidence_bag_hash="h", schema_version=1,
        )
        s.add(snapshot)
        s.commit()
        assertion_id, airport_id = assertion.id, airport.id
    engine.dispose()
    return assertion_id, airport_id


# ---------------------------------------------------------------------------
# Clean upgrade / exact schema parity
# ---------------------------------------------------------------------------


class TestCleanUpgrade:
    def test_upgrade_creates_table(self, tmp_path):
        db = tmp_path / "clean.db"
        _pre_kar1_db(db)
        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["table_exists"] is True
        assert result["ready"] is True

    def test_exact_columns(self, tmp_path):
        db = tmp_path / "cols.db"
        _pre_kar1_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        cols = {row[1]: (row[2], bool(row[3]), bool(row[5])) for row in conn.execute(f"PRAGMA table_info({TABLE_NAME})")}
        conn.close()
        assert cols == {
            "id": ("INTEGER", True, True),
            "source_assertion_id": ("INTEGER", True, False),
            "evidence_bag_snapshot_id": ("INTEGER", True, False),
            "action": ("VARCHAR(30)", True, False),
            "reason": ("TEXT", True, False),
            "reviewer": ("VARCHAR(100)", True, False),
            "matched_airport_id": ("INTEGER", False, False),
            "created_at": ("DATETIME", True, False),
            "supersedes_resolution_id": ("INTEGER", False, False),
        }

    def test_exact_foreign_keys_via_pragma(self, tmp_path):
        db = tmp_path / "fks.db"
        _pre_kar1_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        fks = {(row[3], row[2], row[4]) for row in conn.execute(f"PRAGMA foreign_key_list({TABLE_NAME})")}
        conn.close()
        assert fks == {
            ("source_assertion_id", "source_assertions", "id"),
            ("source_assertion_id", "source_assertion_evidence_bags", "source_assertion_id"),
            ("evidence_bag_snapshot_id", "source_assertion_evidence_bags", "id"),
            ("matched_airport_id", "airports", "id"),
            ("supersedes_resolution_id", TABLE_NAME, "id"),
        }

    def test_no_on_delete_cascade(self, tmp_path):
        db = tmp_path / "nocascade.db"
        _pre_kar1_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        rows = conn.execute(f"PRAGMA foreign_key_list({TABLE_NAME})").fetchall()
        conn.close()
        for row in rows:
            assert row[5] == "NO ACTION"
            assert row[6] == "NO ACTION"

    def test_row_count_zero_after_upgrade(self, tmp_path):
        db = tmp_path / "zero.db"
        _pre_kar1_db(db)
        migration.upgrade(db)
        assert migration.inspect(db)["count"] == 0

    def test_no_trigger_objects_created_by_migration(self, tmp_path):
        db = tmp_path / "notrigger.db"
        _pre_kar1_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        triggers = conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
        conn.close()
        assert triggers == []

    def test_no_other_table_touched(self, tmp_path):
        """Additive-only: every other table's own row count is unaffected
        by running this migration (task requirement: no ALTER TABLE, no
        existing row read/changed/merged/deleted anywhere)."""
        db = tmp_path / "isolated.db"
        _pre_kar1_db(db)
        assertion_id, airport_id = _seed_assertion_with_snapshot(db)
        conn = sqlite3.connect(str(db))
        before = {
            name: conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            for name in ("airports", "sources", "source_assertions", "source_assertion_evidence_bags")
        }
        conn.close()
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        after = {
            name: conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            for name in ("airports", "sources", "source_assertions", "source_assertion_evidence_bags")
        }
        conn.close()
        assert before == after


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_second_upgrade_is_safe_no_op(self, tmp_path):
        db = tmp_path / "idem.db"
        _pre_kar1_db(db)
        migration.upgrade(db)
        before = migration.inspect(db)
        migration.upgrade(db)
        after = migration.inspect(db)
        assert before == after

    def test_second_upgrade_preserves_rows(self, tmp_path):
        db = tmp_path / "idem2.db"
        _pre_kar1_db(db)
        migration.upgrade(db)
        assertion_id, airport_id = _seed_assertion_with_snapshot(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            record_source_assertion_identity_resolution(
                s, source_assertion_id=assertion_id, action="DEFER_IDENTITY_REVIEW",
                reviewer="human:x", reason="x",
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
        _pre_kar1_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(f"CREATE TABLE {TABLE_NAME} (id INTEGER PRIMARY KEY, wrong_column TEXT)")
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError, match="do not match the expected KAR1 schema"):
            migration.upgrade(db)

    def test_missing_named_check_constraint_fails_closed(self, tmp_path):
        db = tmp_path / "noconstraint.db"
        _pre_kar1_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            f"CREATE TABLE {TABLE_NAME} ("
            "id INTEGER NOT NULL PRIMARY KEY, source_assertion_id INTEGER NOT NULL, "
            "evidence_bag_snapshot_id INTEGER NOT NULL, action VARCHAR(30) NOT NULL, "
            "reason TEXT NOT NULL, reviewer VARCHAR(100) NOT NULL, matched_airport_id INTEGER, "
            "created_at DATETIME NOT NULL, supersedes_resolution_id INTEGER, "
            "FOREIGN KEY(source_assertion_id) REFERENCES source_assertions (id), "
            "FOREIGN KEY(matched_airport_id) REFERENCES airports (id), "
            f"FOREIGN KEY(supersedes_resolution_id) REFERENCES {TABLE_NAME} (id))"
        )
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError, match="missing expected named constraint"):
            migration.upgrade(db)

    def test_missing_composite_snapshot_fk_fails_closed(self, tmp_path):
        """A table shaped like the expected schema but missing the causal-
        integrity composite ForeignKeyConstraint (evidence_bag_snapshot_id,
        source_assertion_id) -> (source_assertion_evidence_bags.id,
        source_assertion_evidence_bags.source_assertion_id) must be
        rejected, not silently accepted as compatible."""
        db = tmp_path / "nocompositefk.db"
        _pre_kar1_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            f"CREATE TABLE {TABLE_NAME} ("
            "id INTEGER NOT NULL PRIMARY KEY, source_assertion_id INTEGER NOT NULL, "
            "evidence_bag_snapshot_id INTEGER NOT NULL, action VARCHAR(30) NOT NULL, "
            "reason TEXT NOT NULL, reviewer VARCHAR(100) NOT NULL, matched_airport_id INTEGER, "
            "created_at DATETIME NOT NULL, supersedes_resolution_id INTEGER, "
            "CONSTRAINT ck_source_assertion_identity_resolutions_action CHECK "
            "(action IN ('ATTACH_TO_EXISTING_AIRPORT','REJECT_ATTACHMENT','DEFER_IDENTITY_REVIEW')), "
            "CONSTRAINT ck_source_assertion_identity_resolutions_attach_target_required CHECK "
            "((action != 'ATTACH_TO_EXISTING_AIRPORT') OR matched_airport_id IS NOT NULL), "
            "CONSTRAINT ck_source_assertion_identity_resolutions_target_only_for_attach CHECK "
            "((action = 'ATTACH_TO_EXISTING_AIRPORT') OR matched_airport_id IS NULL), "
            "FOREIGN KEY(source_assertion_id) REFERENCES source_assertions (id), "
            "FOREIGN KEY(matched_airport_id) REFERENCES airports (id), "
            f"FOREIGN KEY(supersedes_resolution_id) REFERENCES {TABLE_NAME} (id))"
            # Deliberately no composite FK on (evidence_bag_snapshot_id, source_assertion_id).
        )
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError):
            migration.upgrade(db)

    def test_missing_expected_index_reported_not_ready(self, tmp_path):
        db = tmp_path / "missingindex.db"
        _pre_kar1_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            f"CREATE TABLE {TABLE_NAME} ("
            "id INTEGER NOT NULL PRIMARY KEY, source_assertion_id INTEGER NOT NULL, "
            "evidence_bag_snapshot_id INTEGER NOT NULL, action VARCHAR(30) NOT NULL, "
            "reason TEXT NOT NULL, reviewer VARCHAR(100) NOT NULL, matched_airport_id INTEGER, "
            "created_at DATETIME NOT NULL, supersedes_resolution_id INTEGER, "
            "CONSTRAINT ck_source_assertion_identity_resolutions_action CHECK "
            "(action IN ('ATTACH_TO_EXISTING_AIRPORT','REJECT_ATTACHMENT','DEFER_IDENTITY_REVIEW')), "
            "CONSTRAINT ck_source_assertion_identity_resolutions_attach_target_required CHECK "
            "((action != 'ATTACH_TO_EXISTING_AIRPORT') OR matched_airport_id IS NOT NULL), "
            "CONSTRAINT ck_source_assertion_identity_resolutions_target_only_for_attach CHECK "
            "((action = 'ATTACH_TO_EXISTING_AIRPORT') OR matched_airport_id IS NULL), "
            "CONSTRAINT fk_source_assertion_identity_resolutions_snapshot_matches_assertion "
            "FOREIGN KEY(evidence_bag_snapshot_id, source_assertion_id) REFERENCES "
            "source_assertion_evidence_bags (id, source_assertion_id), "
            "FOREIGN KEY(source_assertion_id) REFERENCES source_assertions (id), "
            "FOREIGN KEY(matched_airport_id) REFERENCES airports (id), "
            f"FOREIGN KEY(supersedes_resolution_id) REFERENCES {TABLE_NAME} (id))"
            # Deliberately no CREATE INDEX statements.
        )
        conn.commit()
        conn.close()
        result = migration.inspect(db)
        assert result["ready"] is False
        with pytest.raises(migration.IncompatibleExistingSchemaError, match="missing expected index"):
            migration.upgrade(db)


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------


class TestAtomicity:
    def test_failed_upgrade_leaves_database_untouched(self, tmp_path):
        db = tmp_path / "atomic.db"
        _pre_kar1_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(f"CREATE TABLE {TABLE_NAME} (id INTEGER PRIMARY KEY, wrong_column TEXT)")
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError):
            migration.upgrade(db)
        # Table still exists exactly as the (wrong) pre-existing shape -
        # never dropped, never partially rebuilt.
        conn = sqlite3.connect(str(db))
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({TABLE_NAME})")}
        conn.close()
        assert cols == {"id", "wrong_column"}


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


class TestDowngrade:
    def test_downgrade_empty_table_drops_it(self, tmp_path):
        db = tmp_path / "downgrade_empty.db"
        _pre_kar1_db(db)
        migration.upgrade(db)
        migration.downgrade(db)
        assert migration.inspect(db)["table_exists"] is False

    def test_downgrade_populated_table_refuses(self, tmp_path):
        db = tmp_path / "downgrade_populated.db"
        _pre_kar1_db(db)
        migration.upgrade(db)
        assertion_id, airport_id = _seed_assertion_with_snapshot(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            record_source_assertion_identity_resolution(
                s, source_assertion_id=assertion_id, action="DEFER_IDENTITY_REVIEW",
                reviewer="human:x", reason="x",
            )
            s.commit()
        engine.dispose()

        with pytest.raises(RuntimeError, match="downgrade\\(\\) refused"):
            migration.downgrade(db)
        assert migration.inspect(db)["table_exists"] is True
        assert migration.inspect(db)["count"] == 1

    def test_downgrade_missing_table_is_a_safe_noop(self, tmp_path):
        db = tmp_path / "downgrade_missing.db"
        _pre_kar1_db(db)
        migration.downgrade(db)
        assert migration.inspect(db)["table_exists"] is False


# ---------------------------------------------------------------------------
# CLI safety gate
# ---------------------------------------------------------------------------


class TestCliSafetyGate:
    def test_main_requires_allow_database_write(self, tmp_path, capsys):
        db = tmp_path / "cligate.db"
        _pre_kar1_db(db)
        with pytest.raises(SystemExit):
            migration.main(["--database", str(db)])
        assert migration.inspect(db)["table_exists"] is False
