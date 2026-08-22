"""Tests for scripts/migrate_unknown_airport_candidates_uac2a.py (UAC2A,
docs/architecture/rwi-governed-new-airport-discovery-design.md).

Every test uses an isolated temp-file SQLite database (tmp_path). Nothing
in this file ever opens data/runway_safe.db - the real migration is
explicitly deferred to a separate, later, explicitly-authorized
operational step. Grep-verified (see TestNoRealDatabaseAccess). Modeled
directly on the already-proven tests/test_signal_disposition_migration.py
pattern.
"""
from __future__ import annotations

import ast
import inspect as inspect_module
import sqlite3

import pytest
from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateIndex, CreateTable

import app.models
from app.database import Base
from app.models import Airport, ReviewerAction, Signal, Source, SourceAssertion
from app.models.unknown_airport_candidate import UnknownAirportCandidate, UnknownAirportCandidateReview
from app.services.unknown_airport_candidate_persistence import (
    find_or_create_unknown_airport_candidate,
    record_unknown_airport_candidate_review,
)
import scripts.migrate_source_assertion_unknown_airport_uac2b as uac2b_migration
import scripts.migrate_unknown_airport_candidates_uac2a as migration

UAC1_TABLES = ("unknown_airport_candidates", "unknown_airport_candidate_reviews")


