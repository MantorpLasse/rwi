"""Tests for the R3 existing-Signal reconciliation gate wired into
app/services/governed_signal_creation.py::create_signal_from_approved_review()
(docs/architecture/existing-signal-reconciliation-r3-governed-creation-report.md,
R3 of docs/architecture/existing-signal-reconciliation-guard-design.md's own
S16 roadmap). Every test uses an isolated in-memory SQLite database, modeled
on tests/test_governed_signal_creation.py's and
tests/test_existing_signal_reconciliation_candidates.py's own established
patterns. Deliberately a separate file from test_governed_signal_creation.py
(that file's own 52 pre-existing tests are the regression net proving this
slice changed no prior behavior; this file is additive, reconciliation-
specific coverage only).
"""
from __future__ import annotations

import ast
import inspect
from datetime import date

import pytest
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
from app.services import governed_signal_creation as gsc
from app.services.evidence_claim_semantics import (
    Claim,
    ClaimCategory,
    ClaimProvenance,
    RelationshipFact,
    TemporalContext,
    TemporalQualifier,
)
from app.services.existing_signal_reconciliation import ExistingSignalReconciliationOutcome
from app.services.governed_signal_creation import (
    ExistingSignalPossibleMatchError,
    create_signal_from_approved_review,
)
from app.services.reviewer_action_persistence import record_reviewer_action

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
    kwargs.setdefault("title", "Existing signal")
    kwargs.setdefault("category", "replacement")
    kwargs.setdefault("confidence", "medium")
    signal = Signal(airport_id=airport.id, **kwargs)
    session.add(signal)
    session.commit()
    return signal


def _installation_identity(session, airport) -> PhysicalInstallationIdentity:
    identity = PhysicalInstallationIdentity(airport_id=airport.id)
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


def _governed_assertion(session, source, airport, *, approved=True, **kwargs) -> SourceAssertion:
    kwargs.setdefault("assertion_type", "project_construction")
    kwargs.setdefault("source_record_identifier", f"rec-{id(kwargs)}-{source.id}")
    kwargs.setdefault("identity_guard_decision", "ATTACH_CONFIRMED")
    kwargs.setdefault("intelligence_review_decision", "REVIEW_REQUIRED")
    kwargs.setdefault("promotion_policy_decision", "HUMAN_REVIEW_REQUIRED")
    assertion = SourceAssertion(source=source, airport=airport, **kwargs)
    session.add(assertion)
    session.commit()
    if approved:
        record_reviewer_action(session, assertion, action="APPROVE_SIGNAL", reason="test approval", reviewer="tester@example.test")
        session.commit()
    return assertion


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


_BASE_FIELDS = dict(title="Proposed signal", category="replacement", confidence="medium")


# --- 1. CLEAR_TO_CREATE permits governed creation ---

class TestClearToCreatePermitsCreation:
    def test_no_candidates_creates_normally(self):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport)

        result = create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        session.commit()

        assert result.created is True
        assert result.reconciliation_decision.outcome == CLEAR
        assert session.query(Signal).count() == 1
        session.close(); engine.dispose()


# --- 2. Advisory-only permits creation ---

class TestAdvisoryOnlyPermitsCreation:
    def test_compatibility_only_still_creates(self):
        engine, session = make_session()
        airport = _airport(session)
        existing = _signal(session, airport, category="replacement", confirmed_vendor="Acme EMAS", last_verified_at=date(2025, 1, 1))
        source = _source(session)
        assertion = _governed_assertion(session, source, airport)
        claims = (_relationship_claim("Acme EMAS"), _temporal_claim(date(2024, 1, 1)))

        result = create_signal_from_approved_review(
            session, assertion, claims=claims, **_BASE_FIELDS,
        )
        session.commit()

        assert result.created is True
        assert result.reconciliation_decision.outcome == CLEAR
        assert existing.id in result.reconciliation_decision.advisory_candidate_signal_ids
        assert session.query(Signal).count() == 2
        session.close(); engine.dispose()


# --- 3/4/5. POSSIBLE match blocks before creation; candidate ids/reasons exposed ---

