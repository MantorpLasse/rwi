"""Tests for app.services.intelligence_review_persistence
(docs/architecture/intelligence-review-persistence-slice4-report.md).

Every test builds an isolated, fresh, in-memory-via-tmp-file SQLite
database - the real data/runway_safe.db is never opened."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import models  # noqa: F401 - registers all metadata
from app.acquisition.mac_granicus_claims import extract_mac_claims
from app.acquisition.mac_granicus_extractor import extract_candidate_fragment
from app.database import Base
from app.models import Airport, Installation, PhysicalInstallationIdentity, Runway, RunwayEnd, Signal, Source, SourceAssertion
from app.services.discovery_evidence_persistence import DiscoverySourceMetadata, persist_discovery_fragment
from app.services.evidence_attachment_guard import AttachmentOutcome, CandidateAirport
from app.services.evidence_claim_semantics import Claim, ClaimCategory, ClaimProvenance, RelationshipFact
from app.services.intelligence_review_persistence import persist_intelligence_review
from app.services.signal_candidate_evaluation import SignalCandidateOutcome

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mac_granicus_emas_procurement_memo_sample.pdf"
MSP_ARTIFACT_IDENTITY = "mac.granicus.document.4.2349.105406"
MSP_SOURCE_LOCATOR = "item-2.3.2"


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def _msp_candidate_airport(airport_id: int) -> CandidateAirport:
    return CandidateAirport(
        id=airport_id,
        name="Minneapolis-St Paul International",
        identifiers=frozenset({"MSP", "KMSP"}),
        canonical_runway_ends=frozenset({"12R", "30L"}),
        canonical_runway_pairs=frozenset({"12R/30L"}),
        known_issuers=frozenset({"Metropolitan Airports Commission"}),
    )


def _real_msp_fragment_and_claims():
    pdf_bytes = FIXTURE_PATH.read_bytes()
    result = extract_candidate_fragment(
        pdf_bytes, "application/pdf", artifact_identity=MSP_ARTIFACT_IDENTITY, source_locator=MSP_SOURCE_LOCATOR,
    )
    assert result is not None
    fragment, _vendors = result
    return fragment, extract_mac_claims(fragment)


def _seed_msp_assertion(session: Session) -> SourceAssertion:
    """Builds the real, governed SourceAssertion for the MSP fixture via
    the already-committed, unmodified Slice-1-era persistence path
    (persist_discovery_fragment) - proving this new slice consumes a
    REAL governed row, not a synthetic shortcut."""
    airport = Airport(id=45, name="Minneapolis-St Paul International", country="USA")
    session.add(airport)
    session.flush()

    fragment, claims = _real_msp_fragment_and_claims()
    result = persist_discovery_fragment(
        session,
        DiscoverySourceMetadata(document_identity=MSP_ARTIFACT_IDENTITY, title="MSP EMAS memo"),
        fragment,
        [_msp_candidate_airport(airport.id)],
    )
    assert result.outcome == AttachmentOutcome.ATTACH_CONFIRMED
    assertion = session.get(SourceAssertion, result.source_assertion_id)
    return assertion, claims


def _prov(subject: str = "x") -> ClaimProvenance:
    return ClaimProvenance(
        artifact_identity="test.artifact.1", source_locator="item-1", fragment_hash="a" * 64, raw_text_excerpt=subject,
    )


def _bare_assertion(session: Session, *, identity_guard_decision: "str | None") -> SourceAssertion:
    source = Source(title="Test Source", source_type="test", reliability_level="unverified")
    session.add(source)
    session.flush()
    assertion = SourceAssertion(
        source_id=source.id, assertion_type="project_construction", source_locator="loc-1",
        raw_fragment_hash="hash-1", artifact_identity="artifact-1", raw_relevant_text="original evidence text",
        identity_guard_decision=identity_guard_decision, identity_guard_reason="original identity reason",
    )
    session.add(assertion)
    session.flush()
    return assertion


# --- 1. MSP golden case ---

class TestMSPGoldenCase:
    def test_review_required_persisted(self, session):
        assertion, claims = _seed_msp_assertion(session)
        result = persist_intelligence_review(session, assertion, claims)
        assert result.outcome == SignalCandidateOutcome.REVIEW_REQUIRED
        assert assertion.intelligence_review_decision == "REVIEW_REQUIRED"
        assert assertion.intelligence_review_reason == result.reason

    def test_identity_and_intelligence_decisions_are_visibly_separate(self, session):
        assertion, claims = _seed_msp_assertion(session)
        persist_intelligence_review(session, assertion, claims)
        assert assertion.identity_guard_decision == "ATTACH_CONFIRMED"
        assert assertion.intelligence_review_decision == "REVIEW_REQUIRED"
        assert assertion.identity_guard_decision != assertion.intelligence_review_decision

    def test_read_back_from_fresh_session_matches(self, session, tmp_path):
        assertion, claims = _seed_msp_assertion(session)
        persist_intelligence_review(session, assertion, claims)
        session.commit()
        assertion_id = assertion.id

        session.close()
        engine2 = create_engine(session.bind.url)
        with Session(engine2) as fresh:
            reloaded = fresh.get(SourceAssertion, assertion_id)
            assert reloaded.intelligence_review_decision == "REVIEW_REQUIRED"
            assert "advance_deposit_purchase_order" in reloaded.intelligence_review_reason
        engine2.dispose()


# --- 2. identity not confirmed ---

class TestIdentityGate:
    @pytest.mark.parametrize(
        "identity_decision",
        ["ATTACH_PROVISIONAL", "REVIEW_REQUIRED", "REJECT_CROSS_AIRPORT", "INSUFFICIENT_IDENTITY", None, "GARBAGE_VALUE"],
    )
    def test_fails_closed_for_anything_but_attach_confirmed(self, session, identity_decision):
        assertion = _bare_assertion(session, identity_guard_decision=identity_decision)
        claims = (
            Claim(
                category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject="bed", statement="fact",
                provenance=_prov(),
                relationship=RelationshipFact(party="Acme", role="sole_source_vendor"),
            ),
        )
        result = persist_intelligence_review(session, assertion, claims)
        assert result.outcome == SignalCandidateOutcome.IDENTITY_NOT_CONFIRMED
        assert assertion.intelligence_review_decision == "IDENTITY_NOT_CONFIRMED"


# --- 3-4. financial semantics ---

class TestFinancialSemantics:
    def test_sfo_40m_unlabeled_amount_never_becomes_contract_value(self, session):
        assertion = _bare_assertion(session, identity_guard_decision="ATTACH_CONFIRMED")
        claims = (
            Claim(category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject="SFO EMAS", statement="SFO is evaluating EMAS options.", provenance=_prov()),
            Claim(
                category=ClaimCategory.RELATIONSHIP, subject="SFO EMAS", statement="mentioned",
                provenance=_prov(), relationship=RelationshipFact(party="Runway Safe", role="mentioned_in_document"),
            ),
        )
        result = persist_intelligence_review(session, assertion, claims)
        assert "$40" not in assertion.intelligence_review_reason
        assert "contract" not in assertion.intelligence_review_reason.lower()
        assert all(c.financial is None for c in result.decision.material_claims)

    def test_advance_deposit_and_cip_ceiling_remain_distinct_after_persistence(self, session):
        assertion, claims = _seed_msp_assertion(session)
        persist_intelligence_review(session, assertion, claims)
        reason = assertion.intelligence_review_reason
        assert "advance_deposit_purchase_order=1590000.00 USD" in reason
        assert "cip_project_ceiling=19000000.00 USD" in reason


# --- 5. temporal safety ---

class TestTemporalSafety:
    def test_planned_2025_remains_planned_future_action_in_persisted_reason(self, session):
        assertion, claims = _seed_msp_assertion(session)
        persist_intelligence_review(session, assertion, claims)
        assert "planned_future_action" in assertion.intelligence_review_reason
        assert "completed" not in assertion.intelligence_review_reason.lower()


# --- 6. idempotency ---

class TestIdempotency:
    def test_repeated_identical_evaluation_is_idempotent(self, session):
        assertion, claims = _seed_msp_assertion(session)
        first = persist_intelligence_review(session, assertion, claims)
        first_decision, first_reason = assertion.intelligence_review_decision, assertion.intelligence_review_reason
        second = persist_intelligence_review(session, assertion, claims)
        assert first.outcome == second.outcome
        assert first.reason == second.reason
        assert assertion.intelligence_review_decision == first_decision
        assert assertion.intelligence_review_reason == first_reason
        assert session.query(SourceAssertion).count() == 1


# --- 7. rollback ---

class TestRollback:
    def test_rollback_leaves_no_persisted_mutation(self, session, tmp_path):
        assertion, claims = _seed_msp_assertion(session)
        session.commit()  # commit the governed baseline row only
        assertion_id = assertion.id
        db_url = session.bind.url

        persist_intelligence_review(session, assertion, claims)
        assert assertion.intelligence_review_decision == "REVIEW_REQUIRED"  # visible in-session
        session.rollback()

        engine2 = create_engine(db_url)
        with Session(engine2) as fresh:
            reloaded = fresh.get(SourceAssertion, assertion_id)
            assert reloaded.intelligence_review_decision is None
            assert reloaded.intelligence_review_reason is None
        engine2.dispose()


# --- 8. unrelated fields unchanged ---

class TestUnrelatedFieldsUnchanged:
    def test_evidence_and_provenance_fields_untouched(self, session):
        assertion, claims = _seed_msp_assertion(session)
        before = (
            assertion.raw_relevant_text, assertion.identity_guard_decision, assertion.identity_guard_reason,
            assertion.artifact_identity, assertion.source_locator, assertion.raw_fragment_hash,
            assertion.airport_id, assertion.assertion_type, assertion.evidence_quality, assertion.review_state,
        )
        persist_intelligence_review(session, assertion, claims)
        after = (
            assertion.raw_relevant_text, assertion.identity_guard_decision, assertion.identity_guard_reason,
            assertion.artifact_identity, assertion.source_locator, assertion.raw_fragment_hash,
            assertion.airport_id, assertion.assertion_type, assertion.evidence_quality, assertion.review_state,
        )
        assert before == after


# --- 9-10. no Signal / no canonical writes ---

class TestNoSignalOrCanonicalWrites:
    def test_zero_signal_rows_created_or_modified(self, session):
        assertion, claims = _seed_msp_assertion(session)
        before = session.scalars(select(Signal)).all()
        persist_intelligence_review(session, assertion, claims)
        after = session.scalars(select(Signal)).all()
        assert before == [] and after == []

    def test_no_canonical_airport_runway_runway_end_installation_writes(self, session):
        assertion, claims = _seed_msp_assertion(session)
        counts_before = {
            "airports": session.query(Airport).count(),
            "runways": session.query(Runway).count(),
            "runway_ends": session.query(RunwayEnd).count(),
            "installations": session.query(Installation).count(),
            "physical_installation_identities": session.query(PhysicalInstallationIdentity).count(),
        }
        persist_intelligence_review(session, assertion, claims)
        counts_after = {
            "airports": session.query(Airport).count(),
            "runways": session.query(Runway).count(),
            "runway_ends": session.query(RunwayEnd).count(),
            "installations": session.query(Installation).count(),
            "physical_installation_identities": session.query(PhysicalInstallationIdentity).count(),
        }
        assert counts_before == counts_after


# --- 11. existing NULL rows remain valid ---

class TestNullRowsRemainValid:
    def test_row_never_reviewed_keeps_null_fields(self, session):
        assertion = _bare_assertion(session, identity_guard_decision="ATTACH_CONFIRMED")
        session.commit()
        reloaded = session.get(SourceAssertion, assertion.id)
        assert reloaded.intelligence_review_decision is None
        assert reloaded.intelligence_review_reason is None


# --- caller owns the transaction: no hidden commit ---

class TestTransactionOwnership:
    def test_service_never_commits(self, session, monkeypatch):
        assertion, claims = _seed_msp_assertion(session)
        commits = []
        original_commit = session.commit
        monkeypatch.setattr(session, "commit", lambda: commits.append(1) or original_commit())
        persist_intelligence_review(session, assertion, claims)
        assert commits == []


# --- EB5: effective identity decision (latest IdentityGuardEvaluation,
# when one validly exists, is authoritative over the historical column) ---

class TestEB5EffectiveIdentityIntegration:
    def _confirmed_evaluation(self, session, assertion, airport_id):
        from app.models.source_assertion_evidence_bag import SourceAssertionEvidenceBag
        from app.models.identity_guard_evaluation import IdentityGuardEvaluation
        from app.services.evidence_bag_serialization import serialize_evidence_bag, hash_serialized_evidence_bag
        from app.services.evidence_attachment_guard import EvidenceBag

        payload = serialize_evidence_bag(EvidenceBag(identifiers=frozenset({"FOO"})))
        snapshot = SourceAssertionEvidenceBag(
            source_assertion_id=assertion.id, evidence_bag_json=payload,
            evidence_bag_hash=hash_serialized_evidence_bag(payload), schema_version=1,
        )
        session.add(snapshot)
        session.flush()
        evaluation = IdentityGuardEvaluation(
            source_assertion_id=assertion.id, evidence_bag_snapshot_id=snapshot.id,
            evaluated_against_airport_id=airport_id, outcome="ATTACH_CONFIRMED", reason="synthetic",
        )
        session.add(evaluation)
        session.flush()
        return evaluation

    def test_historical_insufficient_latest_confirmed_now_reaches_evaluate_signal_candidate(self, session):
        airport = Airport(name="Foo Regional Airport", country="USA")
        session.add(airport)
        session.flush()
        assertion = _bare_assertion(session, identity_guard_decision="INSUFFICIENT_IDENTITY")
        assertion.airport_id = airport.id
        session.flush()
        self._confirmed_evaluation(session, assertion, airport.id)

        claims = (
            Claim(
                category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject="bed", statement="fact",
                provenance=_prov(), relationship=RelationshipFact(party="Acme", role="sole_source_vendor"),
            ),
        )
        result = persist_intelligence_review(session, assertion, claims)
        assert result.outcome != SignalCandidateOutcome.IDENTITY_NOT_CONFIRMED
        # historical field untouched
        assert assertion.identity_guard_decision == "INSUFFICIENT_IDENTITY"

    def test_candidate_linked_unresolved_assertion_remains_identity_not_confirmed(self, session):
        """§13/§34: even with a malformed evaluation present, an assertion
        with no canonical airport_id must never become identity-confirmed
        through intelligence review."""
        assertion = _bare_assertion(session, identity_guard_decision="INSUFFICIENT_IDENTITY")
        assert assertion.airport_id is None
        claims = (
            Claim(
                category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject="bed", statement="fact",
                provenance=_prov(), relationship=RelationshipFact(party="Acme", role="sole_source_vendor"),
            ),
        )
        result = persist_intelligence_review(session, assertion, claims)
        assert result.outcome == SignalCandidateOutcome.IDENTITY_NOT_CONFIRMED
