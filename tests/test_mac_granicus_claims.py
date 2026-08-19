"""Tests for app.acquisition.mac_granicus_claims
(docs/architecture/mac-granicus-claim-extraction-slice2-report.md)."""
from __future__ import annotations

import ast
import inspect
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.acquisition.mac_granicus_claims import extract_mac_claims
from app.acquisition import mac_granicus_claims as claims_module
from app.acquisition.mac_granicus_extractor import extract_candidate_fragment
from app.services.discovery_candidate_fragment import CandidateFragment, ExtractedDate, ExtractedMoney
from app.services.evidence_claim_semantics import ClaimCategory, TemporalQualifier

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mac_granicus_emas_procurement_memo_sample.pdf"

MSP_ARTIFACT_IDENTITY = "mac.granicus.document.4.2349.105406"
MSP_SOURCE_LOCATOR = "item-2.3.2"


def _real_fragment() -> CandidateFragment:
    pdf_bytes = FIXTURE_PATH.read_bytes()
    result = extract_candidate_fragment(
        pdf_bytes, "application/pdf",
        artifact_identity=MSP_ARTIFACT_IDENTITY, source_locator=MSP_SOURCE_LOCATOR,
    )
    assert result is not None
    fragment, _vendors = result
    return fragment


def _fragment(raw_text: str, **kwargs) -> CandidateFragment:
    kwargs.setdefault("artifact_identity", "test.artifact.1")
    kwargs.setdefault("source_locator", "item-1.1.1")
    return CandidateFragment(raw_text=raw_text, **kwargs)


# --- Real MSP golden case (A-I) ---

class TestRealMSPGoldenCase:
    def test_produces_seven_claims(self):
        claims = extract_mac_claims(_real_fragment())
        assert len(claims) == 7

    def test_claim_a_lifecycle_condition(self):
        claims = extract_mac_claims(_real_fragment())
        matches = [c for c in claims if "life expectancy" in c.statement]
        assert len(matches) == 1
        assert matches[0].category == ClaimCategory.EXPLICIT_DOCUMENT_FACT

    def test_claim_b_required_action(self):
        claims = extract_mac_claims(_real_fragment())
        matches = [c for c in claims if "replacement is required" in c.statement]
        assert len(matches) == 1
        assert matches[0].category == ClaimCategory.EXPLICIT_DOCUMENT_FACT

    def test_claim_c_sole_source_relationship_is_requested_not_confirmed(self):
        claims = extract_mac_claims(_real_fragment())
        matches = [c for c in claims if c.relationship and c.relationship.role == "requested_sole_source_vendor"]
        assert len(matches) == 1
        claim = matches[0]
        assert claim.category == ClaimCategory.RELATIONSHIP
        assert claim.relationship.party == "Runway Safe"
        assert "pending" in claim.relationship.scope

    def test_claim_d_procedural_request_with_deposit_financial(self):
        claims = extract_mac_claims(_real_fragment())
        matches = [c for c in claims if c.category == ClaimCategory.PROCEDURAL_REQUEST]
        assert len(matches) == 1
        claim = matches[0]
        assert claim.financial is not None
        assert claim.financial.amount == Decimal("1590000.00")
        assert claim.financial.semantic_role == "advance_deposit_purchase_order"
        assert claim.temporal.qualifier == TemporalQualifier.REQUESTED_PENDING_APPROVAL
        assert claim.temporal.as_of_date == date(2024, 8, 28)

    def test_claim_f_cip_ceiling_financial_with_not_established(self):
        claims = extract_mac_claims(_real_fragment())
        matches = [c for c in claims if c.financial and c.financial.semantic_role == "cip_project_ceiling"]
        assert len(matches) == 1
        claim = matches[0]
        assert claim.financial.amount == Decimal("19000000.00")
        assert claim.category == ClaimCategory.EXPLICIT_DOCUMENT_FACT
        assert "contract_value" in claim.financial.not_established
        assert claim.temporal.qualifier == TemporalQualifier.HISTORICAL_FACT
        assert claim.temporal.as_of_date == date(2023, 12, 18)

    def test_claim_h_planned_future_installation(self):
        claims = extract_mac_claims(_real_fragment())
        matches = [c for c in claims if c.category == ClaimCategory.TEMPORAL_STATEMENT]
        assert len(matches) == 1
        claim = matches[0]
        assert claim.temporal.qualifier == TemporalQualifier.PLANNED_FUTURE_ACTION
        assert claim.temporal.as_of_date == date(2024, 8, 28)
        assert "2025" in claim.temporal.detail

    def test_claim_i_installation_oversight_distinct_from_sole_source(self):
        claims = extract_mac_claims(_real_fragment())
        matches = [c for c in claims if c.relationship and c.relationship.role == "installation_oversight"]
        assert len(matches) == 1
        claim = matches[0]
        assert claim.relationship.party == "Runway Safe"
        assert "not the installation contractor" in claim.relationship.scope

    def test_no_contract_value_or_award_claim_fabricated(self):
        claims = extract_mac_claims(_real_fragment())
        for claim in claims:
            if claim.financial:
                assert claim.financial.semantic_role not in {"contract_value", "vendor_revenue", "total_project_cost"}
            assert "confirmed" not in claim.statement.lower() or "not" in claim.statement.lower()

    def test_no_pending_claim_says_approved_awarded_executed_completed(self):
        # The CIP claim (F) genuinely IS historical/approved and is
        # allowed to say so. Only claims describing still-pending staff
        # requests (C, D) must never use these words.
        claims = extract_mac_claims(_real_fragment())
        pending = [c for c in claims if c.temporal and c.temporal.qualifier == TemporalQualifier.REQUESTED_PENDING_APPROVAL]
        relationship_pending = [c for c in claims if c.relationship and c.relationship.role == "requested_sole_source_vendor"]
        forbidden = {"approved", "awarded", "executed", "completed"}
        for claim in [*pending, *relationship_pending]:
            words = set(claim.statement.lower().replace(".", "").split())
            assert not (words & forbidden), claim.statement