class TestPossibleMatchBlocksCreation:
    def test_runway_anchor_blocks_before_any_write(self):
        engine, session = make_session()
        airport = _airport(session)
        runway = _runway(session, airport)
        existing = _signal(session, airport, runway_id=runway.id)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)

        signals_before = session.query(Signal).count()
        with pytest.raises(ExistingSignalPossibleMatchError) as excinfo:
            create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)

        assert excinfo.value.decision.outcome == POSSIBLE
        assert excinfo.value.decision.candidate_signal_ids == (existing.id,)
        assert any("identity_anchor:runway_id" in r for r in excinfo.value.decision.reasons)
        assert session.query(Signal).count() == signals_before
        assert assertion.signal_id is None
        session.close(); engine.dispose()

    def test_error_message_contains_candidate_ids_without_db_query(self):
        engine, session = make_session()
        airport = _airport(session)
        runway = _runway(session, airport)
        existing = _signal(session, airport, runway_id=runway.id)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)

        with pytest.raises(ExistingSignalPossibleMatchError) as excinfo:
            create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        assert str(existing.id) in str(excinfo.value)
        session.close(); engine.dispose()


# --- 6/7. ALREADY_LINKED reuse / dangling linked Signal fails closed ---

class TestAlreadyLinkedReuse:
    def test_signal_id_already_set_reuses_existing(self):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport)
        first = create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        session.commit()

        second = create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        session.commit()

        assert second.created is False
        assert second.signal.id == first.signal.id
        assert second.reconciliation_decision.outcome == LINKED
        assert second.reconciliation_decision.signal_id == first.signal.id
        assert session.query(Signal).count() == 1
        session.close(); engine.dispose()

    def test_already_linked_ignores_anchor_backed_other_candidates(self):
        # Once linked, R1's own ALREADY_LINKED short-circuit means an
        # unrelated anchor-backed candidate elsewhere can never re-open the
        # question - the existing link is authoritative.
        engine, session = make_session()
        airport = _airport(session)
        runway = _runway(session, airport)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport)
        first = create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        session.commit()

        # a second, unrelated Signal now exists sharing the runway - must
        # not disturb the already-linked repeat call
        _signal(session, airport, runway_id=runway.id)
        assertion.runway_id = runway.id
        session.commit()

        second = create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        assert second.created is False
        assert second.signal.id == first.signal.id
        session.close(); engine.dispose()

    def test_dangling_signal_id_fails_closed_via_existing_drift_check(self):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport)
        assertion.signal_id = 999999
        session.commit()

        with pytest.raises(ValueError, match="no longer exists"):
            create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        session.close(); engine.dispose()


# --- 8/11. Governance gates execute before reconciliation ---

class TestGovernanceGatesFirst:
    def test_invalid_approval_state_with_anchor_backed_existing_signal_raises_governance_error(self):
        engine, session = make_session()
        airport = _airport(session)
        runway = _runway(session, airport)
        _signal(session, airport, runway_id=runway.id)
        source = _source(session)
        # not approved - no ReviewerAction recorded at all
        assertion = _governed_assertion(session, source, airport, approved=False, runway_id=runway.id)

        with pytest.raises(ValueError) as excinfo:
            create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        assert not isinstance(excinfo.value, ExistingSignalPossibleMatchError)
        assert "ReviewerAction" in str(excinfo.value)
        session.close(); engine.dispose()

    def test_wrong_identity_guard_decision_with_anchor_backed_existing_signal_raises_governance_error(self):
        engine, session = make_session()
        airport = _airport(session)
        runway = _runway(session, airport)
        _signal(session, airport, runway_id=runway.id)
        source = _source(session)
        assertion = _governed_assertion(
            session, source, airport, approved=False, runway_id=runway.id,
            identity_guard_decision="ATTACH_PROVISIONAL",
        )
        with pytest.raises(ValueError) as excinfo:
            create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        assert not isinstance(excinfo.value, ExistingSignalPossibleMatchError)
        assert "identity_guard_decision" in str(excinfo.value)
        session.close(); engine.dispose()


# --- runway_id trust boundary: the CREATE REQUEST's own proposed runway_id
# must never manufacture its own reconciliation anchor. Only
# SourceAssertion.runway_id (the EVIDENCE's own, already-resolved runway) is
# structural fact this guard may treat as identity evidence - added at the
# R3 review checkpoint, the single most important adversarial gap the review
# explicitly named. Confirmed both directions.

