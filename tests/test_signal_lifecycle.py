"""SLT1 (docs/architecture/rwi-signal-temporal-relevance-opportunity-
lifecycle-design.md): app.static_export.signal_lifecycle.derive_signal_lifecycle().

Pure-function unit tests use transient (never session-added) Signal/Source
ORM instances, matching this repository's own convention for testing pure
domain logic without database overhead. Every Signal constructed here is
never added to a session, never flushed, never committed - proving the
derivation itself is read-only requires no database at all.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.models import Signal, Source
from app.static_export.signal_lifecycle import (
    ACTIVE_STATUS_GRACE_YEARS,
    GRANT_DEVELOPING_WINDOW_YEARS,
    INCIDENT_RESEARCH_WINDOW_YEARS,
    SignalLifecycleState,
    derive_signal_lifecycle,
)

TODAY = date(2026, 8, 28)


def _incident_signal(airport_name: str, incident_date: str, **overrides) -> Signal:
    title = f"{airport_name} — EMAS-ersättning väntas efter incident ({incident_date})"
    kwargs = dict(
        title=title, category="replacement_after_incident", confidence="high",
        status="identified", probability_score=8.0,
    )
    kwargs.update(overrides)
    return Signal(**kwargs)


def _grant_signal(fiscal_year: int, **overrides) -> Signal:
    kwargs = dict(
        title="USAspending grant", category="new_installation", confidence="high",
        status="identified", probability_score=8.0, planning_year=fiscal_year,
        source=Source(title="grant", source_type="usaspending_grant"),
    )
    kwargs.update(overrides)
    return Signal(**kwargs)


# --- 1. REALIZED_HISTORICAL: the only machine-derivable unambiguous case ---

class TestRealizedHistorical:
    def test_installation_link_is_realized_historical(self):
        signal = Signal(title="x", category="new_installation", confidence="high", installation_id=73)
        result = derive_signal_lifecycle(signal, today=TODAY)
        assert result.state == SignalLifecycleState.REALIZED_HISTORICAL

    def test_completed_status_without_installation_link_is_realized_historical(self):
        """graduate_signal_to_installation.py always sets both together for a
        real governed graduation, but this module treats either alone as
        sufficient - status="completed" is itself explicit, unambiguous
        human-recorded evidence."""
        signal = Signal(title="x", category="replacement", confidence="high", status="completed")
        result = derive_signal_lifecycle(signal, today=TODAY)
        assert result.state == SignalLifecycleState.REALIZED_HISTORICAL

    def test_realized_historical_takes_priority_over_everything_else(self):
        """Even an incident-derived signal (normally age-gated) is
        REALIZED_HISTORICAL once graduated - the strongest evidence wins,
        checked first."""
        signal = _incident_signal("Test", "1999-01-01", installation_id=5)
        result = derive_signal_lifecycle(signal, today=TODAY)
        assert result.state == SignalLifecycleState.REALIZED_HISTORICAL


# --- 2. Incident-derived: the design doc's own real examples, exactly ---

class TestIncidentDerived:
    def test_jfk_1999_is_stale(self):
        signal = _incident_signal("JFK", "1999-05-01")
        result = derive_signal_lifecycle(signal, today=TODAY)
        assert result.state == SignalLifecycleState.STALE_UNRESOLVED
        assert "1999-05-01" in result.reason

    def test_gmu_2006_is_stale(self):
        signal = _incident_signal("GMU", "2006-07-01")
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.STALE_UNRESOLVED

    def test_lga_2016_is_stale(self):
        signal = _incident_signal("LGA", "2016-10-01")
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.STALE_UNRESOLVED

    def test_bkl_2018_is_stale(self):
        signal = _incident_signal("BKL", "2018-02-01")
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.STALE_UNRESOLVED

    def test_rdg_2021_07_boundary_is_stale(self):
        """5.16 years old as of TODAY - just past the research window."""
        signal = _incident_signal("RDG", "2021-07-01")
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.STALE_UNRESOLVED

    def test_sua_2021_09_boundary_is_developing_watch(self):
        """4.99 years old as of TODAY - just inside the research window;
        proves the boundary is a real, computed comparison, not a
        coincidence of two identical-looking dates landing on the same
        side."""
        signal = _incident_signal("SUA", "2021-09-01")
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.DEVELOPING_WATCH

    def test_roa_2025_is_developing_watch(self):
        signal = _incident_signal("ROA", "2025-09-01")
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.DEVELOPING_WATCH

    def test_teb_2026_is_developing_watch(self):
        signal = _incident_signal("TEB", "2026-04-01")
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.DEVELOPING_WATCH

    def test_old_and_recent_incident_share_confidence_and_score_unchanged(self):
        """Direct proof of the mission's own Phase 5 requirement: an old
        incident retains its real, earned confidence/score - lifecycle
        changes placement, never the evidence-confidence fields
        themselves."""
        old = _incident_signal("GMU", "2006-07-01")
        new = _incident_signal("ROA", "2025-09-01")
        derive_signal_lifecycle(old, today=TODAY)
        derive_signal_lifecycle(new, today=TODAY)
        assert old.confidence == new.confidence == "high"
        assert old.probability_score == new.probability_score == 8.0

    def test_unparseable_incident_title_fails_conservatively_to_stale(self):
        """A hand-retitled incident signal (title no longer matches the
        generator's own format) never crashes and never guesses - explicit
        conservative default per mission hard boundary 15."""
        signal = Signal(
            title="Someone renamed this signal", category="replacement_after_incident",
            confidence="high", status="identified",
        )
        result = derive_signal_lifecycle(signal, today=TODAY)
        assert result.state == SignalLifecycleState.STALE_UNRESOLVED
        assert "could not be determined" in result.reason

    def test_incident_research_window_constant_is_five_years(self):
        assert INCIDENT_RESEARCH_WINDOW_YEARS == 5.0


# --- 3. Federal-grant-derived (USAspending/AIP/IIJA) ---

class TestGrantDerived:
    def test_fy2026_grant_is_active(self):
        signal = _grant_signal(2026)
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.ACTIVE_OPPORTUNITY

    def test_fy2025_grant_is_developing_watch(self):
        signal = _grant_signal(2025)
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.DEVELOPING_WATCH

    def test_fy2024_grant_boundary_is_developing_watch(self):
        signal = _grant_signal(2024)
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.DEVELOPING_WATCH

    def test_fy2023_grant_is_stale(self):
        signal = _grant_signal(2023)
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.STALE_UNRESOLVED

    def test_fy2021_grant_is_stale(self):
        signal = _grant_signal(2021)
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.STALE_UNRESOLVED

    def test_aip_and_iija_grants_use_the_same_rule_as_usaspending(self):
        for source_type in ("aip_grant", "iija_grant"):
            signal = _grant_signal(2026, source=Source(title="g", source_type=source_type))
            assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.ACTIVE_OPPORTUNITY

    def test_grant_with_no_fiscal_year_fails_conservatively_to_stale(self):
        signal = Signal(
            title="grant", category="new_installation", confidence="high",
            source=Source(title="g", source_type="usaspending_grant"), planning_year=None,
        )
        result = derive_signal_lifecycle(signal, today=TODAY)
        assert result.state == SignalLifecycleState.STALE_UNRESOLVED

    def test_grant_developing_window_constant_is_two_years(self):
        assert GRANT_DEVELOPING_WINDOW_YEARS == 2


# --- 4. Structured opportunity evidence (G1-shaped: CIP/ALP/master plan/etc.) ---

class TestStructuredOpportunity:
    def test_committed_status_with_future_year_is_active(self):
        signal = Signal(title="x", category="new_installation", confidence="planned", status="alp", planning_year=2027)
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.ACTIVE_OPPORTUNITY

    def test_committed_status_with_current_year_is_active(self):
        signal = Signal(title="x", category="replacement", confidence="confirmed", status="procurement", planning_year=2026)
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.ACTIVE_OPPORTUNITY

    def test_environmental_review_with_future_year_is_still_developing_watch(self):
        """HYA-shaped (design doc S4): status maturity decides here, not
        year - an early/uncertain pipeline stage stays DEVELOPING_WATCH even
        with an explicit future year."""
        signal = Signal(title="x", category="replacement", confidence="planned", status="environmental_review", planning_year=2027)
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.DEVELOPING_WATCH

    def test_master_plan_status_is_developing_watch(self):
        signal = Signal(title="x", category="study", confidence="planned", status="master_plan", planning_year=2026)
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.DEVELOPING_WATCH

    def test_replacement_watch_category_is_developing_watch_regardless_of_confidence(self):
        signal = Signal(title="x", category="replacement_watch", confidence="speculative", status="identified")
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.DEVELOPING_WATCH

    def test_speculative_confidence_alone_is_developing_watch(self):
        signal = Signal(title="x", category="new_installation", confidence="speculative", status="identified")
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.DEVELOPING_WATCH

    def test_in_progress_construction_window_is_active(self):
        signal = Signal(
            title="x", category="maintenance", confidence="confirmed", status="under construction",
            construction_start=date(2026, 3, 30), completion_date=date(2026, 10, 3),
        )
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.ACTIVE_OPPORTUNITY

    def test_future_construction_window_is_active(self):
        signal = Signal(
            title="x", category="new_installation", confidence="confirmed", status="under construction",
            construction_start=date(2026, 8, 31), completion_date=date(2026, 11, 15),
        )
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.ACTIVE_OPPORTUNITY

    def test_committed_status_within_grace_window_is_active(self):
        """MDW/id68-shaped: planning_year one year stale, status=design - a
        committed pipeline stage does not stop being active the instant its
        own year passes by a small margin."""
        signal = Signal(title="x", category="maintenance", confidence="high", status="design", planning_year=2025)
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.ACTIVE_OPPORTUNITY

    def test_committed_status_beyond_grace_window_downgrades_to_developing_watch(self):
        signal = Signal(title="x", category="replacement", confidence="high", status="design", planning_year=2020)
        result = derive_signal_lifecycle(signal, today=TODAY)
        assert result.state == SignalLifecycleState.DEVELOPING_WATCH
        assert "stale" in result.reason

    def test_active_status_grace_constant_is_two_years(self):
        assert ACTIVE_STATUS_GRACE_YEARS == 2


# --- 5. Fallback: no title-sniffing, fails conservatively ---

class TestFallbackNoTextSniffing:
    def test_vendor_confirmed_no_status_no_year_is_developing_watch_not_active(self):
        """id 64/66/67-shaped (Santos Dumont/Charlotte/MSP "Runway Safe
        bekräftad leverantör"): no structured status, year, or installation
        link - the design document's own human-analyst read called this
        ACTIVE_OPPORTUNITY by reading the title text; this module
        deliberately never inspects title/notes text (mission hard boundary:
        no per-row narrative reading, no hardcoded special cases) and
        conservatively reads DEVELOPING_WATCH instead. This is the
        documented, intentional 3-signal divergence from the design
        document's own S4 count."""
        signal = Signal(
            title="Charlotte Douglas EMAS-order (Runway Safe bekräftad leverantör)",
            category="new_installation", confidence="high", confirmed_vendor=None,
        )
        result = derive_signal_lifecycle(signal, today=TODAY)
        assert result.state == SignalLifecycleState.DEVELOPING_WATCH
        assert "conservative default" in result.reason

    def test_future_year_alone_no_status_is_active(self):
        signal = Signal(title="x", category="new_installation", confidence="medium", target_year=2028)
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.ACTIVE_OPPORTUNITY

    def test_stale_year_alone_no_status_is_stale(self):
        signal = Signal(title="x", category="new_installation", confidence="medium", target_year=2019)
        assert derive_signal_lifecycle(signal, today=TODAY).state == SignalLifecycleState.STALE_UNRESOLVED

    def test_completely_bare_manually_created_signal_is_developing_watch(self):
        signal = Signal(title="Hand-entered lead", category="unknown", confidence="low")
        result = derive_signal_lifecycle(signal, today=TODAY)
        assert result.state == SignalLifecycleState.DEVELOPING_WATCH
        assert "insufficient structured evidence" in result.reason


# --- 6. Legacy confidence vocabulary never crashes, never mis-scored ---

class TestLegacyConfidenceVocabulary:
    def test_all_real_confidence_values_are_handled_without_error(self):
        for confidence in ("high", "medium", "low", "confirmed", "programmed", "planned", "speculative"):
            signal = Signal(title="x", category="replacement", confidence=confidence, status="identified")
            result = derive_signal_lifecycle(signal, today=TODAY)
            assert isinstance(result.state, SignalLifecycleState)


# --- 7. Determinism / read-only / no mutation ---

class TestDeterminismAndReadOnly:
    def test_same_inputs_always_produce_the_same_result(self):
        signal = _incident_signal("GMU", "2006-07-01")
        first = derive_signal_lifecycle(signal, today=TODAY)
        second = derive_signal_lifecycle(signal, today=TODAY)
        assert first == second

    def test_derivation_never_mutates_the_signal(self):
        signal = _incident_signal("GMU", "2006-07-01")
        before = dict(vars(signal))
        derive_signal_lifecycle(signal, today=TODAY)
        after = dict(vars(signal))
        assert before == after

    def test_result_is_frozen_dataclass(self):
        signal = Signal(title="x", category="replacement", confidence="high")
        result = derive_signal_lifecycle(signal, today=TODAY)
        with pytest.raises(Exception):
            result.state = SignalLifecycleState.OTHER  # type: ignore[misc]
