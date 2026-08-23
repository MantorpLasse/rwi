"""Tests for app.services.promotion_policy_persistence
(docs/architecture/promotion-policy-persistence-slice7-report.md).

Every test builds an isolated, fresh, in-memory-via-tmp-file SQLite
database - the real data/runway_safe.db is never opened."""
from __future__ import annotations

import ast
import inspect
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import models  # noqa: F401 - registers all metadata
from app.acquisition.mac_granicus_claims import extract_mac_claims
from app.acquisition.mac_granicus_extractor import extract_candidate_fragment
from app.database import Base
from app.models import Airport, Installation, PhysicalInstallationIdentity, Runway, RunwayEnd, Signal, Source, SourceAssertion
from app.services import promotion_policy_persistence as ppp
from app.services.discovery_evidence_persistence import DiscoverySourceMetadata, persist_discovery_fragment
from app.services.evidence_attachment_guard import AttachmentOutcome, CandidateAirport
from app.services.evidence_claim_semantics import Claim, ClaimCategory, ClaimProvenance, FinancialFact, RelationshipFact, TemporalContext, TemporalQualifier
from app.services.intelligence_review_persistence import persist_intelligence_review
from app.services.promotion_policy_evaluation import PromotionPolicyContext, PromotionPolicyOutcome, SourceAuthorityTier
from app.services.promotion_policy_persistence import persist_promotion_policy
from app.services.signal_candidate_evaluation import SignalCandidateOutcome

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mac_granicus_emas_procurement_memo_sample.pdf"
MSP_ARTIFACT_IDENTITY = "mac.granicus.document.4.2349.105406"
MSP_SOURCE_LOCATOR = "item-2.3.2"

TIER_1 = PromotionPolicyContext(source_authority_tier=SourceAuthorityTier.TIER_1_PRIMARY_OFFICIAL)
UNKNOWN_TIER = PromotionPolicyContext(source_authority_tier=None)


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def _msp_candidate_airport(airport_id: int) -> CandidateAirport:
    return CandidateAirport(
        id=airport_id, name="Minneapolis-St Paul International",
        identifiers=frozenset({"MSP", "KMSP"}), canonical_runway_ends=frozenset({"12R", "30L"}),
        canonical_runway_pairs=frozenset({"12R/30L"}), known_issuers=frozenset({"Metropolitan Airports Commission"}),
    )


def _real_msp_fragment_and_claims():
    pdf_bytes = FIXTURE_PATH.read_bytes()
    result = extract_candidate_fragment(pdf_bytes, "application/pdf", artifact_identity=MSP_ARTIFACT_IDENTITY, source_locator=MSP_SOURCE_LOCATOR)
    assert result is not None
    fragment, _vendors = result
    return fragment, extract_mac_claims(fragment)


def _seed_msp_assertion(session: Session):
    airport = Airport(id=45, name="Minneapolis-St Paul International", country="USA")
    session.add(airport)
    session.flush()
    fragment, claims = _real_msp_fragment_and_claims()
    result = persist_discovery_fragment(
        session, DiscoverySourceMetadata(document_identity=MSP_ARTIFACT_IDENTITY, title="MSP EMAS memo"),
        fragment, [_msp_candidate_airport(airport.id)],
    )
    assert result.outcome == AttachmentOutcome.ATTACH_CONFIRMED
    assertion = session.get(SourceAssertion, result.source_assertion_id)
    return assertion, claims


def _prov(subject: str = "x") -> ClaimProvenance:
    return ClaimProvenance(artifact_identity="test.artifact.1", source_locator="item-1", fragment_hash="a" * 64, raw_text_excerpt=subject)


def _fact(subject: str, statement: str = "fact", **kwargs) -> Claim:
    return Claim(category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject=subject, statement=statement, provenance=_prov(subject), **kwargs)


def _relationship_claim(subject: str, party: str, role: str) -> Claim:
    return Claim(category=ClaimCategory.RELATIONSHIP, subject=subject, statement=f"{party} as {role}", provenance=_prov(subject), relationship=RelationshipFact(party=party, role=role))