class TestRunwayIdTrustBoundary:
    def test_proposed_signal_runway_id_alone_cannot_manufacture_an_anchor(self):
        # SourceAssertion.runway_id is NULL (the evidence itself resolves no
        # runway); the create request's own runway_id=X happens to match an
        # existing candidate's runway_id. This must NOT become an anchor -
        # the human's choice of what runway to assign the NEW Signal is not
        # itself evidence about what the SOURCE DOCUMENT concerns.
        engine, session = make_session()
        airport = _airport(session)
        runway = _runway(session, airport)
        existing = _signal(session, airport, runway_id=runway.id)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport)  # runway_id left unset on the assertion itself
        assert assertion.runway_id is None

        result = create_signal_from_approved_review(
            session, assertion, runway_id=runway.id, **_BASE_FIELDS,  # proposed Signal's own runway
        )
        session.commit()
        assert result.created is True
        assert result.reconciliation_decision.outcome == CLEAR
        assert result.reconciliation_decision.candidate_signal_ids == ()
        assert not any(
            "identity_anchor:runway_id" in r for r in result.reconciliation_decision.advisory_reasons
        )
        session.close(); engine.dispose()

    def test_evidence_owned_runway_id_drives_reconciliation_not_the_proposed_one(self):
        # SourceAssertion.runway_id = X (matches an existing candidate); the
        # create request proposes a DIFFERENT runway Y for the new Signal.
        # The evidence-owned X must still drive reconciliation - the human's
        # differing choice of Y for the new Signal must not suppress or
        # replace what the evidence itself structurally concerns.
        engine, session = make_session()
        airport = _airport(session)
        runway_x = _runway(session, airport, designation="09/27")
        runway_y = _runway(session, airport, designation="04/22")
        existing = _signal(session, airport, runway_id=runway_x.id)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway_x.id)

        with pytest.raises(ExistingSignalPossibleMatchError) as excinfo:
            create_signal_from_approved_review(
                session, assertion, runway_id=runway_y.id, **_BASE_FIELDS,
            )
        assert excinfo.value.decision.candidate_signal_ids == (existing.id,)
        assert any("identity_anchor:runway_id" in r for r in excinfo.value.decision.reasons)
        session.close(); engine.dispose()


# --- ALREADY_LINKED drift check must not be bypassed by an unrelated
# anchor-backed candidate elsewhere at the same airport (R3 review checkpoint)

class TestAlreadyLinkedDriftNotBypassedByAnchor:
    def test_drift_error_still_fires_even_with_unrelated_anchor_backed_candidate_present(self):
        engine, session = make_session()
        airport = _airport(session)
        runway = _runway(session, airport)
        linked_signal = _signal(session, airport, title="linked")
        # a totally unrelated, independently anchor-backed Signal also
        # exists at this airport - must not distract from the drift check
        _signal(session, airport, runway_id=runway.id, title="other")

        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id, signal_id=linked_signal.id)

        with pytest.raises(ValueError) as excinfo:
            create_signal_from_approved_review(session, assertion, title="DIFFERENT TITLE", category="replacement", confidence="high")
        assert not isinstance(excinfo.value, ExistingSignalPossibleMatchError)
        assert "different core fields" in str(excinfo.value)
        session.close(); engine.dispose()


# --- 9/10. Runway anchor block / physical-installation anchor block ---

