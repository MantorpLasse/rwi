"""Tests for scripts/record_signal_lifecycle_assessment.py (RWI HQ "SLT2 -
Governed Signal Lifecycle Assessment" mission).

Every test uses an isolated temp-file SQLite database (tmp_path). Nothing in
this file ever opens data/runway_safe.db. Modeled directly on
tests/test_resolve_source_assertion_identity.py's own `run_*()`-direct-call
convention.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app import models as _models  # noqa: F401
from app.models import Airport, Signal
from app.models.signal_lifecycle_assessment import SignalLifecycleAssessment
from scripts.record_signal_lifecycle_assessment import (
    RecordSignalLifecycleAssessmentConfig,
    run_record,
)

MSP_REASON = (
    "Confirmed vendor/order and associated 2025 execution window has elapsed; "
    "treat as realized/historical for intelligence relevance. Physical EMAS "
    "completion, formal acceptance and final payment remain unconfirmed."
)


def _make_db(path, **signal_overrides):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        airport = Airport(name="Minneapolis-Saint Paul International", country="USA")
        s.add(airport)
        s.flush()
        kwargs = dict(
            airport_id=airport.id, title="MSP EMAS-order (Runway Safe bekräftad leverantör)",
            category="replacement", confidence="high", confirmed_vendor="Runway Safe",
        )
        kwargs.update(signal_overrides)
        signal = Signal(**kwargs)
        s.add(signal)
        s.commit()
        signal_id = signal.id
    engine.dispose()
    return signal_id


class TestInspectModeIsReadOnly:
    def test_inspect_shows_machine_and_effective_lifecycle_no_writes(self, tmp_path):
        db = tmp_path / "inspect.db"
        signal_id = _make_db(db)

        config = RecordSignalLifecycleAssessmentConfig(database=db, signal_id=signal_id)
        result = run_record(config)

        assert result.signal_found is True
        assert result.machine_state == "active_opportunity"
        assert result.effective_state == "active_opportunity"
        assert result.effective_source == "machine"
        assert result.assessment_history == []
        assert result.written is False

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.query(SignalLifecycleAssessment).count() == 0

    def test_inspect_missing_signal_reports_blocker(self, tmp_path):
        db = tmp_path / "missing.db"
        Base.metadata.create_all(create_engine(f"sqlite:///{db}"))
        config = RecordSignalLifecycleAssessmentConfig(database=db, signal_id=999999)
        result = run_record(config)
        assert result.signal_found is False
        assert result.blockers


class TestPreviewModeIsReadOnly:
    def test_preview_shows_before_after_without_writing(self, tmp_path):
        db = tmp_path / "preview.db"
        signal_id = _make_db(db)

        config = RecordSignalLifecycleAssessmentConfig(
            database=db, signal_id=signal_id, state="realized_historical",
            reason=MSP_REASON, reviewer="human:rwi-owner", allow_database_write=False,
        )
        result = run_record(config)

        assert result.action_eligible is True
        assert result.written is False
        # Preview reports the pre-write effective state (write did not happen).
        assert result.effective_state == "active_opportunity"
        assert result.effective_source == "machine"

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.query(SignalLifecycleAssessment).count() == 0

    def test_preview_rejects_invalid_state(self, tmp_path):
        db = tmp_path / "preview_invalid.db"
        signal_id = _make_db(db)
        config = RecordSignalLifecycleAssessmentConfig(
            database=db, signal_id=signal_id, state="not_a_real_state",
            reason="x", reviewer="human:rwi-owner",
        )
        result = run_record(config)
        assert result.action_eligible is False
        assert result.action_refusal_reason


class TestWriteRequiresExplicitGate:
    def test_write_requires_allow_database_write(self, tmp_path):
        db = tmp_path / "gate.db"
        signal_id = _make_db(db)
        config = RecordSignalLifecycleAssessmentConfig(
            database=db, signal_id=signal_id, state="realized_historical",
            reason=MSP_REASON, reviewer="human:rwi-owner", allow_database_write=False,
        )
        run_record(config)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.query(SignalLifecycleAssessment).count() == 0

    def test_write_with_gate_records_assessment_and_updates_effective_state(self, tmp_path):
        db = tmp_path / "write.db"
        signal_id = _make_db(db)
        config = RecordSignalLifecycleAssessmentConfig(
            database=db, signal_id=signal_id, state="realized_historical",
            reason=MSP_REASON, reviewer="human:rwi-owner", allow_database_write=True,
        )
        result = run_record(config)

        assert result.written is True
        assert result.effective_state == "realized_historical"
        assert result.effective_source == "governed_assessment"

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            rows = s.query(SignalLifecycleAssessment).filter_by(signal_id=signal_id).all()
            assert len(rows) == 1
            assert rows[0].state == "realized_historical"
            assert rows[0].reviewer == "human:rwi-owner"

            signal = s.get(Signal, signal_id)
            assert signal.status is None
            assert signal.completion_date is None
            assert signal.published is True  # unchanged default

    def test_write_is_idempotent_no_op_on_exact_repeat(self, tmp_path):
        db = tmp_path / "write_repeat.db"
        signal_id = _make_db(db)
        config = RecordSignalLifecycleAssessmentConfig(
            database=db, signal_id=signal_id, state="realized_historical",
            reason=MSP_REASON, reviewer="human:rwi-owner", allow_database_write=True,
        )
        first = run_record(config)
        second = run_record(config)

        assert first.written is True
        assert second.written is True
        assert second.written_is_noop is True
        assert first.written_assessment_id == second.written_assessment_id

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.query(SignalLifecycleAssessment).filter_by(signal_id=signal_id).count() == 1