def _award_claim(**overrides) -> Claim:
    defaults = dict(
        subject="EMAS replacement, Runway 27", statement="Airport Commission awarded Vendor X a contract for EMAS replacement at Runway 27.",
        temporal=TemporalContext(qualifier=TemporalQualifier.HISTORICAL_FACT, as_of_date=date(2026, 6, 1)),
        financial=FinancialFact(amount=Decimal("12500000"), currency="USD", semantic_role="contract_award_amount"),
        relationship=RelationshipFact(party="Vendor X", role="awarded_contractor"),
    )
    defaults.update(overrides)
    return _fact(**defaults)


def _completion_claim(**overrides) -> Claim:
    defaults = dict(
        subject="EMAS replacement, Runway 11", statement="Replacement of the Runway 11 EMAS was completed on 2026-08-15.",
        temporal=TemporalContext(qualifier=TemporalQualifier.COMPLETED, as_of_date=date(2026, 8, 15)),
    )
    defaults.update(overrides)
    return _fact(**defaults)


def _bare_assertion(session: Session, *, identity_guard_decision: "str | None" = "ATTACH_CONFIRMED") -> SourceAssertion:
    source = Source(title="Test Source", source_type="test", reliability_level="official")
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


# --- 6-7. MSP full chain / three-field audit trail ---

class TestMSPFullChain:
    def test_msp_promotion_policy_is_human_review_required(self, session):
        assertion, claims = _seed_msp_assertion(session)
        result = persist_promotion_policy(session, assertion, claims, TIER_1)
        assert result.outcome == PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED
        assert assertion.promotion_policy_decision == "HUMAN_REVIEW_REQUIRED"

    def test_msp_is_never_do_not_promote(self, session):
        assertion, claims = _seed_msp_assertion(session)
        result = persist_promotion_policy(session, assertion, claims, TIER_1)
        assert result.outcome != PromotionPolicyOutcome.DO_NOT_PROMOTE

    def test_all_three_decision_pairs_coexist_on_the_same_row(self, session):
        assertion, claims = _seed_msp_assertion(session)
        intel_result = persist_intelligence_review(session, assertion, claims)
        promo_result = persist_promotion_policy(session, assertion, claims, TIER_1)

        assert assertion.identity_guard_decision == "ATTACH_CONFIRMED"
        assert assertion.intelligence_review_decision == "REVIEW_REQUIRED"
        assert assertion.promotion_policy_decision == "HUMAN_REVIEW_REQUIRED"
        assert intel_result.outcome == SignalCandidateOutcome.REVIEW_REQUIRED
        assert promo_result.outcome == PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED
        # Three genuinely distinct values/questions, never merged.
        assert len({assertion.identity_guard_decision, assertion.intelligence_review_decision, assertion.promotion_policy_decision}) == 3

    def test_promotion_policy_does_not_require_intelligence_review_to_run_first(self, session):
        # persist_promotion_policy() depends only on identity_guard_decision
        # and claims, never on intelligence_review_decision already being set.
        assertion, claims = _seed_msp_assertion(session)
        assert assertion.intelligence_review_decision is None
        result = persist_promotion_policy(session, assertion, claims, TIER_1)
        assert result.outcome == PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED
        assert assertion.intelligence_review_decision is None  # still untouched


# --- 8-10. Explicit award / completion golden cases, no Signal ---

class TestGoldenAutoEligibleCases:
    def test_explicit_award_persists_auto_eligible(self, session):
        assertion = _bare_assertion(session)
        claim = _award_claim()
        result = persist_promotion_policy(session, assertion, (claim,), TIER_1)
        assert result.outcome == PromotionPolicyOutcome.AUTO_ELIGIBLE
        assert assertion.promotion_policy_decision == "AUTO_ELIGIBLE"

    def test_completion_persists_auto_eligible(self, session):
        assertion = _bare_assertion(session)
        claim = _completion_claim()
        result = persist_promotion_policy(session, assertion, (claim,), TIER_1)
        assert result.outcome == PromotionPolicyOutcome.AUTO_ELIGIBLE

    def test_auto_eligible_creates_no_signal_row(self, session):
        assertion = _bare_assertion(session)
        claim = _award_claim()
        before = session.scalars(select(Signal)).all()
        persist_promotion_policy(session, assertion, (claim,), TIER_1)
        after = session.scalars(select(Signal)).all()
        assert before == [] and after == []

    def test_completion_auto_eligible_creates_no_signal_row(self, session):
        assertion = _bare_assertion(session)
        claim = _completion_claim()
        before = session.scalars(select(Signal)).all()
        persist_promotion_policy(session, assertion, (claim,), TIER_1)
        after = session.scalars(select(Signal)).all()
        assert before == [] and after == []