def _pre_uac1_db(path):
    """A full pre-UAC1 schema (every table except the two this migration
    creates) - the realistic "not yet migrated" starting state.

    UAC2B review-checkpoint fix: source_assertions now carries a forward
    FK to unknown_airport_candidates (added by UAC2B), so it can no
    longer be copied via plain Table.to_metadata() into a MetaData that
    excludes that table - SQLAlchemy's own DDL table-sort fails with
    NoReferencedTableError once create_all() tries to resolve it. Built
    via the full current schema instead, then source_assertions is
    rebuilt back to its exact pre-UAC2B shape using
    migrate_source_assertion_unknown_airport_uac2b's own frozen snapshot
    (the same helper technique
    tests/test_source_assertion_unknown_airport_migration.py already
    uses for its own "neither UAC2A nor UAC2B applied" starting state) -
    this file's own UAC1_TABLES stay excluded exactly as before."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()

    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=OFF")
    for table_name in UAC1_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table_name}")
    replacement = "source_assertions__pre_uac1_presetup"
    conn.execute(uac2b_migration._pre_uac2b_create_table_sql(replacement))
    quoted = ", ".join(f'"{c}"' for c in uac2b_migration._PRE_UAC2B_COLUMNS)
    conn.execute(f'INSERT INTO "{replacement}" ({quoted}) SELECT {quoted} FROM source_assertions')
    conn.execute("DROP TABLE source_assertions")
    conn.execute(f'ALTER TABLE "{replacement}" RENAME TO source_assertions')
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


def _seed_one_airport(db_path):
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        airport = Airport(name="Migration Test Airport", country="XX")
        s.add(airport)
        s.commit()
        airport_id = airport.id
    engine.dispose()
    return airport_id


def _fingerprint_row_kwargs(**overrides):
    kwargs = dict(raw_name="Foo Regional Airport", raw_city="Fooville", raw_country="Fictionland")
    kwargs.update(overrides)
    return kwargs


# ---------------------------------------------------------------------------
# Clean upgrade / exact schema parity
# ---------------------------------------------------------------------------


class TestCleanUpgrade:
    def test_upgrade_creates_both_tables(self, tmp_path):
        db = tmp_path / "clean.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["tables_exist"] == {
            "unknown_airport_candidates": True, "unknown_airport_candidate_reviews": True,
        }
        assert result["ready"] is True

    def test_exact_candidate_columns(self, tmp_path):
        db = tmp_path / "cols.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        cols = {row[1]: (row[2], bool(row[3]), bool(row[5])) for row in conn.execute("PRAGMA table_info(unknown_airport_candidates)")}
        conn.close()
        assert cols == {
            "id": ("INTEGER", True, True),
            "candidate_fingerprint": ("VARCHAR(64)", True, False),
            "raw_name": ("VARCHAR(200)", True, False),
            "raw_city": ("VARCHAR(100)", False, False),
            "raw_state_region": ("VARCHAR(100)", False, False),
            "raw_country": ("VARCHAR(100)", False, False),
            "raw_iata_code": ("VARCHAR(20)", False, False),
            "raw_icao_code": ("VARCHAR(20)", False, False),
            "raw_faa_lid": ("VARCHAR(20)", False, False),
            "raw_runway_designation": ("VARCHAR(20)", False, False),
            "evidence_source_locator": ("TEXT", False, False),
            "evidence_artifact_identity": ("TEXT", False, False),
            "resolved_airport_id": ("INTEGER", False, False),
            "created_at": ("DATETIME", True, False),
        }

    def test_exact_review_columns(self, tmp_path):
        db = tmp_path / "cols2.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        cols = {row[1]: (row[2], bool(row[3]), bool(row[5])) for row in conn.execute("PRAGMA table_info(unknown_airport_candidate_reviews)")}
        conn.close()
        assert cols == {
            "id": ("INTEGER", True, True),
            "candidate_id": ("INTEGER", True, False),
            "action": ("VARCHAR(30)", True, False),
            "reason": ("TEXT", True, False),
            "reviewer": ("VARCHAR(100)", True, False),
            "matched_airport_id": ("INTEGER", False, False),
            "created_at": ("DATETIME", True, False),
            "supersedes_review_id": ("INTEGER", False, False),
        }

    def test_exact_foreign_keys_via_pragma(self, tmp_path):
        db = tmp_path / "fks.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        candidate_fks = {(row[3], row[2], row[4]) for row in conn.execute("PRAGMA foreign_key_list(unknown_airport_candidates)")}
        review_fks = {(row[3], row[2], row[4]) for row in conn.execute("PRAGMA foreign_key_list(unknown_airport_candidate_reviews)")}
        conn.close()
        assert candidate_fks == {("resolved_airport_id", "airports", "id")}
        assert review_fks == {
            ("candidate_id", "unknown_airport_candidates", "id"),
            ("matched_airport_id", "airports", "id"),
            ("supersedes_review_id", "unknown_airport_candidate_reviews", "id"),
        }

    def test_no_on_delete_cascade(self, tmp_path):
        db = tmp_path / "nocascade.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        rows = conn.execute("PRAGMA foreign_key_list(unknown_airport_candidate_reviews)").fetchall()
        conn.close()
        for row in rows:
            assert row[5] == "NO ACTION"  # on_delete column
            assert row[6] == "NO ACTION"  # on_update column

    def test_fingerprint_unique_constraint_via_raw_sql(self, tmp_path):
        db = tmp_path / "unique.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO unknown_airport_candidates (candidate_fingerprint, raw_name, created_at) "
            "VALUES ('deadbeef', 'Foo Regional Airport', '2026-01-01')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            conn.execute(
                "INSERT INTO unknown_airport_candidates (candidate_fingerprint, raw_name, created_at) "
                "VALUES ('deadbeef', 'Foo Regional Airport (dup)', '2026-01-02')"
            )
        conn.rollback()
        conn.close()

    def test_row_counts_zero_after_upgrade(self, tmp_path):
        db = tmp_path / "zero.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["counts"] == {"unknown_airport_candidates": 0, "unknown_airport_candidate_reviews": 0}

    def test_no_trigger_objects_created_by_migration(self, tmp_path):
        db = tmp_path / "notrigger.db"
        _pre_uac1_db(db)
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
        _pre_uac1_db(db)
        migration.upgrade(db)
        before = migration.inspect(db)
        migration.upgrade(db)  # must not raise
        after = migration.inspect(db)
        assert before == after

    def test_second_upgrade_preserves_rows(self, tmp_path):
        db = tmp_path / "idem2.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        airport_id = _seed_one_airport(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = find_or_create_unknown_airport_candidate(s, **_fingerprint_row_kwargs()).candidate
            s.commit()
            record_unknown_airport_candidate_review(
                s, candidate, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
                matched_airport_id=airport_id,
            )
            s.commit()
        engine.dispose()

        migration.upgrade(db)  # idempotent re-run after real data exists
        result = migration.inspect(db)
        assert result["counts"] == {"unknown_airport_candidates": 1, "unknown_airport_candidate_reviews": 1}


# ---------------------------------------------------------------------------
# Partial-schema / incompatible-schema safety
# ---------------------------------------------------------------------------


class TestPartialAndIncompatibleSchema:
    def test_neither_table_exists_full_create(self, tmp_path):
        db = tmp_path / "neither.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        assert migration.inspect(db)["ready"] is True

    def test_candidate_exists_correctly_review_absent_safe_completion(self, tmp_path):
        db = tmp_path / "candidate_only.db"
        _pre_uac1_db(db)
        _create_table_raw(db, "unknown_airport_candidates")
        before = migration.inspect(db)
        assert before["tables_exist"] == {
            "unknown_airport_candidates": True, "unknown_airport_candidate_reviews": False,
        }
        migration.upgrade(db)
        after = migration.inspect(db)
        assert after["ready"] is True

    def test_review_exists_correctly_candidate_absent_safe_completion(self, tmp_path):
        """A correctly-shaped review table with no candidate table present
        yet is unusual (its FK target didn't exist at CREATE TABLE time -
        SQLite permits this since it does not validate FK targets at
        creation) but not incompatible: each table is independently
        verified against the current model."""
        db = tmp_path / "review_only.db"
        _pre_uac1_db(db)
        _create_table_raw(db, "unknown_airport_candidate_reviews")
        before = migration.inspect(db)
        assert before["tables_exist"] == {
            "unknown_airport_candidates": False, "unknown_airport_candidate_reviews": True,
        }
        migration.upgrade(db)
        after = migration.inspect(db)
        assert after["ready"] is True

    def test_wrong_columns_fails_closed(self, tmp_path):
        db = tmp_path / "wrongcols.db"
        _pre_uac1_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE unknown_airport_candidates (id INTEGER PRIMARY KEY, wrong_column TEXT)")
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError, match="do not match the expected UAC1 schema"):
            migration.upgrade(db)
        # Refusing must not have created the second table either - the whole
        # operation is one transaction.
        result = migration.inspect(db)
        assert result["tables_exist"]["unknown_airport_candidate_reviews"] is False

    def test_wrong_foreign_key_target_fails_closed(self, tmp_path):
        db = tmp_path / "wrongfk.db"
        _pre_uac1_db(db)
        conn = sqlite3.connect(str(db))
        # Right column shape, but resolved_airport_id points at the wrong table.
        conn.execute(
            "CREATE TABLE unknown_airport_candidates ("
            "id INTEGER NOT NULL PRIMARY KEY, candidate_fingerprint VARCHAR(64) NOT NULL, "
            "raw_name VARCHAR(200) NOT NULL, raw_city VARCHAR(100), raw_state_region VARCHAR(100), "
            "raw_country VARCHAR(100), raw_iata_code VARCHAR(20), raw_icao_code VARCHAR(20), "
            "raw_faa_lid VARCHAR(20), raw_runway_designation VARCHAR(20), evidence_source_locator TEXT, "
            "evidence_artifact_identity TEXT, resolved_airport_id INTEGER, created_at DATETIME NOT NULL, "
            "CONSTRAINT uq_unknown_airport_candidates_fingerprint UNIQUE (candidate_fingerprint), "
            "FOREIGN KEY(resolved_airport_id) REFERENCES signals (id))"
        )
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError, match="foreign keys do not match"):
            migration.upgrade(db)

    def test_misleading_same_name_incompatible_table_never_dropped_or_rebuilt(self, tmp_path):
        db = tmp_path / "misleading.db"
        _pre_uac1_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE unknown_airport_candidate_reviews (id INTEGER PRIMARY KEY, totally_different TEXT)")
        conn.execute("INSERT INTO unknown_airport_candidate_reviews (totally_different) VALUES ('irreplaceable data')")
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError):
            migration.upgrade(db)
        # The unrelated, pre-existing (if oddly-named) table's data must survive untouched.
        conn = sqlite3.connect(str(db))
        row = conn.execute("SELECT totally_different FROM unknown_airport_candidate_reviews").fetchone()
        conn.close()
        assert row[0] == "irreplaceable data"

    def test_missing_named_check_constraint_fails_closed(self, tmp_path):
        db = tmp_path / "noconstraint.db"
        _pre_uac1_db(db)
        conn = sqlite3.connect(str(db))
        # Right columns/FK, but the CHECK constraints were never actually
        # added (e.g. a hand-edited or manually-restored table).
        conn.execute(
            "CREATE TABLE unknown_airport_candidate_reviews ("
            "id INTEGER NOT NULL PRIMARY KEY, candidate_id INTEGER NOT NULL, action VARCHAR(30) NOT NULL, "
            "reason TEXT NOT NULL, reviewer VARCHAR(100) NOT NULL, matched_airport_id INTEGER, "
            "created_at DATETIME NOT NULL, supersedes_review_id INTEGER, "
            "FOREIGN KEY(candidate_id) REFERENCES unknown_airport_candidates (id), "
            "FOREIGN KEY(matched_airport_id) REFERENCES airports (id), "
            "FOREIGN KEY(supersedes_review_id) REFERENCES unknown_airport_candidate_reviews (id))"
        )
        conn.commit()
        conn.close()
        _create_table_raw(db, "unknown_airport_candidates")
        with pytest.raises(migration.IncompatibleExistingSchemaError, match="missing expected named constraint"):
            migration.upgrade(db)

    def test_extra_unexpected_column_fails_closed(self, tmp_path):
        db = tmp_path / "extracol.db"
        _pre_uac1_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE unknown_airport_candidates ("
            "id INTEGER NOT NULL PRIMARY KEY, candidate_fingerprint VARCHAR(64) NOT NULL, "
            "raw_name VARCHAR(200) NOT NULL, raw_city VARCHAR(100), raw_state_region VARCHAR(100), "
            "raw_country VARCHAR(100), raw_iata_code VARCHAR(20), raw_icao_code VARCHAR(20), "
            "raw_faa_lid VARCHAR(20), raw_runway_designation VARCHAR(20), evidence_source_locator TEXT, "
            "evidence_artifact_identity TEXT, resolved_airport_id INTEGER, created_at DATETIME NOT NULL, "
            "unexpected_extra_field TEXT, "
            "CONSTRAINT uq_unknown_airport_candidates_fingerprint UNIQUE (candidate_fingerprint), "
            "FOREIGN KEY(resolved_airport_id) REFERENCES airports (id))"
        )
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError, match="columns do not match"):
            migration.upgrade(db)

    def test_wrong_nullability_fails_closed(self, tmp_path):
        db = tmp_path / "wrongnull.db"
        _pre_uac1_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE unknown_airport_candidates ("
            "id INTEGER NOT NULL PRIMARY KEY, candidate_fingerprint VARCHAR(64) NOT NULL, "
            "raw_name VARCHAR(200), raw_city VARCHAR(100), raw_state_region VARCHAR(100), "  # raw_name nullable - wrong
            "raw_country VARCHAR(100), raw_iata_code VARCHAR(20), raw_icao_code VARCHAR(20), "
            "raw_faa_lid VARCHAR(20), raw_runway_designation VARCHAR(20), evidence_source_locator TEXT, "
            "evidence_artifact_identity TEXT, resolved_airport_id INTEGER, created_at DATETIME NOT NULL, "
            "CONSTRAINT uq_unknown_airport_candidates_fingerprint UNIQUE (candidate_fingerprint), "
            "FOREIGN KEY(resolved_airport_id) REFERENCES airports (id))"
        )
        conn.commit()
        conn.close()
        with pytest.raises(migration.IncompatibleExistingSchemaError, match="columns do not match"):
            migration.upgrade(db)

    def test_wrong_type_fails_closed(self, tmp_path):
        db = tmp_path / "wrongtype.db"
        _pre_uac1_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE unknown_airport_candidate_reviews ("
            "id INTEGER NOT NULL PRIMARY KEY, candidate_id INTEGER NOT NULL, action INTEGER NOT NULL, "  # action wrong type
            "reason TEXT NOT NULL, reviewer VARCHAR(100) NOT NULL, matched_airport_id INTEGER, "
            "created_at DATETIME NOT NULL, supersedes_review_id INTEGER, "
            "CONSTRAINT ck_unknown_airport_candidate_reviews_action CHECK (action > 0), "
            "CONSTRAINT ck_unknown_airport_candidate_reviews_match_target_required CHECK (1), "
            "CONSTRAINT ck_unknown_airport_candidate_reviews_match_target_only_for_match CHECK (1), "
            "FOREIGN KEY(candidate_id) REFERENCES unknown_airport_candidates (id), "
            "FOREIGN KEY(matched_airport_id) REFERENCES airports (id), "
            "FOREIGN KEY(supersedes_review_id) REFERENCES unknown_airport_candidate_reviews (id))"
        )
        conn.commit()
        conn.close()
        _create_table_raw(db, "unknown_airport_candidates")
        with pytest.raises(migration.IncompatibleExistingSchemaError, match="columns do not match"):
            migration.upgrade(db)

    def test_missing_expected_index_fails_closed(self, tmp_path):
        """UAC2A review-checkpoint fix: a table with every expected
        column/FK/named-constraint correct, but missing one or more of
        the ORM model's own plain (non-UNIQUE-backed) indexes, must not
        be silently accepted - it does not genuinely match the ORM model,
        and upgrade() would never create the missing index for an
        already-existing table (the index-creation loop only runs for a
        table it creates fresh)."""
        db = tmp_path / "missingindex.db"
        _pre_uac1_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE unknown_airport_candidates ("
            "id INTEGER NOT NULL PRIMARY KEY, candidate_fingerprint VARCHAR(64) NOT NULL, "
            "raw_name VARCHAR(200) NOT NULL, raw_city VARCHAR(100), raw_state_region VARCHAR(100), "
            "raw_country VARCHAR(100), raw_iata_code VARCHAR(20), raw_icao_code VARCHAR(20), "
            "raw_faa_lid VARCHAR(20), raw_runway_designation VARCHAR(20), evidence_source_locator TEXT, "
            "evidence_artifact_identity TEXT, resolved_airport_id INTEGER, created_at DATETIME NOT NULL, "
            "CONSTRAINT uq_unknown_airport_candidates_fingerprint UNIQUE (candidate_fingerprint), "
            "FOREIGN KEY(resolved_airport_id) REFERENCES airports (id))"
            # Deliberately no CREATE INDEX statements at all - the UNIQUE
            # constraint's own autoindex exists, but neither
            # ix_..._candidate_fingerprint nor ix_..._resolved_airport_id does.
        )
        conn.commit()
        conn.close()

        result = migration.inspect(db)
        assert result["matches_expected_schema"]["unknown_airport_candidates"] is False
        assert result["ready"] is False

        with pytest.raises(migration.IncompatibleExistingSchemaError, match="missing expected index"):
            migration.upgrade(db)

    def test_extra_unexpected_index_does_not_cause_false_rejection(self, tmp_path):
        """The inverse of the case above: an existing, otherwise
        correctly-shaped table carrying one extra, unrelated index (e.g.
        a DBA-added performance index on raw_country) must NOT be treated
        as incompatible - only a MISSING expected index is a problem, an
        extra one is harmless."""
        db = tmp_path / "extraindex.db"
        _pre_uac1_db(db)
        _create_table_raw(db, "unknown_airport_candidates")
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE INDEX ix_unknown_airport_candidates_raw_country_extra "
            "ON unknown_airport_candidates (raw_country)"
        )
        conn.commit()
        conn.close()
        _create_table_raw(db, "unknown_airport_candidate_reviews")

        result = migration.inspect(db)
        assert result["matches_expected_schema"]["unknown_airport_candidates"] is True
        assert result["ready"] is True
        migration.upgrade(db)  # must not raise
        assert migration.inspect(db)["ready"] is True


# ---------------------------------------------------------------------------
# inspect()/upgrade() parity
# ---------------------------------------------------------------------------


class TestInspectTrustworthiness:
    def test_inspect_never_reports_ready_for_a_table_upgrade_would_refuse(self, tmp_path):
        db = tmp_path / "sneaky.db"
        _pre_uac1_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE unknown_airport_candidates ("
            "id INTEGER NOT NULL PRIMARY KEY, candidate_fingerprint VARCHAR(64) NOT NULL, "
            "raw_name VARCHAR(200) NOT NULL, raw_city VARCHAR(100), raw_state_region VARCHAR(100), "
            "raw_country VARCHAR(100), raw_iata_code VARCHAR(20), raw_icao_code VARCHAR(20), "
            "raw_faa_lid VARCHAR(20), raw_runway_designation VARCHAR(20), evidence_source_locator TEXT, "
            "evidence_artifact_identity TEXT, resolved_airport_id INTEGER, created_at DATETIME NOT NULL, "
            "extra_unexpected_column TEXT, "
            "FOREIGN KEY(resolved_airport_id) REFERENCES airports (id))"  # missing UNIQUE constraint
        )
        conn.commit()
        conn.close()
        _create_table_raw(db, "unknown_airport_candidate_reviews")  # correctly shaped (candidate table doesn't exist yet, fine - FK target validated lazily)

        result = migration.inspect(db)
        assert result["ready"] is False
        assert result["matches_expected_schema"]["unknown_airport_candidates"] is False

        with pytest.raises(migration.IncompatibleExistingSchemaError):
            migration.upgrade(db)

    def test_inspect_ready_true_only_for_genuinely_matching_schema(self, tmp_path):
        db = tmp_path / "healthy.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["ready"] is True
        assert result["matches_expected_schema"] == {
            "unknown_airport_candidates": True, "unknown_airport_candidate_reviews": True,
        }


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


class TestDowngrade:
    def test_downgrade_empty_success(self, tmp_path):
        db = tmp_path / "downgrade_empty.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        migration.downgrade(db)
        result = migration.inspect(db)
        assert result["tables_exist"] == {
            "unknown_airport_candidates": False, "unknown_airport_candidate_reviews": False,
        }

    def test_downgrade_restores_pre_uac1_schema(self, tmp_path):
        db = tmp_path / "restore.db"
        _pre_uac1_db(db)
        before_tables = set(sqlite3.connect(str(db)).execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        migration.upgrade(db)
        migration.downgrade(db)
        after_tables = set(sqlite3.connect(str(db)).execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        assert before_tables == after_tables

    def test_downgrade_candidate_rows_refused(self, tmp_path):
        db = tmp_path / "candidate_rows.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            find_or_create_unknown_airport_candidate(s, **_fingerprint_row_kwargs())
            s.commit()
        engine.dispose()
        with pytest.raises(RuntimeError, match=r"downgrade\(\) refused"):
            migration.downgrade(db)
        assert migration.inspect(db)["tables_exist"]["unknown_airport_candidates"] is True

    def test_downgrade_review_rows_refused(self, tmp_path):
        db = tmp_path / "review_rows.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        airport_id = _seed_one_airport(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = find_or_create_unknown_airport_candidate(s, **_fingerprint_row_kwargs()).candidate
            s.commit()
            record_unknown_airport_candidate_review(
                s, candidate, action="DEFER", reason="x", reviewer="human:x",
            )
            s.commit()
        engine.dispose()
        with pytest.raises(RuntimeError, match=r"downgrade\(\) refused"):
            migration.downgrade(db)
        assert migration.inspect(db)["tables_exist"]["unknown_airport_candidate_reviews"] is True

    def test_downgrade_both_nonempty_refused(self, tmp_path):
        db = tmp_path / "both_rows.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        airport_id = _seed_one_airport(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = find_or_create_unknown_airport_candidate(s, **_fingerprint_row_kwargs()).candidate
            s.commit()
            record_unknown_airport_candidate_review(
                s, candidate, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
                matched_airport_id=airport_id,
            )
            s.commit()
        engine.dispose()
        with pytest.raises(RuntimeError, match=r"downgrade\(\) refused"):
            migration.downgrade(db)

    def test_downgrade_refusal_atomicity_schema_and_rows_intact(self, tmp_path):
        db = tmp_path / "atomic.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            find_or_create_unknown_airport_candidate(s, **_fingerprint_row_kwargs())
            s.commit()
        engine.dispose()

        before = migration.inspect(db)
        with pytest.raises(RuntimeError):
            migration.downgrade(db)
        after = migration.inspect(db)
        assert before == after  # no partial DROP, no row loss, nothing changed

    def test_round_trip_upgrade_downgrade_upgrade(self, tmp_path):
        db = tmp_path / "roundtrip.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        migration.downgrade(db)
        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["ready"] is True
        assert result["counts"] == {"unknown_airport_candidates": 0, "unknown_airport_candidate_reviews": 0}


class TestDdlAtomicity:
    """Both upgrade() and downgrade() wrap every CREATE/DROP TABLE inside
    one BEGIN IMMEDIATE ... COMMIT block. These tests inject a real
    failure between the first and second table operation and confirm
    SQLite's own rollback actually undoes the first one too - proven
    directly, matching migrate_signal_disposition_d4d2.py's own precedent."""

    def test_upgrade_failure_between_tables_leaves_neither_table_created(self, tmp_path, monkeypatch):
        db = tmp_path / "upgrade_atomic.db"
        _pre_uac1_db(db)

        real_table_exists = migration._table_exists

        def _crash_before_second_table(connection, name):
            result = real_table_exists(connection, name)
            if name == "unknown_airport_candidate_reviews" and not result:
                raise RuntimeError("simulated crash before creating the second table")
            return result

        monkeypatch.setattr(migration, "_table_exists", _crash_before_second_table)
        with pytest.raises(RuntimeError, match="simulated crash"):
            migration.upgrade(db)
        monkeypatch.undo()

        result = migration.inspect(db)
        assert result["tables_exist"] == {
            "unknown_airport_candidates": False, "unknown_airport_candidate_reviews": False,
        }

    def test_downgrade_failure_between_tables_leaves_both_tables_intact(self, tmp_path, monkeypatch):
        db = tmp_path / "downgrade_atomic.db"
        _pre_uac1_db(db)
        migration.upgrade(db)

        real_table_exists = migration._table_exists

        def _crash_before_second_drop(connection, name):
            result = real_table_exists(connection, name)
            if name == "unknown_airport_candidates" and result:
                raise RuntimeError("simulated crash before dropping the second (parent) table")
            return result

        monkeypatch.setattr(migration, "_table_exists", _crash_before_second_drop)
        with pytest.raises(RuntimeError, match="simulated crash"):
            migration.downgrade(db)
        monkeypatch.undo()

        result = migration.inspect(db)
        assert result["tables_exist"] == {
            "unknown_airport_candidates": True, "unknown_airport_candidate_reviews": True,
        }
        assert result["ready"] is True