class TestAnchorFamilies:
    def test_runway_anchor_blocks(self):
        engine, session = make_session()
        airport = _airport(session)
        runway = _runway(session, airport)
        existing = _signal(session, airport, runway_id=runway.id)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)

        with pytest.raises(ExistingSignalPossibleMatchError) as excinfo:
            create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        assert excinfo.value.decision.candidate_signal_ids == (existing.id,)
        session.close(); engine.dispose()

    def test_physical_installation_anchor_blocks(self):
        engine, session = make_session()
        airport = _airport(session)
        identity = _installation_identity(session, airport)
        existing = _signal(session, airport)
        supporting_source = _source(session)
        supporting_assertion = _governed_assertion(session, supporting_source, airport, approved=False, signal_id=existing.id)
        _link(session, supporting_assertion, identity)

        source = _source(session)
        assertion = _governed_assertion(session, source, airport)
        _link(session, assertion, identity)

        with pytest.raises(ExistingSignalPossibleMatchError) as excinfo:
            create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        assert excinfo.value.decision.candidate_signal_ids == (existing.id,)
        session.close(); engine.dispose()

    def test_superseded_physical_installation_link_no_longer_blocks(self):
        # Regression-tests the R2 review-checkpoint defect through the full
        # R3 path: a retracted SAME_PHYSICAL_INSTALLATION decision must not
        # keep blocking Signal creation.
        engine, session = make_session()
        airport = _airport(session)
        identity = _installation_identity(session, airport)
        existing = _signal(session, airport)
        supporting_source = _source(session)
        supporting_assertion = _governed_assertion(session, supporting_source, airport, approved=False, signal_id=existing.id)
        original = _link(session, supporting_assertion, identity)
        _link(session, supporting_assertion, identity=None, outcome="UNRESOLVED", supersedes_link_id=original.id)

        source = _source(session)
        assertion = _governed_assertion(session, source, airport)
        _link(session, assertion, identity)

        result = create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        session.commit()
        assert result.created is True
        assert result.reconciliation_decision.outcome == CLEAR
        session.close(); engine.dispose()

    def test_provenance_anchor_blocks(self):
        engine, session = make_session()
        airport = _airport(session)
        existing = _signal(session, airport)
        supporting_source = _source(session, title="Governed supporting doc")
        _governed_assertion(session, supporting_source, airport, approved=False, signal_id=existing.id)

        # the NEW subject cites the exact same source document
        assertion = _governed_assertion(session, supporting_source, airport)

        with pytest.raises(ExistingSignalPossibleMatchError) as excinfo:
            create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        assert excinfo.value.decision.candidate_signal_ids == (existing.id,)
        session.close(); engine.dispose()

    def test_legacy_signal_source_id_alone_is_not_a_provenance_anchor(self):
        engine, session = make_session()
        airport = _airport(session)
        legacy_source = _source(session, title="Legacy single-doc source")
        existing = _signal(session, airport, source_id=legacy_source.id)  # no supporting_source_assertions

        assertion = _governed_assertion(session, legacy_source, airport)
        result = create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        session.commit()
        assert result.created is True
        assert result.reconciliation_decision.outcome == CLEAR
        session.close(); engine.dispose()


# --- 13. Compatibility-only case creates ---

class TestCompatibilityOnlyCaseCreates:
    def test_maximum_compatibility_zero_anchor_still_creates(self):
        engine, session = make_session()
        airport = _airport(session)
        existing = _signal(
            session, airport, category="replacement", confirmed_vendor="Acme EMAS",
            planning_year=2025, last_verified_at=date(2025, 5, 1),
        )
        source = _source(session)
        assertion = _governed_assertion(session, source, airport)
        claims = (_relationship_claim("Acme EMAS"), _temporal_claim(date(2024, 1, 1)))

        result = create_signal_from_approved_review(
            session, assertion, claims=claims, planning_year=2025, **_BASE_FIELDS,
        )
        session.commit()
        assert result.created is True
        assert result.reconciliation_decision.outcome == CLEAR
        assert existing.id in result.reconciliation_decision.advisory_candidate_signal_ids
        session.close(); engine.dispose()


# --- 14. Multiple blocking candidates ---

class TestMultipleBlockingCandidates:
    def test_two_independently_anchored_candidates_both_surfaced_none_created(self):
        engine, session = make_session()
        airport = _airport(session)
        runway = _runway(session, airport)
        signal_via_runway = _signal(session, airport, runway_id=runway.id)

        identity = _installation_identity(session, airport)
        signal_via_installation = _signal(session, airport)
        supporting_source = _source(session)
        supporting_assertion = _governed_assertion(session, supporting_source, airport, approved=False, signal_id=signal_via_installation.id)
        _link(session, supporting_assertion, identity)

        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
        _link(session, assertion, identity)

        with pytest.raises(ExistingSignalPossibleMatchError) as excinfo:
            create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        assert excinfo.value.decision.candidate_signal_ids == tuple(sorted([signal_via_runway.id, signal_via_installation.id]))
        assert session.query(Signal).count() == 2  # only the two pre-existing ones
        session.close(); engine.dispose()


