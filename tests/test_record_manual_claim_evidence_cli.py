"""Tests for scripts/record_manual_claim_evidence.py ("RWI - First-Class
Manual Claim Evidence - Implementation" mission).

Every test uses an isolated temp-file SQLite database (tmp_path). Nothing
in this file ever opens data/runway_safe.db.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app import models as _models  # noqa: F401
from app.models import Airport, Source, SourceAssertion
from app.models.manual_claim_evidence import ManualClaimEvidence
from scripts.record_manual_claim_evidence import RecordManualClaimEvidenceConfig, main, run_record

_TEXT = "테스트공항 (Test Airport) EMAS project: KRW 27,000,000,000 secured for 2025년, 2026년."


def _make_db(path):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        airport = Airport(name="Test Airport", country="Testland")
        s.add(airport); s.commit()
        source = Source(title="Authority Record", source_type="Authority", reliability_level="official")
        s.add(source); s.commit()
        assertion = SourceAssertion(
            source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
            raw_relevant_text=_TEXT, source_record_identifier="rec-1", evidence_quality="direct_strong",
            identity_guard_decision="ATTACH_CONFIRMED",
        )
        s.add(assertion); s.commit()
        assertion_id = assertion.id
    engine.dispose()
    return assertion_id


class TestDryRun:
    def test_preview_shown_no_write_without_analyst(self, tmp_path):
        db = tmp_path / "test.db"
        assertion_id = _make_db(db)
        result = run_record(RecordManualClaimEvidenceConfig(
            database=db, source_assertion_id=assertion_id, claim_category="explicit_document_fact",
            subject="EMAS project", statement="Budget secured.",
            evidence_excerpt="테스트공항 (Test Airport) EMAS project: KRW 27,000,000,000 secured",
        ))
        assert result.preview is not None
        assert result.preview["eligible"] is True
        assert result.written is False

    def test_preview_causes_zero_writes(self, tmp_path):
        db = tmp_path / "test.db"
        assertion_id = _make_db(db)
        before_bytes = db.read_bytes()
        run_record(RecordManualClaimEvidenceConfig(
            database=db, source_assertion_id=assertion_id, claim_category="explicit_document_fact",
            subject="EMAS project", statement="Budget secured.",
            evidence_excerpt="테스트공항 (Test Airport) EMAS project: KRW 27,000,000,000 secured",
        ))
        assert db.read_bytes() == before_bytes

    def test_dry_run_with_analyst_does_not_write(self, tmp_path):
        db = tmp_path / "test.db"
        assertion_id = _make_db(db)
        result = run_record(RecordManualClaimEvidenceConfig(
            database=db, source_assertion_id=assertion_id, claim_category="explicit_document_fact",
            subject="EMAS project", statement="Budget secured.",
            evidence_excerpt="테스트공항 (Test Airport) EMAS project: KRW 27,000,000,000 secured",
            analyst="human:tester",
        ))
        assert result.decision_eligible is True
        assert result.written is False
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.query(ManualClaimEvidence).count() == 0


class TestWrite:
    def test_allow_database_write_required(self, tmp_path):
        db = tmp_path / "test.db"
        assertion_id = _make_db(db)
        result = run_record(RecordManualClaimEvidenceConfig(
            database=db, source_assertion_id=assertion_id, claim_category="explicit_document_fact",
            subject="EMAS project", statement="Budget secured.",
            evidence_excerpt="테스트공항 (Test Airport) EMAS project: KRW 27,000,000,000 secured",
            analyst="human:tester", allow_database_write=False,
        ))
        assert result.written is False

    def test_write_persists_claim(self, tmp_path):
        db = tmp_path / "test.db"
        assertion_id = _make_db(db)
        result = run_record(RecordManualClaimEvidenceConfig(
            database=db, source_assertion_id=assertion_id, claim_category="explicit_document_fact",
            subject="EMAS project", statement="Budget secured.",
            evidence_excerpt="테스트공항 (Test Airport) EMAS project: KRW 27,000,000,000 secured",
            financial_amount="27000000000", financial_amount_evidence_token="27,000,000,000",
            financial_currency="KRW", financial_semantic_role="budget_secured",
            analyst="human:tester", allow_database_write=True,
        ))
        assert result.written is True
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            row = s.get(ManualClaimEvidence, result.written_id)
            assert row.financial_amount == "27000000000"
            assert row.financial_currency == "KRW"

    def test_ineligible_never_writes(self, tmp_path):
        db = tmp_path / "test.db"
        assertion_id = _make_db(db)
        result = run_record(RecordManualClaimEvidenceConfig(
            database=db, source_assertion_id=assertion_id, claim_category="explicit_document_fact",
            subject="x", statement="y", evidence_excerpt="not present in preserved text",
            analyst="human:tester", allow_database_write=True,
        ))
        assert result.written is False
        assert result.decision_eligible is False


class TestMain:
    def test_main_returns_zero_on_successful_dry_run(self, tmp_path):
        db = tmp_path / "test.db"
        assertion_id = _make_db(db)
        code = main([
            "--database", str(db), "--source-assertion-id", str(assertion_id),
            "--claim-category", "explicit_document_fact", "--subject", "EMAS project",
            "--statement", "Budget secured.",
            "--evidence-excerpt", "테스트공항 (Test Airport) EMAS project: KRW 27,000,000,000 secured",
        ])
        assert code == 0

    def test_main_returns_nonzero_on_bad_excerpt(self, tmp_path):
        db = tmp_path / "test.db"
        assertion_id = _make_db(db)
        code = main([
            "--database", str(db), "--source-assertion-id", str(assertion_id),
            "--claim-category", "explicit_document_fact", "--subject", "x", "--statement", "y",
            "--evidence-excerpt", "not present",
        ])
        assert code == 1
