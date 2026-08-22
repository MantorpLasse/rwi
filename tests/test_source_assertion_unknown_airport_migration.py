"""Tests for scripts/migrate_source_assertion_unknown_airport_uac2b.py
(UAC2B, docs/architecture/rwi-uac2b-sourceassertion-unknown-airport-integration-report.md).

Every test uses an isolated temp-file SQLite database (tmp_path). Nothing
in this file ever opens data/runway_safe.db - the real migration is
explicitly deferred to a separate, later, explicitly-authorized
operational step. Grep-verified (see TestNoRealDatabaseAccess). Modeled
directly on the already-proven tests/test_unknown_airport_candidate_migration.py
and tests/test_signal_disposition_migration.py patterns, adapted for a
table-rebuild migration against an EXISTING, real-data-bearing table.
"""
from __future__ import annotations

import ast
import inspect as inspect_module
import sqlite3

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

import app.models as _models
from app.database import Base
from app.models import Airport, Source, SourceAssertion
from app.models.unknown_airport_candidate import UnknownAirportCandidate
from app.services.discovery_candidate_fragment import CandidateFragment
from app.services.discovery_evidence_persistence import (
    DiscoverySourceMetadata,
    persist_candidate_linked_source_assertion,
    persist_discovery_fragment,
)
from app.services.evidence_attachment_guard import CandidateAirport
from app.services.unknown_airport_candidate_persistence import find_or_create_unknown_airport_candidate
import scripts.migrate_source_assertion_unknown_airport_uac2b as uac2b_migration
import scripts.migrate_unknown_airport_candidates_uac2a as uac2a_migration