# --- 15. Disconfirming runway ---

class TestDisconfirmingRunway:
    def test_differing_runway_id_does_not_block_when_no_other_anchor(self):
        engine, session = make_session()
        airport = _airport(session)
        runway_a = _runway(session, airport, designation="09/27")
        runway_b = _runway(session, airport, designation="04/22")
        _signal(session, airport, runway_id=runway_b.id, category="replacement", confirmed_vendor="Acme")
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway_a.id)
        claims = (_relationship_claim("Acme"),)

        result = create_signal_from_approved_review(session, assertion, claims=claims, **_BASE_FIELDS)
        session.commit()
        assert result.created is True
        assert result.reconciliation_decision.outcome == CLEAR
        # disconfirmed candidate must not even appear as advisory
        assert result.reconciliation_decision.advisory_candidate_signal_ids == ()
        session.close(); engine.dispose()


# --- 16. MSP historical pre-resolution result ---

class TestMSPHistoricalPreResolution:
    def test_pre_resolution_msp_shape_is_clear_to_create_with_advisory_candidates(self):
        engine, session = make_session()
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
        _governed_assertion(session, source_67, airport, approved=False, signal_id=signal_67.id)
        _governed_assertion(session, source_41, airport, approved=False, signal_id=signal_41.id)

        subject_source = _source(session, title="Procurement memo")
        assertion = _governed_assertion(session, subject_source, airport)
        claims = (_relationship_claim("Runway Safe"), _temporal_claim(date(2024, 8, 1)))

        result = create_signal_from_approved_review(
            session, assertion, claims=claims, title="MSP Runway 30L EMAS replacement - advance deposit requested",
            category="replacement", confidence="medium", status="identified",
        )
        session.commit()

        assert result.created is True
        assert result.reconciliation_decision.outcome == CLEAR
        assert result.reconciliation_decision.candidate_signal_ids == ()
        assert set(result.reconciliation_decision.advisory_candidate_signal_ids) == {signal_67.id, signal_41.id}
        assert session.query(Signal).count() == 3
        session.close(); engine.dispose()


# --- 18/19. Title similarity irrelevant / money irrelevant ---

