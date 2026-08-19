"""Tests for app.services.existing_signal_reconciliation_candidates
(docs/architecture/existing-signal-reconciliation-r2-candidate-adapter-report.md,
R2 of docs/architecture/existing-signal-reconciliation-guard-design.md's own
S16 roadmap). Every test uses an isolated in-memory SQLite database, modeled
on tests/test_governed_signal_creation.py's own established pattern.
"""
from __future__ import annotations

import ast
import inspect
from datetime import date

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    Airport,
    InstallationAssertionLink,
    PhysicalInstallationIdentity,
    Runway,
    Signal,
    Source,
    SourceAssertion,
)
from app.services import existing_signal_reconciliation_candidates as r2
from app.services.evidence_claim_semantics import (
    Claim,
    ClaimCategory,
    ClaimProvenance,
    RelationshipFact,
    TemporalContext,
    TemporalQualifier,
)
from app.services.existing_signal_reconciliation import (
    ExistingSignalReconciliationOutcome,
    evaluate_existing_signal_reconciliation,
)
from app.services.existing_signal_reconciliation_candidates import (
    build_reconciliation_subject,
    find_reconciliation_candidates,
)

CLEAR = ExistingSignalReconciliationOutcome.CLEAR_TO_CREATE
POSSIBLE = ExistingSignalReconciliationOutcome.POSSIBLE_EXISTING_SIGNAL_MATCH
LINKED = ExistingSignalReconciliationOutcome.ALREADY_LINKED


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def _airport(session, name="Test Airport", code="ZZZ") -> Airport:
    airport = Airport(name=name, iata_code=code, country="USA")
    session.add(airport)
    session.commit()
    return airport


def _runway(session, airport, designation="09/27") -> Runway:
    runway = Runway(airport_id=airport.id, designation=designation)
    session.add(runway)
    session.commit()
    return runway


def _source(session, title="Test source") -> Source:
    source = Source(title=title, source_type="web_discovery")
    session.add(source)
    session.commit()
    return source


def _signal(session, airport, **kwargs) -> Signal:
    kwargs.setdefault("title", "Test signal")
    kwargs.setdefault("category", "replacement")
    kwargs.setdefault("confidence", "medium")
    signal = Signal(airport_id=airport.id, **kwargs)
    session.add(signal)
    session.commit()
    return signal


def _assertion(session, source, airport=None, **kwargs) -> SourceAssertion:
    kwargs.setdefault("assertion_type", "project_construction")
    kwargs.setdefault("source_record_identifier", f"record-{id(kwargs)}-{source.id}")
    assertion = SourceAssertion(source=source, airport=airport, **kwargs)
    session.add(assertion)
    session.commit()
    return assertion


def _installation_identity(session, airport, runway=None) -> PhysicalInstallationIdentity:
    identity = PhysicalInstallationIdentity(airport_id=airport.id, runway_id=runway.id if runway else None)
    session.add(identity)
    session.commit()
    return identity


def _link(session, assertion, identity, outcome="SAME_PHYSICAL_INSTALLATION", supersedes_link_id=None) -> InstallationAssertionLink:
    link = InstallationAssertionLink(
        assertion_id=assertion.id,
        physical_installation_id=identity.id if identity else None,
        outcome=outcome,
        reason="test fixture",
        actor="test",
        supersedes_link_id=supersedes_link_id,
    )
    session.add(link)
    session.commit()
    return link


def _relationship_claim(party: str) -> Claim:
    return Claim(
        category=ClaimCategory.RELATIONSHIP,
        subject="vendor relationship",
        statement=f"{party} named as a relationship party",
        provenance=ClaimProvenance(
            artifact_identity="test.artifact.1", source_locator="loc-1", fragment_hash="hash-1",
            raw_text_excerpt="(fixture)",
        ),
        relationship=RelationshipFact(party=party, role="requested_sole_source_vendor"),
    )


def _temporal_claim(as_of: date) -> Claim:
    return Claim(
        category=ClaimCategory.TEMPORAL_STATEMENT,
        subject="document date",
        statement="document dated",
        provenance=ClaimProvenance(
            artifact_identity="test.artifact.1", source_locator="loc-1", fragment_hash="hash-1",
            raw_text_excerpt="(fixture)",
        ),
        temporal=TemporalContext(qualifier=TemporalQualifier.CURRENT_STATE_AS_OF_DOCUMENT_DATE, as_of_date=as_of),
    )