# ---------------------------------------------------------------------------
# Write authorization / backup / wrong-DB isolation
# ---------------------------------------------------------------------------


class TestWriteGate:
    def test_main_requires_allow_database_write(self, tmp_path):
        db = tmp_path / "gate.db"
        _pre_uac1_db(db)
        with pytest.raises(SystemExit):
            migration.main(["--database", str(db)])
        assert migration.inspect(db)["tables_exist"]["unknown_airport_candidates"] is False

    def test_main_requires_allow_database_write_for_downgrade_mode_too(self, tmp_path):
        """§15/§21: both command modes must be independently proven, not
        just the (default) upgrade path."""
        db = tmp_path / "gate_downgrade.db"
        _pre_uac1_db(db)
        migration.upgrade(db)  # direct call, bypassing main(), to set up a real migrated state
        with pytest.raises(SystemExit):
            migration.main(["--database", str(db), "--downgrade"])
        # Refused before any drop - both tables must still exist.
        assert migration.inspect(db)["tables_exist"] == {
            "unknown_airport_candidates": True, "unknown_airport_candidate_reviews": True,
        }

    def test_main_no_sessionlocal_reference_ast(self):
        tree = ast.parse(inspect_module.getsource(migration))
        code_identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert "SessionLocal" not in code_identifiers
        assert "create_engine" not in code_identifiers  # raw sqlite3 only, never an ORM engine

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

    def test_malformed_database_file_fails_closed(self, tmp_path):
        malformed = tmp_path / "not_a_real_database.db"
        malformed.write_bytes(b"this is not a sqlite file at all")
        with pytest.raises(sqlite3.DatabaseError):
            migration.upgrade(malformed)