class TestTitleAndFinancialIrrelevance:
    def test_identical_titles_no_anchor_still_creates(self):
        engine, session = make_session()
        airport = _airport(session)
        _signal(session, airport, title="Proposed signal")  # byte-identical title, no anchor
        source = _source(session)
        assertion = _governed_assertion(session, source, airport)

        result = create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        session.commit()
        assert result.created is True
        assert result.reconciliation_decision.outcome == CLEAR
        assert result.reconciliation_decision.candidate_signal_ids == ()
        session.close(); engine.dispose()

    def test_wildly_different_titles_with_genuine_anchor_still_blocks(self):
        # Proves titles are irrelevant in BOTH directions: identical titles
        # with no anchor still create (above); here, completely unrelated-
        # looking titles with a genuine structural anchor still block.
        engine, session = make_session()
        airport = _airport(session)
        runway = _runway(session, airport)
        existing = _signal(session, airport, runway_id=runway.id, title="Federal grant record FY2019 archive entry #4471")
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)

        with pytest.raises(ExistingSignalPossibleMatchError) as excinfo:
            create_signal_from_approved_review(
                session, assertion, title="Totally unrelated-sounding proposed title", category="replacement", confidence="high",
            )
        assert excinfo.value.decision.candidate_signal_ids == (existing.id,)
        session.close(); engine.dispose()

    def test_large_matching_dollar_claim_never_becomes_advisory_or_anchor_evidence(self):
        # Adversarial SFO-$40M-style case: a claim carries a large financial
        # fact whose amount happens to exactly match an existing candidate's
        # own dollar field. Neither must ever influence the outcome or
        # appear in any reason - only the unrelated, genuine category match
        # may.
        from decimal import Decimal

        from app.services.evidence_claim_semantics import FinancialFact

        engine, session = make_session()
        airport = _airport(session)
        existing = _signal(session, airport, category="replacement", estimated_total_value_usd=Decimal("40000000.00"))
        source = _source(session)
        assertion = _governed_assertion(session, source, airport)
        big_money_claim = Claim(
            category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject="budget", statement="a large dollar figure",
            provenance=ClaimProvenance(artifact_identity="a", source_locator="l", fragment_hash="h", raw_text_excerpt="x"),
            financial=FinancialFact(amount=Decimal("40000000.00"), currency="USD", semantic_role="cip_project_ceiling"),
        )

        result = create_signal_from_approved_review(
            session, assertion, claims=(big_money_claim,), category="replacement", title="new", confidence="high",
        )
        session.commit()
        assert result.reconciliation_decision.outcome == CLEAR
        assert result.reconciliation_decision.candidate_signal_ids == ()
        assert not any("40000000" in r or "usd" in r.lower() for r in result.reconciliation_decision.advisory_reasons)
        assert all(r.startswith(f"signal {existing.id}: compatibility:category") for r in result.reconciliation_decision.advisory_reasons)
        assert result.signal.estimated_total_value_usd is None  # never reaches the created Signal either
        session.close(); engine.dispose()

    def test_no_financial_field_accepted_by_create_call(self):
        # The module's own pre-existing docstring (Slice 9C, predating R3)
        # legitimately names these fields in prose to explain why they were
        # never accepted - a whole-file text scan would false-positive on
        # that history. The real, R3-relevant safety property is that the
        # reconciliation call itself never passes anything money-shaped -
        # checked precisely via the actual call sites' source segments.
        source_text = inspect.getsource(gsc)
        tree = ast.parse(source_text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in (
                "build_reconciliation_subject", "find_reconciliation_candidates", "evaluate_existing_signal_reconciliation",
            ):
                call_source = ast.get_source_segment(source_text, node) or ""
                assert "estimated_total_value_usd" not in call_source
                assert "estimated_emas_value_usd" not in call_source
        params = inspect.signature(create_signal_from_approved_review).parameters
        assert "estimated_total_value_usd" not in params
        assert "estimated_emas_value_usd" not in params

    def test_reconciliation_gate_never_reads_title_or_notes_fields(self):
        source_text = inspect.getsource(gsc)
        tree = ast.parse(source_text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in (
                "build_reconciliation_subject", "find_reconciliation_candidates",
            ):
                call_source = ast.get_source_segment(source_text, node) or ""
                assert "title" not in call_source
                assert "notes" not in call_source


# --- 20. Provider agnostic ---

class TestProviderAgnostic:
    def test_reconciliation_gate_itself_never_names_a_source_family(self):
        # This module's own pre-existing docstring (Slice 9C, predating R3)
        # already names "MSP" once, legitimately, as historical rationale
        # for why financial fields were never accepted - a whole-file scan
        # would false-positive on that pre-existing prose. The property this
        # slice is actually responsible for is that the NEW reconciliation
        # integration itself (imports, the reconciliation call block, and
        # the new helper/exception this slice added) names no source family
        # - checked directly against that added code, not the whole file.
        source_text = inspect.getsource(gsc)
        new_code_start = source_text.index("RECONCILIATION GATE")
        new_code = source_text[new_code_start:]
        for token in ("MAC", "MSP", "FAA", "Runway Safe", "USAspending", "Granicus"):
            assert token not in new_code, f"forbidden token found in R3 code: {token!r}"


# --- 21. International case ---

class TestInternationalCase:
    def test_non_us_airport_and_vendor_reach_identical_semantics(self):
        engine, session = make_session()
        airport = _airport(session, name="Haneda Airport", code="HND")
        runway = _runway(session, airport, designation="05/23")
        existing = _signal(session, airport, runway_id=runway.id, confirmed_vendor="Taiyo Safety Materials KK")
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)

        with pytest.raises(ExistingSignalPossibleMatchError) as excinfo:
            create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        assert excinfo.value.decision.candidate_signal_ids == (existing.id,)
        session.close(); engine.dispose()


# --- 22. Deterministic reconciliation result surfaced ---

class TestDeterministicResult:
    def test_repeated_evaluation_of_the_same_state_produces_equal_decision(self):
        # Read-only repetition of the exact same DB state (no creation
        # in between, which would itself add a new candidate for any
        # subsequent call at the same airport - a separate, expected effect
        # already covered by TestIdempotentSecondCall, not what this test is
        # about).
        from app.services.existing_signal_reconciliation import evaluate_existing_signal_reconciliation
        from app.services.existing_signal_reconciliation_candidates import (
            build_reconciliation_subject,
            find_reconciliation_candidates,
        )

        engine, session = make_session()
        airport = _airport(session)
        _signal(session, airport, category="replacement", confirmed_vendor="Acme")
        source = _source(session)
        assertion = _governed_assertion(session, source, airport)
        claims = (_relationship_claim("Acme"),)

        subject = build_reconciliation_subject(assertion, claims, category="replacement")
        candidates = find_reconciliation_candidates(session, assertion)
        first = evaluate_existing_signal_reconciliation(subject, candidates)
        second = evaluate_existing_signal_reconciliation(subject, candidates)
        assert first == second
        session.close(); engine.dispose()


# --- 23/24. Rollback / no commit ---

class TestTransactionBehavior:
    def test_clear_to_create_then_rollback_leaves_no_signal(self):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport)

        create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        session.rollback()
        assert session.query(Signal).count() == 0
        session.close(); engine.dispose()

    def test_possible_match_leaves_no_pending_signal_even_before_rollback(self):
        engine, session = make_session()
        airport = _airport(session)
        runway = _runway(session, airport)
        _signal(session, airport, runway_id=runway.id)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)

        with pytest.raises(ExistingSignalPossibleMatchError):
            create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        assert session.new == set() or len(session.new) == 0
        session.close(); engine.dispose()

    def test_service_never_calls_session_commit(self):
        source_text = inspect.getsource(gsc)
        tree = ast.parse(source_text)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "commit"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "session"
            ):
                raise AssertionError("forbidden session.commit() call found")