# --- 11. SFO-$40M adversarial ---

class TestSFO40MAdversarial:
    def test_never_auto_eligible_and_no_fabricated_contract_language(self, session):
        assertion = _bare_assertion(session)
        fact_claim = _fact("SFO EMAS context", statement="SFO is evaluating EMAS options.")
        relationship_claim = _relationship_claim("SFO EMAS context", "Runway Safe", "mentioned_in_document")
        claims = (fact_claim, relationship_claim)
        result = persist_promotion_policy(session, assertion, claims, TIER_1)
        assert result.outcome != PromotionPolicyOutcome.AUTO_ELIGIBLE
        assert "$40" not in assertion.promotion_policy_reason
        assert "contract" not in assertion.promotion_policy_reason.lower()


# --- 12-15. Direct DO_NOT_PROMOTE cases (reachable upstream outcomes) ---

class TestDirectDoNotPromoteCases:
    def test_insufficient_materiality_persists_do_not_promote(self, session):
        assertion = _bare_assertion(session)
        claim = _fact("EMAS bed", statement="The airport has an EMAS bed.")
        result = persist_promotion_policy(session, assertion, (claim,), TIER_1)
        assert result.signal_candidate.outcome == SignalCandidateOutcome.INSUFFICIENT_MATERIALITY
        assert result.outcome == PromotionPolicyOutcome.DO_NOT_PROMOTE
        assert assertion.promotion_policy_decision == "DO_NOT_PROMOTE"

    def test_identity_not_confirmed_persists_do_not_promote(self, session):
        assertion = _bare_assertion(session, identity_guard_decision="INSUFFICIENT_IDENTITY")
        claim = _award_claim()
        result = persist_promotion_policy(session, assertion, (claim,), TIER_1)
        assert result.signal_candidate.outcome == SignalCandidateOutcome.IDENTITY_NOT_CONFIRMED
        assert result.outcome == PromotionPolicyOutcome.DO_NOT_PROMOTE

    def test_contradicted_persists_do_not_promote(self, session):
        assertion = _bare_assertion(session)
        claims = (
            _relationship_claim("EMAS bed", "Acme Corp", "sole_source_vendor"),
            _relationship_claim("EMAS bed", "Beta LLC", "sole_source_vendor"),
        )
        result = persist_promotion_policy(session, assertion, claims, TIER_1)
        assert result.signal_candidate.outcome == SignalCandidateOutcome.CONTRADICTED
        assert result.outcome == PromotionPolicyOutcome.DO_NOT_PROMOTE

    def test_duplicate_within_evidence_persists_do_not_promote(self, session):
        assertion = _bare_assertion(session)
        claim = _award_claim()
        result = persist_promotion_policy(session, assertion, (claim, claim, claim), TIER_1)
        assert result.signal_candidate.outcome == SignalCandidateOutcome.DUPLICATE_WITHIN_EVIDENCE
        assert result.outcome == PromotionPolicyOutcome.DO_NOT_PROMOTE

    def test_context_superseded_persists_do_not_promote_even_for_otherwise_eligible_claim(self, session):
        assertion = _bare_assertion(session)
        claim = _award_claim()
        context = PromotionPolicyContext(source_authority_tier=SourceAuthorityTier.TIER_1_PRIMARY_OFFICIAL, superseded=True)
        result = persist_promotion_policy(session, assertion, (claim,), context)
        assert result.signal_candidate.outcome == SignalCandidateOutcome.REVIEW_REQUIRED  # would otherwise qualify
        assert result.outcome == PromotionPolicyOutcome.DO_NOT_PROMOTE
        assert assertion.promotion_policy_decision == "DO_NOT_PROMOTE"

    def test_do_not_promote_never_deletes_the_source_assertion(self, session):
        assertion = _bare_assertion(session)
        claim = _fact("EMAS bed", statement="The airport has an EMAS bed.")
        persist_promotion_policy(session, assertion, (claim,), TIER_1)
        assert session.get(SourceAssertion, assertion.id) is not None
        assert session.get(SourceAssertion, assertion.id).raw_relevant_text == "original evidence text"


