"""Tests for scripts/migrate_evidence_bag_persistence_eb2.py (EB2,
docs/architecture/rwi-full-evidencebag-persistence-design.md).

Every test uses an isolated temp-file SQLite database (tmp_path). Nothing
in this file ever opens data/runway_safe.db - the real migration is
explicitly deferred to a separate, later EB6 slice. Grep-verified (see
TestNoRealDatabaseAccess).
"""
from __future__ import annotations

import ast
import inspect as inspect_module
import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import MetaData, create_engine
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateIndex, CreateTable

import app.models
from app.database import Base
from app.models import Airport, Source, SourceAssertion
from app.models.identity_guard_evaluation import IdentityGuardEvaluation
from app.models.source_assertion_evidence_bag import SourceAssertionEvidenceBag
from app.models.unknown_airport_candidate import UnknownAirportCandidate, UnknownAirportCandidateReview
from app.services.evidence_attachment_guard import AttachmentOutcome, EvidenceBag
from app.services.evidence_bag_serialization import (
    deserialize_evidence_bag,
    hash_serialized_evidence_bag,
    serialize_evidence_bag,
)
import scripts.migrate_evidence_bag_persistence_eb2 as migration

EB2_TABLES = migration.TABLES


def _tables_depending_on(table_names: "set[str]") -> "set[str]":
    """Transitive closure: every table (anywhere in Base.metadata) that
    has a foreign key pointing, directly or indirectly, at any table in
    `table_names`. A realistic "not yet migrated" pre-EB2 database could
    not contain such a table either - it would have no valid FK target
    (KAR1's own source_assertion_identity_resolutions is the first real
    example: its causal-integrity composite ForeignKeyConstraint points at
    source_assertion_evidence_bags, an EB2 table, so a pre-EB2 fixture
    must exclude it too, exactly like it already excludes the EB2 tables
    themselves). Computed generically, not by hardcoding any specific
    table name, so this stays correct for any future table with the same
    kind of dependency."""
    dependents: "set[str]" = set()
    changed = True
    while changed:
        changed = False
        for name, table in Base.metadata.tables.items():
            if name in table_names or name in dependents:
                continue
            referenced = {fk.column.table.name for column in table.columns for fk in column.foreign_keys}
            if referenced & (table_names | dependents):
                dependents.add(name)
                changed = True
    return dependents


def _pre_eb2_db(path):
    """A full pre-EB2 schema (every table except the two this migration
    creates, and any table that itself structurally depends on one of
    them via a foreign key) - the realistic "not yet migrated" starting
    state."""
    engine = create_engine(f"sqlite:///{path}")
    excluded = set(EB2_TABLES) | _tables_depending_on(set(EB2_TABLES))
    pre_meta = MetaData()
    for name, table in Base.metadata.tables.items():
        if name not in excluded:
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


def _seed_source_assertion(db_path, *, raw_name="Foo"):
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        source = Source(title="t", source_type="web_discovery", external_id=f"discovery:{raw_name}")
        session.add(source)
        session.flush()
        assertion = SourceAssertion(
            source_id=source.id, assertion_type="project_construction",
            raw_relevant_text=f"{raw_name} evidence.", artifact_identity=f"art-{raw_name}",
            source_locator="p1", raw_fragment_hash=f"hash-{raw_name}",
            identity_guard_decision="INSUFFICIENT_IDENTITY", identity_guard_reason="original fact",
        )
        session.add(assertion)
        session.commit()
        assertion_id = assertion.id
    engine.dispose()
    return assertion_id


def _seed_airport(db_path, *, name="Real Airport"):
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        airport = Airport(name=name, country="XX")
        session.add(airport)
        session.commit()
        airport_id = airport.id
    engine.dispose()
    return airport_id


# ---------------------------------------------------------------------------
# A. Fresh upgrade
# ---------------------------------------------------------------------------


class TestCleanUpgrade:
    def test_fresh_upgrade_creates_both_tables_empty(self, tmp_path):
        db = tmp_path / "clean.db"
        _pre_eb2_db(db)
        before = migration.inspect(db)
        assert before["tables_exist"] == {name: False for name in EB2_TABLES}
        assert before["ready"] is False

        migration.upgrade(db)

        after = migration.inspect(db)
        assert after["tables_exist"] == {name: True for name in EB2_TABLES}
        assert after["counts"] == {name: 0 for name in EB2_TABLES}
        assert after["ready"] is True
        assert after["foreign_key_check"] == []

    def test_no_existing_table_touched(self, tmp_path):
        db = tmp_path / "no_touch.db"
        _pre_eb2_db(db)
        assertion_id = _seed_source_assertion(db)
        airport_id = _seed_airport(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            before_assertion = (
                session.query(SourceAssertion.raw_relevant_text, SourceAssertion.identity_guard_decision)
                .filter(SourceAssertion.id == assertion_id).one()
            )
            before_airport = session.query(Airport.name, Airport.country).filter(Airport.id == airport_id).one()
        engine.dispose()

        migration.upgrade(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            after_assertion = (
                session.query(SourceAssertion.raw_relevant_text, SourceAssertion.identity_guard_decision)
                .filter(SourceAssertion.id == assertion_id).one()
            )
            after_airport = session.query(Airport.name, Airport.country).filter(Airport.id == airport_id).one()
        engine.dispose()
        assert before_assertion == after_assertion
        assert before_airport == after_airport


# ---------------------------------------------------------------------------
# B. Schema parity (compiled from ORM)
# ---------------------------------------------------------------------------


class TestSchemaParity:
    def test_migrated_schema_structurally_matches_orm_model(self, tmp_path):
        db = tmp_path / "parity.db"
        _pre_eb2_db(db)
        migration.upgrade(db)

        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        for name in EB2_TABLES:
            reasons = migration._schema_mismatch_reasons(conn, name)
            assert reasons == [], f"{name}: {reasons}"
        conn.close()

    def test_snapshot_table_exact_columns(self, tmp_path):
        db = tmp_path / "snap_cols.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        cols = {row[1]: (row[2], bool(row[3]), bool(row[5])) for row in conn.execute(
            "PRAGMA table_info(source_assertion_evidence_bags)"
        )}
        conn.close()
        assert cols == {
            "id": ("INTEGER", True, True),
            "source_assertion_id": ("INTEGER", True, False),
            "evidence_bag_json": ("TEXT", True, False),
            "evidence_bag_hash": ("VARCHAR(64)", True, False),
            "schema_version": ("INTEGER", True, False),
            "created_at": ("DATETIME", True, False),
        }

    def test_evaluation_table_exact_columns(self, tmp_path):
        db = tmp_path / "eval_cols.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        cols = {row[1]: (row[2], bool(row[3]), bool(row[5])) for row in conn.execute(
            "PRAGMA table_info(identity_guard_evaluations)"
        )}
        conn.close()
        assert cols == {
            "id": ("INTEGER", True, True),
            "source_assertion_id": ("INTEGER", True, False),
            "evidence_bag_snapshot_id": ("INTEGER", True, False),
            "evaluated_against_airport_id": ("INTEGER", True, False),
            "triggering_review_id": ("INTEGER", False, False),
            "outcome": ("VARCHAR(30)", True, False),
            "reason": ("TEXT", True, False),
            "created_at": ("DATETIME", True, False),
        }


