"""Tests for the new direct-unique-source structural anchor (R1's
`identity_anchor:direct_unique_source`, R2's `is_uniquely_sourced`) - the
approved fix for the real SA81/Signal44 legacy reconciliation blind spot
(docs/architecture: rwi-legacy-signal-reconciliation-gap-design and its own
real-DB blast-radius review).

Deliberately a separate file from tests/test_existing_signal_reconciliation.py
and tests/test_existing_signal_reconciliation_candidates.py (both files'
existing, all-passing test suites are the regression net proving this slice
changed no prior behavior for any anchor/compatibility rule already there;
this file is additive, new-anchor-specific coverage only). Fixtures below
mirror the REAL shapes found during the design mission's own read-only
blast-radius review of the production database (source_id=18/airport_id=63
one-grant-one-Signal shape; source_id=12 bulk-dataset five-collision shape;
source_id=3/45 cross-airport shapes) - never opens data/runway_safe.db
itself, matching this repository's own established "tests never touch the
real DB" discipline.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Signal, Source, SourceAssertion
from app.services.existing_signal_reconciliation import (
    ExistingSignalReconciliationOutcome,
    ExistingSignalReconciliationSubject,
    ReconciliationCandidateSignal,
    evaluate_existing_signal_reconciliation,
)
from app.services.existing_signal_reconciliation_candidates import (
    build_reconciliation_subject,
    find_reconciliation_candidates,
)

CLEAR = ExistingSignalReconciliationOutcome.CLEAR_TO_CREATE
POSSIBLE = ExistingSignalReconciliationOutcome.POSSIBLE_EXISTING_SIGNAL_MATCH


def subject(**kwargs) -> ExistingSignalReconciliationSubject:
    return ExistingSignalReconciliationSubject(**kwargs)


def candidate(signal_id: int, **kwargs) -> ReconciliationCandidateSignal:
    return ReconciliationCandidateSignal(signal_id=signal_id, **kwargs)


# ---------------------------------------------------------------------------
# 1-4. R1 unit-level: the new anchor's own precondition logic, isolated.
# ---------------------------------------------------------------------------


class TestDirectUniqueSourceAnchor:
    def test_unique_source_zero_linked_assertions_is_an_anchor(self):
        s = subject(airport_id=63, source_id=18)
        c = candidate(44, airport_id=63, source_id=18, is_uniquely_sourced=True)
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == POSSIBLE
        assert decision.candidate_signal_ids == (44,)
        assert any(
            "identity_anchor:direct_unique_source" in r and "source_id=18" in r for r in decision.reasons
        )

    def test_non_unique_source_id_never_anchors(self):
        s = subject(airport_id=19, source_id=12)
        c = candidate(13, airport_id=19, source_id=12, is_uniquely_sourced=False)
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()

    def test_zero_linked_assertions_required_even_when_unique(self):
        # A candidate WITH supporting assertions must be anchored only
        # through the existing provenance rule, never the new one - even if
        # is_uniquely_sourced happens to be True.
        s = subject(airport_id=1, source_id=18)
        c = candidate(
            44, airport_id=1, source_id=18, is_uniquely_sourced=True,
            supporting_source_ids=(99,),  # some OTHER already-linked source
        )
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()

    def test_differing_source_id_never_anchors_regardless_of_uniqueness(self):
        s = subject(airport_id=1, source_id=18)
        c = candidate(44, airport_id=1, source_id=19, is_uniquely_sourced=True)
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR

    def test_subject_with_no_source_id_never_anchors(self):
        s = subject(airport_id=1, source_id=None)
        c = candidate(44, airport_id=1, source_id=18, is_uniquely_sourced=True)
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR


class TestCompatibilityStillNeverAnchors:
    """Items 8-11: category/year/vendor/text-equality must remain
    COMPATIBILITY-only even in the presence of the new anchor logic - no
    weakening of Invariant 21."""

    def test_same_airport_and_year_alone_is_not_an_anchor(self):
        s = subject(airport_id=1, reference_year=2022)
        c = candidate(44, airport_id=1, reference_year=2022, is_uniquely_sourced=True)
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()

    def test_same_category_alone_is_not_an_anchor(self):
        s = subject(airport_id=1, category="new_installation")
        c = candidate(44, airport_id=1, category="new_installation", is_uniquely_sourced=True)
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR

    def test_vendor_overlap_alone_is_not_an_anchor(self):
        s = subject(airport_id=1, vendor_names=("Acme",))
        c = candidate(44, airport_id=1, confirmed_vendor="Acme", is_uniquely_sourced=True)
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR

    def test_no_raw_text_field_exists_on_either_dataclass(self):
        # Structural, not behavioral: text equality cannot even be expressed
        # as an input to this module, so it structurally cannot become an
        # anchor - matches the existing UNSAFE_FOR_RECONCILIATION discipline.
        import dataclasses

        for cls in (ExistingSignalReconciliationSubject, ReconciliationCandidateSignal):
            field_names = {f.name for f in dataclasses.fields(cls)}
            assert not any("text" in name or "title" in name or "notes" in name for name in field_names)


# ---------------------------------------------------------------------------
# 5-13. R2 + R1 integration, real production shapes (in-memory fixtures).
# ---------------------------------------------------------------------------


def _make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


class TestSA81Signal44RealShape:
    """Mirrors the real production shape exactly: one usaspending_grant
    Source (id analog), one legacy Signal created directly from it (zero
    supporting SourceAssertions), one later-governed SourceAssertion citing
    the same Source, airport_id shared, same runway/project context."""

    def _seed(self, session):
        from app.models import Airport

        airport = Airport(name="Greenville Downtown", iata_code="GMU", icao_code="KGMU", country="USA")
        session.add(airport)
        session.flush()
        source = Source(
            title="USAspending grant: Greenville Airport Commission", source_type="usaspending_grant",
            published_date=date(2022, 9, 13),
        )
        session.add(source)
        session.flush()
        legacy_signal = Signal(
            airport_id=airport.id, source_id=source.id, title="USAspending grant - $8.3M, FY2022",
            category="new_installation", confidence="high", status="identified", planning_year=2022,
        )
        session.add(legacy_signal)
        session.flush()
        governed_assertion = SourceAssertion(
            source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
            raw_relevant_text="RUNWAY 1 ENGINEERED MATERIAL ARRESTING SYSTEM (EMAS)",
            evidence_quality="direct_strong", source_record_identifier="rec-sa81",
        )
        session.add(governed_assertion)
        session.commit()
        return governed_assertion, legacy_signal

    def test_becomes_blocking_possible_match(self):
        engine, session = _make_db()
        assertion, legacy_signal = self._seed(session)

        subj = build_reconciliation_subject(assertion)
        candidates = find_reconciliation_candidates(session, assertion)
        decision = evaluate_existing_signal_reconciliation(subj, candidates)

        assert decision.outcome == POSSIBLE
        assert decision.candidate_signal_ids == (legacy_signal.id,)
        assert any("identity_anchor:direct_unique_source" in r for r in decision.reasons)
        session.close(); engine.dispose()

    def test_deterministic_across_repeated_evaluation(self):
        engine, session = _make_db()
        assertion, _ = self._seed(session)
        subj = build_reconciliation_subject(assertion)
        candidates = find_reconciliation_candidates(session, assertion)
        first = evaluate_existing_signal_reconciliation(subj, candidates)
        second = evaluate_existing_signal_reconciliation(subj, candidates)
        assert first == second
        session.close(); engine.dispose()


class TestSignal36ShapeRemainsNonBlocking:
    """A second, unrelated Signal at the same airport with a DIFFERENT
    source_id must never be swept in as a false positive."""

    def test_different_source_signal_is_not_anchored(self):
        engine, session = _make_db()
        from app.models import Airport

        airport = Airport(name="Greenville Downtown", country="USA")
        session.add(airport)
        session.flush()
        unrelated_source = Source(title="Bulk incident dataset", source_type="faa_tableau")
        session.add(unrelated_source)
        session.flush()
        unrelated_signal = Signal(
            airport_id=airport.id, source_id=unrelated_source.id, title="Unrelated incident signal",
            category="replacement_after_incident", confidence="medium", status="identified",
        )
        session.add(unrelated_signal)
        target_source = Source(title="USAspending grant", source_type="usaspending_grant")
        session.add(target_source)
        session.flush()
        assertion = SourceAssertion(
            source_id=target_source.id, airport_id=airport.id, assertion_type="project_construction",
            raw_relevant_text="ENGINEERED MATERIAL ARRESTING SYSTEM", source_record_identifier="rec-target",
        )
        session.add(assertion)
        session.commit()

        subj = build_reconciliation_subject(assertion)
        candidates = find_reconciliation_candidates(session, assertion)
        decision = evaluate_existing_signal_reconciliation(subj, candidates)

        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()
        session.close(); engine.dispose()


class TestSource12BulkCollisionShape:
    """Real-shape reproduction of the production Source-12 (bulk FAA
    incidents dataset) same-airport, multi-Signal collision - the exact
    counterexample class that rules out treating bare source_id equality as
    a general anchor."""

    def test_multiple_same_airport_signals_sharing_a_bulk_source_stay_non_blocking(self):
        engine, session = _make_db()
        from app.models import Airport

        airport = Airport(name="Bob Hope", country="USA")
        session.add(airport)
        session.flush()
        bulk_source = Source(title="FAA EMAS Incidents and Installations map", source_type="faa_tableau")
        session.add(bulk_source)
        session.flush()
        incident_2017 = Signal(
            airport_id=airport.id, source_id=bulk_source.id, title="Incident 2017",
            category="replacement_after_incident", confidence="medium", status="identified",
        )
        incident_2018 = Signal(
            airport_id=airport.id, source_id=bulk_source.id, title="Incident 2018",
            category="replacement_after_incident", confidence="medium", status="identified",
        )
        session.add_all([incident_2017, incident_2018])
        session.commit()

        # A hypothetical new SourceAssertion citing the same bulk dataset.
        new_assertion = SourceAssertion(
            source_id=bulk_source.id, airport_id=airport.id, assertion_type="project_construction",
            raw_relevant_text="ENGINEERED MATERIAL ARRESTING SYSTEM", source_record_identifier="rec-bulk-new",
        )
        session.add(new_assertion)
        session.commit()

        subj = build_reconciliation_subject(new_assertion)
        candidates = find_reconciliation_candidates(session, new_assertion)
        assert {c.signal_id for c in candidates} == {incident_2017.id, incident_2018.id}
        for c in candidates:
            assert c.is_uniquely_sourced is False

        decision = evaluate_existing_signal_reconciliation(subj, candidates)
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()
        session.close(); engine.dispose()


class TestCrossAirportShapeIsolated:
    """Real-shape reproduction of Source3/Source45: one Source's Signals
    span multiple DIFFERENT airports. Even though such a source_id would be
    non-unique database-wide (so the new anchor would never fire for it
    regardless), this proves R2's own airport scoping means a subject at
    Airport X never even sees a candidate Signal from Airport Y in the
    first place - no global source-id leak across airport scope."""

    def test_cross_airport_signals_never_become_candidates_for_each_other(self):
        engine, session = _make_db()
        from app.models import Airport

        airport_a = Airport(name="Airport A", country="USA")
        airport_b = Airport(name="Airport B", country="USA")
        session.add_all([airport_a, airport_b])
        session.flush()
        shared_source = Source(title="Multi-airport report", source_type="faa_construction_report")
        session.add(shared_source)
        session.flush()
        signal_a = Signal(
            airport_id=airport_a.id, source_id=shared_source.id, title="Project A",
            category="new_installation", confidence="medium", status="identified",
        )
        signal_b = Signal(
            airport_id=airport_b.id, source_id=shared_source.id, title="Project B",
            category="new_installation", confidence="medium", status="identified",
        )
        session.add_all([signal_a, signal_b])
        session.commit()

        new_assertion = SourceAssertion(
            source_id=shared_source.id, airport_id=airport_a.id, assertion_type="project_construction",
            raw_relevant_text="ENGINEERED MATERIAL ARRESTING SYSTEM", source_record_identifier="rec-shared-new",
        )
        session.add(new_assertion)
        session.commit()

        candidates = find_reconciliation_candidates(session, new_assertion)
        candidate_ids = {c.signal_id for c in candidates}
        assert signal_a.id in candidate_ids
        assert signal_b.id not in candidate_ids  # different airport - never even a candidate
        session.close(); engine.dispose()


class TestModernSignalProvenanceUnaffected:
    """Item 6/7: a Signal WITH a linked SourceAssertion (modern, or the
    already-resolved MSP-shaped conflict case) must preserve existing
    provenance behavior byte-for-byte - the new anchor is inert whenever
    supporting_source_ids/supporting_artifact_identities is non-empty."""

    def test_modern_signal_with_linked_assertion_still_anchors_via_existing_provenance_rule(self):
        engine, session = _make_db()
        from app.models import Airport

        airport = Airport(name="Modern Airport", country="USA")
        session.add(airport)
        session.flush()
        source = Source(title="Modern governed source", source_type="web_discovery")
        session.add(source)
        session.flush()
        signal = Signal(
            airport_id=airport.id, source_id=source.id, title="Modern signal",
            category="replacement", confidence="high", status="identified",
        )
        session.add(signal)
        session.flush()
        linking_assertion = SourceAssertion(
            source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
            raw_relevant_text="text", signal_id=signal.id, source_record_identifier="rec-linking",
        )
        session.add(linking_assertion)
        session.commit()

        new_assertion = SourceAssertion(
            source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
            raw_relevant_text="new text citing the same document", source_record_identifier="rec-modern-new",
        )
        session.add(new_assertion)
        session.commit()

        subj = build_reconciliation_subject(new_assertion)
        candidates = find_reconciliation_candidates(session, new_assertion)
        target = next(c for c in candidates if c.signal_id == signal.id)
        assert target.supporting_source_ids == (source.id,)  # existing anchor path is populated

        decision = evaluate_existing_signal_reconciliation(subj, candidates)
        assert decision.outcome == POSSIBLE
        assert decision.candidate_signal_ids == (signal.id,)
        # Fired via the EXISTING provenance rule, never the new one.
        assert any("identity_anchor:provenance" in r for r in decision.reasons)
        assert not any("identity_anchor:direct_unique_source" in r for r in decision.reasons)
        session.close(); engine.dispose()

    def test_conflicting_direct_source_id_vs_linked_provenance_fails_closed_unchanged(self):
        # Real-shape reproduction of the already-resolved Signal67 case:
        # Signal.source_id differs from its own linked assertion's
        # source_id. The new anchor must never "repair" or override this -
        # a direct match on the Signal's OWN source_id must not even be
        # considered once it has any supporting assertion at all.
        engine, session = _make_db()
        from app.models import Airport

        airport = Airport(name="Historical Airport", country="USA")
        session.add(airport)
        session.flush()
        original_source = Source(title="Newsletter mention", source_type="shareholder_newsletter")
        later_source = Source(title="Governed procurement memo", source_type="web_discovery")
        session.add_all([original_source, later_source])
        session.flush()
        signal = Signal(
            airport_id=airport.id, source_id=original_source.id, title="Historical signal",
            category="replacement", confidence="high", status="identified",
        )
        session.add(signal)
        session.flush()
        later_linking_assertion = SourceAssertion(
            source_id=later_source.id, airport_id=airport.id, assertion_type="project_construction",
            raw_relevant_text="later document text", signal_id=signal.id, source_record_identifier="rec-later-linking",
        )
        session.add(later_linking_assertion)
        session.commit()

        # A new subject citing the ORIGINAL (newsletter) source directly -
        # must NOT anchor, since the candidate already has supporting
        # assertions (even though none of them cite this exact source_id).
        new_assertion = SourceAssertion(
            source_id=original_source.id, airport_id=airport.id, assertion_type="project_construction",
            raw_relevant_text="newsletter text again", source_record_identifier="rec-newsletter-new",
        )
        session.add(new_assertion)
        session.commit()

        subj = build_reconciliation_subject(new_assertion)
        candidates = find_reconciliation_candidates(session, new_assertion)
        target = next(c for c in candidates if c.signal_id == signal.id)
        assert target.supporting_source_ids == (later_source.id,)  # conflicts with target.source_id

        decision = evaluate_existing_signal_reconciliation(subj, candidates)
        assert decision.outcome == CLEAR  # unchanged from pre-existing behavior
        assert decision.candidate_signal_ids == ()
        session.close(); engine.dispose()