# --- 1/2. Same-airport candidate discovery / wrong airport excluded ---

class TestCandidateScope:
    def test_same_airport_candidate_discovered(self):
        engine, session = make_session()
        airport = _airport(session)
        signal = _signal(session, airport)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)

        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert [c.signal_id for c in candidates] == [signal.id]
        session.close(); engine.dispose()

    def test_wrong_airport_excluded(self):
        engine, session = make_session()
        airport_a = _airport(session, name="A", code="AAA")
        airport_b = _airport(session, name="B", code="BBB")
        _signal(session, airport_b)  # different airport - must not appear
        source = _source(session)
        subject_assertion = _assertion(session, source, airport_a)

        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates == ()
        session.close(); engine.dispose()

    def test_no_candidates_at_all_returns_empty_tuple(self):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates == ()
        session.close(); engine.dispose()


# --- 3. Deterministic ordering ---

class TestDeterministicOrdering:
    def test_candidates_ordered_by_signal_id_ascending(self):
        engine, session = make_session()
        airport = _airport(session)
        s3 = _signal(session, airport)
        s1 = _signal(session, airport)
        s2 = _signal(session, airport)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)

        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert [c.signal_id for c in candidates] == sorted([s1.id, s2.id, s3.id])
        session.close(); engine.dispose()

    def test_repeated_calls_produce_equal_tuples(self):
        engine, session = make_session()
        airport = _airport(session)
        _signal(session, airport)
        _signal(session, airport)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)

        first = find_reconciliation_candidates(session, subject_assertion)
        second = find_reconciliation_candidates(session, subject_assertion)
        assert first == second
        session.close(); engine.dispose()


# --- 4. NULL airport ---

class TestNullAirport:
    def test_null_airport_and_no_linked_signal_returns_empty(self):
        engine, session = make_session()
        source = _source(session)
        subject_assertion = _assertion(session, source, airport=None)
        assert subject_assertion.airport_id is None
        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates == ()
        session.close(); engine.dispose()

    def test_null_airport_does_not_trigger_global_scan(self):
        engine, session = make_session()
        other_airport = _airport(session)
        _signal(session, other_airport)  # exists in DB, must not be returned
        source = _source(session)
        subject_assertion = _assertion(session, source, airport=None)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates == ()
        session.close(); engine.dispose()

    def test_null_airport_but_linked_signal_still_returned(self):
        engine, session = make_session()
        airport = _airport(session)
        signal = _signal(session, airport)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport=None, signal_id=signal.id)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert [c.signal_id for c in candidates] == [signal.id]
        session.close(); engine.dispose()


# --- 5. Already-linked subject ---

class TestAlreadyLinkedSubject:
    def test_subject_existing_signal_id_mirrors_source_assertion(self):
        engine, session = make_session()
        airport = _airport(session)
        signal = _signal(session, airport)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport, signal_id=signal.id)

        subject = build_reconciliation_subject(subject_assertion)
        assert subject.existing_signal_id == signal.id
        session.close(); engine.dispose()

    def test_unlinked_subject_has_none_existing_signal_id(self):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        subject = build_reconciliation_subject(subject_assertion)
        assert subject.existing_signal_id is None
        session.close(); engine.dispose()

    def test_linked_signal_still_appears_even_with_differing_airport(self):
        # data-integrity anomaly (linked Signal at a different airport than
        # the assertion) must not be silently hidden by airport scoping
        engine, session = make_session()
        airport_a = _airport(session, name="A", code="AAA")
        airport_b = _airport(session, name="B", code="BBB")
        signal_at_b = _signal(session, airport_b)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport_a, signal_id=signal_at_b.id)

        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert signal_at_b.id in [c.signal_id for c in candidates]
        session.close(); engine.dispose()


# --- 6. Runway_id mapping ---

