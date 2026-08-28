"""Tests for scripts/resolve_source_assertion_legacy_identity.py
(docs/architecture/rwi-legacy-attached-sourceassertion-identity-governance-
design.md).

Every test uses an isolated temp-file SQLite database (tmp_path). Nothing
in this file ever opens data/runway_safe.db. Modeled directly on
tests/test_resolve_source_assertion_identity.py's own `run_resolve()`-
direct-call convention.
"""
from __future__ import annotations

import sqlite3

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app import models as _models  # noqa: F401
from app.models import Airport, Source, SourceAssertion
from app.models.source_assertion_legacy_identity_attestation import SourceAssertionLegacyIdentityAttestation
import scripts.migrate_source_assertion_legacy_identity_attestation as migration
from scripts.resolve_source_assertion_legacy_identity import ResolveLegacyIdentityConfig, run_resolve


def _make_db(path, *, extra_assertion_kwargs=None):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        airport = Airport(name="Greenville Downtown", country="USA", iata_code="GMU", icao_code="KGMU", faa_code="GMU")
        s.add(airport)
        s.commit()
        source = Source(title="USAspending grant", source_type="usaspending_grant")
        s.add(source)
        s.commit()
        kwargs = dict(
            source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
            raw_relevant_text="GREENVILLE RUNWAY 1/19 EMAS project", raw_product_type="EMAS",
            source_record_identifier="rec-1", evidence_quality="direct_strong",
        )
        if extra_assertion_kwargs:
            kwargs.update(extra_assertion_kwargs)
        assertion = SourceAssertion(**kwargs)
        s.add(assertion)
        s.commit()
        assertion_id, airport_id = assertion.id, airport.id
    engine.dispose()
    return assertion_id, airport_id


class TestInspect:
    def test_inspect_shows_eligibility_and_current_eb5_state(self, tmp_path):
        db = tmp_path / "inspect.db"
        assertion_id, airport_id = _make_db(db)
        config = ResolveLegacyIdentityConfig(database=db, source_assertion_id=assertion_id)

        result = run_resolve(config)

        assert result.assertion_found is True
        assert result.airport_id == airport_id
        assert result.airport_iata == "GMU"
        assert result.airport_icao == "KGMU"
        assert result.eligible is True
        assert result.eligibility_blocker is None
        assert result.current_effective_decision == "INSUFFICIENT_IDENTITY"
        assert result.preview_snapshot_hash is not None
        assert result.attestation_history == []

    def test_inspect_causes_zero_writes(self, tmp_path):
        db = tmp_path / "inspect.db"
        assertion_id, _ = _make_db(db)
        before = db.stat().st_mtime_ns
        before_bytes = db.read_bytes()

        run_resolve(ResolveLegacyIdentityConfig(database=db, source_assertion_id=assertion_id))

        assert db.read_bytes() == before_bytes

    def test_inspect_reports_ineligible_row(self, tmp_path):
        db = tmp_path / "inspect.db"
        assertion_id, _ = _make_db(db, extra_assertion_kwargs={"identity_guard_decision": "ATTACH_CONFIRMED"})

        result = run_resolve(ResolveLegacyIdentityConfig(database=db, source_assertion_id=assertion_id))

        assert result.eligible is False
        assert "identity_guard_decision" in result.eligibility_blocker

    def test_inspect_nonexistent_assertion_is_blocked(self, tmp_path):
        db = tmp_path / "inspect.db"
        _make_db(db)

        result = run_resolve(ResolveLegacyIdentityConfig(database=db, source_assertion_id=99999))

        assert result.blockers