# --- 17-19. Source authority handling ---

class TestSourceAuthority:
    def test_unknown_source_tier_cannot_become_auto_eligible(self, session):
        assertion = _bare_assertion(session)
        claim = _award_claim()
        result = persist_promotion_policy(session, assertion, (claim,), UNKNOWN_TIER)
        assert result.outcome != PromotionPolicyOutcome.AUTO_ELIGIBLE
        assert result.outcome == PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED

    def test_source_reliability_level_official_never_implies_tier1(self, session):
        # Hard safety invariant (task S8): Source.reliability_level="official"
        # must never silently become Tier 1 - the service must not even look
        # at it (it has no Source/reliability_level dependency at all).
        source = Source(title="Test Source", source_type="test", reliability_level="official")
        session.add(source)
        session.flush()
        assertion = SourceAssertion(
            source_id=source.id, assertion_type="project_construction", source_locator="loc-1",
            raw_fragment_hash="hash-1", artifact_identity="artifact-1",
            identity_guard_decision="ATTACH_CONFIRMED", identity_guard_reason="reason",
        )
        session.add(assertion)
        session.flush()
        claim = _award_claim()
        # No source_authority_tier supplied at all - "official" on the real
        # Source row must NOT be read/inferred by the service.
        result = persist_promotion_policy(session, assertion, (claim,), PromotionPolicyContext())
        assert result.outcome != PromotionPolicyOutcome.AUTO_ELIGIBLE
        # The service must not even reference reliability_level - it has no
        # way to read it (no Source import at all, see the dedicated
        # import-boundary test below). AST-based, not substring: the
        # module's own docstring legitimately explains, in prose, that it
        # never reads this field - a naive substring search would
        # false-positive on that explanation.
        tree = ast.parse(inspect.getsource(ppp))
        attribute_accesses = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert "reliability_level" not in attribute_accesses

    def test_source_module_never_imports_source_model(self):
        source = inspect.getsource(ppp)
        tree = ast.parse(source)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
        assert "Source" not in imported_names

    def test_tier3_context_forces_human_review_for_otherwise_eligible_candidate(self, session):
        assertion = _bare_assertion(session)
        claim = _award_claim()
        context = PromotionPolicyContext(source_authority_tier=SourceAuthorityTier.TIER_3_CREDIBLE_SECONDARY)
        result = persist_promotion_policy(session, assertion, (claim,), context)
        assert result.outcome == PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED


# --- 20-22. Idempotency / recomputation / preservation ---

class TestIdempotencyAndRecomputation:
    def test_idempotent_repeat_with_identical_inputs(self, session):
        assertion, claims = _seed_msp_assertion(session)
        first = persist_promotion_policy(session, assertion, claims, TIER_1)
        first_decision, first_reason = assertion.promotion_policy_decision, assertion.promotion_policy_reason
        second = persist_promotion_policy(session, assertion, claims, TIER_1)
        assert first.outcome == second.outcome
        assert first.reason == second.reason
        assert assertion.promotion_policy_decision == first_decision
        assert assertion.promotion_policy_reason == first_reason
        assert session.query(SourceAssertion).count() == 1

    def test_changed_context_recomputes_only_the_two_policy_fields(self, session):
        assertion = _bare_assertion(session)
        claim = _award_claim()
        persist_promotion_policy(session, assertion, (claim,), UNKNOWN_TIER)
        assert assertion.promotion_policy_decision == "HUMAN_REVIEW_REQUIRED"

        before = (
            assertion.raw_relevant_text, assertion.identity_guard_decision, assertion.identity_guard_reason,
            assertion.intelligence_review_decision, assertion.intelligence_review_reason,
            assertion.artifact_identity, assertion.source_locator, assertion.raw_fragment_hash,
        )
        persist_promotion_policy(session, assertion, (claim,), TIER_1)
        after = (
            assertion.raw_relevant_text, assertion.identity_guard_decision, assertion.identity_guard_reason,
            assertion.intelligence_review_decision, assertion.intelligence_review_reason,
            assertion.artifact_identity, assertion.source_locator, assertion.raw_fragment_hash,
        )
        assert assertion.promotion_policy_decision == "AUTO_ELIGIBLE"  # policy fields DID change
        assert before == after  # everything else did not

    def test_unrelated_fields_preserved(self, session):
        assertion, claims = _seed_msp_assertion(session)
        before = (
            assertion.raw_relevant_text, assertion.identity_guard_decision, assertion.identity_guard_reason,
            assertion.artifact_identity, assertion.source_locator, assertion.raw_fragment_hash,
            assertion.airport_id, assertion.assertion_type, assertion.evidence_quality, assertion.review_state,
        )
        persist_promotion_policy(session, assertion, claims, TIER_1)
        after = (
            assertion.raw_relevant_text, assertion.identity_guard_decision, assertion.identity_guard_reason,
            assertion.artifact_identity, assertion.source_locator, assertion.raw_fragment_hash,
            assertion.airport_id, assertion.assertion_type, assertion.evidence_quality, assertion.review_state,
        )
        assert before == after

    def test_intelligence_review_fields_never_touched(self, session):
        assertion, claims = _seed_msp_assertion(session)
        persist_intelligence_review(session, assertion, claims)
        before = (assertion.intelligence_review_decision, assertion.intelligence_review_reason)
        persist_promotion_policy(session, assertion, claims, TIER_1)
        after = (assertion.intelligence_review_decision, assertion.intelligence_review_reason)
        assert before == after


