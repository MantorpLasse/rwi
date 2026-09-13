"""Tests for app.models.signal_lifecycle_assessment.SignalLifecycleAssessment
and app.services.signal_lifecycle_assessment (RWI HQ "SLT2 - Governed Signal
Lifecycle Assessment" mission).

Every test uses an isolated in-memory SQLite database, matching this
repository's own established convention for testing append-only governance
models (tests/test_migrate_signal_amendment_action.py and friends).
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Installation, Signal
from app.models.signal_lifecycle_assessment import SignalLifecycleAssessment
from app.static_export.signal_lifecycle import SignalLifecycleState
from app.services.signal_lifecycle_assessment import (
    _VALID_STATES,
    SignalLifecycleAssessmentError,
    get_latest_signal_lifecycle_assessment,
    record_signal_lifecycle_assessment,
    resolve_effective_signal_lifecycle,
)

TODAY = date(2026, 9, 13)


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_signal(session: Session, **overrides) -> Signal:
    airport = Airport(name="Test Field", country="XX")
    session.add(airport)
    session.flush()
    kwargs = dict(airport_id=airport.id, title="Test signal", category="replacement", confidence="high")
    kwargs.update(overrides)
    signal = Signal(**kwargs)
    session.add(signal)
    session.commit()
    return signal


def test_valid_states_literal_stays_in_sync_with_the_real_enum():
    """app.services.signal_lifecycle_assessment._VALID_STATES is a literal
    tuple (to avoid a module-level import cycle with app.static_export -
    see that module's own comment), not a live import of
    SignalLifecycleState - this guards against silent vocabulary drift if a
    future mission ever changes that enum's members."""
    assert _VALID_STATES == frozenset(state.value for state in SignalLifecycleState)


MSP_REASON = (
    "Confirmed vendor/order and associated 2025 execution window has elapsed; "
    "treat as realized/historical for intelligence relevance. Physical EMAS "
    "completion, formal acceptance and final payment remain unconfirmed."
)


# --- 1/2/3/4: effective resolution, machine baseline preserved -------------


class TestEffectiveResolution:
    def test_no_slt2_row_effective_equals_slt1(self):
        engine = _engine()
        with Session(engine) as session:
            signal = _seed_signal(session, confirmed_vendor="Runway Safe")
            effective = resolve_effective_signal_lifecycle(session, signal, today=TODAY)
            assert effective.effective_state == SignalLifecycleState.ACTIVE_OPPORTUNITY
            assert effective.machine_state == SignalLifecycleState.ACTIVE_OPPORTUNITY
            assert effective.effective_source == "machine"
            assert effective.reviewer is None
            assert effective.assessed_at is None

    def test_slt2_row_overrides_effective_state(self):
        engine = _engine()
        with Session(engine) as session:
            signal = _seed_signal(session, confirmed_vendor="Runway Safe")
            record_signal_lifecycle_assessment(
                session, signal_id=signal.id, state="realized_historical",
                reason=MSP_REASON, reviewer="human:rwi-owner",
            )
            session.commit()
            effective = resolve_effective_signal_lifecycle(session, signal, today=TODAY)
            assert effective.effective_state == SignalLifecycleState.REALIZED_HISTORICAL
            assert effective.effective_source == "governed_assessment"

    def test_machine_lifecycle_remains_inspectable_after_override(self):
        """The SLT1 machine baseline must never be discarded just because a
        governed override exists - it stays available as separate
        provenance."""
        engine = _engine()
        with Session(engine) as session:
            signal = _seed_signal(session, confirmed_vendor="Runway Safe")
            record_signal_lifecycle_assessment(
                session, signal_id=signal.id, state="realized_historical",
                reason=MSP_REASON, reviewer="human:rwi-owner",
            )
            session.commit()
            effective = resolve_effective_signal_lifecycle(session, signal, today=TODAY)
            assert effective.machine_state == SignalLifecycleState.ACTIVE_OPPORTUNITY
            assert "confirmed vendor" in effective.machine_reason
            assert effective.effective_state != effective.machine_state

    def test_reason_reviewer_timestamp_exposed_by_effective_resolver(self):
        engine = _engine()
        with Session(engine) as session:
            signal = _seed_signal(session)
            record_signal_lifecycle_assessment(
                session, signal_id=signal.id, state="stale_unresolved",
                reason="No new evidence in over five years.", reviewer="human:rwi-owner",
            )
            session.commit()
            effective = resolve_effective_signal_lifecycle(session, signal, today=TODAY)
            assert effective.effective_reason == "No new evidence in over five years."
            assert effective.reviewer == "human:rwi-owner"
            assert effective.assessed_at is not None


# --- 5/6/7/8: immutability and validation ----------------------------------


class TestImmutabilityAndValidation:
    def test_assessment_row_is_immutable(self):
        engine = _engine()
        with Session(engine) as session:
            signal = _seed_signal(session)
            assessment = record_signal_lifecycle_assessment(
                session, signal_id=signal.id, state="developing_watch",
                reason="Recent evidence, timing uncertain.", reviewer="human:rwi-owner",
            )
            session.commit()
            assessment.reason = "Edited after the fact"
            with pytest.raises(ValueError):
                session.commit()
            session.rollback()

    def test_assessment_row_cannot_be_deleted(self):
        engine = _engine()
        with Session(engine) as session:
            signal = _seed_signal(session)
            assessment = record_signal_lifecycle_assessment(
                session, signal_id=signal.id, state="developing_watch",
                reason="Recent evidence, timing uncertain.", reviewer="human:rwi-owner",
            )
            session.commit()
            session.delete(assessment)
            with pytest.raises(ValueError):
                session.commit()
            session.rollback()

    def test_invalid_lifecycle_state_rejected(self):
        engine = _engine()
        with Session(engine) as session:
            signal = _seed_signal(session)
            with pytest.raises(SignalLifecycleAssessmentError):
                record_signal_lifecycle_assessment(
                    session, signal_id=signal.id, state="not_a_real_state",
                    reason="x", reviewer="human:rwi-owner",
                )

    def test_blank_reason_rejected(self):
        engine = _engine()
        with Session(engine) as session:
            signal = _seed_signal(session)
            with pytest.raises(SignalLifecycleAssessmentError):
                record_signal_lifecycle_assessment(
                    session, signal_id=signal.id, state="active_opportunity",
                    reason="   ", reviewer="human:rwi-owner",
                )

    def test_blank_reviewer_rejected(self):
        engine = _engine()
        with Session(engine) as session:
            signal = _seed_signal(session)
            with pytest.raises(SignalLifecycleAssessmentError):
                record_signal_lifecycle_assessment(
                    session, signal_id=signal.id, state="active_opportunity",
                    reason="x", reviewer="   ",
                )

    def test_nonexistent_signal_rejected(self):
        engine = _engine()
        with Session(engine) as session:
            with pytest.raises(SignalLifecycleAssessmentError):
                record_signal_lifecycle_assessment(
                    session, signal_id=999999, state="active_opportunity",
                    reason="x", reviewer="human:rwi-owner",
                )


# --- 9/10: idempotency and appended history --------------------------------


class TestIdempotencyAndHistory:
    def test_repeated_identical_assessment_is_idempotent_no_op(self):
        engine = _engine()
        with Session(engine) as session:
            signal = _seed_signal(session)
            first = record_signal_lifecycle_assessment(
                session, signal_id=signal.id, state="stale_unresolved",
                reason="Same reason.", reviewer="human:rwi-owner",
            )
            session.commit()
            second = record_signal_lifecycle_assessment(
                session, signal_id=signal.id, state="stale_unresolved",
                reason="Same reason.", reviewer="human:rwi-owner",
            )
            session.commit()
            assert first.id == second.id
            count = session.query(SignalLifecycleAssessment).filter_by(signal_id=signal.id).count()
            assert count == 1

    def test_later_different_assessment_becomes_effective_earlier_preserved(self):
        engine = _engine()
        with Session(engine) as session:
            signal = _seed_signal(session)
            first = record_signal_lifecycle_assessment(
                session, signal_id=signal.id, state="developing_watch",
                reason="Initial read.", reviewer="human:rwi-owner",
            )
            session.commit()
            second = record_signal_lifecycle_assessment(
                session, signal_id=signal.id, state="realized_historical",
                reason="New evidence changes the read.", reviewer="human:rwi-owner",
            )
            session.commit()
            assert first.id != second.id

            latest = get_latest_signal_lifecycle_assessment(session, signal.id)
            assert latest.id == second.id

            effective = resolve_effective_signal_lifecycle(session, signal, today=TODAY)
            assert effective.effective_state == SignalLifecycleState.REALIZED_HISTORICAL

            # Earlier row still exists, unedited.
            preserved_first = session.get(SignalLifecycleAssessment, first.id)
            assert preserved_first is not None
            assert preserved_first.state == "developing_watch"
            assert preserved_first.reason == "Initial read."


# --- 11/12/13/14: no Signal mutation of any kind ---------------------------


class TestNoSignalMutation:
    def test_signal_status_unchanged_after_assessment_write(self):
        engine = _engine()
        with Session(engine) as session:
            signal = _seed_signal(session)
            assert signal.status is None
            record_signal_lifecycle_assessment(
                session, signal_id=signal.id, state="realized_historical",
                reason=MSP_REASON, reviewer="human:rwi-owner",
            )
            session.commit()
            session.refresh(signal)
            assert signal.status is None

    def test_signal_completion_date_unchanged_after_assessment_write(self):
        engine = _engine()
        with Session(engine) as session:
            signal = _seed_signal(session)
            assert signal.completion_date is None
            record_signal_lifecycle_assessment(
                session, signal_id=signal.id, state="realized_historical",
                reason=MSP_REASON, reviewer="human:rwi-owner",
            )
            session.commit()
            session.refresh(signal)
            assert signal.completion_date is None

    def test_signal_published_unchanged_after_assessment_write(self):
        engine = _engine()
        with Session(engine) as session:
            signal = _seed_signal(session, published=True)
            record_signal_lifecycle_assessment(
                session, signal_id=signal.id, state="realized_historical",
                reason=MSP_REASON, reviewer="human:rwi-owner",
            )
            session.commit()
            session.refresh(signal)
            assert signal.published is True

    def test_no_installation_created(self):
        engine = _engine()
        with Session(engine) as session:
            signal = _seed_signal(session)
            before = session.query(Installation).count()
            record_signal_lifecycle_assessment(
                session, signal_id=signal.id, state="realized_historical",
                reason=MSP_REASON, reviewer="human:rwi-owner",
            )
            session.commit()
            after = session.query(Installation).count()
            assert before == after == 0
            session.refresh(signal)
            assert signal.installation_id is None


# --- 17: MSP-shaped benchmark ------------------------------------------------


class TestMSPBenchmark:
    def test_msp_shaped_benchmark_machine_active_governed_realized(self):
        engine = _engine()
        with Session(engine) as session:
            signal = _seed_signal(
                session, title="MSP EMAS-order (Runway Safe bekräftad leverantör)",
                category="replacement", confirmed_vendor="Runway Safe",
            )
            before = resolve_effective_signal_lifecycle(session, signal, today=TODAY)
            assert before.machine_state == SignalLifecycleState.ACTIVE_OPPORTUNITY
            assert before.effective_state == SignalLifecycleState.ACTIVE_OPPORTUNITY
            assert before.effective_source == "machine"

            record_signal_lifecycle_assessment(
                session, signal_id=signal.id, state="realized_historical",
                reason=MSP_REASON, reviewer="human:rwi-owner",
            )
            session.commit()

            after = resolve_effective_signal_lifecycle(session, signal, today=TODAY)
            assert after.machine_state == SignalLifecycleState.ACTIVE_OPPORTUNITY
            assert after.effective_state == SignalLifecycleState.REALIZED_HISTORICAL
            assert after.effective_source == "governed_assessment"

            session.refresh(signal)
            assert signal.status is None
            assert signal.completion_date is None
            assert signal.installation_id is None