class TestInspectNeverCrashes:
    """Adversarial-review finding, fixed: inspect() is documented as a
    pure, always-safe diagnostic ("never mutates anything") but its
    original implementation let a raw sqlite3.OperationalError
    ("foreign key mismatch") propagate uncaught whenever the on-disk
    schema is internally inconsistent in a specific way - an existing
    identity_guard_evaluations table (created correctly by a prior real
    upgrade()) whose composite FK can no longer be resolved because
    source_assertion_evidence_bags was later, separately altered out
    from under it (bypassing this migration entirely - exactly what a
    direct, unreviewed schema edit could do). upgrade() was already safe
    in the identical scenario (its own per-table _schema_mismatch_reasons()
    check refuses the incompatible parent BEFORE ever reaching the final
    whole-database PRAGMA foreign_key_check), but inspect() had no such
    early exit and crashed instead of returning a structured "not ready"
    result."""

    def test_inspect_never_raises_for_an_internally_inconsistent_schema(self, tmp_path):
        db = tmp_path / "inconsistent.db"
        _pre_eb2_db(db)
        migration.upgrade(db)  # identity_guard_evaluations now exists, correctly
        # Directly (not via migration) replace the parent table with a
        # shape lacking the supporting composite UNIQUE - the child's own
        # already-existing composite FK can no longer be resolved.
        conn = sqlite3.connect(str(db))
        conn.execute("DROP TABLE source_assertion_evidence_bags")
        conn.execute(
            "CREATE TABLE source_assertion_evidence_bags (id INTEGER NOT NULL PRIMARY KEY, "
            "source_assertion_id INTEGER NOT NULL, evidence_bag_json TEXT NOT NULL, "
            "evidence_bag_hash VARCHAR(64) NOT NULL, schema_version INTEGER NOT NULL, "
            "created_at DATETIME NOT NULL, UNIQUE (source_assertion_id), "
            "FOREIGN KEY(source_assertion_id) REFERENCES source_assertions (id))"
        )
        conn.execute(
            "CREATE INDEX ix_source_assertion_evidence_bags_evidence_bag_hash "
            "ON source_assertion_evidence_bags (evidence_bag_hash)"
        )
        conn.commit()
        conn.close()

        result = migration.inspect(db)  # must not raise
        assert result["ready"] is False
        assert result["foreign_key_check"] is None
        assert result["foreign_key_check_error"] is not None
        assert "foreign key mismatch" in result["foreign_key_check_error"]

    def test_upgrade_raises_a_clean_runtime_error_not_a_raw_operational_error(self, tmp_path):
        db = tmp_path / "inconsistent_upgrade.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        conn.execute("DROP TABLE source_assertion_evidence_bags")
        conn.execute(
            "CREATE TABLE source_assertion_evidence_bags (id INTEGER NOT NULL PRIMARY KEY, "
            "source_assertion_id INTEGER NOT NULL, evidence_bag_json TEXT NOT NULL, "
            "evidence_bag_hash VARCHAR(64) NOT NULL, schema_version INTEGER NOT NULL, "
            "created_at DATETIME NOT NULL, UNIQUE (source_assertion_id), "
            "FOREIGN KEY(source_assertion_id) REFERENCES source_assertions (id))"
        )
        conn.commit()
        conn.close()

        # upgrade() already fails closed here via its own per-table
        # comparison (the missing composite UNIQUE is caught before the
        # final whole-database PRAGMA is ever reached) - confirming this
        # remains true, and that the database is left untouched.
        with pytest.raises(migration.IncompatibleExistingSchemaError):
            migration.upgrade(db)
        result = migration.inspect(db)
        assert result["tables_exist"]["identity_guard_evaluations"] is True


# ---------------------------------------------------------------------------
# C. Composite FK - PRIMARY ATTACK
# ---------------------------------------------------------------------------


class TestCompositeForeignKey:
    def test_raw_sql_cross_assertion_mismatch_rejected(self, tmp_path):
        db = tmp_path / "composite.db"
        _pre_eb2_db(db)
        a1 = _seed_source_assertion(db, raw_name="A1")
        a2 = _seed_source_assertion(db, raw_name="A2")
        airport_id = _seed_airport(db)
        migration.upgrade(db)

        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        p1 = serialize_evidence_bag(EvidenceBag(identifiers=frozenset({"A1E"})))
        p2 = serialize_evidence_bag(EvidenceBag(identifiers=frozenset({"A2E"})))
        conn.execute(
            "INSERT INTO source_assertion_evidence_bags (source_assertion_id, evidence_bag_json, evidence_bag_hash, "
            "schema_version, created_at) VALUES (?, ?, ?, 1, datetime('now'))",
            (a1, p1, hash_serialized_evidence_bag(p1)),
        )
        snap1_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO source_assertion_evidence_bags (source_assertion_id, evidence_bag_json, evidence_bag_hash, "
            "schema_version, created_at) VALUES (?, ?, ?, 1, datetime('now'))",
            (a2, p2, hash_serialized_evidence_bag(p2)),
        )
        snap2_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()

        # ATTACK: evaluation claims a1 but references a2's own snapshot.
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            conn.execute(
                "INSERT INTO identity_guard_evaluations (source_assertion_id, evidence_bag_snapshot_id, "
                "evaluated_against_airport_id, outcome, reason, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
                (a1, snap2_id, airport_id, AttachmentOutcome.ATTACH_CONFIRMED.value, "x"),
            )
        conn.close()

    def test_raw_sql_correct_pairing_succeeds(self, tmp_path):
        db = tmp_path / "composite_ok.db"
        _pre_eb2_db(db)
        a1 = _seed_source_assertion(db, raw_name="A1")
        airport_id = _seed_airport(db)
        migration.upgrade(db)

        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        p1 = serialize_evidence_bag(EvidenceBag(identifiers=frozenset({"A1E"})))
        conn.execute(
            "INSERT INTO source_assertion_evidence_bags (source_assertion_id, evidence_bag_json, evidence_bag_hash, "
            "schema_version, created_at) VALUES (?, ?, ?, 1, datetime('now'))",
            (a1, p1, hash_serialized_evidence_bag(p1)),
        )
        snap1_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()

        conn.execute(
            "INSERT INTO identity_guard_evaluations (source_assertion_id, evidence_bag_snapshot_id, "
            "evaluated_against_airport_id, outcome, reason, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (a1, snap1_id, airport_id, AttachmentOutcome.ATTACH_CONFIRMED.value, "x"),
        )
        conn.commit()
        count = conn.execute("SELECT count(*) FROM identity_guard_evaluations").fetchone()[0]
        conn.close()
        assert count == 1

    def test_pragma_foreign_key_list_shows_grouped_composite_entry(self, tmp_path):
        db = tmp_path / "composite_pragma.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = conn.execute("PRAGMA foreign_key_list(identity_guard_evaluations)").fetchall()
        conn.close()
        by_id: "dict[int, list]" = {}
        for row in rows:
            by_id.setdefault(row[0], []).append(row)
        composite_groups = [group for group in by_id.values() if len(group) > 1]
        assert len(composite_groups) == 1
        group = composite_groups[0]
        targets = {(r[3], r[2], r[4]) for r in group}
        assert targets == {
            ("evidence_bag_snapshot_id", "source_assertion_evidence_bags", "id"),
            ("source_assertion_id", "source_assertion_evidence_bags", "source_assertion_id"),
        }

    def test_sqlite_master_stores_the_named_composite_fk(self, tmp_path):
        db = tmp_path / "composite_sql.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='identity_guard_evaluations'"
        ).fetchone()[0]
        conn.close()
        assert "fk_identity_guard_evaluations_snapshot_matches_assertion" in sql
        assert "FOREIGN KEY(evidence_bag_snapshot_id, source_assertion_id)" in sql


# ---------------------------------------------------------------------------
# Adversarial-review addition: malformed-composite-FK detection matrix
# (mission Part 4) - proves inspect()/upgrade() distinguish a GENUINE
# composite constraint from schema shapes that superficially resemble one.
# ---------------------------------------------------------------------------


_EVALUATIONS_COMMON_DDL = """
    id INTEGER NOT NULL PRIMARY KEY,
    source_assertion_id INTEGER NOT NULL,
    evidence_bag_snapshot_id INTEGER NOT NULL,
    evaluated_against_airport_id INTEGER NOT NULL,
    triggering_review_id INTEGER,
    outcome VARCHAR(30) NOT NULL,
    reason TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    CONSTRAINT ck_identity_guard_evaluations_outcome CHECK (outcome IN
        ('ATTACH_CONFIRMED', 'ATTACH_PROVISIONAL', 'REVIEW_REQUIRED', 'REJECT_CROSS_AIRPORT', 'INSUFFICIENT_IDENTITY')),
    FOREIGN KEY(source_assertion_id) REFERENCES source_assertions (id),
    FOREIGN KEY(evaluated_against_airport_id) REFERENCES airports (id),
    FOREIGN KEY(triggering_review_id) REFERENCES unknown_airport_candidate_reviews (id)
"""


def _rebuild_evaluations_table(db, extra_fk_sql):
    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE identity_guard_evaluations")
    conn.execute(f"CREATE TABLE identity_guard_evaluations ({_EVALUATIONS_COMMON_DDL}, {extra_fk_sql})")
    conn.commit()
    conn.close()