def _pre_uac2a_and_uac2b_db(path):
    """The realistic "neither UAC2A nor UAC2B applied yet" starting
    state: full current schema, then the two UAC2A tables dropped and
    source_assertions rebuilt back to its exact pre-UAC2B shape (using
    the migration's own frozen pre-UAC2B snapshot - proven correct by
    TestPreUac2bSnapshotSanity below)."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()

    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DROP TABLE unknown_airport_candidate_reviews")
    conn.execute("DROP TABLE unknown_airport_candidates")
    replacement = "source_assertions__presetup"
    conn.execute(uac2b_migration._pre_uac2b_create_table_sql(replacement))
    quoted = ", ".join(f'"{c}"' for c in uac2b_migration._PRE_UAC2B_COLUMNS)
    conn.execute(f'INSERT INTO "{replacement}" ({quoted}) SELECT {quoted} FROM source_assertions')
    conn.execute("DROP TABLE source_assertions")
    conn.execute(f'ALTER TABLE "{replacement}" RENAME TO source_assertions')
    conn.commit()
    conn.close()


def _apply_uac2a(path):
    uac2a_migration.upgrade(path)


def _seed_airport(session, name="Migration Test Airport", **kwargs) -> Airport:
    airport = Airport(name=name, country="XX", **kwargs)
    session.add(airport)
    session.flush()
    return airport


def _seed_source(session, title="Migration Test Source") -> Source:
    source = Source(title=title, source_type="web_discovery")
    session.add(source)
    session.flush()
    return source


def _foo_candidate_kwargs(**overrides):
    kwargs = dict(raw_name="Foo Regional Airport", raw_city="Fooville", raw_country="Fictionland")
    kwargs.update(overrides)
    return kwargs


# ---------------------------------------------------------------------------
# Sanity: the hand-written pre-UAC2B snapshot is genuinely correct
# ---------------------------------------------------------------------------


class TestPreUac2bSnapshotSanity:
    def test_pre_uac2b_helper_produces_exactly_the_expected_pre_uac2b_columns(self, tmp_path):
        db = tmp_path / "sanity.db"
        _pre_uac2a_and_uac2b_db(db)
        conn = sqlite3.connect(str(db))
        cols = [row[1] for row in conn.execute("PRAGMA table_info(source_assertions)")]
        conn.close()
        assert cols == list(uac2b_migration._PRE_UAC2B_COLUMNS)

    def test_pre_uac2b_helper_has_no_new_column_no_new_check_no_new_fk(self, tmp_path):
        db = tmp_path / "sanity2.db"
        _pre_uac2a_and_uac2b_db(db)
        conn = sqlite3.connect(str(db))
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='source_assertions'"
        ).fetchone()[0]
        fks = {(row[3], row[2]) for row in conn.execute("PRAGMA foreign_key_list(source_assertions)")}
        conn.close()
        assert "unknown_airport_candidate_id" not in sql
        assert uac2b_migration.NEW_CHECK_CONSTRAINT_NAME not in sql
        assert ("unknown_airport_candidate_id", "unknown_airport_candidates") not in fks

    def test_pre_uac2b_helper_preserves_all_original_named_constraints(self, tmp_path):
        db = tmp_path / "sanity3.db"
        _pre_uac2a_and_uac2b_db(db)
        conn = sqlite3.connect(str(db))
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='source_assertions'"
        ).fetchone()[0]
        conn.close()
        for name in (
            "ck_source_assertions_type", "ck_source_assertions_evidence_quality",
            "ck_source_assertions_review_state", "ck_source_assertions_record_identity",
            "uq_source_assertions_source_record", "uq_source_assertions_locator_fragment",
        ):
            assert name in sql

    def test_uac2a_tables_absent_in_starting_state(self, tmp_path):
        db = tmp_path / "sanity4.db"
        _pre_uac2a_and_uac2b_db(db)
        conn = sqlite3.connect(str(db))
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "unknown_airport_candidates" not in tables
        assert "unknown_airport_candidate_reviews" not in tables


# ---------------------------------------------------------------------------
# M. UAC2A dependency
# ---------------------------------------------------------------------------


class TestUac2aDependency:
    def test_upgrade_refuses_when_uac2a_not_applied(self, tmp_path):
        db = tmp_path / "nouac2a.db"
        _pre_uac2a_and_uac2b_db(db)
        with pytest.raises(uac2b_migration.Uac2aNotAppliedError):
            uac2b_migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        cols = [row[1] for row in conn.execute("PRAGMA table_info(source_assertions)")]
        conn.close()
        assert "unknown_airport_candidate_id" not in cols  # nothing was touched

    def test_upgrade_refuses_when_uac2a_partially_applied(self, tmp_path):
        db = tmp_path / "partialuac2a.db"
        _pre_uac2a_and_uac2b_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE unknown_airport_candidates (id INTEGER PRIMARY KEY, wrong_column TEXT)")
        conn.commit()
        conn.close()
        with pytest.raises(uac2b_migration.Uac2aNotAppliedError):
            uac2b_migration.upgrade(db)

    def test_upgrade_does_not_auto_create_uac2a_tables(self, tmp_path):
        db = tmp_path / "noautocreate.db"
        _pre_uac2a_and_uac2b_db(db)
        with pytest.raises(uac2b_migration.Uac2aNotAppliedError):
            uac2b_migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "unknown_airport_candidates" not in tables

    def test_upgrade_succeeds_once_uac2a_genuinely_applied(self, tmp_path):
        db = tmp_path / "uac2aready.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        uac2b_migration.upgrade(db)  # must not raise
        assert uac2b_migration.inspect(db)["ready"] is True


# ---------------------------------------------------------------------------
# Clean upgrade / exact schema parity
# ---------------------------------------------------------------------------


class TestCleanUpgrade:
    def test_upgrade_adds_column_check_and_fk(self, tmp_path):
        db = tmp_path / "clean.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        uac2b_migration.upgrade(db)

        conn = sqlite3.connect(str(db))
        cols = {row[1]: (row[2], bool(row[3])) for row in conn.execute("PRAGMA table_info(source_assertions)")}
        fks = {(row[3], row[2], row[4]) for row in conn.execute("PRAGMA foreign_key_list(source_assertions)")}
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='source_assertions'"
        ).fetchone()[0]
        conn.close()

        assert cols["unknown_airport_candidate_id"] == ("INTEGER", False)
        assert ("unknown_airport_candidate_id", "unknown_airport_candidates", "id") in fks
        assert uac2b_migration.NEW_CHECK_CONSTRAINT_NAME in sql

    def test_upgrade_preserves_every_pre_existing_column(self, tmp_path):
        db = tmp_path / "preserve_cols.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        uac2b_migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(source_assertions)")}
        conn.close()
        assert set(uac2b_migration._PRE_UAC2B_COLUMNS).issubset(cols)

    def test_upgrade_preserves_every_pre_existing_named_constraint(self, tmp_path):
        db = tmp_path / "preserve_constraints.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        uac2b_migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='source_assertions'"
        ).fetchone()[0]
        conn.close()
        for name in (
            "ck_source_assertions_type", "ck_source_assertions_evidence_quality",
            "ck_source_assertions_review_state", "ck_source_assertions_record_identity",
            "uq_source_assertions_source_record", "uq_source_assertions_locator_fragment",
        ):
            assert name in sql

    def test_upgrade_preserves_every_pre_existing_index(self, tmp_path):
        db = tmp_path / "preserve_indexes.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        conn = sqlite3.connect(str(db))
        before_indexes = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='source_assertions'"
            )
        }
        conn.close()
        uac2b_migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        after_indexes = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='source_assertions'"
            )
        }
        conn.close()
        assert before_indexes.issubset(after_indexes)
        assert "ix_source_assertions_unknown_airport_candidate_id" in after_indexes

    def test_upgrade_leaves_other_tables_untouched(self, tmp_path):
        db = tmp_path / "otherunchanged.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        conn = sqlite3.connect(str(db))
        before = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        uac2b_migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        after = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert before == after  # no new/removed table - only source_assertions was rebuilt in place


# ---------------------------------------------------------------------------
# Idempotency (O)
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_second_upgrade_is_safe_no_op(self, tmp_path):
        db = tmp_path / "idem.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        uac2b_migration.upgrade(db)
        before = uac2b_migration.inspect(db)
        uac2b_migration.upgrade(db)
        after = uac2b_migration.inspect(db)
        assert before == after

    def test_second_upgrade_preserves_rows_and_links(self, tmp_path):
        db = tmp_path / "idem2.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        uac2b_migration.upgrade(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = find_or_create_unknown_airport_candidate(s, **_foo_candidate_kwargs()).candidate
            s.commit()
            fragment = CandidateFragment(artifact_identity="art-1", source_locator="p1", raw_text="Evidence.")
            persist_candidate_linked_source_assertion(
                s, DiscoverySourceMetadata(document_identity="doc-1", title="t"), fragment,
                unknown_airport_candidate_id=candidate.id,
            )
            s.commit()
        engine.dispose()

        uac2b_migration.upgrade(db)  # idempotent re-run after real data exists
        result = uac2b_migration.inspect(db)
        assert result["row_count"] == 1
        assert result["linked_row_count"] == 1


# ---------------------------------------------------------------------------
# Partial / incompatible schema
# ---------------------------------------------------------------------------


class TestPartialAndIncompatibleSchema:
    def test_column_exists_without_fk_fails_closed(self, tmp_path):
        db = tmp_path / "nofk.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("ALTER TABLE source_assertions ADD COLUMN unknown_airport_candidate_id INTEGER")
        conn.commit()
        conn.close()
        with pytest.raises(uac2b_migration.IncompatibleExistingSchemaError, match="missing expected named constraint"):
            uac2b_migration.upgrade(db)

    def test_column_correct_but_check_absent_fails_closed(self, tmp_path):
        db = tmp_path / "nocheck.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "ALTER TABLE source_assertions ADD COLUMN unknown_airport_candidate_id INTEGER "
            "REFERENCES unknown_airport_candidates(id)"
        )
        conn.commit()
        conn.close()
        with pytest.raises(uac2b_migration.IncompatibleExistingSchemaError, match="missing expected named constraint"):
            uac2b_migration.upgrade(db)

    def test_uac2a_missing_entirely_fails_closed_before_touching_source_assertions(self, tmp_path):
        db = tmp_path / "nouac2aagain.db"
        _pre_uac2a_and_uac2b_db(db)
        with pytest.raises(uac2b_migration.Uac2aNotAppliedError):
            uac2b_migration.upgrade(db)
        conn = sqlite3.connect(str(db))
        cols = [row[1] for row in conn.execute("PRAGMA table_info(source_assertions)")]
        conn.close()
        assert cols == list(uac2b_migration._PRE_UAC2B_COLUMNS)  # completely untouched

    def test_candidate_table_incompatible_fails_closed(self, tmp_path):
        db = tmp_path / "badcandidate.db"
        _pre_uac2a_and_uac2b_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE unknown_airport_candidates (id INTEGER PRIMARY KEY, totally_wrong TEXT)")
        conn.commit()
        conn.close()
        with pytest.raises(uac2b_migration.Uac2aNotAppliedError):
            uac2b_migration.upgrade(db)

    def test_source_assertions_table_missing_fails_closed(self, tmp_path):
        db = tmp_path / "notable.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP TABLE source_assertions")
        conn.commit()
        conn.close()
        with pytest.raises(RuntimeError, match="does not exist"):
            uac2b_migration.upgrade(db)


# ---------------------------------------------------------------------------
# Downgrade (K, L)
# ---------------------------------------------------------------------------


class TestDowngrade:
    def test_downgrade_empty_links_success(self, tmp_path):
        db = tmp_path / "downgrade_empty.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        uac2b_migration.upgrade(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _seed_airport(s)
            source = _seed_source(s)
            s.add(SourceAssertion(
                source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
                source_record_identifier="r1",
            ))
            s.commit()
        engine.dispose()

        uac2b_migration.downgrade(db)
        result = uac2b_migration.inspect(db)
        assert result["unknown_airport_candidate_id_column_present"] is False

        conn = sqlite3.connect(str(db))
        assert conn.execute("SELECT count(*) FROM source_assertions").fetchone()[0] == 1
        cols = [row[1] for row in conn.execute("PRAGMA table_info(source_assertions)")]
        conn.close()
        assert cols == list(uac2b_migration._PRE_UAC2B_COLUMNS)

    def test_downgrade_refused_with_candidate_links(self, tmp_path):
        db = tmp_path / "downgrade_refused.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        uac2b_migration.upgrade(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = find_or_create_unknown_airport_candidate(s, **_foo_candidate_kwargs()).candidate
            s.commit()
            fragment = CandidateFragment(artifact_identity="art-refuse", source_locator="p1", raw_text="Evidence.")
            persist_candidate_linked_source_assertion(
                s, DiscoverySourceMetadata(document_identity="doc-refuse", title="t"), fragment,
                unknown_airport_candidate_id=candidate.id,
            )
            s.commit()
        engine.dispose()

        with pytest.raises(RuntimeError, match=r"downgrade\(\) refused"):
            uac2b_migration.downgrade(db)
        assert uac2b_migration.inspect(db)["unknown_airport_candidate_id_column_present"] is True

    def test_downgrade_refusal_is_atomic(self, tmp_path):
        db = tmp_path / "downgrade_atomic_refuse.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        uac2b_migration.upgrade(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = find_or_create_unknown_airport_candidate(s, **_foo_candidate_kwargs()).candidate
            s.commit()
            fragment = CandidateFragment(artifact_identity="art-atomic", source_locator="p1", raw_text="Evidence.")
            persist_candidate_linked_source_assertion(
                s, DiscoverySourceMetadata(document_identity="doc-atomic", title="t"), fragment,
                unknown_airport_candidate_id=candidate.id,
            )
            s.commit()
        engine.dispose()

        before = uac2b_migration.inspect(db)
        with pytest.raises(RuntimeError):
            uac2b_migration.downgrade(db)
        after = uac2b_migration.inspect(db)
        assert before == after

    def test_downgrade_is_a_safe_no_op_when_column_already_absent(self, tmp_path):
        db = tmp_path / "downgrade_noop.db"
        _pre_uac2a_and_uac2b_db(db)
        uac2b_migration.downgrade(db)  # must not raise
        conn = sqlite3.connect(str(db))
        cols = [row[1] for row in conn.execute("PRAGMA table_info(source_assertions)")]
        conn.close()
        assert cols == list(uac2b_migration._PRE_UAC2B_COLUMNS)

    def test_round_trip_upgrade_downgrade_upgrade(self, tmp_path):
        db = tmp_path / "roundtrip.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        uac2b_migration.upgrade(db)
        uac2b_migration.downgrade(db)
        uac2b_migration.upgrade(db)
        assert uac2b_migration.inspect(db)["ready"] is True


class TestDdlAtomicity:
    def test_upgrade_failure_leaves_pre_uac2b_schema_intact(self, tmp_path, monkeypatch):
        db = tmp_path / "upgrade_atomic.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)

        def _crash(connection):
            raise RuntimeError("simulated crash during rebuild")

        monkeypatch.setattr(uac2b_migration, "_rebuild_to_current_schema", _crash)
        with pytest.raises(RuntimeError, match="simulated crash"):
            uac2b_migration.upgrade(db)
        monkeypatch.undo()

        conn = sqlite3.connect(str(db))
        cols = [row[1] for row in conn.execute("PRAGMA table_info(source_assertions)")]
        conn.close()
        assert cols == list(uac2b_migration._PRE_UAC2B_COLUMNS)

    def test_downgrade_failure_leaves_post_uac2b_schema_intact(self, tmp_path, monkeypatch):
        db = tmp_path / "downgrade_atomic.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        uac2b_migration.upgrade(db)

        def _crash(connection):
            raise RuntimeError("simulated crash during downgrade rebuild")

        monkeypatch.setattr(uac2b_migration, "_rebuild_to_pre_uac2b_schema", _crash)
        with pytest.raises(RuntimeError, match="simulated crash"):
            uac2b_migration.downgrade(db)
        monkeypatch.undo()

        assert uac2b_migration.inspect(db)["ready"] is True

    def test_upgrade_failure_after_real_drop_before_rename_rolls_back_completely(self, tmp_path, monkeypatch):
        """Review §15: the two tests above only prove "failure before any
        DDL statement runs" (the whole rebuild function is replaced
        before it does anything) - a materially weaker claim than
        genuine mid-sequence atomicity. This test performs the REAL
        CREATE/INSERT/DROP statements (calling the actual compiled DDL,
        not a mock), then crashes deliberately AFTER the real DROP TABLE
        but BEFORE the RENAME - proving SQLite's own transactional DDL
        rollback genuinely undoes an already-executed DROP TABLE (and
        the earlier CREATE/INSERT of the replacement table) when the
        surrounding transaction is rolled back, not merely that the
        Python function never got the chance to run any DDL at all."""
        db = tmp_path / "middrop.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO airports (id, name, country, created_at, updated_at) "
            "VALUES (1, 'A', 'XX', '2026-01-01', '2026-01-01')"
        )
        conn.execute("INSERT INTO sources (id, title, source_type, reliability_level) VALUES (1, 's', 'web_discovery', 'unverified')")
        conn.execute(
            "INSERT INTO source_assertions (source_id, airport_id, assertion_type, source_record_identifier, "
            "evidence_quality, review_state, created_at) VALUES (1, 1, 'project_construction', 'mid-drop-1', "
            "'unverified_candidate', 'unreviewed', '2026-01-01')"
        )
        conn.commit()
        conn.close()

        def _crash_after_real_drop(connection):
            table = uac2b_migration.Base.metadata.tables[uac2b_migration.TABLE]
            replacement = f"{uac2b_migration.TABLE}__uac2b_new"
            create_sql = str(uac2b_migration.CreateTable(table).compile(dialect=uac2b_migration.sqlite.dialect()))
            create_sql = create_sql.replace(f"CREATE TABLE {uac2b_migration.TABLE}", f"CREATE TABLE {replacement}", 1)
            connection.execute(create_sql)
            old_columns = [row[1] for row in connection.execute(f"PRAGMA table_info({uac2b_migration.TABLE})")]
            quoted = ", ".join(f'"{c}"' for c in old_columns)
            connection.execute(f'INSERT INTO "{replacement}" ({quoted}) SELECT {quoted} FROM "{uac2b_migration.TABLE}"')
            connection.execute(f'DROP TABLE "{uac2b_migration.TABLE}"')
            raise RuntimeError("simulated crash after real DROP, before RENAME")

        monkeypatch.setattr(uac2b_migration, "_rebuild_to_current_schema", _crash_after_real_drop)
        with pytest.raises(RuntimeError, match="simulated crash after real DROP"):
            uac2b_migration.upgrade(db)
        monkeypatch.undo()

        conn = sqlite3.connect(str(db))
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "source_assertions" in tables  # the DROP was rolled back
        assert "source_assertions__uac2b_new" not in tables  # no orphaned replacement table left behind
        cols = [row[1] for row in conn.execute("PRAGMA table_info(source_assertions)")]
        assert cols == list(uac2b_migration._PRE_UAC2B_COLUMNS)  # original pre-UAC2B shape, fully intact
        count = conn.execute("SELECT count(*) FROM source_assertions").fetchone()[0]
        conn.close()
        assert count == 1  # the seeded row survived the rollback

    def test_downgrade_failure_after_real_drop_before_rename_rolls_back_completely(self, tmp_path, monkeypatch):
        """The downgrade-direction counterpart to the test above."""
        db = tmp_path / "middrop_downgrade.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        uac2b_migration.upgrade(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _seed_airport(s)
            source = _seed_source(s)
            s.add(SourceAssertion(
                source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
                source_record_identifier="mid-drop-downgrade-1",
            ))
            s.commit()
        engine.dispose()

        def _crash_after_real_drop(connection):
            replacement = f"{uac2b_migration.TABLE}__uac2b_downgrade"
            connection.execute(uac2b_migration._pre_uac2b_create_table_sql(replacement))
            quoted = ", ".join(f'"{c}"' for c in uac2b_migration._PRE_UAC2B_COLUMNS)
            connection.execute(f'INSERT INTO "{replacement}" ({quoted}) SELECT {quoted} FROM "{uac2b_migration.TABLE}"')
            connection.execute(f'DROP TABLE "{uac2b_migration.TABLE}"')
            raise RuntimeError("simulated crash after real DROP, before RENAME")

        monkeypatch.setattr(uac2b_migration, "_rebuild_to_pre_uac2b_schema", _crash_after_real_drop)
        with pytest.raises(RuntimeError, match="simulated crash after real DROP"):
            uac2b_migration.downgrade(db)
        monkeypatch.undo()

        conn = sqlite3.connect(str(db))
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "source_assertions" in tables
        assert "source_assertions__uac2b_downgrade" not in tables
        cols = [row[1] for row in conn.execute("PRAGMA table_info(source_assertions)")]
        assert "unknown_airport_candidate_id" in cols  # still post-UAC2B shape - downgrade never completed
        count = conn.execute("SELECT count(*) FROM source_assertions").fetchone()[0]
        conn.close()
        assert count == 1


# ---------------------------------------------------------------------------
# Raw SQL constraint attacks
# ---------------------------------------------------------------------------


class TestRawSqlAttacks:
    def _ready_db(self, tmp_path, name):
        db = tmp_path / name
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        uac2b_migration.upgrade(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _seed_airport(s)
            candidate = find_or_create_unknown_airport_candidate(s, **_foo_candidate_kwargs()).candidate
            source = _seed_source(s)
            s.commit()
            ids = (airport.id, candidate.id, source.id)
        engine.dispose()
        return db, ids

    def test_airport_id_only_accepted(self, tmp_path):
        db, (airport_id, candidate_id, source_id) = self._ready_db(tmp_path, "airport_only.db")
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO source_assertions (source_id, airport_id, assertion_type, source_record_identifier, "
            "evidence_quality, review_state, created_at) VALUES (?, ?, 'project_construction', 'r-a', "
            "'unverified_candidate', 'unreviewed', '2026-01-01')", (source_id, airport_id),
        )
        conn.commit()
        conn.close()

    def test_candidate_id_only_accepted(self, tmp_path):
        db, (airport_id, candidate_id, source_id) = self._ready_db(tmp_path, "candidate_only.db")
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO source_assertions (source_id, unknown_airport_candidate_id, assertion_type, "
            "source_record_identifier, evidence_quality, review_state, created_at) VALUES (?, ?, "
            "'project_construction', 'r-b', 'unverified_candidate', 'unreviewed', '2026-01-01')",
            (source_id, candidate_id),
        )
        conn.commit()
        conn.close()

    def test_both_null_accepted(self, tmp_path):
        db, (airport_id, candidate_id, source_id) = self._ready_db(tmp_path, "both_null.db")
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO source_assertions (source_id, assertion_type, source_record_identifier, "
            "evidence_quality, review_state, created_at) VALUES (?, 'project_construction', 'r-c', "
            "'unverified_candidate', 'unreviewed', '2026-01-01')", (source_id,),
        )
        conn.commit()
        conn.close()

    def test_both_non_null_rejected_by_check(self, tmp_path):
        db, (airport_id, candidate_id, source_id) = self._ready_db(tmp_path, "both_nonnull.db")
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                "INSERT INTO source_assertions (source_id, airport_id, unknown_airport_candidate_id, "
                "assertion_type, source_record_identifier, evidence_quality, review_state, created_at) "
                "VALUES (?, ?, ?, 'project_construction', 'r-d', 'unverified_candidate', 'unreviewed', "
                "'2026-01-01')", (source_id, airport_id, candidate_id),
            )
        conn.rollback()
        conn.close()

    def test_candidate_id_nonexistent_rejected_by_fk(self, tmp_path):
        db, (airport_id, candidate_id, source_id) = self._ready_db(tmp_path, "badcandidatefk.db")
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute(
                "INSERT INTO source_assertions (source_id, unknown_airport_candidate_id, assertion_type, "
                "source_record_identifier, evidence_quality, review_state, created_at) VALUES (?, 999999, "
                "'project_construction', 'r-e', 'unverified_candidate', 'unreviewed', '2026-01-01')",
                (source_id,),
            )
        conn.rollback()
        conn.close()

    def test_airport_id_nonexistent_rejected_by_existing_fk(self, tmp_path):
        db, (airport_id, candidate_id, source_id) = self._ready_db(tmp_path, "badairportfk.db")
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute(
                "INSERT INTO source_assertions (source_id, airport_id, assertion_type, "
                "source_record_identifier, evidence_quality, review_state, created_at) VALUES (?, 999999, "
                "'project_construction', 'r-f', 'unverified_candidate', 'unreviewed', '2026-01-01')",
                (source_id,),
            )
        conn.rollback()
        conn.close()

    def test_both_valid_ids_still_rejected_by_check(self, tmp_path):
        """Both FKs individually valid does not bypass the mutual-
        exclusivity CHECK - it fires regardless of whether the targets
        themselves exist."""
        db, (airport_id, candidate_id, source_id) = self._ready_db(tmp_path, "bothvalid.db")
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                "INSERT INTO source_assertions (source_id, airport_id, unknown_airport_candidate_id, "
                "assertion_type, source_record_identifier, evidence_quality, review_state, created_at) "
                "VALUES (?, ?, ?, 'project_construction', 'r-g', 'unverified_candidate', 'unreviewed', "
                "'2026-01-01')", (source_id, airport_id, candidate_id),
            )
        conn.rollback()
        conn.close()


# ---------------------------------------------------------------------------
# Backup / write authorization / wrong-DB isolation
# ---------------------------------------------------------------------------


class TestWriteGate:
    def test_main_requires_allow_database_write(self, tmp_path):
        db = tmp_path / "gate.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        with pytest.raises(SystemExit):
            uac2b_migration.main(["--database", str(db)])
        assert uac2b_migration.inspect(db)["unknown_airport_candidate_id_column_present"] is False

    def test_main_requires_allow_database_write_for_downgrade_mode_too(self, tmp_path):
        db = tmp_path / "gate_downgrade.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        uac2b_migration.upgrade(db)
        with pytest.raises(SystemExit):
            uac2b_migration.main(["--database", str(db), "--downgrade"])
        assert uac2b_migration.inspect(db)["unknown_airport_candidate_id_column_present"] is True

    def test_main_no_sessionlocal_reference_ast(self):
        tree = ast.parse(inspect_module.getsource(uac2b_migration))
        code_identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert "SessionLocal" not in code_identifiers

    def test_nonexistent_database_path_fails_closed_on_backup(self, tmp_path):
        missing = tmp_path / "does_not_exist.db"
        with pytest.raises(FileNotFoundError):
            uac2b_migration.backup_database(missing, backup_directory=tmp_path / "backups")


class TestBackup:
    def test_backup_created_before_write(self, tmp_path):
        db = tmp_path / "backup_src.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        backup_dir = tmp_path / "backups"
        before_bytes = db.read_bytes()
        result = uac2b_migration.backup_database(db, backup_directory=backup_dir)
        assert result.exists()
        assert result.read_bytes() == before_bytes

    def test_backup_is_independently_readable_pre_uac2b_schema(self, tmp_path):
        db = tmp_path / "backup_read.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        backup_dir = tmp_path / "backups"
        backup_path = uac2b_migration.backup_database(db, backup_directory=backup_dir)
        conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
        cols = [row[1] for row in conn.execute("PRAGMA table_info(source_assertions)")]
        conn.close()
        assert "unknown_airport_candidate_id" not in cols

    def test_backup_matches_source_before_mutation(self, tmp_path):
        db = tmp_path / "backup_match.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            _seed_airport(s)
            s.commit()
        engine.dispose()
        backup_dir = tmp_path / "backups"
        backup_path = uac2b_migration.backup_database(db, backup_directory=backup_dir)

        uac2b_migration.upgrade(db)

        conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
        cols = [row[1] for row in conn.execute("PRAGMA table_info(source_assertions)")]
        count = conn.execute("SELECT count(*) FROM airports").fetchone()[0]
        conn.close()
        assert "unknown_airport_candidate_id" not in cols
        assert count == 1

    def test_backup_passes_integrity_check(self, tmp_path):
        db = tmp_path / "backup_integrity.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        backup_dir = tmp_path / "backups"
        backup_path = uac2b_migration.backup_database(db, backup_directory=backup_dir)
        conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        conn.close()
        assert result == "ok"
        assert fk_violations == []

    def test_main_creates_backup_on_write(self, tmp_path, capsys):
        db = tmp_path / "main_backup.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        code = uac2b_migration.main(["--database", str(db), "--allow-database-write"])
        assert code == 0
        captured = capsys.readouterr()
        assert "Backup created:" in captured.out


class TestWrongDatabaseIsolation:
    def test_migration_touches_only_the_named_database(self, tmp_path):
        target = tmp_path / "target.db"
        protected = tmp_path / "protected.db"
        _pre_uac2a_and_uac2b_db(target)
        _apply_uac2a(target)
        _pre_uac2a_and_uac2b_db(protected)
        _apply_uac2a(protected)
        before_protected = protected.read_bytes()

        uac2b_migration.upgrade(target)

        assert protected.read_bytes() == before_protected
        assert uac2b_migration.inspect(target)["ready"] is True
        assert uac2b_migration.inspect(protected)["unknown_airport_candidate_id_column_present"] is False


# ---------------------------------------------------------------------------
# Existing-data preservation (J)
# ---------------------------------------------------------------------------


class TestExistingDataPreservation:
    def test_representative_populated_source_assertions_preserved_field_for_field(self, tmp_path):
        """Seeds via raw SQL against the genuinely pre-UAC2B schema - the
        ORM model already declares unknown_airport_candidate_id
        unconditionally, so it cannot be used to construct rows against a
        database that doesn't have that column yet (this is the realistic
        "real production data, not yet migrated" starting state)."""
        db = tmp_path / "preserve.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)

        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO airports (id, name, country, iata_code, created_at, updated_at) "
            "VALUES (1, 'Preserve Airport', 'XX', 'PPP', '2026-01-01', '2026-01-01')"
        )
        conn.execute("INSERT INTO sources (id, title, source_type, reliability_level) VALUES (1, 'Preserve Source', 'web_discovery', 'unverified')")
        conn.execute(
            "INSERT INTO source_assertions (id, source_id, airport_id, assertion_type, source_record_identifier, "
            "raw_relevant_text, identity_guard_decision, evidence_quality, review_state, created_at) VALUES "
            "(1, 1, 1, 'project_construction', 'preserve-1', 'Real evidence text one.', 'ATTACH_CONFIRMED', "
            "'unverified_candidate', 'unreviewed', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO source_assertions (id, source_id, airport_id, assertion_type, source_record_identifier, "
            "raw_relevant_text, identity_guard_decision, evidence_quality, review_state, created_at) VALUES "
            "(2, 1, NULL, 'project_construction', 'preserve-2', 'Real evidence text two.', 'INSUFFICIENT_IDENTITY', "
            "'unverified_candidate', 'unreviewed', '2026-01-01')"
        )
        conn.commit()
        snapshot = conn.execute(
            "SELECT id, source_id, airport_id, assertion_type, source_record_identifier, "
            "raw_relevant_text, identity_guard_decision FROM source_assertions ORDER BY id"
        ).fetchall()
        conn.close()

        uac2b_migration.upgrade(db)

        engine2 = create_engine(f"sqlite:///{db}")
        with Session(engine2) as s:
            after = [
                (r.id, r.source_id, r.airport_id, r.assertion_type, r.source_record_identifier,
                 r.raw_relevant_text, r.identity_guard_decision)
                for r in s.query(SourceAssertion).order_by(SourceAssertion.id).all()
            ]
            new_column_values = [r.unknown_airport_candidate_id for r in s.query(SourceAssertion).all()]
        engine2.dispose()

        assert after == snapshot
        assert all(v is None for v in new_column_values)  # zero backfill


# ---------------------------------------------------------------------------
# Model/migration parity (G, H) - against a genuinely migrated schema,
# never Base.metadata.create_all()
# ---------------------------------------------------------------------------


class TestModelMigrationParity:
    def test_known_airport_evidence_persists_unchanged_post_migration(self, tmp_path):
        """Backward compatibility (F): persist_discovery_fragment() for a
        known airport, against a genuinely migrated (not create_all'd)
        schema."""
        db = tmp_path / "parity_known.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        uac2b_migration.upgrade(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _seed_airport(s, faa_code="BOS")
            s.commit()
            candidate_airport = CandidateAirport(id=airport.id, name=airport.name, identifiers=frozenset({"BOS"}))
            fragment = CandidateFragment(
                artifact_identity="bos-art", source_locator="p1", raw_text="BOS EMAS work.",
                airport_identifiers=frozenset({"BOS"}),
            )
            result = persist_discovery_fragment(
                s, DiscoverySourceMetadata(document_identity="bos-doc", title="t"), fragment, [candidate_airport],
            )
            assert result.attached_airport_id == airport.id
            assert result.attached_unknown_airport_candidate_id is None
            assertion = s.get(SourceAssertion, result.source_assertion_id)
            assert assertion.airport_id == airport.id
            assert assertion.unknown_airport_candidate_id is None
        engine.dispose()

    def test_candidate_linked_evidence_persists_post_migration(self, tmp_path):
        db = tmp_path / "parity_candidate.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        uac2b_migration.upgrade(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = find_or_create_unknown_airport_candidate(s, **_foo_candidate_kwargs()).candidate
            s.commit()
            fragment = CandidateFragment(artifact_identity="foo-art", source_locator="p1", raw_text="Foo evidence.")
            result = persist_candidate_linked_source_assertion(
                s, DiscoverySourceMetadata(document_identity="foo-doc", title="t"), fragment,
                unknown_airport_candidate_id=candidate.id,
            )
            assert result.attached_airport_id is None
            assert result.attached_unknown_airport_candidate_id == candidate.id
        engine.dispose()

    def test_dual_identity_rejected_post_migration(self, tmp_path):
        db = tmp_path / "parity_dual.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        uac2b_migration.upgrade(db)

        from sqlalchemy.exc import IntegrityError

        engine = create_engine(f"sqlite:///{db}")

        @event.listens_for(engine, "connect")
        def _enable_fk(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        with Session(engine) as s:
            airport = _seed_airport(s)
            candidate = find_or_create_unknown_airport_candidate(s, **_foo_candidate_kwargs()).candidate
            source = _seed_source(s)
            s.commit()
            s.add(SourceAssertion(
                source_id=source.id, airport_id=airport.id, unknown_airport_candidate_id=candidate.id,
                assertion_type="project_construction", source_record_identifier="dual-parity",
            ))
            with pytest.raises(IntegrityError):
                s.commit()
            s.rollback()
        engine.dispose()

    def test_multiple_assertions_share_one_candidate_post_migration(self, tmp_path):
        db = tmp_path / "parity_multi.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        uac2b_migration.upgrade(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            candidate = find_or_create_unknown_airport_candidate(s, **_foo_candidate_kwargs()).candidate
            s.commit()
            for i in range(3):
                persist_candidate_linked_source_assertion(
                    s, DiscoverySourceMetadata(document_identity=f"multi-doc-{i}", title="t"),
                    CandidateFragment(artifact_identity=f"multi-art-{i}", source_locator="p1", raw_text=f"Evidence {i}."),
                    unknown_airport_candidate_id=candidate.id,
                )
            s.commit()
            linked = s.query(SourceAssertion).filter_by(unknown_airport_candidate_id=candidate.id).all()
            assert len(linked) == 3
            assert s.query(UnknownAirportCandidate).count() == 1
        engine.dispose()

    def test_no_canonical_side_effects_post_migration(self, tmp_path):
        db = tmp_path / "parity_canon.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        uac2b_migration.upgrade(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airports_before = s.query(Airport).count()
            candidate = find_or_create_unknown_airport_candidate(s, **_foo_candidate_kwargs()).candidate
            s.commit()
            persist_candidate_linked_source_assertion(
                s, DiscoverySourceMetadata(document_identity="canon-doc", title="t"),
                CandidateFragment(artifact_identity="canon-art", source_locator="p1", raw_text="Evidence."),
                unknown_airport_candidate_id=candidate.id,
            )
            assert s.query(Airport).count() == airports_before
        engine.dispose()


# ---------------------------------------------------------------------------
# Zero backfill
# ---------------------------------------------------------------------------


class TestZeroBackfill:
    def test_no_ast_reference_to_discovery_business_logic_modules(self):
        tree = ast.parse(inspect_module.getsource(uac2b_migration))
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

    def test_no_orm_object_construction_in_migration_source(self):
        tree = ast.parse(inspect_module.getsource(uac2b_migration))
        forbidden = {"Airport", "Runway", "RunwayEnd", "Installation", "Signal", "SourceAssertion", "UnknownAirportCandidate"}
        found = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden
        }
        assert found == set()


# ---------------------------------------------------------------------------
# No real database access
# ---------------------------------------------------------------------------


class TestNoRealDatabaseAccess:
    def test_no_reference_to_the_real_database_path_in_migration_module(self):
        tree = ast.parse(inspect_module.getsource(uac2b_migration))
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

    def test_inspect_never_mutates(self, tmp_path):
        db = tmp_path / "inspectonly.db"
        _pre_uac2a_and_uac2b_db(db)
        _apply_uac2a(db)
        before = db.read_bytes()
        uac2b_migration.inspect(db)
        uac2b_migration.inspect(db)
        assert db.read_bytes() == before