# --- 25. publication=False preserved ---

class TestPublicationSafety:
    def test_created_signal_still_unpublished(self):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport)
        result = create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        session.commit()
        assert result.signal.published is False
        session.close(); engine.dispose()

    def test_blocked_case_creates_no_new_signal_published_or_not(self):
        engine, session = make_session()
        airport = _airport(session)
        runway = _runway(session, airport)
        # the pre-existing candidate is published=True by its own legacy
        # default (app/models/signal.py's own documented backward-
        # compatibility default) - unrelated to the guard's own behavior;
        # what matters here is that the BLOCKED attempt creates no new row.
        _signal(session, airport, runway_id=runway.id)
        signal_count_before = session.query(Signal).count()
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
        with pytest.raises(ExistingSignalPossibleMatchError):
            create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        assert session.query(Signal).count() == signal_count_before
        session.close(); engine.dispose()


# --- 26. ReviewerAction unchanged ---

class TestReviewerActionUnchanged:
    def test_reviewer_action_row_count_unchanged_by_blocked_creation(self):
        from app.models import ReviewerAction
        engine, session = make_session()
        airport = _airport(session)
        runway = _runway(session, airport)
        _signal(session, airport, runway_id=runway.id)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
        count_before = session.query(ReviewerAction).count()
        with pytest.raises(ExistingSignalPossibleMatchError):
            create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        assert session.query(ReviewerAction).count() == count_before
        session.close(); engine.dispose()


# --- 27. No canonical writes ---

class TestNoCanonicalWrites:
    def test_blocked_creation_does_not_touch_airport_or_source(self):
        engine, session = make_session()
        airport = _airport(session)
        runway = _runway(session, airport)
        _signal(session, airport, runway_id=runway.id)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
        airport_name_before, source_title_before = airport.name, source.title
        with pytest.raises(ExistingSignalPossibleMatchError):
            create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        assert (airport.name, source.title) == (airport_name_before, source_title_before)
        session.close(); engine.dispose()


# --- 28. Second call idempotent (no repeated candidate analysis creates a second Signal) ---