class TestBackup:
    def test_backup_created_before_write(self, tmp_path):
        db = tmp_path / "backup_src.db"
        _pre_uac1_db(db)
        backup_dir = tmp_path / "backups"
        before_bytes = db.read_bytes()

        result = migration.backup_database(db, backup_directory=backup_dir)
        assert result.exists()
        assert result.read_bytes() == before_bytes

    def test_backup_is_independently_readable_pre_uac1_schema(self, tmp_path):
        db = tmp_path / "backup_read.db"
        _pre_uac1_db(db)
        backup_dir = tmp_path / "backups"
        backup_path = migration.backup_database(db, backup_directory=backup_dir)

        conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "unknown_airport_candidates" not in tables
        assert "unknown_airport_candidate_reviews" not in tables
        assert "airports" in tables  # pre-existing domain table present

    def test_backup_matches_source_before_mutation(self, tmp_path):
        db = tmp_path / "backup_match.db"
        _pre_uac1_db(db)
        _seed_one_airport(db)
        backup_dir = tmp_path / "backups"
        backup_path = migration.backup_database(db, backup_directory=backup_dir)

        migration.upgrade(db)  # mutates the source after backup was taken

        conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
        count = conn.execute("SELECT count(*) FROM airports").fetchone()[0]
        has_uac1 = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='unknown_airport_candidates'").fetchone())
        conn.close()
        assert count == 1
        assert has_uac1 is False  # backup is frozen at pre-migration state

    def test_backup_passes_integrity_check(self, tmp_path):
        db = tmp_path / "backup_integrity.db"
        _pre_uac1_db(db)
        _seed_one_airport(db)
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
        _pre_uac1_db(db)
        code = migration.main(["--database", str(db), "--allow-database-write"])
        assert code == 0
        captured = capsys.readouterr()
        assert "Backup created:" in captured.out