class TestMalformedCompositeFkDetection:
    def test_two_separate_single_column_fks_are_not_accepted_as_composite(self, tmp_path):
        """The exact attack this migration's own composite-FK-aware
        comparison exists to catch: two INDEPENDENT single-column FKs
        with column-pair targets identical to the genuine composite
        constraint's own members do NOT enforce causal integrity (each
        column would validate against ANY matching row independently),
        and must be refused as incompatible."""
        db = tmp_path / "fake_composite.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        _rebuild_evaluations_table(
            db,
            "FOREIGN KEY(evidence_bag_snapshot_id) REFERENCES source_assertion_evidence_bags (id), "
            "FOREIGN KEY(source_assertion_id) REFERENCES source_assertion_evidence_bags (source_assertion_id)",
        )
        result = migration.inspect(db)
        assert result["matches_expected_schema"]["identity_guard_evaluations"] is False
        with pytest.raises(migration.IncompatibleExistingSchemaError):
            migration.upgrade(db)

    def test_composite_fk_with_wrong_parent_column_order_rejected(self, tmp_path):
        """(evidence_bag_snapshot_id, source_assertion_id) ->
        (source_assertion_id, id) instead of (id, source_assertion_id) -
        a genuinely different (and semantically WRONG) pairing, not
        merely a formatting difference."""
        db = tmp_path / "wrong_parent_order.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        _rebuild_evaluations_table(
            db,
            "CONSTRAINT fk_identity_guard_evaluations_snapshot_matches_assertion "
            "FOREIGN KEY(evidence_bag_snapshot_id, source_assertion_id) "
            "REFERENCES source_assertion_evidence_bags (source_assertion_id, id)",
        )
        result = migration.inspect(db)
        assert result["matches_expected_schema"]["identity_guard_evaluations"] is False
        with pytest.raises(migration.IncompatibleExistingSchemaError):
            migration.upgrade(db)

    def test_correct_composite_fk_plus_extra_redundant_single_column_fk_rejected(self, tmp_path):
        """An extra, unexpected constraint beyond what the ORM model
        declares is itself a mismatch - this migration's own schema
        comparison must not silently tolerate additional, unreviewed
        constraints even if the genuine composite one is also present."""
        db = tmp_path / "extra_fk.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        _rebuild_evaluations_table(
            db,
            "CONSTRAINT fk_identity_guard_evaluations_snapshot_matches_assertion "
            "FOREIGN KEY(evidence_bag_snapshot_id, source_assertion_id) "
            "REFERENCES source_assertion_evidence_bags (id, source_assertion_id), "
            "FOREIGN KEY(evidence_bag_snapshot_id) REFERENCES source_assertion_evidence_bags (id)",
        )
        result = migration.inspect(db)
        assert result["matches_expected_schema"]["identity_guard_evaluations"] is False
        with pytest.raises(migration.IncompatibleExistingSchemaError):
            migration.upgrade(db)

    def test_composite_fk_referencing_the_same_parent_column_twice_rejected(self, tmp_path):
        """Adversarial-review correction: SQLite's own DDL parser does
        NOT reliably refuse `REFERENCES parent (id, id)` at CREATE TABLE
        time (verified directly - an earlier draft of this test wrongly
        assumed it always does, which happened to hold in one ad-hoc
        manual probe but does not reproduce in a clean, isolated
        re-test). The property that actually matters, and that this
        migration is actually responsible for, is that its OWN
        composite-FK-aware comparison rejects this shape regardless -
        `(evidence_bag_snapshot_id -> id, source_assertion_id -> id)`
        does not match the expected group
        `(evidence_bag_snapshot_id -> id, source_assertion_id ->
        source_assertion_id)`, since both members would target the same
        parent column instead of their own distinct, correct ones."""
        db = tmp_path / "self_referential_parent.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        _rebuild_evaluations_table(
            db,
            "CONSTRAINT fk_identity_guard_evaluations_snapshot_matches_assertion "
            "FOREIGN KEY(evidence_bag_snapshot_id, source_assertion_id) "
            "REFERENCES source_assertion_evidence_bags (id, id)",
        )
        result = migration.inspect(db)
        assert result["matches_expected_schema"]["identity_guard_evaluations"] is False
        with pytest.raises(migration.IncompatibleExistingSchemaError):
            migration.upgrade(db)


class TestSupportingUniqueConstraintDetection:
    """mission Part 6: the composite FK on identity_guard_evaluations
    depends on source_assertion_evidence_bags carrying a genuine UNIQUE
    (or PK) constraint on exactly (id, source_assertion_id) together."""

    _SNAPSHOT_COMMON_DDL = """
        id INTEGER NOT NULL PRIMARY KEY,
        source_assertion_id INTEGER NOT NULL,
        evidence_bag_json TEXT NOT NULL,
        evidence_bag_hash VARCHAR(64) NOT NULL,
        schema_version INTEGER NOT NULL,
        created_at DATETIME NOT NULL,
        FOREIGN KEY(source_assertion_id) REFERENCES source_assertions (id)
    """

    def _rebuild_snapshot_table(self, db, extra_sql, with_hash_index=True):
        conn = sqlite3.connect(str(db))
        conn.execute("DROP TABLE source_assertion_evidence_bags")
        conn.execute(f"CREATE TABLE source_assertion_evidence_bags ({self._SNAPSHOT_COMMON_DDL}, {extra_sql})")
        if with_hash_index:
            conn.execute(
                "CREATE INDEX ix_source_assertion_evidence_bags_evidence_bag_hash "
                "ON source_assertion_evidence_bags (evidence_bag_hash)"
            )
        conn.commit()
        conn.close()

    def test_missing_supporting_composite_unique_rejected(self, tmp_path):
        db = tmp_path / "missing_unique.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        self._rebuild_snapshot_table(db, "UNIQUE (source_assertion_id)")
        result = migration.inspect(db)
        assert result["matches_expected_schema"]["source_assertion_evidence_bags"] is False
        with pytest.raises(migration.IncompatibleExistingSchemaError):
            migration.upgrade(db)

    def test_non_unique_index_instead_of_unique_constraint_rejected(self, tmp_path):
        db = tmp_path / "nonunique_index.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        self._rebuild_snapshot_table(db, "UNIQUE (source_assertion_id)")
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE INDEX ix_fake_composite ON source_assertion_evidence_bags (id, source_assertion_id)")
        conn.commit()
        conn.close()
        result = migration.inspect(db)
        assert result["matches_expected_schema"]["source_assertion_evidence_bags"] is False
        with pytest.raises(migration.IncompatibleExistingSchemaError):
            migration.upgrade(db)

    def test_correct_unique_plus_extra_unrelated_unique_rejected(self, tmp_path):
        db = tmp_path / "extra_unique.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        self._rebuild_snapshot_table(
            db,
            "UNIQUE(id, source_assertion_id), UNIQUE(source_assertion_id), UNIQUE(evidence_bag_hash)",
        )
        result = migration.inspect(db)
        assert result["matches_expected_schema"]["source_assertion_evidence_bags"] is False
        with pytest.raises(migration.IncompatibleExistingSchemaError):
            migration.upgrade(db)

    def test_supporting_unique_column_order_is_semantically_irrelevant_and_accepted(self, tmp_path):
        """Unlike the composite FK's own from/to column PAIRING (where
        order determines meaning), a UNIQUE constraint is a pure column
        SET with no direction - declaring it as
        UNIQUE(source_assertion_id, id) instead of UNIQUE(id,
        source_assertion_id) is genuinely, functionally equivalent SQL,
        and must be accepted, not falsely flagged as a mismatch."""
        db = tmp_path / "reordered_unique.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        self._rebuild_snapshot_table(
            db,
            "CONSTRAINT uq_source_assertion_evidence_bags_id_source_assertion_id "
            "UNIQUE (source_assertion_id, id), UNIQUE(source_assertion_id)",
        )
        result = migration.inspect(db)
        assert result["matches_expected_schema"]["source_assertion_evidence_bags"] is True
        migration.upgrade(db)  # must not raise


# ---------------------------------------------------------------------------
# G/H. Partial and incompatible schema
# ---------------------------------------------------------------------------