# --- Duplicate suppression ---

class TestDuplicateSuppression:
    def test_real_fragment_has_only_one_advance_deposit_financial_claim(self):
        claims = extract_mac_claims(_real_fragment())
        deposit_claims = [c for c in claims if c.financial and c.financial.semantic_role == "advance_deposit_purchase_order"]
        assert len(deposit_claims) == 1

    def test_ambiguous_conflicting_labels_for_same_amount_omits_financial_claim(self):
        fragment = _fragment(
            "Authorize staff to issue a Purchase Order to Acme Corp in the amount of $500,000.00 for materials.",
            money_values=(
                ExtractedMoney(raw_text="$500,000.00", numeric_value=Decimal("500000.00"), currency="USD", context_label="advance_deposit"),
                ExtractedMoney(raw_text="$500,000.00", numeric_value=Decimal("500000.00"), currency="USD", context_label="cip_project_ceiling"),
            ),
        )
        claims = extract_mac_claims(fragment)
        assert all(c.financial is None for c in claims)

    def test_unlabeled_only_amount_produces_no_financial_claim(self):
        fragment = _fragment(
            "Authorize staff to issue a Purchase Order to Acme Corp in the amount of $500,000.00 for materials.",
            money_values=(
                ExtractedMoney(raw_text="$500,000.00", numeric_value=Decimal("500000.00"), currency="USD", context_label=None),
            ),
        )
        claims = extract_mac_claims(fragment)
        assert all(c.financial is None for c in claims)


# --- Fail-closed behavior ---

class TestFailClosed:
    def test_unrelated_agenda_item_produces_empty_tuple(self):
        fragment = _fragment(
            "Recommend that the full Commission approve the purchase of a new reliever airport radio system "
            "for the aircraft rescue and firefighting department.",
        )
        assert extract_mac_claims(fragment) == ()

    def test_money_and_vendor_present_but_no_relationship_phrase_yields_no_relationship_claim(self):
        fragment = _fragment(
            "The 2025 EMAS Replacement CIP project is budgeted at $19,000,000.00. Runway Safe is a known "
            "manufacturer of EMAS materials used at many airports nationwide.",
            money_values=(
                ExtractedMoney(raw_text="$19,000,000.00", numeric_value=Decimal("19000000.00"), currency="USD", context_label="cip_project_ceiling"),
            ),
        )
        claims = extract_mac_claims(fragment)
        assert all(c.relationship is None for c in claims)
        for claim in claims:
            if claim.financial:
                assert claim.financial.semantic_role != "contract_value"

    def test_no_for_action_marker_and_no_matching_phrases_yields_empty_tuple(self):
        fragment = _fragment("This is a routine informational update with no procurement content at all.")
        assert extract_mac_claims(fragment) == ()

    def test_cip_approval_fact_still_emitted_when_amount_not_in_money_values(self):
        # Checkpoint-review correction: claim F must not silently vanish
        # just because its dollar figure fails to resolve to a
        # semantic_role - the historical approval fact is still true.
        # financial=None is the correct, honest representation (matches
        # claim D's already-established financial=None-is-acceptable
        # shape), never the whole claim's disappearance.
        fragment = _fragment(
            "On December 18, 2023, the Commission approved the 2024-2030 CIP listing which included the "
            "work associated with the 2025 30L EMAS Replacement in the amount of $19,000,000.00.",
            money_values=(),  # deliberately empty - amount cannot resolve
            dates=(ExtractedDate(raw_text="December 18, 2023", normalized_date=date(2023, 12, 18), semantic_role="prior_approval_date"),),
        )
        claims = extract_mac_claims(fragment)
        assert len(claims) == 1
        assert claims[0].category == ClaimCategory.EXPLICIT_DOCUMENT_FACT
        assert claims[0].financial is None
        assert claims[0].temporal.qualifier == TemporalQualifier.HISTORICAL_FACT