class TestRunwayIdMapping:
    def test_subject_runway_id_from_source_assertion_column(self):
        engine, session = make_session()
        airport = _airport(session)
        runway = _runway(session, airport)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport, runway_id=runway.id)
        subject = build_reconciliation_subject(subject_assertion)
        assert subject.runway_id == runway.id
        session.close(); engine.dispose()

    def test_subject_runway_id_null_when_unset(self):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        subject = build_reconciliation_subject(subject_assertion)
        assert subject.runway_id is None
        session.close(); engine.dispose()

    def test_candidate_runway_id_from_signal_column(self):
        engine, session = make_session()
        airport = _airport(session)
        runway = _runway(session, airport)
        signal = _signal(session, airport, runway_id=runway.id)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates[0].runway_id == runway.id
        session.close(); engine.dispose()


# --- 7. Category mapping ---

class TestCategoryMapping:
    def test_candidate_category_from_signal_column(self):
        engine, session = make_session()
        airport = _airport(session)
        _signal(session, airport, category="replacement")
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates[0].category == "replacement"
        session.close(); engine.dispose()

    def test_subject_category_is_explicit_caller_context_only(self):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        assert build_reconciliation_subject(subject_assertion).category is None
        assert build_reconciliation_subject(subject_assertion, category="replacement").category == "replacement"
        session.close(); engine.dispose()


# --- 8/9. Vendor mapping: structured only, never text-derived ---

class TestVendorMapping:
    def test_subject_vendor_names_from_relationship_claims(self):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        claims = (_relationship_claim("Acme EMAS"),)
        subject = build_reconciliation_subject(subject_assertion, claims)
        assert subject.vendor_names == ("Acme EMAS",)
        session.close(); engine.dispose()

    def test_subject_vendor_names_empty_without_claims(self):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        subject = build_reconciliation_subject(subject_assertion)
        assert subject.vendor_names == ()
        session.close(); engine.dispose()

    def test_candidate_vendor_from_confirmed_vendor_and_likely_supplier_columns(self):
        engine, session = make_session()
        airport = _airport(session)
        _signal(session, airport, confirmed_vendor="Acme EMAS", likely_supplier="Beta Safety")
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates[0].confirmed_vendor == "Acme EMAS"
        assert candidates[0].likely_supplier == "Beta Safety"
        session.close(); engine.dispose()

    def test_module_source_never_reads_title_or_notes_for_vendor(self):
        source_text = inspect.getsource(r2)
        assert "signal.title" not in source_text.lower()
        assert "signal.notes" not in source_text.lower()
        assert "source_notes" not in source_text.lower()


# --- 10. Temporal / year mapping ---

class TestTemporalYearMapping:
    def test_subject_evidence_date_is_earliest_claim_as_of_date(self):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        claims = (_temporal_claim(date(2024, 8, 1)), _temporal_claim(date(2024, 9, 1)))
        subject = build_reconciliation_subject(subject_assertion, claims)
        assert subject.evidence_date == date(2024, 8, 1)
        session.close(); engine.dispose()

    def test_subject_reference_year_is_explicit_context_only(self):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        assert build_reconciliation_subject(subject_assertion).reference_year is None
        assert build_reconciliation_subject(subject_assertion, reference_year=2024).reference_year == 2024
        session.close(); engine.dispose()

    def test_candidate_evidence_date_from_last_verified_at(self):
        engine, session = make_session()
        airport = _airport(session)
        _signal(session, airport, last_verified_at=date(2025, 5, 1))
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates[0].evidence_date == date(2025, 5, 1)
        session.close(); engine.dispose()

    def test_candidate_reference_year_priority_target_over_planning(self):
        engine, session = make_session()
        airport = _airport(session)
        _signal(session, airport, target_year=2026, planning_year=2025)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates[0].reference_year == 2026
        session.close(); engine.dispose()

    def test_candidate_reference_year_falls_back_to_construction_start(self):
        engine, session = make_session()
        airport = _airport(session)
        _signal(session, airport, construction_start=date(2027, 3, 1))
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates[0].reference_year == 2027
        session.close(); engine.dispose()

    def test_manual_year_estimate_never_used(self):
        engine, session = make_session()
        airport = _airport(session)
        _signal(session, airport, manual_year_estimate=1999)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates[0].reference_year is None
        session.close(); engine.dispose()


# --- 11. Supporting SourceAssertion ids ---

