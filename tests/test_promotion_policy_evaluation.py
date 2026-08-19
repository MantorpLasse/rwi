"""Tests for app.services.promotion_policy_evaluation
(docs/architecture/promotion-policy-core-slice6-report.md)."""
from __future__ import annotations

import ast
import inspect
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.acquisition.mac_granicus_claims import extract_mac_claims
from app.acquisition.mac_granicus_extractor import extract_candidate_fragment
from app.services import promotion_policy_evaluation as ppm
from app.services.evidence_attachment_guard import AttachmentOutcome
from app.services.evidence_claim_semantics import (
    Claim,
    ClaimCategory,
    ClaimProvenance,
    FinancialFact,
    RelationshipFact,
    TemporalContext,
    TemporalQualifier,
)
from app.services.promotion_policy_evaluation import (
    PromotionPolicyContext,
    PromotionPolicyOutcome,
    SourceAuthorityTier,
    evaluate_promotion_policy,
)
from app.services.signal_candidate_evaluation import (
    SignalCandidateContext,
    SignalCandidateDecision,
    SignalCandidateOutcome,
    evaluate_signal_candidate,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mac_granicus_emas_procurement_memo_sample.pdf"
CONFIRMED = SignalCandidateContext(identity_decision=AttachmentOutcome.ATTACH_CONFIRMED)
TIER_1 = PromotionPolicyContext(source_authority_tier=SourceAuthorityTier.TIER_1_PRIMARY_OFFICIAL)


def _prov(subject: str = "x") -> ClaimProvenance:
    return ClaimProvenance(
        artifact_identity="test.artifact.1", source_locator="item-1", fragment_hash="a" * 64, raw_text_excerpt=subject,
    )


def _fact(subject: str, statement: str = "fact", **kwargs) -> Claim:
    return Claim(category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject=subject, statement=statement, provenance=_prov(subject), **kwargs)


def _request(subject: str, statement: str = "request", **kwargs) -> Claim:
    return Claim(category=ClaimCategory.PROCEDURAL_REQUEST, subject=subject, statement=statement, provenance=_prov(subject), **kwargs)


def _relationship_claim(subject: str, party: str, role: str) -> Claim:
    return Claim(
        category=ClaimCategory.RELATIONSHIP, subject=subject, statement=f"{party} related as {role}",
        provenance=_prov(subject), relationship=RelationshipFact(party=party, role=role),
    )


def _temporal_claim(subject: str, qualifier: TemporalQualifier, as_of: "date | None" = None) -> Claim:
    return Claim(
        category=ClaimCategory.TEMPORAL_STATEMENT, subject=subject, statement="temporal",
        provenance=_prov(subject), temporal=TemporalContext(qualifier=qualifier, as_of_date=as_of),
    )


def _review_required(claims: "tuple[Claim, ...]") -> SignalCandidateDecision:
    decision = evaluate_signal_candidate(claims, CONFIRMED)
    assert decision.outcome == SignalCandidateOutcome.REVIEW_REQUIRED
    return decision


def _real_msp_claims() -> "tuple[Claim, ...]":
    pdf_bytes = FIXTURE_PATH.read_bytes()
    result = extract_candidate_fragment(
        pdf_bytes, "application/pdf", artifact_identity="mac.granicus.document.4.2349.105406", source_locator="item-2.3.2",
    )
    assert result is not None
    fragment, _vendors = result
    return extract_mac_claims(fragment)


def _award_claim(**overrides) -> Claim:
    defaults = dict(
        subject="EMAS replacement, Runway 27",
        statement="Airport Commission awarded Vendor X a contract for EMAS replacement at Runway 27.",
        temporal=TemporalContext(qualifier=TemporalQualifier.HISTORICAL_FACT, as_of_date=date(2026, 6, 1)),
        financial=FinancialFact(amount=Decimal("12500000"), currency="USD", semantic_role="contract_award_amount"),
        relationship=RelationshipFact(party="Vendor X", role="awarded_contractor"),
    )
    defaults.update(overrides)
    return _fact(**defaults)


def _completion_claim(**overrides) -> Claim:
    defaults = dict(
        subject="EMAS replacement, Runway 11",
        statement="Replacement of the Runway 11 EMAS was completed on 2026-08-15.",
        temporal=TemporalContext(qualifier=TemporalQualifier.COMPLETED, as_of_date=date(2026, 8, 15)),
    )
    defaults.update(overrides)
    return _fact(**defaults)


# --- 1. MSP #222 golden case ---

class TestMSPGoldenCase:
    def test_msp_is_human_review_required_even_with_tier1_authority(self):
        claims = _real_msp_claims()
        signal_candidate = _review_required(claims)
        decision = evaluate_promotion_policy(signal_candidate, claims, TIER_1)
        assert decision.outcome == PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED

    def test_msp_reasons_are_structural(self):
        claims = _real_msp_claims()
        signal_candidate = _review_required(claims)
        decision = evaluate_promotion_policy(signal_candidate, claims, TIER_1)
        blocking = " ".join(decision.blocking_reasons)
        assert "advance_deposit_purchase_order" in blocking
        assert "cip_project_ceiling" in blocking
        assert "requested_sole_source_vendor" in blocking
        assert "installation_oversight" in blocking

    def test_msp_is_not_do_not_promote(self):
        claims = _real_msp_claims()
        signal_candidate = _review_required(claims)
        decision = evaluate_promotion_policy(signal_candidate, claims, TIER_1)
        assert decision.outcome != PromotionPolicyOutcome.DO_NOT_PROMOTE


# --- 2-3, 13, 16, 19, 21. Golden AUTO_ELIGIBLE cases ---

class TestGoldenAutoEligibleCases:
    def test_explicit_tier1_award_is_auto_eligible(self):
        claim = _award_claim()
        signal_candidate = _review_required((claim,))
        decision = evaluate_promotion_policy(signal_candidate, (claim,), TIER_1)
        assert decision.outcome == PromotionPolicyOutcome.AUTO_ELIGIBLE

    def test_explicit_tier1_completion_is_auto_eligible(self):
        claim = _completion_claim()
        signal_candidate = _review_required((claim,))
        decision = evaluate_promotion_policy(signal_candidate, (claim,), TIER_1)
        assert decision.outcome == PromotionPolicyOutcome.AUTO_ELIGIBLE

    def test_confirmed_contract_vendor_role_also_qualifies(self):
        claim = _award_claim(relationship=RelationshipFact(party="Vendor X", role="confirmed_contract_vendor"))
        signal_candidate = _review_required((claim,))
        decision = evaluate_promotion_policy(signal_candidate, (claim,), TIER_1)
        assert decision.outcome == PromotionPolicyOutcome.AUTO_ELIGIBLE

    def test_auto_eligible_reason_explicitly_disclaims_writing_a_signal(self):
        claim = _award_claim()
        signal_candidate = _review_required((claim,))
        decision = evaluate_promotion_policy(signal_candidate, (claim,), TIER_1)
        assert "does not create, update, or publish any signal" in decision.reason.lower()


# --- 4-8. SignalCandidate outcome mapping to DO_NOT_PROMOTE ---

class TestSignalCandidateOutcomeMapping:
    def _decision(self, outcome: SignalCandidateOutcome) -> SignalCandidateDecision:
        return SignalCandidateDecision(outcome=outcome, reason="synthetic")

    def test_insufficient_materiality_maps_to_do_not_promote(self):
        decision = evaluate_promotion_policy(self._decision(SignalCandidateOutcome.INSUFFICIENT_MATERIALITY), (), TIER_1)
        assert decision.outcome == PromotionPolicyOutcome.DO_NOT_PROMOTE

    def test_identity_not_confirmed_maps_to_do_not_promote(self):
        decision = evaluate_promotion_policy(self._decision(SignalCandidateOutcome.IDENTITY_NOT_CONFIRMED), (), TIER_1)
        assert decision.outcome == PromotionPolicyOutcome.DO_NOT_PROMOTE

    def test_contradicted_maps_to_do_not_promote(self):
        decision = evaluate_promotion_policy(self._decision(SignalCandidateOutcome.CONTRADICTED), (), TIER_1)
        assert decision.outcome == PromotionPolicyOutcome.DO_NOT_PROMOTE

    def test_duplicate_within_evidence_maps_to_do_not_promote(self):
        decision = evaluate_promotion_policy(self._decision(SignalCandidateOutcome.DUPLICATE_WITHIN_EVIDENCE), (), TIER_1)
        assert decision.outcome == PromotionPolicyOutcome.DO_NOT_PROMOTE

    def test_stale_or_superseded_maps_to_do_not_promote(self):
        decision = evaluate_promotion_policy(self._decision(SignalCandidateOutcome.STALE_OR_SUPERSEDED), (), TIER_1)
        assert decision.outcome == PromotionPolicyOutcome.DO_NOT_PROMOTE

    def test_only_review_required_proceeds_past_the_mapping_gate(self):
        claim = _award_claim()
        signal_candidate = _review_required((claim,))
        decision = evaluate_promotion_policy(signal_candidate, (claim,), TIER_1)
        assert decision.outcome != PromotionPolicyOutcome.DO_NOT_PROMOTE


# --- 9-10. Source authority tier ---

class TestSourceAuthorityTier:
    def test_unknown_source_tier_blocks_auto_eligibility(self):
        claim = _award_claim()
        signal_candidate = _review_required((claim,))
        context = PromotionPolicyContext(source_authority_tier=None)
        decision = evaluate_promotion_policy(signal_candidate, (claim,), context)
        assert decision.outcome == PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED

    def test_tier3_source_blocks_auto_eligibility(self):
        claim = _award_claim()
        signal_candidate = _review_required((claim,))
        context = PromotionPolicyContext(source_authority_tier=SourceAuthorityTier.TIER_3_CREDIBLE_SECONDARY)
        decision = evaluate_promotion_policy(signal_candidate, (claim,), context)
        assert decision.outcome == PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED

    def test_tier2_source_also_blocks_auto_eligibility(self):
        claim = _award_claim()
        signal_candidate = _review_required((claim,))
        context = PromotionPolicyContext(source_authority_tier=SourceAuthorityTier.TIER_2_OFFICIAL_GOVERNMENT)
        decision = evaluate_promotion_policy(signal_candidate, (claim,), context)
        assert decision.outcome == PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED


# --- 11-12. Financial allowlist ---

class TestFinancialAllowlist:
    def test_safe_completion_claim_still_blocked_by_unrelated_unsafe_financial_claim(self):
        # Checkpoint-review regression: the whole-claim-set refinement (§7 of
        # the Slice 6 report) must catch an unsafe financial claim even when
        # it sits on a DIFFERENT claim object from the one satisfying the
        # happened-event rule - a document describing both a completed
        # event and a separate, unrelated budget ceiling must not let the
        # ceiling's ambiguity slip through just because it wasn't attached
        # to the "safe" claim.
        completion_claim = _completion_claim()
        unrelated_ceiling_claim = _fact(
            "Unrelated CIP line item", statement="approved",
            temporal=TemporalContext(qualifier=TemporalQualifier.HISTORICAL_FACT, as_of_date=date(2023, 12, 18)),
            financial=FinancialFact(amount=Decimal("19000000"), currency="USD", semantic_role="cip_project_ceiling"),
        )
        claims = (completion_claim, unrelated_ceiling_claim)
        signal_candidate = _review_required(claims)
        decision = evaluate_promotion_policy(signal_candidate, claims, TIER_1)
        assert decision.outcome == PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED
        assert "cip_project_ceiling" in " ".join(decision.blocking_reasons)

    def test_cip_ceiling_blocks_auto_eligibility(self):
        claim = _fact(
            "CIP", statement="approved", temporal=TemporalContext(qualifier=TemporalQualifier.HISTORICAL_FACT, as_of_date=date(2023, 12, 18)),
            financial=FinancialFact(amount=Decimal("19000000"), currency="USD", semantic_role="cip_project_ceiling"),
        )
        signal_candidate = _review_required((claim,))
        decision = evaluate_promotion_policy(signal_candidate, (claim,), TIER_1)
        assert decision.outcome == PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED
        assert "cip_project_ceiling" in " ".join(decision.blocking_reasons)

    def test_advance_deposit_blocks_auto_eligibility(self):
        claim = _request(
            "PO", statement="requests advance deposit PO",
            temporal=TemporalContext(qualifier=TemporalQualifier.REQUESTED_PENDING_APPROVAL, as_of_date=date(2024, 8, 28)),
            financial=FinancialFact(amount=Decimal("1590000"), currency="USD", semantic_role="advance_deposit_purchase_order"),
        )
        signal_candidate = _review_required((claim,))
        decision = evaluate_promotion_policy(signal_candidate, (claim,), TIER_1)
        assert decision.outcome == PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED
        assert "advance_deposit_purchase_order" in " ".join(decision.blocking_reasons)

    def test_estimated_project_cost_blocks_auto_eligibility(self):
        claim = _fact(
            "estimate", temporal=TemporalContext(qualifier=TemporalQualifier.HISTORICAL_FACT, as_of_date=date(2024, 1, 1)),
            financial=FinancialFact(amount=Decimal("5000000"), currency="USD", semantic_role="estimated_project_cost"),
        )
        signal_candidate = _review_required((claim,))
        decision = evaluate_promotion_policy(signal_candidate, (claim,), TIER_1)
        assert decision.outcome == PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED

    def test_safe_explicit_award_amount_qualifies(self):
        claim = _award_claim()
        signal_candidate = _review_required((claim,))
        decision = evaluate_promotion_policy(signal_candidate, (claim,), TIER_1)
        assert decision.outcome == PromotionPolicyOutcome.AUTO_ELIGIBLE
        assert any("financial" in r for r in decision.auto_eligibility_reasons)


# --- 14-16. Procedural/temporal policy ---

class TestProceduralAndTemporalPolicy:
    def test_requested_procedural_state_blocks_auto_eligibility(self):
        claim = _request(
            "PO", temporal=TemporalContext(qualifier=TemporalQualifier.REQUESTED_PENDING_APPROVAL, as_of_date=date(2024, 8, 28)),
            financial=FinancialFact(amount=Decimal("1590000"), currency="USD", semantic_role="advance_deposit_purchase_order"),
        )
        signal_candidate = _review_required((claim,))
        decision = evaluate_promotion_policy(signal_candidate, (claim,), TIER_1)
        assert decision.outcome == PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED
        assert "no_happened_event" in decision.blocking_reasons

    def test_planned_future_state_blocks_auto_eligibility(self):
        fact_claim = _fact("EMAS bed", statement="requires replacement")
        planned_claim = _temporal_claim("installation", TemporalQualifier.PLANNED_FUTURE_ACTION, as_of=date(2024, 8, 28))
        claims = (fact_claim, planned_claim)
        signal_candidate = _review_required(claims)
        decision = evaluate_promotion_policy(signal_candidate, claims, TIER_1)
        assert decision.outcome == PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED
        assert "no_happened_event" in decision.blocking_reasons

    def test_completed_state_qualifies(self):
        claim = _completion_claim()
        signal_candidate = _review_required((claim,))
        decision = evaluate_promotion_policy(signal_candidate, (claim,), TIER_1)
        assert decision.outcome == PromotionPolicyOutcome.AUTO_ELIGIBLE

    def test_no_current_time_reinterprets_planned_as_completed(self):
        # Session's real system date is 2026-08-19, well past the claim's
        # own 2024/2025 dates. Outcome must not change.
        fact_claim = _fact("EMAS bed", statement="requires replacement")
        planned_claim = _temporal_claim("installation", TemporalQualifier.PLANNED_FUTURE_ACTION, as_of=date(2024, 8, 28))
        claims = (fact_claim, planned_claim)
        signal_candidate = _review_required(claims)
        decision = evaluate_promotion_policy(signal_candidate, claims, TIER_1)
        assert decision.outcome != PromotionPolicyOutcome.AUTO_ELIGIBLE


# --- 17-19. Relationship policy ---

class TestRelationshipPolicy:
    def test_sole_source_relationship_alone_does_not_equal_award(self):
        fact_claim = _fact("EMAS bed", temporal=TemporalContext(qualifier=TemporalQualifier.HISTORICAL_FACT, as_of_date=date(2024, 1, 1)))
        relationship_claim = _relationship_claim("EMAS bed", "Acme", "requested_sole_source_vendor")
        claims = (fact_claim, relationship_claim)
        signal_candidate = _review_required(claims)
        decision = evaluate_promotion_policy(signal_candidate, claims, TIER_1)
        assert decision.outcome == PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED
        assert "requested_sole_source_vendor" in " ".join(decision.blocking_reasons)

    def test_oversight_relationship_alone_does_not_equal_award(self):
        fact_claim = _fact("installation", temporal=TemporalContext(qualifier=TemporalQualifier.HISTORICAL_FACT, as_of_date=date(2024, 1, 1)))
        relationship_claim = _relationship_claim("installation", "Acme", "installation_oversight")
        claims = (fact_claim, relationship_claim)
        signal_candidate = _review_required(claims)
        decision = evaluate_promotion_policy(signal_candidate, claims, TIER_1)
        assert decision.outcome == PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED
        assert "installation_oversight" in " ".join(decision.blocking_reasons)

    def test_explicit_awarded_vendor_relationship_qualifies(self):
        claim = _award_claim()
        signal_candidate = _review_required((claim,))
        decision = evaluate_promotion_policy(signal_candidate, (claim,), TIER_1)
        assert decision.outcome == PromotionPolicyOutcome.AUTO_ELIGIBLE
        assert any("relationship" in r for r in decision.auto_eligibility_reasons)


# --- 20-21. Corroboration ---

class TestCorroboration:
    def test_missing_required_corroboration_forces_human_review(self):
        claim = _award_claim()
        signal_candidate = _review_required((claim,))
        context = PromotionPolicyContext(
            source_authority_tier=SourceAuthorityTier.TIER_1_PRIMARY_OFFICIAL,
            requires_corroboration=True, corroborating_source_count=1,
        )
        decision = evaluate_promotion_policy(signal_candidate, (claim,), context)
        assert decision.outcome == PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED
        assert "insufficient_corroboration" in decision.blocking_reasons

    def test_corroborated_when_required_qualifies(self):
        claim = _award_claim()
        signal_candidate = _review_required((claim,))
        context = PromotionPolicyContext(
            source_authority_tier=SourceAuthorityTier.TIER_1_PRIMARY_OFFICIAL,
            requires_corroboration=True, corroborating_source_count=2,
        )
        decision = evaluate_promotion_policy(signal_candidate, (claim,), context)
        assert decision.outcome == PromotionPolicyOutcome.AUTO_ELIGIBLE

    def test_explicit_tier1_event_needs_no_corroboration_by_default(self):
        claim = _completion_claim()
        signal_candidate = _review_required((claim,))
        context = PromotionPolicyContext(source_authority_tier=SourceAuthorityTier.TIER_1_PRIMARY_OFFICIAL)
        decision = evaluate_promotion_policy(signal_candidate, (claim,), context)
        assert decision.outcome == PromotionPolicyOutcome.AUTO_ELIGIBLE


# --- 22. SFO-$40M adversarial case ---

class TestSFO40MAdversarial:
    def test_unlabeled_amount_and_weak_relationship_never_auto_eligible(self):
        fact_claim = _fact("SFO EMAS context", statement="SFO is evaluating EMAS options.")
        relationship_claim = _relationship_claim("SFO EMAS context", "Runway Safe", "mentioned_in_document")
        claims = (fact_claim, relationship_claim)
        signal_candidate = _review_required(claims)
        decision = evaluate_promotion_policy(signal_candidate, claims, TIER_1)
        assert decision.outcome != PromotionPolicyOutcome.AUTO_ELIGIBLE
        assert decision.outcome == PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED
        assert "$40" not in decision.reason
        assert "contract" not in decision.reason.lower()

    def test_insufficient_materiality_variant_maps_to_do_not_promote_never_auto(self):
        signal_candidate = SignalCandidateDecision(outcome=SignalCandidateOutcome.INSUFFICIENT_MATERIALITY, reason="synthetic")
        decision = evaluate_promotion_policy(signal_candidate, (), TIER_1)
        assert decision.outcome == PromotionPolicyOutcome.DO_NOT_PROMOTE
        assert decision.outcome != PromotionPolicyOutcome.AUTO_ELIGIBLE


# --- 23. International readiness ---

class TestInternationalReadiness:
    def test_non_us_non_usd_claim_reaches_auto_eligible_identically(self):
        claim = _fact(
            "Haneda RESA replacement, Runway 05", statement="The Haneda Airport Authority awarded Taiyo Safety Materials KK a contract.",
            temporal=TemporalContext(qualifier=TemporalQualifier.HISTORICAL_FACT, as_of_date=date(2026, 1, 1)),
            financial=FinancialFact(amount=Decimal("450000000"), currency="JPY", semantic_role="contract_award_amount"),
            relationship=RelationshipFact(party="Taiyo Safety Materials KK", role="awarded_contractor"),
        )
        signal_candidate = _review_required((claim,))
        decision = evaluate_promotion_policy(signal_candidate, (claim,), TIER_1)
        assert decision.outcome == PromotionPolicyOutcome.AUTO_ELIGIBLE
        assert "MAC" not in decision.reason and "MSP" not in decision.reason and "FAA" not in decision.reason


# --- 24. Deterministic reason ---

class TestDeterminism:
    def test_same_input_produces_identical_decision(self):
        claim = _award_claim()
        signal_candidate = _review_required((claim,))
        first = evaluate_promotion_policy(signal_candidate, (claim,), TIER_1)
        second = evaluate_promotion_policy(signal_candidate, (claim,), TIER_1)
        assert first == second


# --- 25. Purity / import boundary ---

class TestPurity:
    def test_no_forbidden_imports(self):
        source = inspect.getsource(ppm)
        tree = ast.parse(source)
        forbidden_modules = {"sqlalchemy", "httpx", "requests", "app.database", "app.models"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(alias.name == m or alias.name.startswith(m + ".") for m in forbidden_modules)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not any(module == m or module.startswith(m + ".") for m in forbidden_modules)

    def test_no_signal_import(self):
        # AST-based, not substring: a naive "import Signal" search
        # false-positives on SignalCandidateDecision/SignalCandidateOutcome,
        # which legitimately contain "Signal" as a prefix but are not
        # app.models.signal.Signal at all.
        source = inspect.getsource(ppm)
        tree = ast.parse(source)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
        assert "Signal" not in imported_names
        assert "app.models.signal" not in source
        assert "app.models" not in source


# --- 26. No current-time dependency ---

class TestNoCurrentTimeDependency:
    def test_no_today_now_utcnow_call_anywhere(self):
        source = inspect.getsource(ppm)
        tree = ast.parse(source)
        forbidden_calls = {"today", "now", "utcnow"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_calls, f"forbidden call: {node.func.attr}"