class TestWrongDatabaseIsolation:
    def test_migration_touches_only_the_named_database(self, tmp_path):
        target = tmp_path / "target.db"
        protected = tmp_path / "protected.db"
        _pre_uac1_db(target)
        _pre_uac1_db(protected)
        before_protected = protected.read_bytes()

        migration.upgrade(target)

        assert protected.read_bytes() == before_protected
        assert migration.inspect(target)["ready"] is True
        assert migration.inspect(protected)["tables_exist"]["unknown_airport_candidates"] is False


# ---------------------------------------------------------------------------
# Existing-data preservation
# ---------------------------------------------------------------------------


class TestExistingDataPreservation:
    def test_representative_domain_rows_unchanged_after_upgrade(self, tmp_path):
        """source_assertions is seeded/read via raw SQL, not the ORM
        SourceAssertion model - the pre-UAC1 fixture database is also
        genuinely pre-UAC2B shaped (UAC2A's own migration never touches
        source_assertions at all, so it remains pre-UAC2B shaped both
        before AND after migration.upgrade() in this test), and the ORM
        model now unconditionally declares the UAC2B-only
        unknown_airport_candidate_id column."""
        db = tmp_path / "preserve.db"
        _pre_uac1_db(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = Airport(name="Preserve Airport", country="XX", iata_code="PPP")
            source = Source(title="Preserve Source", source_type="official")
            s.add_all([airport, source])
            s.commit()
            signal = Signal(airport=airport, title="Preserve Signal", category="replacement", confidence="high", source_id=source.id)
            s.add(signal)
            s.commit()
            airport_id, source_id, signal_id = airport.id, source.id, signal.id
        engine.dispose()

        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO source_assertions (source_id, airport_id, assertion_type, source_record_identifier, "
            "signal_id, identity_guard_decision, intelligence_review_decision, promotion_policy_decision, "
            "evidence_quality, review_state, created_at) VALUES (?, ?, 'project_construction', 'preserve-1', ?, "
            "'ATTACH_CONFIRMED', 'REVIEW_REQUIRED', 'HUMAN_REVIEW_REQUIRED', 'unverified_candidate', "
            "'unreviewed', '2026-01-01')", (source_id, airport_id, signal_id),
        )
        assertion_id = conn.execute("SELECT id FROM source_assertions").fetchone()[0]
        conn.execute(
            "INSERT INTO reviewer_actions (source_assertion_id, action, reason, reviewer, created_at) "
            "VALUES (?, 'APPROVE_SIGNAL', 'x', 'human:x', '2026-01-01')", (assertion_id,),
        )
        conn.commit()
        snapshot_source_assertions = conn.execute(
            "SELECT id, signal_id, identity_guard_decision FROM source_assertions"
        ).fetchall()
        conn.close()

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            snapshot = {
                "airports": [(r.id, r.name, r.country, r.iata_code) for r in s.query(Airport).all()],
                "sources": [(r.id, r.title, r.source_type) for r in s.query(Source).all()],
                "signals": [(r.id, r.title, r.category, r.confidence, r.source_id) for r in s.query(Signal).all()],
                "source_assertions": snapshot_source_assertions,
                "reviewer_actions": [(r.id, r.action, r.reason, r.reviewer) for r in s.query(ReviewerAction).all()],
            }
        engine.dispose()

        migration.upgrade(db)

        conn = sqlite3.connect(str(db))
        after_source_assertions = conn.execute(
            "SELECT id, signal_id, identity_guard_decision FROM source_assertions"
        ).fetchall()
        conn.close()

        engine2 = create_engine(f"sqlite:///{db}")
        with Session(engine2) as s:
            after = {
                "airports": [(r.id, r.name, r.country, r.iata_code) for r in s.query(Airport).all()],
                "sources": [(r.id, r.title, r.source_type) for r in s.query(Source).all()],
                "signals": [(r.id, r.title, r.category, r.confidence, r.source_id) for r in s.query(Signal).all()],
                "source_assertions": after_source_assertions,
                "reviewer_actions": [(r.id, r.action, r.reason, r.reviewer) for r in s.query(ReviewerAction).all()],
            }
        engine2.dispose()
        assert after == snapshot

    def test_only_the_two_new_empty_tables_appear(self, tmp_path):
        db = tmp_path / "onlytwo.db"
        _pre_uac1_db(db)
        before = set(sqlite3.connect(str(db)).execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        migration.upgrade(db)
        after = set(sqlite3.connect(str(db)).execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        new_tables = {t[0] for t in after - before}
        assert new_tables == {"unknown_airport_candidates", "unknown_airport_candidate_reviews"}


# ---------------------------------------------------------------------------
# Model/migration (ORM/service) parity
# ---------------------------------------------------------------------------


class TestModelMigrationParity:
    def test_fresh_session_can_read_and_write_via_uac1_service(self, tmp_path):
        db = tmp_path / "parity.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        airport_id = _seed_one_airport(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = find_or_create_unknown_airport_candidate(s, **_fingerprint_row_kwargs()).candidate
            s.commit()
            review = record_unknown_airport_candidate_review(
                s, candidate, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
                matched_airport_id=airport_id,
            )
            s.commit()
            assert s.query(UnknownAirportCandidate).count() == 1
            assert s.query(UnknownAirportCandidateReview).count() == 1
            assert review.matched_airport_id == airport_id
        engine.dispose()

    def test_fingerprint_uniqueness_still_works_post_migration(self, tmp_path):
        db = tmp_path / "parity_fp.db"
        _pre_uac1_db(db)
        migration.upgrade(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            first = find_or_create_unknown_airport_candidate(s, **_fingerprint_row_kwargs())
            s.commit()
            second = find_or_create_unknown_airport_candidate(s, **_fingerprint_row_kwargs())
            s.commit()
            assert second.created is False
            assert second.candidate.id == first.candidate.id
            assert s.query(UnknownAirportCandidate).count() == 1
        engine.dispose()

    def test_candidate_field_immutability_still_works_post_migration(self, tmp_path):
        """The UAC1 review-checkpoint fix (before_update immutability
        guard) is a Python-level ORM event listener, not persisted
        schema - confirm it still fires correctly against a migrated (not
        create_all'd) DB."""
        db = tmp_path / "parity_immut.db"
        _pre_uac1_db(db)
        migration.upgrade(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = find_or_create_unknown_airport_candidate(s, **_fingerprint_row_kwargs()).candidate
            s.commit()
            candidate.raw_name = "Changed Name"
            with pytest.raises(ValueError, match="immutable after creation"):
                s.commit()
            s.rollback()
        engine.dispose()

    def test_review_append_only_guard_still_works_post_migration(self, tmp_path):
        db = tmp_path / "parity_append.db"
        _pre_uac1_db(db)
        migration.upgrade(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = find_or_create_unknown_airport_candidate(s, **_fingerprint_row_kwargs()).candidate
            s.commit()
            review = record_unknown_airport_candidate_review(s, candidate, action="DEFER", reason="x", reviewer="human:x")
            s.commit()
            review.reason = "changed"
            with pytest.raises(ValueError, match="immutable"):
                s.commit()
            s.rollback()
        engine.dispose()

    def test_review_check_vocabulary_still_enforced_post_migration_via_service(self, tmp_path):
        db = tmp_path / "parity_vocab.db"
        _pre_uac1_db(db)
        migration.upgrade(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = find_or_create_unknown_airport_candidate(s, **_fingerprint_row_kwargs()).candidate
            s.commit()
            with pytest.raises(ValueError, match="action must be one of"):
                record_unknown_airport_candidate_review(s, candidate, action="NOT_REAL", reason="x", reviewer="human:x")
        engine.dispose()


# ---------------------------------------------------------------------------
# Raw-SQL attacks (CHECK/UNIQUE/FK bypassing the ORM entirely, against the
# genuinely MIGRATED schema)
# ---------------------------------------------------------------------------


class TestRawSqlAttacks:
    def test_invalid_action_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "rawcheck.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO unknown_airport_candidates (candidate_fingerprint, raw_name, created_at) "
            "VALUES ('fp1', 'Foo Regional', '2026-01-01')"
        )
        candidate_id = conn.execute("SELECT id FROM unknown_airport_candidates").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                "INSERT INTO unknown_airport_candidate_reviews (candidate_id, action, reason, reviewer, created_at) "
                "VALUES (?, 'NOT_A_REAL_ACTION', 'x', 'human:x', '2026-01-01')", (candidate_id,),
            )
        conn.rollback()
        conn.close()

    def test_lowercase_action_rejected_raw_sql(self, tmp_path):
        """UAC2A review §12: the CHECK constraint's `action IN (...)` list
        is a case-sensitive SQLite string comparison - 'defer' must be
        rejected exactly like any other value outside the four exact
        uppercase strings, proven directly against raw SQL (not merely
        the service's own Python-level exact-match check, already tested
        in test_unknown_airport_candidate_persistence.py)."""
        db = tmp_path / "lowercase.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO unknown_airport_candidates (candidate_fingerprint, raw_name, created_at) "
            "VALUES ('fp1', 'Foo Regional', '2026-01-01')"
        )
        candidate_id = conn.execute("SELECT id FROM unknown_airport_candidates").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                "INSERT INTO unknown_airport_candidate_reviews (candidate_id, action, reason, reviewer, created_at) "
                "VALUES (?, 'defer', 'x', 'human:x', '2026-01-01')", (candidate_id,),
            )
        conn.rollback()
        conn.close()

    def test_invalid_resolved_airport_id_fk_rejected_raw_sql(self, tmp_path):
        """UAC2A review §12: the candidate table's own FK
        (resolved_airport_id -> airports.id) is enforced by SQLite
        itself, independent of the fact that no UAC1/UAC2A code ever
        writes to this column today."""
        db = tmp_path / "badresolvedfk.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute(
                "INSERT INTO unknown_airport_candidates "
                "(candidate_fingerprint, raw_name, resolved_airport_id, created_at) "
                "VALUES ('fp1', 'Foo Regional', 999999, '2026-01-01')"
            )
        conn.rollback()
        conn.close()

    def test_duplicate_fingerprint_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "rawdup.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO unknown_airport_candidates (candidate_fingerprint, raw_name, created_at) "
            "VALUES ('fp1', 'Foo Regional', '2026-01-01')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            conn.execute(
                "INSERT INTO unknown_airport_candidates (candidate_fingerprint, raw_name, created_at) "
                "VALUES ('fp1', 'Foo Regional (dup)', '2026-01-02')"
            )
        conn.rollback()
        conn.close()

    def test_review_referencing_nonexistent_candidate_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "nocandidate.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute(
                "INSERT INTO unknown_airport_candidate_reviews (candidate_id, action, reason, reviewer, created_at) "
                "VALUES (999999, 'DEFER', 'x', 'human:x', '2026-01-01')"
            )
        conn.rollback()
        conn.close()

    def test_review_referencing_nonexistent_matched_airport_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "noairport.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO unknown_airport_candidates (candidate_fingerprint, raw_name, created_at) "
            "VALUES ('fp1', 'Foo Regional', '2026-01-01')"
        )
        candidate_id = conn.execute("SELECT id FROM unknown_airport_candidates").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute(
                "INSERT INTO unknown_airport_candidate_reviews "
                "(candidate_id, action, reason, reviewer, matched_airport_id, created_at) "
                "VALUES (?, 'MATCH_EXISTING_AIRPORT', 'x', 'human:x', 999999, '2026-01-01')", (candidate_id,),
            )
        conn.rollback()
        conn.close()

    def test_review_referencing_nonexistent_supersedes_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "nosupersedes.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO unknown_airport_candidates (candidate_fingerprint, raw_name, created_at) "
            "VALUES ('fp1', 'Foo Regional', '2026-01-01')"
        )
        candidate_id = conn.execute("SELECT id FROM unknown_airport_candidates").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute(
                "INSERT INTO unknown_airport_candidate_reviews "
                "(candidate_id, action, reason, reviewer, supersedes_review_id, created_at) "
                "VALUES (?, 'DEFER', 'x', 'human:x', 999999, '2026-01-01')", (candidate_id,),
            )
        conn.rollback()
        conn.close()

    def test_match_action_without_target_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "matchnotarget.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO unknown_airport_candidates (candidate_fingerprint, raw_name, created_at) "
            "VALUES ('fp1', 'Foo Regional', '2026-01-01')"
        )
        candidate_id = conn.execute("SELECT id FROM unknown_airport_candidates").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                "INSERT INTO unknown_airport_candidate_reviews (candidate_id, action, reason, reviewer, created_at) "
                "VALUES (?, 'MATCH_EXISTING_AIRPORT', 'x', 'human:x', '2026-01-01')", (candidate_id,),
            )
        conn.rollback()
        conn.close()

    def test_target_on_non_match_action_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "targetnotmatch.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        airport_id = _seed_one_airport(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO unknown_airport_candidates (candidate_fingerprint, raw_name, created_at) "
            "VALUES ('fp1', 'Foo Regional', '2026-01-01')"
        )
        candidate_id = conn.execute("SELECT id FROM unknown_airport_candidates").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                "INSERT INTO unknown_airport_candidate_reviews "
                "(candidate_id, action, reason, reviewer, matched_airport_id, created_at) "
                "VALUES (?, 'DEFER', 'x', 'human:x', ?, '2026-01-01')", (candidate_id, airport_id),
            )
        conn.rollback()
        conn.close()

    def test_null_raw_name_rejected_raw_sql(self, tmp_path):
        db = tmp_path / "nullname.db"
        _pre_uac1_db(db)
        migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL constraint failed"):
            conn.execute(
                "INSERT INTO unknown_airport_candidates (candidate_fingerprint, raw_name, created_at) "
                "VALUES ('fp1', NULL, '2026-01-01')"
            )
        conn.rollback()
        conn.close()