class TestSupportingSourceIds:
    def test_supporting_source_ids_from_linked_assertions_only(self):
        engine, session = make_session()
        airport = _airport(session)
        signal = _signal(session, airport, source_id=None)
        supporting_source = _source(session, title="Supporting doc")
        _assertion(session, supporting_source, airport, signal_id=signal.id)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)

        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates[0].supporting_source_ids == (supporting_source.id,)
        session.close(); engine.dispose()

    def test_signal_own_source_id_column_not_used_as_supporting_provenance(self):
        engine, session = make_session()
        airport = _airport(session)
        legacy_source = _source(session, title="Legacy single-document source")
        signal = _signal(session, airport, source_id=legacy_source.id)  # no supporting_source_assertions
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)

        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates[0].supporting_source_ids == ()
        session.close(); engine.dispose()


# --- 12. Supporting artifact identities ---

class TestSupportingArtifactIdentities:
    def test_supporting_artifact_identities_from_linked_assertions(self):
        engine, session = make_session()
        airport = _airport(session)
        signal = _signal(session, airport)
        supporting_source = _source(session)
        _assertion(session, supporting_source, airport, signal_id=signal.id, artifact_identity="doc.artifact.1")
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)

        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates[0].supporting_artifact_identities == ("doc.artifact.1",)
        session.close(); engine.dispose()

    def test_null_artifact_identity_excluded(self):
        engine, session = make_session()
        airport = _airport(session)
        signal = _signal(session, airport)
        supporting_source = _source(session)
        _assertion(session, supporting_source, airport, signal_id=signal.id, artifact_identity=None)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates[0].supporting_artifact_identities == ()
        session.close(); engine.dispose()


# --- 13/14. Physical-installation transitive mapping ---

class TestPhysicalInstallationTransitiveMapping:
    def test_transitive_installation_id_via_supporting_assertion(self):
        engine, session = make_session()
        airport = _airport(session)
        signal = _signal(session, airport)
        supporting_source = _source(session)
        supporting_assertion = _assertion(session, supporting_source, airport, signal_id=signal.id)
        identity = _installation_identity(session, airport)
        _link(session, supporting_assertion, identity)

        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates[0].physical_installation_ids == (identity.id,)
        session.close(); engine.dispose()

    def test_unresolved_link_outcome_does_not_contribute_identity(self):
        engine, session = make_session()
        airport = _airport(session)
        signal = _signal(session, airport)
        supporting_source = _source(session)
        supporting_assertion = _assertion(session, supporting_source, airport, signal_id=signal.id)
        _link(session, supporting_assertion, identity=None, outcome="UNRESOLVED")

        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates[0].physical_installation_ids == ()
        session.close(); engine.dispose()

    def test_no_installation_link_produces_empty_tuple(self):
        engine, session = make_session()
        airport = _airport(session)
        _signal(session, airport)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates[0].physical_installation_ids == ()
        session.close(); engine.dispose()

    def test_subject_physical_installation_ids_from_own_links(self):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        identity = _installation_identity(session, airport)
        _link(session, subject_assertion, identity)
        subject = build_reconciliation_subject(subject_assertion)
        assert subject.physical_installation_ids == (identity.id,)
        session.close(); engine.dispose()

    def test_unrelated_installation_link_on_a_different_assertion_does_not_leak_in(self):
        # An InstallationAssertionLink exists in the database, but it
        # belongs to a SourceAssertion that is not a supporting assertion of
        # any candidate Signal - it must never contaminate a candidate's own
        # physical_installation_ids.
        engine, session = make_session()
        airport = _airport(session)
        signal = _signal(session, airport)  # candidate has no supporting assertions at all

        unrelated_source = _source(session, title="Unrelated")
        unrelated_assertion = _assertion(session, unrelated_source, airport)  # signal_id=None
        unrelated_identity = _installation_identity(session, airport)
        _link(session, unrelated_assertion, unrelated_identity)

        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates[0].physical_installation_ids == ()
        session.close(); engine.dispose()


# --- Retracted (superseded) installation-identity decisions must not count ---