class TestPartialAndIncompatibleSchema:
    def test_neither_table_exists_full_upgrade(self, tmp_path):
        db = tmp_path / "partial_a.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        assert migration.inspect(db)["ready"] is True

    def test_snapshot_correct_evaluation_missing_completes_safely(self, tmp_path):
        db = tmp_path / "partial_b.db"
        _pre_eb2_db(db)
        _create_table_raw(db, "source_assertion_evidence_bags")
        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["ready"] is True
        assert result["tables_exist"]["identity_guard_evaluations"] is True

    def test_snapshot_incompatible_evaluation_absent_fails_closed_entirely(self, tmp_path):
        db = tmp_path / "partial_d.db"
        _pre_eb2_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE source_assertion_evidence_bags (id INTEGER PRIMARY KEY, "
            "wrong_column TEXT)"
        )
        conn.commit()
        conn.close()

        with pytest.raises(migration.IncompatibleExistingSchemaError):
            migration.upgrade(db)
        # evaluation table must never even be attempted.
        result_conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        exists = result_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='identity_guard_evaluations'"
        ).fetchone()
        result_conn.close()
        assert exists is None

    def test_snapshot_correct_evaluation_incompatible_fails_closed(self, tmp_path):
        db = tmp_path / "partial_e.db"
        _pre_eb2_db(db)
        _create_table_raw(db, "source_assertion_evidence_bags")
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE identity_guard_evaluations (id INTEGER PRIMARY KEY, wrong_column TEXT)")
        conn.commit()
        conn.close()

        with pytest.raises(migration.IncompatibleExistingSchemaError):
            migration.upgrade(db)
        # Snapshot table must remain exactly as it was (untouched).
        result = migration.inspect(db)
        assert result["matches_expected_schema"]["source_assertion_evidence_bags"] is True

    def test_both_correct_full_upgrade_no_op(self, tmp_path):
        db = tmp_path / "partial_f.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        migration.upgrade(db)  # should just verify, not error
        assert migration.inspect(db)["ready"] is True

    def test_both_incompatible_fails_closed(self, tmp_path):
        db = tmp_path / "partial_g.db"
        _pre_eb2_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE source_assertion_evidence_bags (id INTEGER PRIMARY KEY, x TEXT)")
        conn.execute("CREATE TABLE identity_guard_evaluations (id INTEGER PRIMARY KEY, y TEXT)")
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError):
            migration.upgrade(db)

    def test_populated_correct_snapshot_evaluation_missing_completes_safely(self, tmp_path):
        db = tmp_path / "partial_h.db"
        _pre_eb2_db(db)
        assertion_id = _seed_source_assertion(db)
        _create_table_raw(db, "source_assertion_evidence_bags")
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            payload = serialize_evidence_bag(EvidenceBag())
            session.add(SourceAssertionEvidenceBag(
                source_assertion_id=assertion_id, evidence_bag_json=payload,
                evidence_bag_hash=hash_serialized_evidence_bag(payload), schema_version=1,
            ))
            session.commit()
        engine.dispose()

        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["ready"] is True
        assert result["counts"]["source_assertion_evidence_bags"] == 1
        assert result["counts"]["identity_guard_evaluations"] == 0