# --- 23-24. Transaction ownership ---

class TestTransactionOwnership:
    def test_service_never_commits(self, session, monkeypatch):
        assertion, claims = _seed_msp_assertion(session)
        commits = []
        original_commit = session.commit
        monkeypatch.setattr(session, "commit", lambda: commits.append(1) or original_commit())
        persist_promotion_policy(session, assertion, claims, TIER_1)
        assert commits == []

    def test_rollback_leaves_no_persisted_mutation(self, session):
        assertion, claims = _seed_msp_assertion(session)
        session.commit()  # commit the governed baseline row only
        assertion_id = assertion.id
        db_url = session.bind.url

        persist_promotion_policy(session, assertion, claims, TIER_1)
        assert assertion.promotion_policy_decision == "HUMAN_REVIEW_REQUIRED"  # visible in-session
        session.rollback()

        engine2 = create_engine(db_url)
        with Session(engine2) as fresh:
            reloaded = fresh.get(SourceAssertion, assertion_id)
            assert reloaded.promotion_policy_decision is None
            assert reloaded.promotion_policy_reason is None
        engine2.dispose()


# --- 25-27. No-Signal / no-canonical-write ---

class TestNoSignalOrCanonicalWrites:
    def test_no_signal_import(self):
        source = inspect.getsource(ppp)
        tree = ast.parse(source)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
        assert "Signal" not in imported_names

    def test_zero_signal_rows_for_msp_human_review(self, session):
        assertion, claims = _seed_msp_assertion(session)
        before = session.scalars(select(Signal)).all()
        persist_promotion_policy(session, assertion, claims, TIER_1)
        after = session.scalars(select(Signal)).all()
        assert before == [] and after == []

    def test_zero_signal_rows_for_do_not_promote(self, session):
        assertion = _bare_assertion(session)
        claim = _fact("EMAS bed", statement="The airport has an EMAS bed.")
        before = session.scalars(select(Signal)).all()
        persist_promotion_policy(session, assertion, (claim,), TIER_1)
        after = session.scalars(select(Signal)).all()
        assert before == [] and after == []

    def test_no_canonical_writes(self, session):
        assertion, claims = _seed_msp_assertion(session)
        counts_before = {
            "airports": session.query(Airport).count(), "runways": session.query(Runway).count(),
            "runway_ends": session.query(RunwayEnd).count(), "installations": session.query(Installation).count(),
            "physical_installation_identities": session.query(PhysicalInstallationIdentity).count(),
        }
        persist_promotion_policy(session, assertion, claims, TIER_1)
        counts_after = {
            "airports": session.query(Airport).count(), "runways": session.query(Runway).count(),
            "runway_ends": session.query(RunwayEnd).count(), "installations": session.query(Installation).count(),
            "physical_installation_identities": session.query(PhysicalInstallationIdentity).count(),
        }
        assert counts_before == counts_after