class TestSupersededInstallationLinkNotCounted:
    """Regression coverage for a real defect found during the R2 review
    checkpoint: a SAME_PHYSICAL_INSTALLATION link that a human has since
    corrected/retracted via a superseding link must not keep contributing a
    live identity anchor. Mirrors the append-only-history "latest decision
    wins" discipline already established for ReviewerAction."""

    def test_retracted_same_link_on_candidate_supporting_assertion_no_longer_counts(self):
        engine, session = make_session()
        airport = _airport(session)
        signal = _signal(session, airport)
        identity = _installation_identity(session, airport)
        supporting_source = _source(session)
        supporting_assertion = _assertion(session, supporting_source, airport, signal_id=signal.id)

        original = _link(session, supporting_assertion, identity, outcome="SAME_PHYSICAL_INSTALLATION")
        _link(session, supporting_assertion, identity=None, outcome="UNRESOLVED", supersedes_link_id=original.id)

        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates[0].physical_installation_ids == ()
        session.close(); engine.dispose()

    def test_retracted_same_link_on_subject_no_longer_counts(self):
        engine, session = make_session()
        airport = _airport(session)
        identity = _installation_identity(session, airport)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)

        original = _link(session, subject_assertion, identity, outcome="SAME_PHYSICAL_INSTALLATION")
        _link(session, subject_assertion, identity=None, outcome="UNRESOLVED", supersedes_link_id=original.id)

        subject = build_reconciliation_subject(subject_assertion)
        assert subject.physical_installation_ids == ()
        session.close(); engine.dispose()

    def test_corrected_different_to_same_link_now_correctly_counts(self):
        # The reverse direction: an initial DIFFERENT_PHYSICAL_INSTALLATION
        # decision later corrected to SAME_PHYSICAL_INSTALLATION must count
        # the corrected (latest) decision, not the stale one.
        engine, session = make_session()
        airport = _airport(session)
        signal = _signal(session, airport)
        wrong_identity = _installation_identity(session, airport)
        correct_identity = _installation_identity(session, airport)
        supporting_source = _source(session)
        supporting_assertion = _assertion(session, supporting_source, airport, signal_id=signal.id)

        original = _link(session, supporting_assertion, wrong_identity, outcome="DIFFERENT_PHYSICAL_INSTALLATION")
        _link(session, supporting_assertion, correct_identity, outcome="SAME_PHYSICAL_INSTALLATION", supersedes_link_id=original.id)

        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates[0].physical_installation_ids == (correct_identity.id,)
        session.close(); engine.dispose()

    def test_full_r1_integration_no_false_positive_anchor_from_retracted_link(self):
        engine, session = make_session()
        airport = _airport(session)
        identity = _installation_identity(session, airport)

        signal = _signal(session, airport)
        supporting_source = _source(session)
        supporting_assertion = _assertion(session, supporting_source, airport, signal_id=signal.id)
        original = _link(session, supporting_assertion, identity, outcome="SAME_PHYSICAL_INSTALLATION")
        _link(session, supporting_assertion, identity=None, outcome="UNRESOLVED", supersedes_link_id=original.id)

        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        _link(session, subject_assertion, identity, outcome="SAME_PHYSICAL_INSTALLATION")

        subject = build_reconciliation_subject(subject_assertion)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        decision = evaluate_existing_signal_reconciliation(subject, candidates)
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()
        session.close(); engine.dispose()


# --- 15/16. Multiple supporting assertions / duplicate nested data deduped ---

class TestMultipleSupportingAssertionsDeduped:
    def test_multiple_supporting_assertions_aggregate_source_ids(self):
        engine, session = make_session()
        airport = _airport(session)
        signal = _signal(session, airport)
        source_a = _source(session, title="A")
        source_b = _source(session, title="B")
        _assertion(session, source_a, airport, signal_id=signal.id)
        _assertion(session, source_b, airport, signal_id=signal.id)

        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates[0].supporting_source_ids == tuple(sorted([source_a.id, source_b.id]))
        session.close(); engine.dispose()

    def test_two_assertions_linked_to_same_installation_identity_deduped(self):
        engine, session = make_session()
        airport = _airport(session)
        signal = _signal(session, airport)
        identity = _installation_identity(session, airport)
        source_a = _source(session, title="A")
        source_b = _source(session, title="B")
        assertion_a = _assertion(session, source_a, airport, signal_id=signal.id)
        assertion_b = _assertion(session, source_b, airport, signal_id=signal.id)
        _link(session, assertion_a, identity)
        _link(session, assertion_b, identity)

        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert candidates[0].physical_installation_ids == (identity.id,)  # deduped, not (id, id)
        session.close(); engine.dispose()