# ---------------------------------------------------------------------------
# I/J. Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_idempotent_on_empty_tables(self, tmp_path):
        db = tmp_path / "idemp_empty.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        first = migration.inspect(db)
        migration.upgrade(db)
        second = migration.inspect(db)
        assert first == second

    def test_idempotent_with_snapshot_and_evaluation_rows_present(self, tmp_path):
        db = tmp_path / "idemp_populated.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        assertion_id = _seed_source_assertion(db, raw_name="羽田空港 Unicode")
        airport_id = _seed_airport(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            bag = EvidenceBag(
                names=frozenset({"羽田空港", 'Tricky, "quoted"\nvalue'}),
                identifiers=frozenset({"HND"}),
            )
            payload = serialize_evidence_bag(bag)
            snapshot = SourceAssertionEvidenceBag(
                source_assertion_id=assertion_id, evidence_bag_json=payload,
                evidence_bag_hash=hash_serialized_evidence_bag(payload), schema_version=1,
            )
            session.add(snapshot)
            session.commit()
            session.add(IdentityGuardEvaluation(
                source_assertion_id=assertion_id, evidence_bag_snapshot_id=snapshot.id,
                evaluated_against_airport_id=airport_id,
                outcome=AttachmentOutcome.ATTACH_CONFIRMED.value, reason="first",
            ))
            session.add(IdentityGuardEvaluation(
                source_assertion_id=assertion_id, evidence_bag_snapshot_id=snapshot.id,
                evaluated_against_airport_id=airport_id,
                outcome=AttachmentOutcome.INSUFFICIENT_IDENTITY.value, reason="second, different outcome",
            ))
            session.commit()
        engine.dispose()

        before_conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        before_snapshot_row = before_conn.execute(
            "SELECT source_assertion_id, evidence_bag_json, evidence_bag_hash, schema_version, created_at "
            "FROM source_assertion_evidence_bags"
        ).fetchall()
        before_eval_rows = before_conn.execute(
            "SELECT source_assertion_id, evidence_bag_snapshot_id, evaluated_against_airport_id, outcome, "
            "reason, created_at FROM identity_guard_evaluations ORDER BY id"
        ).fetchall()
        before_conn.close()

        migration.upgrade(db)  # second call - must be a genuine no-op

        after_conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        after_snapshot_row = after_conn.execute(
            "SELECT source_assertion_id, evidence_bag_json, evidence_bag_hash, schema_version, created_at "
            "FROM source_assertion_evidence_bags"
        ).fetchall()
        after_eval_rows = after_conn.execute(
            "SELECT source_assertion_id, evidence_bag_snapshot_id, evaluated_against_airport_id, outcome, "
            "reason, created_at FROM identity_guard_evaluations ORDER BY id"
        ).fetchall()
        after_conn.close()

        assert before_snapshot_row == after_snapshot_row
        assert before_eval_rows == after_eval_rows
        # Unicode/tricky payload round-trips through the raw SQL read.
        restored = deserialize_evidence_bag(after_snapshot_row[0][1])
        assert restored == bag


# ---------------------------------------------------------------------------
# K/L/M. Downgrade policy
# ---------------------------------------------------------------------------


class TestDowngrade:
    def test_downgrade_both_empty_succeeds(self, tmp_path):
        db = tmp_path / "downgrade_empty.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        migration.downgrade(db)
        result = migration.inspect(db)
        assert result["tables_exist"] == {name: False for name in EB2_TABLES}

    def test_downgrade_refuses_when_only_snapshot_rows_exist_zero_evaluations(self, tmp_path):
        """The mission's own explicit challenge to the earlier design
        recommendation: snapshot rows are governed audit history in their
        own right, independent of whether any evaluation exists yet."""
        db = tmp_path / "downgrade_snapshot_only.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        assertion_id = _seed_source_assertion(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            payload = serialize_evidence_bag(EvidenceBag(identifiers=frozenset({"IRREPLACEABLE"})))
            session.add(SourceAssertionEvidenceBag(
                source_assertion_id=assertion_id, evidence_bag_json=payload,
                evidence_bag_hash=hash_serialized_evidence_bag(payload), schema_version=1,
            ))
            session.commit()
        engine.dispose()

        with pytest.raises(RuntimeError, match="source_assertion_evidence_bags"):
            migration.downgrade(db)
        result = migration.inspect(db)
        assert result["tables_exist"] == {name: True for name in EB2_TABLES}
        assert result["counts"]["source_assertion_evidence_bags"] == 1

    def test_downgrade_refuses_when_evaluation_rows_exist(self, tmp_path):
        db = tmp_path / "downgrade_eval.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        assertion_id = _seed_source_assertion(db)
        airport_id = _seed_airport(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            payload = serialize_evidence_bag(EvidenceBag())
            snapshot = SourceAssertionEvidenceBag(
                source_assertion_id=assertion_id, evidence_bag_json=payload,
                evidence_bag_hash=hash_serialized_evidence_bag(payload), schema_version=1,
            )
            session.add(snapshot)
            session.commit()
            session.add(IdentityGuardEvaluation(
                source_assertion_id=assertion_id, evidence_bag_snapshot_id=snapshot.id,
                evaluated_against_airport_id=airport_id,
                outcome=AttachmentOutcome.ATTACH_CONFIRMED.value, reason="x",
            ))
            session.commit()
        engine.dispose()

        with pytest.raises(RuntimeError):
            migration.downgrade(db)
        result = migration.inspect(db)
        assert result["counts"]["identity_guard_evaluations"] == 1

    def test_downgrade_refuses_with_multiple_snapshots_across_different_assertions(self, tmp_path):
        db = tmp_path / "downgrade_multi.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        a1 = _seed_source_assertion(db, raw_name="A1")
        a2 = _seed_source_assertion(db, raw_name="A2")
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            for assertion_id in (a1, a2):
                payload = serialize_evidence_bag(EvidenceBag(identifiers=frozenset({str(assertion_id)})))
                session.add(SourceAssertionEvidenceBag(
                    source_assertion_id=assertion_id, evidence_bag_json=payload,
                    evidence_bag_hash=hash_serialized_evidence_bag(payload), schema_version=1,
                ))
            session.commit()
        engine.dispose()

        with pytest.raises(RuntimeError):
            migration.downgrade(db)
        result = migration.inspect(db)
        assert result["counts"]["source_assertion_evidence_bags"] == 2

    def test_downgrade_refuses_even_for_orphan_rows_created_with_fk_disabled(self, tmp_path):
        """A malformed/orphan snapshot row (referencing a SourceAssertion
        that does not exist, constructible only by disabling FK
        enforcement) still counts as "a row present" for downgrade's own
        purposes - downgrade's row-count check does not, and should not,
        attempt to distinguish valid governed history from malformed
        garbage; either way, dropping the table would destroy data no
        schema operation should silently discard. A human must resolve
        the malformed state by hand, not have it silently vanish via
        downgrade."""
        db = tmp_path / "downgrade_orphan.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=OFF")
        payload = serialize_evidence_bag(EvidenceBag())
        conn.execute(
            "INSERT INTO source_assertion_evidence_bags (source_assertion_id, evidence_bag_json, "
            "evidence_bag_hash, schema_version, created_at) VALUES (999999, ?, ?, 1, datetime('now'))",
            (payload, hash_serialized_evidence_bag(payload)),
        )
        conn.commit()
        conn.close()

        with pytest.raises(RuntimeError):
            migration.downgrade(db)
        result = migration.inspect(db)
        assert result["counts"]["source_assertion_evidence_bags"] == 1


# ---------------------------------------------------------------------------
# N/O. Atomicity
# ---------------------------------------------------------------------------


class TestDdlAtomicity:
    def test_upgrade_failure_between_tables_leaves_neither_table_created(self, tmp_path, monkeypatch):
        db = tmp_path / "upgrade_atomic.db"
        _pre_eb2_db(db)

        real_table_exists = migration._table_exists

        def _crash_before_second_table(connection, name):
            result = real_table_exists(connection, name)
            if name == "identity_guard_evaluations" and not result:
                raise RuntimeError("simulated crash before creating the second table")
            return result

        monkeypatch.setattr(migration, "_table_exists", _crash_before_second_table)
        with pytest.raises(RuntimeError, match="simulated crash"):
            migration.upgrade(db)
        monkeypatch.undo()

        result = migration.inspect(db)
        assert result["tables_exist"] == {name: False for name in EB2_TABLES}

    def test_downgrade_failure_between_tables_leaves_both_tables_intact(self, tmp_path, monkeypatch):
        db = tmp_path / "downgrade_atomic.db"
        _pre_eb2_db(db)
        migration.upgrade(db)

        real_table_exists = migration._table_exists

        def _crash_before_second_drop(connection, name):
            result = real_table_exists(connection, name)
            if name == "source_assertion_evidence_bags" and result:
                raise RuntimeError("simulated crash before dropping the second (parent) table")
            return result

        monkeypatch.setattr(migration, "_table_exists", _crash_before_second_drop)
        with pytest.raises(RuntimeError, match="simulated crash"):
            migration.downgrade(db)
        monkeypatch.undo()

        result = migration.inspect(db)
        assert result["tables_exist"] == {name: True for name in EB2_TABLES}
        assert result["ready"] is True


# ---------------------------------------------------------------------------
# P/Q. Backup / write gate
# ---------------------------------------------------------------------------


class TestWriteGate:
    def test_main_requires_allow_database_write(self, tmp_path):
        db = tmp_path / "gate.db"
        _pre_eb2_db(db)
        with pytest.raises(SystemExit):
            migration.main(["--database", str(db)])
        assert migration.inspect(db)["tables_exist"]["source_assertion_evidence_bags"] is False

    def test_main_no_sessionlocal_or_orm_engine_ast(self):
        tree = ast.parse(inspect_module.getsource(migration))
        code_identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert "SessionLocal" not in code_identifiers
        assert "create_engine" not in code_identifiers

    def test_write_gate_is_the_only_thing_preventing_default_path_mutation(self, tmp_path, monkeypatch):
        def _must_not_be_called(*args, **kwargs):
            raise AssertionError("upgrade()/downgrade() must never be called without --allow-database-write")

        monkeypatch.setattr(migration, "upgrade", _must_not_be_called)
        monkeypatch.setattr(migration, "downgrade", _must_not_be_called)
        with pytest.raises(SystemExit):
            migration.main([])

    def test_nonexistent_database_path_fails_closed_on_backup(self, tmp_path):
        missing = tmp_path / "does_not_exist.db"
        with pytest.raises(FileNotFoundError):
            migration.backup_database(missing, backup_directory=tmp_path / "backups")


class TestBackup:
    def test_backup_created_before_write(self, tmp_path):
        db = tmp_path / "backup_src.db"
        _pre_eb2_db(db)
        backup_dir = tmp_path / "backups"
        before_bytes = db.read_bytes()
        result = migration.backup_database(db, backup_directory=backup_dir)
        assert result.exists()
        assert result.read_bytes() == before_bytes

    def test_backup_is_independently_readable_pre_eb2_schema(self, tmp_path):
        db = tmp_path / "backup_read.db"
        _pre_eb2_db(db)
        backup_dir = tmp_path / "backups"
        backup_path = migration.backup_database(db, backup_directory=backup_dir)

        conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "source_assertion_evidence_bags" not in tables
        assert "identity_guard_evaluations" not in tables
        assert "source_assertions" in tables

    def test_backup_matches_source_before_mutation_and_passes_integrity_check(self, tmp_path):
        db = tmp_path / "backup_match.db"
        _pre_eb2_db(db)
        assertion_id = _seed_source_assertion(db)
        backup_dir = tmp_path / "backups"
        backup_path = migration.backup_database(db, backup_directory=backup_dir)

        migration.upgrade(db)  # mutates the source after backup was taken

        conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
        count = conn.execute("SELECT count(*) FROM source_assertions").fetchone()[0]
        has_eb2 = bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='source_assertion_evidence_bags'"
        ).fetchone())
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        conn.close()
        assert count == 1
        assert has_eb2 is False
        assert integrity == "ok"
        assert fk_violations == []

    def test_main_creates_backup_on_write(self, tmp_path, capsys):
        db = tmp_path / "main_backup.db"
        _pre_eb2_db(db)
        code = migration.main(["--database", str(db), "--allow-database-write"])
        assert code == 0
        captured = capsys.readouterr()
        assert "Backup created:" in captured.out


# ---------------------------------------------------------------------------
# R. Wrong-database isolation
# ---------------------------------------------------------------------------


class TestWrongDatabaseIsolation:
    def test_migration_touches_only_the_named_database(self, tmp_path):
        target = tmp_path / "target.db"
        protected = tmp_path / "protected.db"
        _pre_eb2_db(target)
        _pre_eb2_db(protected)
        before_protected = protected.read_bytes()

        migration.upgrade(target)

        assert protected.read_bytes() == before_protected
        assert migration.inspect(target)["ready"] is True
        assert migration.inspect(protected)["ready"] is False


# ---------------------------------------------------------------------------
# S. Existing-data preservation
# ---------------------------------------------------------------------------