# --- 28-29. Deterministic reason / international ---

class TestDeterminismAndInternational:
    def test_deterministic_persisted_reason(self, session):
        assertion, claims = _seed_msp_assertion(session)
        first = persist_promotion_policy(session, assertion, claims, TIER_1)
        second = persist_promotion_policy(session, assertion, claims, TIER_1)
        assert first.reason == second.reason

    def test_international_non_usd_case_persists_auto_eligible(self, session):
        assertion = _bare_assertion(session)
        claim = _fact(
            "Haneda RESA replacement, Runway 05", statement="The Haneda Airport Authority awarded Taiyo Safety Materials KK a contract.",
            temporal=TemporalContext(qualifier=TemporalQualifier.HISTORICAL_FACT, as_of_date=date(2026, 1, 1)),
            financial=FinancialFact(amount=Decimal("450000000"), currency="JPY", semantic_role="contract_award_amount"),
            relationship=RelationshipFact(party="Taiyo Safety Materials KK", role="awarded_contractor"),
        )
        result = persist_promotion_policy(session, assertion, (claim,), TIER_1)
        assert result.outcome == PromotionPolicyOutcome.AUTO_ELIGIBLE
        assert "MAC" not in assertion.promotion_policy_reason and "MSP" not in assertion.promotion_policy_reason


# --- 30. Existing NULL rows remain valid ---

class TestNullRowsRemainValid:
    def test_row_never_reviewed_keeps_null_policy_fields(self, session):
        assertion = _bare_assertion(session)
        session.commit()
        reloaded = session.get(SourceAssertion, assertion.id)
        assert reloaded.promotion_policy_decision is None
        assert reloaded.promotion_policy_reason is None


# --- Purity of the persistence module itself ---

class TestPersistenceModulePurity:
    def test_no_current_time_calls(self):
        source = inspect.getsource(ppp)
        tree = ast.parse(source)
        forbidden_calls = {"today", "now", "utcnow"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_calls


# --- EB5: promotion policy automatically stays in sync with the shared
# effective-identity-decision helper, without its own copy of the
# precedence logic (docs/architecture/rwi-eb5-downstream-identity-consumption-report.md) ---

class TestEB5EffectiveIdentityIntegration:
    def test_historical_insufficient_latest_confirmed_reaches_promotion_policy(self, session):
        from app.models.source_assertion_evidence_bag import SourceAssertionEvidenceBag
        from app.models.identity_guard_evaluation import IdentityGuardEvaluation
        from app.services.evidence_bag_serialization import serialize_evidence_bag, hash_serialized_evidence_bag
        from app.services.evidence_attachment_guard import EvidenceBag

        airport = Airport(name="Foo Regional Airport", country="USA")
        session.add(airport)
        session.flush()
        assertion = _bare_assertion(session, identity_guard_decision="INSUFFICIENT_IDENTITY")
        assertion.airport_id = airport.id
        session.flush()

        payload = serialize_evidence_bag(EvidenceBag(identifiers=frozenset({"FOO"})))
        snapshot = SourceAssertionEvidenceBag(
            source_assertion_id=assertion.id, evidence_bag_json=payload,
            evidence_bag_hash=hash_serialized_evidence_bag(payload), schema_version=1,
        )
        session.add(snapshot)
        session.flush()
        session.add(IdentityGuardEvaluation(
            source_assertion_id=assertion.id, evidence_bag_snapshot_id=snapshot.id,
            evaluated_against_airport_id=airport.id, outcome="ATTACH_CONFIRMED", reason="synthetic",
        ))
        session.flush()

        claim = _completion_claim()
        result = persist_promotion_policy(session, assertion, (claim,), TIER_1)
        from app.services.signal_candidate_evaluation import SignalCandidateOutcome

        assert result.signal_candidate.outcome != SignalCandidateOutcome.IDENTITY_NOT_CONFIRMED
        # historical field untouched, and intelligence_review_decision
        # (a column this module never writes) remains untouched too
        assert assertion.identity_guard_decision == "INSUFFICIENT_IDENTITY"
        assert assertion.intelligence_review_decision is None