# --- 17/18. MSP-shaped fixture: pre-resolution and post-resolution ---

class TestMSPShapedFixture:
    """A faithful, structural fixture reconstructing the real MSP #222/#67/#41
    shapes already proven by R1's own golden-case tests - built here through
    real ORM rows and this module's own adapter functions, not by
    hand-constructing R1 dataclasses directly."""

    def _build(self, session):
        airport = _airport(session, name="Minneapolis St. Paul International", code="MSP")
        signal_67 = _signal(
            session, airport, title="EMAS order (vendor confirmed)", category="replacement",
            confidence="high", confirmed_vendor="Runway Safe", last_verified_at=date(2025, 5, 1),
        )
        signal_41 = _signal(
            session, airport, title="Federal grant record", category="replacement",
            confidence="high", planning_year=2025,
        )
        source_67 = _source(session, title="Confirmed-order source")
        source_41 = _source(session, title="Grant source")
        _assertion(session, source_67, airport, signal_id=signal_67.id, source_record_identifier="rec-67")
        _assertion(session, source_41, airport, signal_id=signal_41.id, source_record_identifier="rec-41")

        subject_source = _source(session, title="Procurement memo")
        subject_assertion = _assertion(session, subject_source, airport, source_record_identifier="rec-222")
        return subject_assertion, signal_67, signal_41

    def test_pre_resolution_reaches_clear_to_create_with_advisory_candidates(self):
        engine, session = make_session()
        subject_assertion, signal_67, signal_41 = self._build(session)
        claims = (_relationship_claim("Runway Safe"), _temporal_claim(date(2024, 8, 1)))

        subject = build_reconciliation_subject(subject_assertion, claims, category="replacement")
        candidates = find_reconciliation_candidates(session, subject_assertion)
        decision = evaluate_existing_signal_reconciliation(subject, candidates)

        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()
        assert set(decision.advisory_candidate_signal_ids) == {signal_67.id, signal_41.id}
        session.close(); engine.dispose()

    def test_pre_resolution_no_hidden_anchor_manufactured(self):
        engine, session = make_session()
        subject_assertion, signal_67, signal_41 = self._build(session)
        claims = (_relationship_claim("Runway Safe"), _temporal_claim(date(2024, 8, 1)))
        subject = build_reconciliation_subject(subject_assertion, claims, category="replacement")
        candidates = find_reconciliation_candidates(session, subject_assertion)
        decision = evaluate_existing_signal_reconciliation(subject, candidates)
        assert decision.reasons == ()
        session.close(); engine.dispose()

    def test_post_resolution_already_linked(self):
        engine, session = make_session()
        subject_assertion, signal_67, signal_41 = self._build(session)
        subject_assertion.signal_id = signal_67.id
        session.commit()

        subject = build_reconciliation_subject(subject_assertion)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        decision = evaluate_existing_signal_reconciliation(subject, candidates)

        assert decision.outcome == LINKED
        assert decision.signal_id == signal_67.id
        session.close(); engine.dispose()


# --- 19/20/21. Anchor-bearing DB fixtures ---

