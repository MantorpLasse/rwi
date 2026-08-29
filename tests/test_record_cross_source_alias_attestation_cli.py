"""Tests for scripts/record_cross_source_alias_attestation.py
(docs/architecture, "RWI - Cross-Source Governed Airport Identity Binding -
Architecture Recon" mission's own Option C).

Every test uses an isolated temp-file SQLite database (tmp_path). Nothing
in this file ever opens data/runway_safe.db.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app import models as _models  # noqa: F401
from app.models import Airport, Source, SourceAssertion
from app.models.source_assertion_cross_source_alias_attestation import (
    SourceAssertionCrossSourceAliasAttestation,
)
from app.services.airport_alias import record_airport_alias
from scripts.record_cross_source_alias_attestation import (
    RecordCrossSourceAliasAttestationConfig,
    main,
    run_record,
)

_EXCERPT = "테스트공항(Test Airport) is the official name."
_ALIAS = "테스트공항"


def _make_db(path):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        airport = Airport(name="Test Airport", country="Testland")
        s.add(airport)
        s.commit()

        source_a = Source(title="Registry", source_type="government", reliability_level="official")
        s.add(source_a)
        s.commit()
        admitting_assertion = SourceAssertion(
            source_id=source_a.id, airport_id=airport.id, assertion_type="airport_inventory",
            raw_relevant_text=_EXCERPT, source_record_identifier="rec-admit", evidence_quality="direct_strong",
        )
        s.add(admitting_assertion)
        s.commit()
        alias_result = record_airport_alias(
            s, airport_id=airport.id, source_id=source_a.id, source_assertion_id=admitting_assertion.id,
            alias=_ALIAS, evidence_excerpt=_EXCERPT, analyst="human:tester",
        )
        s.commit()

        source_b = Source(title="Independent authority", source_type="government", reliability_level="official")
        s.add(source_b)
        s.commit()
        being_attested = SourceAssertion(
            source_id=source_b.id, airport_id=airport.id, assertion_type="airport_inventory",
            raw_relevant_text=f"{_ALIAS} EMAS project underway.", source_record_identifier="rec-attest",
            evidence_quality="direct_strong", identity_guard_decision="ATTACH_PROVISIONAL",
        )
        s.add(being_attested)
        s.commit()
        alias_id, assertion_id = alias_result.alias_id, being_attested.id
    engine.dispose()
    return alias_id, assertion_id


class TestDryRun:
    def test_preview_shown_no_write_without_analyst_reason(self, tmp_path):
        db = tmp_path / "test.db"
        alias_id, assertion_id = _make_db(db)

        result = run_record(RecordCrossSourceAliasAttestationConfig(
            database=db, source_assertion_id=assertion_id, matched_alias_id=alias_id,
        ))

        assert result.preview is not None
        assert result.preview["eligible"] is True
        assert result.written is False

    def test_preview_causes_zero_writes(self, tmp_path):
        db = tmp_path / "test.db"
        alias_id, assertion_id = _make_db(db)
        before_bytes = db.read_bytes()

        run_record(RecordCrossSourceAliasAttestationConfig(
            database=db, source_assertion_id=assertion_id, matched_alias_id=alias_id,
        ))

        assert db.read_bytes() == before_bytes

    def test_dry_run_with_analyst_and_reason_does_not_write(self, tmp_path):
        db = tmp_path / "test.db"
        alias_id, assertion_id = _make_db(db)

        result = run_record(RecordCrossSourceAliasAttestationConfig(
            database=db, source_assertion_id=assertion_id, matched_alias_id=alias_id,
            analyst="human:tester", reason="Independent official source uses the governed alias.",
        ))

        assert result.decision_eligible is True
        assert result.written is False

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.query(SourceAssertionCrossSourceAliasAttestation).count() == 0

    def test_nonexistent_source_assertion_blocked(self, tmp_path):
        db = tmp_path / "test.db"
        alias_id, _assertion_id = _make_db(db)

        result = run_record(RecordCrossSourceAliasAttestationConfig(
            database=db, source_assertion_id=999999, matched_alias_id=alias_id,
        ))

        assert result.blockers


class TestWrite:
    def test_allow_database_write_required(self, tmp_path):
        db = tmp_path / "test.db"
        alias_id, assertion_id = _make_db(db)

        result = run_record(RecordCrossSourceAliasAttestationConfig(
            database=db, source_assertion_id=assertion_id, matched_alias_id=alias_id,
            analyst="human:tester", reason="reason", allow_database_write=False,
        ))

        assert result.written is False

    def test_write_persists_attestation(self, tmp_path):
        db = tmp_path / "test.db"
        alias_id, assertion_id = _make_db(db)

        result = run_record(RecordCrossSourceAliasAttestationConfig(
            database=db, source_assertion_id=assertion_id, matched_alias_id=alias_id,
            analyst="human:tester", reason="reason", allow_database_write=True,
        ))

        assert result.written is True
        assert result.written_attestation_id is not None

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            row = s.get(SourceAssertionCrossSourceAliasAttestation, result.written_attestation_id)
            assert row.source_assertion_id == assertion_id
            assert row.matched_alias_id == alias_id

    def test_ineligible_never_writes_even_with_flag(self, tmp_path):
        db = tmp_path / "test.db"
        alias_id, assertion_id = _make_db(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assertion = s.get(SourceAssertion, assertion_id)
            assertion.identity_guard_decision = "INSUFFICIENT_IDENTITY"
            s.commit()

        result = run_record(RecordCrossSourceAliasAttestationConfig(
            database=db, source_assertion_id=assertion_id, matched_alias_id=alias_id,
            analyst="human:tester", reason="reason", allow_database_write=True,
        ))

        assert result.written is False
        assert result.decision_eligible is False


class TestMain:
    def test_main_returns_zero_on_successful_dry_run(self, tmp_path):
        db = tmp_path / "test.db"
        alias_id, assertion_id = _make_db(db)

        code = main([
            "--database", str(db), "--source-assertion-id", str(assertion_id),
            "--matched-alias-id", str(alias_id),
        ])
        assert code == 0

    def test_main_returns_nonzero_on_blocked(self, tmp_path):
        db = tmp_path / "test.db"
        _alias_id, _assertion_id = _make_db(db)

        code = main([
            "--database", str(db), "--source-assertion-id", "999999",
            "--matched-alias-id", "999999",
        ])
        assert code == 1