# ---------------------------------------------------------------------------
# Zero backfill / no discovery business logic
# ---------------------------------------------------------------------------


class TestZeroBackfill:
    def test_upgrade_never_inserts_a_row_even_with_real_airport_data_present(self, tmp_path):
        db = tmp_path / "nobackfill.db"
        _pre_uac1_db(db)
        _seed_one_airport(db)
        migration.upgrade(db)
        result = migration.inspect(db)
        assert result["counts"] == {"unknown_airport_candidates": 0, "unknown_airport_candidate_reviews": 0}

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
            "unknown_airport_candidate_persistence", "acquisition", "mac_granicus", "fleet_health",
        )
        for module_name in imported_modules:
            for forbidden in forbidden_substrings:
                assert forbidden not in module_name, f"unexpected business-logic import: {module_name}"

    def test_no_construction_of_candidate_or_review_orm_objects_in_migration_source(self):
        """AST-level proof this script creates schema only - it never
        constructs an UnknownAirportCandidate/UnknownAirportCandidateReview
        ORM instance anywhere (which would imply a row-level write, not a
        schema-level one)."""
        tree = ast.parse(inspect_module.getsource(migration))
        forbidden = {"UnknownAirportCandidate", "UnknownAirportCandidateReview", "Airport"}
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
        _pre_uac1_db(db)
        before = db.read_bytes()
        migration.inspect(db)
        migration.inspect(db)
        assert db.read_bytes() == before

    def test_inspect_reports_foreign_key_check_clean(self, tmp_path):
        db = tmp_path / "fkcheck.db"
        _pre_uac1_db(db)
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