class TestAnchorBearingFixtures:
    def test_shared_runway_id_anchor_reaches_possible_match(self):
        engine, session = make_session()
        airport = _airport(session)
        runway = _runway(session, airport)
        signal = _signal(session, airport, runway_id=runway.id, category="replacement", confirmed_vendor="Acme")
        source = _source(session)
        subject_assertion = _assertion(session, source, airport, runway_id=runway.id)
        claims = (_relationship_claim("Acme"),)

        subject = build_reconciliation_subject(subject_assertion, claims, category="replacement")
        candidates = find_reconciliation_candidates(session, subject_assertion)
        decision = evaluate_existing_signal_reconciliation(subject, candidates)

        assert decision.outcome == POSSIBLE
        assert decision.candidate_signal_ids == (signal.id,)
        session.close(); engine.dispose()

    def test_shared_physical_installation_anchor_reaches_possible_match(self):
        engine, session = make_session()
        airport = _airport(session)
        signal = _signal(session, airport)
        identity = _installation_identity(session, airport)
        supporting_source = _source(session)
        supporting_assertion = _assertion(session, supporting_source, airport, signal_id=signal.id)
        _link(session, supporting_assertion, identity)

        subject_source = _source(session)
        subject_assertion = _assertion(session, subject_source, airport)
        _link(session, subject_assertion, identity)

        subject = build_reconciliation_subject(subject_assertion)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        decision = evaluate_existing_signal_reconciliation(subject, candidates)

        assert decision.outcome == POSSIBLE
        assert decision.candidate_signal_ids == (signal.id,)
        session.close(); engine.dispose()

    def test_multiple_independently_anchored_candidates_both_returned(self):
        engine, session = make_session()
        airport = _airport(session)
        runway = _runway(session, airport)
        signal_via_runway = _signal(session, airport, runway_id=runway.id)

        signal_via_installation = _signal(session, airport)
        identity = _installation_identity(session, airport)
        supporting_source = _source(session)
        supporting_assertion = _assertion(session, supporting_source, airport, signal_id=signal_via_installation.id)
        _link(session, supporting_assertion, identity)

        subject_source = _source(session)
        subject_assertion = _assertion(session, subject_source, airport, runway_id=runway.id)
        _link(session, subject_assertion, identity)

        subject = build_reconciliation_subject(subject_assertion)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        decision = evaluate_existing_signal_reconciliation(subject, candidates)

        assert decision.outcome == POSSIBLE
        assert decision.candidate_signal_ids == tuple(sorted([signal_via_runway.id, signal_via_installation.id]))
        session.close(); engine.dispose()


# --- 22. Disconfirming runway case ---

class TestDisconfirmingRunwayCase:
    def test_differing_populated_runway_id_faithfully_passed_and_excluded_by_r1(self):
        engine, session = make_session()
        airport = _airport(session)
        runway_a = _runway(session, airport, designation="09/27")
        runway_b = _runway(session, airport, designation="04/22")
        signal = _signal(session, airport, runway_id=runway_b.id, category="replacement", confirmed_vendor="Acme")
        source = _source(session)
        subject_assertion = _assertion(session, source, airport, runway_id=runway_a.id)
        claims = (_relationship_claim("Acme"),)

        subject = build_reconciliation_subject(subject_assertion, claims, category="replacement")
        candidates = find_reconciliation_candidates(session, subject_assertion)
        # R2 must not filter away or rewrite the contradictory runway fact
        assert candidates[0].runway_id == runway_b.id
        assert subject.runway_id == runway_a.id

        decision = evaluate_existing_signal_reconciliation(subject, candidates)
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()
        assert decision.advisory_candidate_signal_ids == ()
        session.close(); engine.dispose()


# --- 24/25. No DB writes / no commit ---

class TestReadOnlySafety:
    def test_no_new_rows_created_by_candidate_discovery(self):
        engine, session = make_session()
        airport = _airport(session)
        _signal(session, airport)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)

        counts_before = {
            "signals": session.query(Signal).count(),
            "source_assertions": session.query(SourceAssertion).count(),
            "installation_links": session.query(InstallationAssertionLink).count(),
        }
        find_reconciliation_candidates(session, subject_assertion)
        counts_after = {
            "signals": session.query(Signal).count(),
            "source_assertions": session.query(SourceAssertion).count(),
            "installation_links": session.query(InstallationAssertionLink).count(),
        }
        assert counts_before == counts_after
        session.close(); engine.dispose()

    def test_no_new_rows_created_by_subject_building(self):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        count_before = session.query(SourceAssertion).count()
        build_reconciliation_subject(subject_assertion, (_relationship_claim("Acme"),), category="replacement")
        count_after = session.query(SourceAssertion).count()
        assert count_before == count_after
        session.close(); engine.dispose()

    def test_module_never_calls_add_flush_commit_delete_update(self):
        # AST-based, but targeted at calls made specifically on a `session`
        # variable - a blanket scan for any attribute named "add" would
        # false-positive on this module's own legitimate `set.add(...)` use
        # (deduplicating ids), which has nothing to do with the ORM session.
        source_text = inspect.getsource(r2)
        tree = ast.parse(source_text)
        forbidden_attrs = {"add", "add_all", "flush", "commit", "delete", "merge", "bulk_update_mappings"}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in forbidden_attrs
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "session"
            ):
                raise AssertionError(f"forbidden session call: session.{node.func.attr}(...)")