class TestDryRun:
    def test_dry_run_shows_eligible_and_causes_zero_writes(self, tmp_path):
        db = tmp_path / "dryrun.db"
        assertion_id, airport_id = _make_db(db)
        before_bytes = db.read_bytes()

        result = run_resolve(ResolveLegacyIdentityConfig(
            database=db, source_assertion_id=assertion_id, decision="CONFIRM_EXISTING_ATTACHMENT",
            reviewer="human:tester", reason="text matches airport", matched_airport_id=airport_id,
        ))

        assert result.decision_eligible is True
        assert result.written is False
        assert db.read_bytes() == before_bytes

    def test_dry_run_reports_refusal_for_wrong_airport(self, tmp_path):
        db = tmp_path / "dryrun.db"
        assertion_id, _airport_id = _make_db(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            other = Airport(name="Other Airport", country="USA")
            s.add(other)
            s.commit()
            other_id = other.id
        engine.dispose()

        result = run_resolve(ResolveLegacyIdentityConfig(
            database=db, source_assertion_id=assertion_id, decision="CONFIRM_EXISTING_ATTACHMENT",
            reviewer="human:tester", reason="x", matched_airport_id=other_id,
        ))

        assert result.decision_eligible is False
        assert "does not equal" in result.decision_refusal_reason


class TestWrite:
    def test_write_requires_allow_database_write_flag(self, tmp_path):
        db = tmp_path / "write.db"
        assertion_id, airport_id = _make_db(db)
        before_bytes = db.read_bytes()

        result = run_resolve(ResolveLegacyIdentityConfig(
            database=db, source_assertion_id=assertion_id, decision="CONFIRM_EXISTING_ATTACHMENT",
            reviewer="human:tester", reason="text matches airport", matched_airport_id=airport_id,
            allow_database_write=False,
        ))

        assert result.written is False
        assert db.read_bytes() == before_bytes

    def test_write_with_allow_database_write_persists_and_updates_eb5(self, tmp_path):
        db = tmp_path / "write.db"
        assertion_id, airport_id = _make_db(db)

        result = run_resolve(ResolveLegacyIdentityConfig(
            database=db, source_assertion_id=assertion_id, decision="CONFIRM_EXISTING_ATTACHMENT",
            reviewer="human:tester", reason="text matches airport", matched_airport_id=airport_id,
            allow_database_write=True,
        ))

        assert result.written is True
        assert result.written_attestation_id is not None
        assert result.current_effective_decision == "ATTACH_CONFIRMED"
        assert result.current_effective_basis == "LEGACY_HUMAN_ATTESTATION"

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            rows = s.query(SourceAssertionLegacyIdentityAttestation).all()
            assert len(rows) == 1
            assertion = s.get(SourceAssertion, assertion_id)
            assert assertion.identity_guard_decision is None  # historical fact untouched
        engine.dispose()

    def test_exactly_one_assertion_targeted_per_invocation(self, tmp_path):
        """No bulk mode exists - the config/CLI only ever accepts a single
        --source-assertion-id, proven structurally (no list/iterable
        parameter exists on the config at all)."""
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(ResolveLegacyIdentityConfig)}
        assert "source_assertion_id" in field_names
        assert not any(name in ("source_assertion_ids", "bulk", "confirm_all") for name in field_names)

    def test_unsafe_row_fails_closed_even_with_allow_database_write(self, tmp_path):
        db = tmp_path / "write.db"
        assertion_id, _ = _make_db(db, extra_assertion_kwargs={"identity_guard_decision": "ATTACH_CONFIRMED"})
        before_bytes = db.read_bytes()

        result = run_resolve(ResolveLegacyIdentityConfig(
            database=db, source_assertion_id=assertion_id, decision="CONFIRM_EXISTING_ATTACHMENT",
            reviewer="human:tester", reason="x", matched_airport_id=1, allow_database_write=True,
        ))

        assert result.written is False
        assert result.decision_eligible is False
        assert db.read_bytes() == before_bytes


class TestReversalViaCli:
    def test_reversal_requires_explicit_supersedes_flag(self, tmp_path):
        db = tmp_path / "reversal.db"
        assertion_id, airport_id = _make_db(db)
        first = run_resolve(ResolveLegacyIdentityConfig(
            database=db, source_assertion_id=assertion_id, decision="CONFIRM_EXISTING_ATTACHMENT",
            reviewer="human:tester", reason="x", matched_airport_id=airport_id, allow_database_write=True,
        ))
        assert first.written is True

        without_supersession = run_resolve(ResolveLegacyIdentityConfig(
            database=db, source_assertion_id=assertion_id, decision="REJECT_EXISTING_ATTACHMENT",
            reviewer="human:tester", reason="actually wrong", allow_database_write=True,
        ))
        assert without_supersession.written is False
        assert without_supersession.decision_eligible is False

        with_supersession = run_resolve(ResolveLegacyIdentityConfig(
            database=db, source_assertion_id=assertion_id, decision="REJECT_EXISTING_ATTACHMENT",
            reviewer="human:tester", reason="actually wrong",
            supersedes_attestation_id=first.written_attestation_id, allow_database_write=True,
        ))
        assert with_supersession.written is True
        assert with_supersession.is_reversal is True


def test_migration_and_cli_work_together_end_to_end(tmp_path):
    """Real end-to-end proof: migrate an unmigrated DB, then inspect/write
    through the CLI - not just isolated unit pieces."""
    db = tmp_path / "e2e.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DROP TABLE IF EXISTS source_assertion_legacy_identity_attestations")
    conn.commit()
    conn.close()
    with Session(engine) as s:
        airport = Airport(name="Greenville Downtown", country="USA", iata_code="GMU", icao_code="KGMU")
        s.add(airport)
        s.commit()
        source = Source(title="grant", source_type="usaspending_grant")
        s.add(source)
        s.commit()
        assertion = SourceAssertion(
            source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
            raw_relevant_text="GREENVILLE EMAS", source_record_identifier="rec-1",
        )
        s.add(assertion)
        s.commit()
        assertion_id, airport_id = assertion.id, airport.id
    engine.dispose()

    migration.upgrade(db)

    result = run_resolve(ResolveLegacyIdentityConfig(
        database=db, source_assertion_id=assertion_id, decision="CONFIRM_EXISTING_ATTACHMENT",
        reviewer="human:tester", reason="matches", matched_airport_id=airport_id, allow_database_write=True,
    ))
    assert result.written is True
    assert result.current_effective_decision == "ATTACH_CONFIRMED"