class TestIdempotentSecondCall:
    def test_advisory_first_call_then_second_call_reuses_same_signal(self):
        engine, session = make_session()
        airport = _airport(session)
        existing = _signal(session, airport, category="replacement", confirmed_vendor="Acme")
        source = _source(session)
        assertion = _governed_assertion(session, source, airport)
        claims = (_relationship_claim("Acme"),)

        first = create_signal_from_approved_review(session, assertion, claims=claims, **_BASE_FIELDS)
        session.commit()
        assert first.created is True
        assert session.query(Signal).count() == 2  # existing + newly created

        second = create_signal_from_approved_review(session, assertion, claims=claims, **_BASE_FIELDS)
        session.commit()
        assert second.created is False
        assert second.signal.id == first.signal.id
        assert session.query(Signal).count() == 2  # unchanged - no third Signal
        session.close(); engine.dispose()


# --- 29. Malformed/empty context fails safely ---

class TestMalformedContextFailsSafely:
    def test_empty_claims_and_no_category_context_still_creates_safely(self):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport)
        result = create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
        session.commit()
        assert result.created is True
        assert result.reconciliation_decision.outcome == CLEAR
        session.close(); engine.dispose()

    def test_claim_with_no_relationship_or_temporal_context_is_harmless(self):
        engine, session = make_session()
        airport = _airport(session)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport)
        bare_claim = Claim(
            category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject="s", statement="s",
            provenance=ClaimProvenance(artifact_identity="a", source_locator="l", fragment_hash="h", raw_text_excerpt="x"),
        )
        result = create_signal_from_approved_review(session, assertion, claims=(bare_claim,), **_BASE_FIELDS)
        session.commit()
        assert result.created is True
        session.close(); engine.dispose()


# --- 30. Reference-year priority ---

class TestReferenceYearPriority:
    def test_target_year_takes_priority_over_planning_year(self):
        engine, session = make_session()
        airport = _airport(session)
        existing = _signal(session, airport, category="replacement", target_year=2026)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport)
        result = create_signal_from_approved_review(
            session, assertion, target_year=2026, planning_year=2020, **_BASE_FIELDS,
        )
        session.commit()
        assert any("compatibility:year" in r for r in result.reconciliation_decision.advisory_reasons)
        session.close(); engine.dispose()

    def test_manual_year_estimate_never_used_for_reference_year(self):
        engine, session = make_session()
        airport = _airport(session)
        existing = _signal(session, airport, category="replacement")  # no reference_year at all on candidate
        source = _source(session)
        assertion = _governed_assertion(session, source, airport)
        result = create_signal_from_approved_review(
            session, assertion, manual_year_estimate=1999, **_BASE_FIELDS,
        )
        session.commit()
        assert not any("compatibility:year" in r for r in result.reconciliation_decision.advisory_reasons)
        session.close(); engine.dispose()

    def test_falls_back_to_construction_start_year(self):
        engine, session = make_session()
        airport = _airport(session)
        existing = _signal(session, airport, category="replacement", target_year=2028)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport)
        result = create_signal_from_approved_review(
            session, assertion, construction_start=date(2028, 6, 1), **_BASE_FIELDS,
        )
        session.commit()
        assert any("compatibility:year" in r for r in result.reconciliation_decision.advisory_reasons)
        session.close(); engine.dispose()

    def test_resolve_reference_year_matches_r2s_own_signal_reference_year(self):
        # Consistency guard: R3's own field-priority helper must never
        # silently drift from R2's already-established priority for reading
        # an EXISTING Signal (see gsc._resolve_reference_year's own
        # docstring for why this is reimplemented rather than imported).
        from app.services.existing_signal_reconciliation_candidates import _signal_reference_year

        cases = [
            dict(target_year=2026, planning_year=2025, procurement_year=2024, construction_start=None, completion_date=None),
            dict(target_year=None, planning_year=2025, procurement_year=2024, construction_start=None, completion_date=None),
            dict(target_year=None, planning_year=None, procurement_year=None, construction_start=date(2027, 1, 1), completion_date=None),
            dict(target_year=None, planning_year=None, procurement_year=None, construction_start=None, completion_date=date(2029, 1, 1)),
            dict(target_year=None, planning_year=None, procurement_year=None, construction_start=None, completion_date=None),
        ]
        for fields in cases:
            r3_value = gsc._resolve_reference_year(**fields)
            fake_signal = Signal(airport_id=1, title="t", category="c", confidence="medium", **fields)
            r2_value = _signal_reference_year(fake_signal)
            assert r3_value == r2_value, f"drift for {fields!r}: R3={r3_value!r} R2={r2_value!r}"