class TestExistingDataPreservation:
    def test_representative_multi_domain_fixture_unchanged_after_upgrade(self, tmp_path):
        db = tmp_path / "existing_data.db"
        _pre_eb2_db(db)
        assertion_id = _seed_source_assertion(db, raw_name="Preserve Me")
        airport_id = _seed_airport(db, name="Preserve Airport")
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            candidate = UnknownAirportCandidate(
                candidate_fingerprint="fp-preserve", raw_name="Preserve Candidate",
            )
            session.add(candidate)
            session.commit()
            review = UnknownAirportCandidateReview(
                candidate_id=candidate.id, action="DEFER", reason="preserve", reviewer="human:x",
            )
            session.add(review)
            session.commit()
            candidate_id, review_id = candidate.id, review.id
        engine.dispose()

        before_conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        before_tables = sorted(row[0] for row in before_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ))
        before_counts = {t: before_conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in before_tables}
        before_conn.close()

        migration.upgrade(db)

        after_conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        after_tables = sorted(row[0] for row in after_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ))
        after_conn.close()

        assert set(after_tables) - set(before_tables) == set(EB2_TABLES)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            for table_name, before_count in before_counts.items():
                after_count = session.execute(
                    __import__("sqlalchemy").text(f"SELECT count(*) FROM {table_name}")
                ).scalar()
                assert after_count == before_count, f"{table_name} row count changed"
            assert session.get(SourceAssertion, assertion_id) is not None
            assert session.get(Airport, airport_id) is not None
            assert session.get(UnknownAirportCandidate, candidate_id) is not None
            assert session.get(UnknownAirportCandidateReview, review_id) is not None
        engine.dispose()


# ---------------------------------------------------------------------------
# T. Legacy SourceAssertion
# ---------------------------------------------------------------------------