# --- 26. Structural text / financial firewall ---

class TestStructuralFirewalls:
    def test_no_financial_field_referenced(self):
        source_text = inspect.getsource(r2)
        lowered = source_text.lower()
        for token in ("estimated_total_value_usd", "estimated_emas_value_usd", "$"):
            assert token not in lowered

    def test_no_title_notes_source_notes_referenced(self):
        source_text = inspect.getsource(r2)
        lowered = source_text.lower()
        for token in (".title", ".notes", "source_notes", "raw_relevant_text", "raw_runway_value"):
            assert token not in lowered

    def test_no_fuzzy_matching_machinery(self):
        source_text = inspect.getsource(r2)
        lowered = source_text.lower()
        for token in ("difflib", "levenshtein", "fuzzywuzzy", "embedding", "cosine"):
            assert token not in lowered


# --- 27. Provider agnosticism ---

class TestProviderAgnosticism:
    def test_no_source_family_names_in_production_source(self):
        source_text = inspect.getsource(r2)
        for token in ("MAC", "MSP", "FAA", "Runway Safe", "USAspending", "Granicus"):
            assert token not in source_text, f"forbidden token found: {token!r}"

    def test_no_acquisition_extraction_import(self):
        source_text = inspect.getsource(r2)
        tree = ast.parse(source_text)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not module.startswith("app.acquisition")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("app.acquisition")


# --- 28. International case ---

class TestInternationalCase:
    def test_non_us_airport_and_vendor_reach_identical_semantics(self):
        engine, session = make_session()
        airport = _airport(session, name="Haneda Airport", code="HND")
        runway = _runway(session, airport, designation="05/23")
        signal = _signal(
            session, airport, runway_id=runway.id, category="replacement",
            confirmed_vendor="Taiyo Safety Materials KK",
        )
        source = _source(session)
        subject_assertion = _assertion(session, source, airport, runway_id=runway.id)
        claims = (_relationship_claim("Taiyo Safety Materials KK"),)

        subject = build_reconciliation_subject(subject_assertion, claims, category="replacement")
        candidates = find_reconciliation_candidates(session, subject_assertion)
        decision = evaluate_existing_signal_reconciliation(subject, candidates)

        assert decision.outcome == POSSIBLE
        assert decision.candidate_signal_ids == (signal.id,)
        session.close(); engine.dispose()


# --- 29. Order invariance ---

class TestOrderInvariance:
    def test_candidate_output_independent_of_signal_insertion_order(self):
        engine, session = make_session()
        airport = _airport(session)
        signal_b = _signal(session, airport)
        signal_a = _signal(session, airport)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)
        candidates = find_reconciliation_candidates(session, subject_assertion)
        assert [c.signal_id for c in candidates] == sorted([signal_a.id, signal_b.id])
        session.close(); engine.dispose()


# --- 30. No obvious N+1 regression ---

class TestQueryEfficiency:
    def test_query_count_does_not_scale_linearly_with_candidate_count(self):
        engine, session = make_session()
        airport = _airport(session)
        for _ in range(8):
            signal = _signal(session, airport)
            supporting_source = _source(session)
            supporting_assertion = _assertion(session, supporting_source, airport, signal_id=signal.id)
            identity = _installation_identity(session, airport)
            _link(session, supporting_assertion, identity)
        source = _source(session)
        subject_assertion = _assertion(session, source, airport)

        statements = []

        def _count(*_args, **_kwargs):
            statements.append(1)

        event.listen(engine, "before_cursor_execute", _count)
        try:
            find_reconciliation_candidates(session, subject_assertion)
        finally:
            event.remove(engine, "before_cursor_execute", _count)

        # 8 candidates, each with a supporting assertion and an installation
        # link - a naive per-Signal loop would issue on the order of
        # 8 x (1 signal query + 1 assertions query + 1 links query) = 24+
        # statements; the batched strategy should stay small and constant
        # regardless of candidate count.
        assert len(statements) < 10, f"query count scaled with candidate count: {len(statements)}"
        session.close(); engine.dispose()