# --- Provenance ---

class TestProvenance:
    def test_all_claims_share_identical_fragment_identity(self):
        fragment = _real_fragment()
        claims = extract_mac_claims(fragment)
        assert claims
        for claim in claims:
            assert claim.provenance.artifact_identity == fragment.artifact_identity
            assert claim.provenance.source_locator == fragment.source_locator
            assert claim.provenance.fragment_hash == fragment.fragment_hash

    def test_raw_text_excerpt_is_not_full_raw_text(self):
        fragment = _real_fragment()
        claims = extract_mac_claims(fragment)
        for claim in claims:
            assert claim.provenance.raw_text_excerpt != fragment.raw_text
            assert len(claim.provenance.raw_text_excerpt) < len(fragment.raw_text)

    def test_excerpt_is_substring_of_raw_text(self):
        fragment = _real_fragment()
        claims = extract_mac_claims(fragment)
        for claim in claims:
            assert claim.provenance.raw_text_excerpt in fragment.raw_text


# --- Temporal safety: current system date must have zero effect ---

class TestTemporalSafetyAgainstCurrentDate:
    def test_planned_future_claim_unaffected_by_current_year_2026(self):
        # Today (per system context) is 2026-08-19 - well past the memo's
        # "planned 2025" language. The claim's meaning must not shift.
        claims = extract_mac_claims(_real_fragment())
        temporal_claims = [c for c in claims if c.category == ClaimCategory.TEMPORAL_STATEMENT]
        assert len(temporal_claims) == 1
        claim = temporal_claims[0]
        assert claim.temporal.qualifier == TemporalQualifier.PLANNED_FUTURE_ACTION
        assert claim.temporal.as_of_date == date(2024, 8, 28)

    def test_no_date_today_or_now_call_anywhere_in_module(self):
        source = inspect.getsource(claims_module)
        tree = ast.parse(source)
        forbidden_calls = {"today", "now", "utcnow"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_calls, f"forbidden call: {node.func.attr}"


# --- Purity / import boundary ---

class TestPurity:
    def test_no_forbidden_imports(self):
        source = inspect.getsource(claims_module)
        tree = ast.parse(source)
        forbidden_modules = {"sqlalchemy", "httpx", "requests", "app.database", "app.models"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(alias.name == m or alias.name.startswith(m + ".") for m in forbidden_modules)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not any(module == m or module.startswith(m + ".") for m in forbidden_modules)

    def test_deterministic_same_fragment_same_result(self):
        fragment = _real_fragment()
        first = extract_mac_claims(fragment)
        second = extract_mac_claims(fragment)
        assert first == second


# --- 10 named adversarial cases (task S15) ---

class TestAdversarialCases:
    def test_case_1_amount_with_no_context_label_no_financial_fabrication(self):
        fragment = _fragment(
            "Authorize staff to issue a Purchase Order to Acme Corp in the amount of $2,000,000.00 for parts.",
            money_values=(ExtractedMoney(raw_text="$2,000,000.00", numeric_value=Decimal("2000000.00"), currency="USD", context_label=None),),
        )
        claims = extract_mac_claims(fragment)
        assert all(c.financial is None for c in claims)

    def test_case_2_sfo_40m_no_contract_value_semantic_collapse(self):
        fragment = _fragment(
            "SFO is evaluating EMAS options. The FAA Airport Improvement Program has historically funded up "
            "to $40,000,000.00 for similar safety-area projects nationwide. Runway Safe is one of several "
            "vendors capable of providing EMAS materials.",
            money_values=(ExtractedMoney(raw_text="$40,000,000.00", numeric_value=Decimal("40000000.00"), currency="USD", context_label=None),),
        )
        claims = extract_mac_claims(fragment)
        assert all(c.financial is None for c in claims)
        assert all(c.relationship is None for c in claims)

    def test_case_3_no_for_action_marker_still_detects_explicit_facts(self):
        fragment = _fragment("The Runway 4-22 EMAS bed has reached its life expectancy and requires replacement.")
        claims = extract_mac_claims(fragment)
        categories = {c.category for c in claims}
        assert ClaimCategory.EXPLICIT_DOCUMENT_FACT in categories
        assert ClaimCategory.PROCEDURAL_REQUEST not in categories

    def test_case_4_vendor_named_without_sole_source_phrase_no_relationship(self):
        fragment = _fragment("Runway Safe previously supplied EMAS material for an unrelated airport project.")
        claims = extract_mac_claims(fragment)
        assert claims == ()

    def test_case_5_past_tense_already_approved_not_marked_pending(self):
        fragment = _fragment(
            "On December 18, 2023, the Commission approved the 2024-2030 CIP listing which included the "
            "work associated with the 2025 30L EMAS Replacement in the amount of $19,000,000.00.",
            money_values=(ExtractedMoney(raw_text="$19,000,000.00", numeric_value=Decimal("19000000.00"), currency="USD", context_label="cip_project_ceiling"),),
            dates=(ExtractedDate(raw_text="December 18, 2023", normalized_date=date(2023, 12, 18), semantic_role="prior_approval_date"),),
        )
        claims = extract_mac_claims(fragment)
        assert len(claims) == 1
        assert claims[0].temporal.qualifier == TemporalQualifier.HISTORICAL_FACT

    def test_case_6_installation_oversight_without_sole_source_stays_separate(self):
        fragment = _fragment(
            "A separate contract will be bid in 2026 for the installation of the blocks by a separate "
            "contractor but under the oversight of Runway Safe.",
        )
        claims = extract_mac_claims(fragment)
        oversight = [c for c in claims if c.relationship and c.relationship.role == "installation_oversight"]
        sole_source = [c for c in claims if c.relationship and c.relationship.role == "requested_sole_source_vendor"]
        assert len(oversight) == 1
        assert len(sole_source) == 0

    def test_case_7_two_different_vendors_two_different_amounts_no_cross_binding(self):
        fragment = _fragment(
            "Authorize staff to issue a Purchase Order to Acme Corp in the amount of $100,000.00 for signage. "
            "Authorize staff to issue a Purchase Order to Beta LLC in the amount of $250,000.00 for paint.",
            money_values=(
                ExtractedMoney(raw_text="$100,000.00", numeric_value=Decimal("100000.00"), currency="USD", context_label="advance_deposit"),
                ExtractedMoney(raw_text="$250,000.00", numeric_value=Decimal("250000.00"), currency="USD", context_label="advance_deposit"),
            ),
        )
        claims = extract_mac_claims(fragment)
        procedural = [c for c in claims if c.category == ClaimCategory.PROCEDURAL_REQUEST]
        assert len(procedural) == 1  # first PO-shaped match only; no fabricated cross-binding
        assert procedural[0].financial.amount in (Decimal("100000.00"), Decimal("250000.00"))

    def test_case_8_empty_money_values_but_po_phrase_present_no_financial_attached(self):
        fragment = _fragment(
            "Authorize staff to issue a Purchase Order to Acme Corp in the amount of $750,000.00 for parts.",
            money_values=(),
        )
        claims = extract_mac_claims(fragment)
        procedural = [c for c in claims if c.category == ClaimCategory.PROCEDURAL_REQUEST]
        assert len(procedural) == 1
        assert procedural[0].financial is None

    def test_case_9_generic_non_mac_boilerplate_yields_no_claims(self):
        fragment = _fragment("Minutes of the regular monthly meeting were approved by unanimous consent.")
        assert extract_mac_claims(fragment) == ()

    def test_case_10_lifecycle_and_replacement_phrases_alone_no_procedural_leak(self):
        fragment = _fragment("The Runway 9-27 lighting system has reached its life expectancy and requires replacement.")
        claims = extract_mac_claims(fragment)
        assert len(claims) == 2
        assert all(c.category == ClaimCategory.EXPLICIT_DOCUMENT_FACT for c in claims)
        assert all(c.financial is None and c.relationship is None for c in claims)


class TestFragmentReuse:
    def test_fixture_still_matches_committed_ground_truth(self):
        fragment = _real_fragment()
        assert len(fragment.fragment_hash) == 64
        assert fragment.runway_pairs == frozenset({"12R/30L"})