class TestLegacySourceAssertionPreservation:
    def test_legacy_source_assertion_unchanged_no_snapshot_no_evaluation_created(self, tmp_path):
        db = tmp_path / "legacy.db"
        _pre_eb2_db(db)
        assertion_id = _seed_source_assertion(db, raw_name="Legacy Row")

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            before = session.get(SourceAssertion, assertion_id)
            before_snapshot = (
                before.id, before.source_id, before.raw_relevant_text, before.identity_guard_decision,
                before.identity_guard_reason, before.artifact_identity, before.source_locator,
                before.raw_fragment_hash, before.created_at,
            )
        engine.dispose()

        migration.upgrade(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            after = session.get(SourceAssertion, assertion_id)
            after_snapshot = (
                after.id, after.source_id, after.raw_relevant_text, after.identity_guard_decision,
                after.identity_guard_reason, after.artifact_identity, after.source_locator,
                after.raw_fragment_hash, after.created_at,
            )
            assert before_snapshot == after_snapshot
            snapshot_count = session.query(SourceAssertionEvidenceBag).filter(
                SourceAssertionEvidenceBag.source_assertion_id == assertion_id
            ).count()
            eval_count = session.query(IdentityGuardEvaluation).filter(
                IdentityGuardEvaluation.source_assertion_id == assertion_id
            ).count()
            assert snapshot_count == 0
            assert eval_count == 0
        engine.dispose()


# ---------------------------------------------------------------------------
# U. ORM / migration parity
# ---------------------------------------------------------------------------


class TestModelMigrationParity:
    """Builds the DB via the real migration ONLY (never create_all()),
    then proves the ORM's own governed behavior (immutability, one-to-one,
    append-only, composite FK, decision CHECK, delete/FK safety) all
    genuinely work against the migrated schema."""

    def test_orm_insert_and_one_to_one_against_migrated_schema(self, tmp_path):
        db = tmp_path / "orm_parity.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        assertion_id = _seed_source_assertion(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            payload = serialize_evidence_bag(EvidenceBag(**{}))
            snapshot = SourceAssertionEvidenceBag(
                source_assertion_id=assertion_id, evidence_bag_json=payload,
                evidence_bag_hash=hash_serialized_evidence_bag(payload), schema_version=1,
            )
            session.add(snapshot)
            session.commit()
            assert snapshot.id is not None

            duplicate = SourceAssertionEvidenceBag(
                source_assertion_id=assertion_id, evidence_bag_json=payload,
                evidence_bag_hash=hash_serialized_evidence_bag(payload), schema_version=1,
            )
            session.add(duplicate)
            with pytest.raises(Exception, match="UNIQUE"):
                session.commit()
        engine.dispose()

    def test_orm_immutability_listeners_fire_against_migrated_schema(self, tmp_path):
        db = tmp_path / "orm_immutable.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        assertion_id = _seed_source_assertion(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            payload = serialize_evidence_bag(EvidenceBag())
            snapshot = SourceAssertionEvidenceBag(
                source_assertion_id=assertion_id, evidence_bag_json=payload,
                evidence_bag_hash=hash_serialized_evidence_bag(payload), schema_version=1,
            )
            session.add(snapshot)
            session.commit()
            snapshot.evidence_bag_json = "{}"
            with pytest.raises(ValueError, match="immutable"):
                session.commit()
        engine.dispose()

    def test_orm_append_only_evaluation_history_against_migrated_schema(self, tmp_path):
        db = tmp_path / "orm_append.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        assertion_id = _seed_source_assertion(db)
        airport_id = _seed_airport(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            payload = serialize_evidence_bag(EvidenceBag())
            snapshot = SourceAssertionEvidenceBag(
                source_assertion_id=assertion_id, evidence_bag_json=payload,
                evidence_bag_hash=hash_serialized_evidence_bag(payload), schema_version=1,
            )
            session.add(snapshot)
            session.commit()
            for outcome in (AttachmentOutcome.INSUFFICIENT_IDENTITY, AttachmentOutcome.ATTACH_CONFIRMED):
                session.add(IdentityGuardEvaluation(
                    source_assertion_id=assertion_id, evidence_bag_snapshot_id=snapshot.id,
                    evaluated_against_airport_id=airport_id, outcome=outcome.value, reason="x",
                ))
            session.commit()
            count = session.query(IdentityGuardEvaluation).filter(
                IdentityGuardEvaluation.source_assertion_id == assertion_id
            ).count()
            assert count == 2
        engine.dispose()

    def test_orm_decision_check_against_migrated_schema(self, tmp_path):
        db = tmp_path / "orm_check.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        assertion_id = _seed_source_assertion(db)
        airport_id = _seed_airport(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            payload = serialize_evidence_bag(EvidenceBag())
            snapshot = SourceAssertionEvidenceBag(
                source_assertion_id=assertion_id, evidence_bag_json=payload,
                evidence_bag_hash=hash_serialized_evidence_bag(payload), schema_version=1,
            )
            session.add(snapshot)
            session.commit()
            session.add(IdentityGuardEvaluation(
                source_assertion_id=assertion_id, evidence_bag_snapshot_id=snapshot.id,
                evaluated_against_airport_id=airport_id, outcome="NOT_A_REAL_OUTCOME", reason="x",
            ))
            with pytest.raises(Exception, match="CHECK constraint failed"):
                session.commit()
        engine.dispose()

    def test_orm_composite_causal_fk_against_migrated_schema(self, tmp_path):
        db = tmp_path / "orm_composite.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        a1 = _seed_source_assertion(db, raw_name="A1")
        a2 = _seed_source_assertion(db, raw_name="A2")
        airport_id = _seed_airport(db)

        engine = create_engine(f"sqlite:///{db}")

        @__import__("sqlalchemy").event.listens_for(engine, "connect")
        def _fk(dbapi_connection, _r):
            c = dbapi_connection.cursor()
            c.execute("PRAGMA foreign_keys=ON")
            c.close()

        with Session(engine) as session:
            p1 = serialize_evidence_bag(EvidenceBag())
            snap1 = SourceAssertionEvidenceBag(
                source_assertion_id=a1, evidence_bag_json=p1,
                evidence_bag_hash=hash_serialized_evidence_bag(p1), schema_version=1,
            )
            p2 = serialize_evidence_bag(EvidenceBag(identifiers=frozenset({"A2"})))
            snap2 = SourceAssertionEvidenceBag(
                source_assertion_id=a2, evidence_bag_json=p2,
                evidence_bag_hash=hash_serialized_evidence_bag(p2), schema_version=1,
            )
            session.add_all([snap1, snap2])
            session.commit()

            mismatched = IdentityGuardEvaluation(
                source_assertion_id=a1, evidence_bag_snapshot_id=snap2.id,
                evaluated_against_airport_id=airport_id,
                outcome=AttachmentOutcome.ATTACH_CONFIRMED.value, reason="x",
            )
            session.add(mismatched)
            with pytest.raises(Exception, match="FOREIGN KEY"):
                session.commit()
        engine.dispose()

    def test_orm_delete_fk_safety_against_migrated_schema(self, tmp_path):
        db = tmp_path / "orm_delete.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        assertion_id = _seed_source_assertion(db)

        engine = create_engine(f"sqlite:///{db}")

        @__import__("sqlalchemy").event.listens_for(engine, "connect")
        def _fk(dbapi_connection, _r):
            c = dbapi_connection.cursor()
            c.execute("PRAGMA foreign_keys=ON")
            c.close()

        with Session(engine) as session:
            assertion = session.get(SourceAssertion, assertion_id)
            payload = serialize_evidence_bag(EvidenceBag())
            snapshot = SourceAssertionEvidenceBag(
                source_assertion_id=assertion_id, evidence_bag_json=payload,
                evidence_bag_hash=hash_serialized_evidence_bag(payload), schema_version=1,
            )
            session.add(snapshot)
            session.commit()
            session.delete(assertion)
            with pytest.raises(Exception, match="FOREIGN KEY"):
                session.commit()
        engine.dispose()


# ---------------------------------------------------------------------------
# V. Serialization/migration parity - the flight-recorder proof
# ---------------------------------------------------------------------------


class TestSerializationMigrationParity:
    def test_full_unicode_comma_newline_evidence_bag_survives_migrated_columns(self, tmp_path):
        db = tmp_path / "serialization_parity.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        assertion_id = _seed_source_assertion(db)

        bag = EvidenceBag(
            identifiers=frozenset({"KFOO", "value, with, commas"}),
            names=frozenset({"Åre Flygplats", "São Paulo/Congonhas", "羽田空港", "مطار القاهرة"}),
            runway_ends=frozenset({"09", "27"}),
            runway_pairs=frozenset({"09/27"}),
            issuers=frozenset({'Quoted "Authority" Name'}),
            locations=frozenset({"line1\nline2\ttabbed"}),
            contradicting_names=frozenset({"Bar Airport"}),
            contradicting_issuers=frozenset({"Bar Authority"}),
            contradicting_locations=frozenset({"Bar City"}),
            alternate_airport_runway_ends=frozenset({"18"}),
            alternate_airport_runway_pairs=frozenset({"18/36"}),
            document_title="emoji title ✈️🛫",
            project_number="PN-1",
            contract_number="CN-1",
            url="https://example.test/x",
        )
        payload = serialize_evidence_bag(bag)
        expected_hash = hash_serialized_evidence_bag(payload)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            snapshot = SourceAssertionEvidenceBag(
                source_assertion_id=assertion_id, evidence_bag_json=payload,
                evidence_bag_hash=expected_hash, schema_version=1,
            )
            session.add(snapshot)
            session.commit()
            snapshot_id = snapshot.id
        engine.dispose()

        # Read back through a completely fresh engine/connection.
        engine2 = create_engine(f"sqlite:///{db}")
        with Session(engine2) as session:
            reloaded = session.get(SourceAssertionEvidenceBag, snapshot_id)
            restored_bag = deserialize_evidence_bag(reloaded.evidence_bag_json)
            assert restored_bag == bag
            assert reloaded.evidence_bag_hash == expected_hash
            assert hash_serialized_evidence_bag(reloaded.evidence_bag_json) == expected_hash
        engine2.dispose()

    def test_raw_sqlite_column_read_matches_python_serialization_byte_for_byte(self, tmp_path):
        """Proves the migrated TEXT column type does not truncate or
        alter the payload - reads the raw column value directly via
        sqlite3, bypassing the ORM entirely."""
        db = tmp_path / "raw_read_parity.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        assertion_id = _seed_source_assertion(db)
        bag = EvidenceBag(names=frozenset({"羽田空港" * 50}))  # long Unicode string
        payload = serialize_evidence_bag(bag)

        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO source_assertion_evidence_bags (source_assertion_id, evidence_bag_json, "
            "evidence_bag_hash, schema_version, created_at) VALUES (?, ?, ?, 1, datetime('now'))",
            (assertion_id, payload, hash_serialized_evidence_bag(payload)),
        )
        conn.commit()
        raw_value = conn.execute("SELECT evidence_bag_json FROM source_assertion_evidence_bags").fetchone()[0]
        conn.close()
        assert raw_value == payload
        assert deserialize_evidence_bag(raw_value) == bag

    def test_nfc_nfd_unicode_normalization_forms_survive_migrated_columns_distinctly(self, tmp_path):
        """The migrated TEXT column must not silently normalize Unicode
        (SQLite's own TEXT storage never does, but this proves it end to
        end through the real migrated schema, not just EB1's own
        create_all() fixture) - two canonically-equivalent but
        codepoint-distinct strings (NFC vs. NFD "café") round-trip as the
        two genuinely different strings they are."""
        import unicodedata

        db = tmp_path / "nfc_nfd_migration.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        assertion_id = _seed_source_assertion(db)

        nfc = unicodedata.normalize("NFC", "cafe" + chr(0x0301))
        nfd = unicodedata.normalize("NFD", "cafe" + chr(0x0301))
        assert nfc != nfd
        bag = EvidenceBag(names=frozenset({nfc, nfd}))
        payload = serialize_evidence_bag(bag)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            session.add(SourceAssertionEvidenceBag(
                source_assertion_id=assertion_id, evidence_bag_json=payload,
                evidence_bag_hash=hash_serialized_evidence_bag(payload), schema_version=1,
            ))
            session.commit()
        engine.dispose()

        conn = sqlite3.connect(str(db))
        raw_value = conn.execute("SELECT evidence_bag_json FROM source_assertion_evidence_bags").fetchone()[0]
        conn.close()
        restored = deserialize_evidence_bag(raw_value)
        assert restored.names == frozenset({nfc, nfd})
        assert len(restored.names) == 2


# ---------------------------------------------------------------------------
# Payload/hash/schema-version consistency boundary (mission Part 18)
# ---------------------------------------------------------------------------


class TestPayloadHashSchemaBoundary:
    def test_migration_permits_direct_raw_insert_with_inconsistent_hash_by_design(self, tmp_path):
        """Documents, does not silently accept: the migrated schema
        itself does not (and per EB1's own review, correctly should not
        yet) enforce hash-matches-payload consistency at the DB layer -
        that remains EB3's own future writer-service responsibility. This
        test proves the migration does not accidentally make that FUTURE
        enforcement impossible (the columns themselves are not
        truncated/normalized/defaulted in any way that would prevent a
        future @validates-style check from working)."""
        db = tmp_path / "consistency_boundary.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        assertion_id = _seed_source_assertion(db)

        real_payload = serialize_evidence_bag(EvidenceBag(identifiers=frozenset({"REAL"})))
        fake_hash = hash_serialized_evidence_bag(serialize_evidence_bag(EvidenceBag(identifiers=frozenset({"FAKE"}))))
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO source_assertion_evidence_bags (source_assertion_id, evidence_bag_json, "
            "evidence_bag_hash, schema_version, created_at) VALUES (?, ?, ?, 1, datetime('now'))",
            (assertion_id, real_payload, fake_hash),
        )
        conn.commit()  # succeeds today - by design, see this test's own docstring
        stored_hash = conn.execute("SELECT evidence_bag_hash FROM source_assertion_evidence_bags").fetchone()[0]
        conn.close()
        assert stored_hash == fake_hash
        assert stored_hash != hash_serialized_evidence_bag(real_payload)

    def test_migration_permits_arbitrary_malformed_hash_string_no_db_format_check(self, tmp_path):
        """The ORM model declares evidence_bag_hash as a plain
        String(64) - no CHECK on its shape. An arbitrary, non-hex,
        wrong-length string is accepted at the DB layer, exactly as the
        committed model specifies (no format enforcement was ever
        promised by EB1, and none should be invented here)."""
        db = tmp_path / "malformed_hash.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        assertion_id = _seed_source_assertion(db)
        payload = serialize_evidence_bag(EvidenceBag())
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO source_assertion_evidence_bags (source_assertion_id, evidence_bag_json, "
            "evidence_bag_hash, schema_version, created_at) VALUES (?, ?, ?, 1, datetime('now'))",
            (assertion_id, payload, "not-a-real-hash-at-all!!"),
        )
        conn.commit()
        stored = conn.execute("SELECT evidence_bag_hash FROM source_assertion_evidence_bags").fetchone()[0]
        conn.close()
        assert stored == "not-a-real-hash-at-all!!"

    def test_migration_permits_malformed_json_payload_no_db_level_json_validation(self, tmp_path):
        """The ORM model declares evidence_bag_json as a plain Text
        column - SQLite has no JSON CHECK on it (the model never declared
        one), so a syntactically-invalid JSON string is accepted at the
        DB layer. The Python-level guard is deserialize_evidence_bag()'s
        own job (proven in tests/test_evidence_bag_persistence.py), not
        the database's."""
        db = tmp_path / "malformed_json.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        assertion_id = _seed_source_assertion(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO source_assertion_evidence_bags (source_assertion_id, evidence_bag_json, "
            "evidence_bag_hash, schema_version, created_at) VALUES (?, ?, ?, 1, datetime('now'))",
            (assertion_id, "{not valid json at all", "somehash"),
        )
        conn.commit()  # DB accepts it - no JSON validity CHECK exists
        stored = conn.execute("SELECT evidence_bag_json FROM source_assertion_evidence_bags").fetchone()[0]
        conn.close()
        assert stored == "{not valid json at all"
        with pytest.raises(Exception):
            deserialize_evidence_bag(stored)  # the Python-level guard, not the DB, catches this


# ---------------------------------------------------------------------------
# W. Delete blocking (real DB, migration-created schema)
# ---------------------------------------------------------------------------


class TestDeleteFkSafety:
    def test_source_assertion_with_snapshot_delete_blocked(self, tmp_path):
        db = tmp_path / "delete_a.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        assertion_id = _seed_source_assertion(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        payload = serialize_evidence_bag(EvidenceBag())
        conn.execute(
            "INSERT INTO source_assertion_evidence_bags (source_assertion_id, evidence_bag_json, "
            "evidence_bag_hash, schema_version, created_at) VALUES (?, ?, ?, 1, datetime('now'))",
            (assertion_id, payload, hash_serialized_evidence_bag(payload)),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            conn.execute("DELETE FROM source_assertions WHERE id=?", (assertion_id,))
        conn.close()

    def test_airport_referenced_by_evaluation_delete_blocked(self, tmp_path):
        db = tmp_path / "delete_b.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        assertion_id = _seed_source_assertion(db)
        airport_id = _seed_airport(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        payload = serialize_evidence_bag(EvidenceBag())
        conn.execute(
            "INSERT INTO source_assertion_evidence_bags (source_assertion_id, evidence_bag_json, "
            "evidence_bag_hash, schema_version, created_at) VALUES (?, ?, ?, 1, datetime('now'))",
            (assertion_id, payload, hash_serialized_evidence_bag(payload)),
        )
        snap_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO identity_guard_evaluations (source_assertion_id, evidence_bag_snapshot_id, "
            "evaluated_against_airport_id, outcome, reason, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (assertion_id, snap_id, airport_id, AttachmentOutcome.ATTACH_CONFIRMED.value, "x"),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            conn.execute("DELETE FROM airports WHERE id=?", (airport_id,))
        conn.close()

    def test_snapshot_referenced_by_evaluation_delete_blocked(self, tmp_path):
        db = tmp_path / "delete_c.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        assertion_id = _seed_source_assertion(db)
        airport_id = _seed_airport(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        payload = serialize_evidence_bag(EvidenceBag())
        conn.execute(
            "INSERT INTO source_assertion_evidence_bags (source_assertion_id, evidence_bag_json, "
            "evidence_bag_hash, schema_version, created_at) VALUES (?, ?, ?, 1, datetime('now'))",
            (assertion_id, payload, hash_serialized_evidence_bag(payload)),
        )
        snap_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO identity_guard_evaluations (source_assertion_id, evidence_bag_snapshot_id, "
            "evaluated_against_airport_id, outcome, reason, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (assertion_id, snap_id, airport_id, AttachmentOutcome.ATTACH_CONFIRMED.value, "x"),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            conn.execute("DELETE FROM source_assertion_evidence_bags WHERE id=?", (snap_id,))
        conn.close()

    def test_unreferenced_unrelated_row_delete_succeeds_baseline(self, tmp_path):
        db = tmp_path / "delete_baseline.db"
        _pre_eb2_db(db)
        migration.upgrade(db)
        unrelated_airport_id = _seed_airport(db, name="Totally Unreferenced")
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("DELETE FROM airports WHERE id=?", (unrelated_airport_id,))
        conn.commit()
        remaining = conn.execute("SELECT count(*) FROM airports WHERE id=?", (unrelated_airport_id,)).fetchone()[0]
        conn.close()
        assert remaining == 0


# ---------------------------------------------------------------------------
# X. Zero-backfill / migration purity
# ---------------------------------------------------------------------------


class TestZeroBackfillAndMigrationPurity:
    def test_upgrade_never_inserts_a_row(self, tmp_path):
        db = tmp_path / "zero_backfill.db"
        _pre_eb2_db(db)
        _seed_source_assertion(db)
        _seed_source_assertion(db, raw_name="Second")
        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["counts"] == {name: 0 for name in EB2_TABLES}

    def test_migration_imports_no_business_orchestration(self):
        tree = ast.parse(inspect_module.getsource(migration))
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
        forbidden_substrings = (
            "discovery_evidence_persistence", "unknown_airport_discovery_integration",
            "evidence_attachment_guard", "evidence_bag_serialization",
            "unknown_airport_candidate_resolution", "governed_signal_creation",
            "promotion_policy", "intelligence_review",
        )
        for module_name in imported_modules:
            for forbidden in forbidden_substrings:
                assert forbidden not in module_name, f"unexpected import: {module_name}"


# ---------------------------------------------------------------------------
# Source neutrality
# ---------------------------------------------------------------------------


class TestSourceNeutrality:
    def test_no_producer_specific_dependency(self):
        source = inspect_module.getsource(migration).lower()
        for term in ("mac", "granicus", "usaspending", "faa_", "n8n", "llm", "openai", "anthropic"):
            assert term not in source, f"unexpected reference: {term!r}"


# ---------------------------------------------------------------------------
# Y. Real DB no-access
# ---------------------------------------------------------------------------


class TestNoRealDatabaseAccess:
    def test_no_real_database_path_literal_outside_docstring_or_default_constant(self):
        """The module docstring's own usage examples AND the single,
        legitimate `DEFAULT_DATABASE = Path("data/runway_safe.db")`
        constant (the same convention every migration script in this
        repository already uses, e.g. migrate_signal_disposition_d4d2.py's
        own identical constant) both legitimately reference the real
        database path - the property actually worth proving is that the
        literal appears NOWHERE ELSE (no second, hidden hardcoded path
        inside upgrade()/downgrade()/backup_database(), which correctly
        always take `database` as an explicit parameter)."""
        tree = ast.parse(inspect_module.getsource(migration))
        docstring_node = tree.body[0].value if tree.body and isinstance(tree.body[0], ast.Expr) else None

        # The one legitimate exception: the string constant nested inside
        # `DEFAULT_DATABASE = Path("data/runway_safe.db")`'s own RHS.
        default_database_string_node = None
        for stmt in tree.body:
            if (
                isinstance(stmt, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "DEFAULT_DATABASE" for t in stmt.targets)
            ):
                for inner in ast.walk(stmt.value):
                    if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                        default_database_string_node = inner
        assert default_database_string_node is not None, "DEFAULT_DATABASE assignment not found"

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node is docstring_node or node is default_database_string_node:
                    continue
                assert "runway_safe.db" not in node.value, (
                    f"unexpected 'runway_safe.db' literal found outside the docstring/DEFAULT_DATABASE "
                    f"constant: {node.value!r}"
                )

    def test_default_database_constant_points_at_the_conventional_path_only(self):
        assert str(migration.DEFAULT_DATABASE) == str(Path("data/runway_safe.db"))
