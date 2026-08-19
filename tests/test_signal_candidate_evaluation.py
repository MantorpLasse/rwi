"""Tests for app.services.signal_candidate_evaluation
(docs/architecture/signal-candidate-core-slice3-report.md)."""
from __future__ import annotations

import ast
import inspect
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.acquisition.mac_granicus_claims import extract_mac_claims
from app.acquisition.mac_granicus_extractor import extract_candidate_fragment
from app.services import signal_candidate_evaluation as scm
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
from app.services.signal_candidate_evaluation import (
    SignalCandidateContext,
    SignalCandidateOutcome,
    evaluate_signal_candidate,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mac_granicus_emas_procurement_memo_sample.pdf"

CONFIRMED = SignalCandidateContext(identity_decision=AttachmentOutcome.ATTACH_CONFIRMED)


def _prov(subject: str = "x") -> ClaimProvenance:
    return ClaimProvenance(
        artifact_identity="test.artifact.1", source_locator="item-1", fragment_hash="a" * 64,
        raw_text_excerpt=subject,
    )


def _fact(subject: str, statement: str = "fact", **kwargs) -> Claim:
    return Claim(category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject=subject, statement=statement, provenance=_prov(subject), **kwargs)


def _request(subject: str, statement: str = "request", **kwargs) -> Claim:
    return Claim(category=ClaimCategory.PROCEDURAL_REQUEST, subject=subject, statement=statement, provenance=_prov(subject), **kwargs)


def _relationship_claim(subject: str, party: str, role: str, scope: str | None = None) -> Claim:
    return Claim(
        category=ClaimCategory.RELATIONSHIP, subject=subject, statement=f"{party} related as {role}",
        provenance=_prov(subject), relationship=RelationshipFact(party=party, role=role, scope=scope),
    )


def _temporal_claim(subject: str, qualifier: TemporalQualifier, as_of: date | None = None, detail: str | None = None) -> Claim:
    return Claim(
        category=ClaimCategory.TEMPORAL_STATEMENT, subject=subject, statement="temporal",
        provenance=_prov(subject), temporal=TemporalContext(qualifier=qualifier, as_of_date=as_of, detail=detail),
    )


def _real_msp_claims() -> tuple[Claim, ...]:
    pdf_bytes = FIXTURE_PATH.read_bytes()
    result = extract_candidate_fragment(
        pdf_bytes, "application/pdf", artifact_identity="mac.granicus.document.4.2349.105406", source_locator="item-2.3.2",
    )
    assert result is not None
    fragment, _vendors = result
    return extract_mac_claims(fragment)


# --- 1-5: MSP golden case ---

class TestMSPGoldenCase:
    def test_msp_review_required(self):
        decision = evaluate_signal_candidate(_real_msp_claims(), CONFIRMED)
        assert decision.outcome == SignalCandidateOutcome.REVIEW_REQUIRED

    def test_material_reasons_preserved(self):
        decision = evaluate_signal_candidate(_real_msp_claims(), CONFIRMED)
        assert "advance_deposit_purchase_order" in decision.reason
        assert "cip_project_ceiling" in decision.reason
        assert len(decision.material_claims) == 7

    def test_financial_roles_preserved_distinctly(self):
        decision = evaluate_signal_candidate(_real_msp_claims(), CONFIRMED)
        assert "1590000.00" in decision.reason or "1590000" in decision.reason
        assert "19000000.00" in decision.reason or "19000000" in decision.reason
        # "installation contract" (a legitimate reference to the type of
        # contract the memo discusses) is fine; a fabricated dollar-value
        # conclusion like "$19,000,000 contract" is not.
        assert "19000000 contract" not in decision.reason.lower()
        assert "1590000 contract" not in decision.reason.lower()

    def test_procedural_request_preserved(self):
        decision = evaluate_signal_candidate(_real_msp_claims(), CONFIRMED)
        assert "pending" in decision.reason.lower()
        # The reason template explicitly says "not approved/awarded/executed"
        # (a negation) - the bare, unnegated word must never appear.
        assert "not approved/awarded/executed" in decision.reason
        assert "was awarded" not in decision.reason.lower()
        assert "was approved" not in decision.reason.lower()

    def test_temporal_planned_state_preserved(self):
        decision = evaluate_signal_candidate(_real_msp_claims(), CONFIRMED)
        assert TemporalQualifier.PLANNED_FUTURE_ACTION.value in decision.reason


# --- 6-7: identity gate ---

class TestIdentityGate:
    def test_identity_not_confirmed(self):
        context = SignalCandidateContext(identity_decision=AttachmentOutcome.INSUFFICIENT_IDENTITY)
        decision = evaluate_signal_candidate((_fact("x"),), context)
        assert decision.outcome == SignalCandidateOutcome.IDENTITY_NOT_CONFIRMED
        assert decision.material_claims == ()

    def test_provisional_identity_fails_closed(self):
        context = SignalCandidateContext(identity_decision=AttachmentOutcome.ATTACH_PROVISIONAL)
        claims = (_fact("bed"), _relationship_claim("bed", "Acme", "sole_source_vendor"))
        decision = evaluate_signal_candidate(claims, context)
        assert decision.outcome == SignalCandidateOutcome.IDENTITY_NOT_CONFIRMED

    def test_reject_cross_airport_fails_closed(self):
        context = SignalCandidateContext(identity_decision=AttachmentOutcome.REJECT_CROSS_AIRPORT)
        decision = evaluate_signal_candidate((_fact("x"),), context)
        assert decision.outcome == SignalCandidateOutcome.IDENTITY_NOT_CONFIRMED


# --- 8: duplicate claims ---

class TestDuplicateHandling:
    def test_exact_duplicate_claims_do_not_double_count(self):
        claim = _fact("bed", financial=FinancialFact(amount=Decimal("1000"), currency="USD", semantic_role="advance_deposit"))
        decision = evaluate_signal_candidate((claim, claim, claim), CONFIRMED)
        assert decision.outcome == SignalCandidateOutcome.DUPLICATE_WITHIN_EVIDENCE
        assert len(decision.material_claims) == 1

    def test_duplicates_alongside_distinct_evidence_still_counted_once(self):
        claim = _fact("bed")
        other = _relationship_claim("bed", "Acme", "sole_source_vendor")
        decision = evaluate_signal_candidate((claim, claim, other), CONFIRMED)
        assert decision.outcome != SignalCandidateOutcome.DUPLICATE_WITHIN_EVIDENCE
        assert len(decision.material_claims) == 2


# --- 9: contradiction ---

class TestContradiction:
    def test_conflicting_parties_for_same_role_and_subject(self):
        claims = (
            _relationship_claim("EMAS bed", "Acme Corp", "sole_source_vendor"),
            _relationship_claim("EMAS bed", "Beta LLC", "sole_source_vendor"),
        )
        decision = evaluate_signal_candidate(claims, CONFIRMED)
        assert decision.outcome == SignalCandidateOutcome.CONTRADICTED
        assert "Acme Corp" in decision.reason and "Beta LLC" in decision.reason

    def test_material_claims_excludes_unrelated_relationship_claims(self):
        # Checkpoint-review correction: CONTRADICTED must surface only the
        # specific claims actually in conflict, not every relationship
        # claim in the whole evidence set.
        conflicting_a = _relationship_claim("EMAS bed", "Acme Corp", "sole_source_vendor")
        conflicting_b = _relationship_claim("EMAS bed", "Beta LLC", "sole_source_vendor")
        unrelated = _relationship_claim("Terminal roof", "Gamma Roofing", "installation_oversight")
        decision = evaluate_signal_candidate((conflicting_a, conflicting_b, unrelated), CONFIRMED)
        assert decision.outcome == SignalCandidateOutcome.CONTRADICTED
        assert set(decision.material_claims) == {conflicting_a, conflicting_b}
        assert unrelated not in decision.material_claims

    def test_same_party_different_subject_is_not_a_contradiction(self):
        claims = (
            _relationship_claim("Runway A", "Acme Corp", "sole_source_vendor"),
            _relationship_claim("Runway B", "Acme Corp", "sole_source_vendor"),
            _fact("Runway A"),
        )
        decision = evaluate_signal_candidate(claims, CONFIRMED)
        assert decision.outcome != SignalCandidateOutcome.CONTRADICTED


# --- 10: stale/superseded ---

class TestStaleOrSuperseded:
    def test_explicit_superseded_context_flag(self):
        context = SignalCandidateContext(
            identity_decision=AttachmentOutcome.ATTACH_CONFIRMED, superseded=True,
            superseded_reason="A later 2025 document confirms the project was cancelled.",
        )
        decision = evaluate_signal_candidate((_fact("bed"),), context)
        assert decision.outcome == SignalCandidateOutcome.STALE_OR_SUPERSEDED
        assert "cancelled" in decision.reason

    def test_old_date_alone_does_not_trigger_staleness(self):
        claims = (_fact("bed"), _temporal_claim("bed", TemporalQualifier.HISTORICAL_FACT, as_of=date(1999, 1, 1)))
        decision = evaluate_signal_candidate(claims, CONFIRMED)
        assert decision.outcome != SignalCandidateOutcome.STALE_OR_SUPERSEDED


# --- 11-12: low materiality ---

class TestLowMateriality:
    def test_airport_identity_only_is_no_claims(self):
        decision = evaluate_signal_candidate((), CONFIRMED)
        assert decision.outcome == SignalCandidateOutcome.INSUFFICIENT_MATERIALITY

    def test_generic_emas_mention_only(self):
        decision = evaluate_signal_candidate((_fact("EMAS bed", "The airport has an EMAS bed."),), CONFIRMED)
        assert decision.outcome == SignalCandidateOutcome.INSUFFICIENT_MATERIALITY

    def test_static_runway_inventory_fact(self):
        decision = evaluate_signal_candidate((_fact("Runway 12R/30L", "Runway 12R/30L is 3048m long."),), CONFIRMED)
        assert decision.outcome == SignalCandidateOutcome.INSUFFICIENT_MATERIALITY

    def test_generic_vendor_mention_with_no_event_claim(self):
        claims = (_relationship_claim("EMAS materials", "Acme Corp", "known_manufacturer"),)
        decision = evaluate_signal_candidate(claims, CONFIRMED)
        assert decision.outcome == SignalCandidateOutcome.INSUFFICIENT_MATERIALITY

    def test_old_non_material_descriptive_fact(self):
        claims = (_fact("terminal", "The terminal was renovated in 1998."),)
        decision = evaluate_signal_candidate(claims, CONFIRMED)
        assert decision.outcome == SignalCandidateOutcome.INSUFFICIENT_MATERIALITY


# --- 13-16: high materiality (generic, synthetic, not MSP-specific) ---

class TestHighMateriality:
    def test_explicit_new_replacement(self):
        claims = (
            _fact("EMAS bed, Runway 9-27", "The EMAS bed has reached its life expectancy."),
            _fact("EMAS bed, Runway 9-27", "EMAS replacement is required."),
        )
        decision = evaluate_signal_candidate(claims, CONFIRMED)
        assert decision.outcome == SignalCandidateOutcome.REVIEW_REQUIRED

    def test_explicit_contract_award(self):
        claims = (
            _fact(
                "EMAS contract", "The Authority awarded a contract to Acme Corp.",
                financial=FinancialFact(amount=Decimal("5000000"), currency="USD", semantic_role="contract_value"),
                relationship=RelationshipFact(party="Acme Corp", role="awarded_contractor"),
            ),
        )
        decision = evaluate_signal_candidate(claims, CONFIRMED)
        assert decision.outcome == SignalCandidateOutcome.REVIEW_REQUIRED

    def test_major_repair_after_overrun(self):
        claims = (
            _fact(
                "runway repair", "A major repair was required after a cost overrun.",
                financial=FinancialFact(amount=Decimal("2000000"), currency="USD", semantic_role="cost_overrun_amount"),
            ),
        )
        decision = evaluate_signal_candidate(claims, CONFIRMED)
        assert decision.outcome == SignalCandidateOutcome.REVIEW_REQUIRED

    def test_planned_new_installation(self):
        claims = (
            _fact("new EMAS installation", "A new EMAS installation is planned."),
            _temporal_claim("new EMAS installation", TemporalQualifier.PLANNED_FUTURE_ACTION, as_of=date(2024, 1, 1), detail="target 2026"),
        )
        decision = evaluate_signal_candidate(claims, CONFIRMED)
        assert decision.outcome == SignalCandidateOutcome.REVIEW_REQUIRED

    def test_vendor_backed_procurement(self):
        claims = (
            _request("advance-deposit PO", "Staff requests authority to issue a PO."),
            _relationship_claim("advance-deposit PO", "Acme Corp", "requested_sole_source_vendor"),
        )
        decision = evaluate_signal_candidate(claims, CONFIRMED)
        assert decision.outcome == SignalCandidateOutcome.REVIEW_REQUIRED

    def test_completion_status_change(self):
        claims = (
            _fact("EMAS project", "The EMAS replacement project is complete."),
            _temporal_claim("EMAS project", TemporalQualifier.COMPLETED, as_of=date(2025, 6, 1)),
        )
        decision = evaluate_signal_candidate(claims, CONFIRMED)
        assert decision.outcome == SignalCandidateOutcome.REVIEW_REQUIRED
        assert "no auto" not in decision.reason.lower()  # sanity: no auto-promotion language ever appears


# --- 17: SFO-$40M adversarial ---

class TestSFO40MAdversarial:
    def test_unlabeled_amount_and_weak_relationship_never_becomes_a_contract_claim(self):
        # The $40M has no resolvable semantic role - per the Slice 1/2
        # discipline this means it structurally CANNOT become a
        # FinancialFact at all (semantic_role is required, never
        # fabricated). So the adversarial claim set carries a bare
        # EMAS-context fact and a weak "mentioned" relationship, with NO
        # financial claim - exactly what a fail-closed extractor would
        # actually hand this evaluator.
        claims = (
            _fact("SFO EMAS context", "SFO is evaluating EMAS options."),
            _relationship_claim("SFO EMAS context", "Runway Safe", "mentioned_in_document"),
        )
        decision = evaluate_signal_candidate(claims, CONFIRMED)
        # REVIEW_REQUIRED is an allowed outcome here (task S18) - the
        # safety property is that the amount never appears at all (it
        # cannot be represented as a FinancialFact without a semantic
        # role) and the reason never states a contract/award conclusion.
        assert decision.outcome in (SignalCandidateOutcome.REVIEW_REQUIRED, SignalCandidateOutcome.INSUFFICIENT_MATERIALITY)
        assert "$40" not in decision.reason
        assert "40,000,000" not in decision.reason
        assert "contract" not in decision.reason.lower()
        assert "awarded" not in decision.reason.lower()
        assert all(claim.financial is None for claim in decision.material_claims)
        if "Runway Safe" in decision.reason:
            assert "mentioned_in_document" in decision.reason

    def test_category_present_but_no_relationship_binding_amount_to_vendor(self):
        claims = (
            _fact("SFO EMAS replacement", "SFO EMAS bed requires replacement."),
        )
        decision = evaluate_signal_candidate(claims, CONFIRMED)
        assert "Runway Safe" not in decision.reason
        assert "$40" not in decision.reason
        assert decision.outcome == SignalCandidateOutcome.INSUFFICIENT_MATERIALITY

    def test_no_financial_fact_can_be_constructed_without_a_semantic_role(self):
        # Structural proof, not just a behavioral one: FinancialFact
        # requires semantic_role with no default - an "unlabeled $40M"
        # cannot be expressed as a FinancialFact at all.
        import inspect as _inspect
        sig = _inspect.signature(FinancialFact)
        assert sig.parameters["semantic_role"].default is _inspect.Parameter.empty


# --- 18: international / non-USD ---

class TestInternationalReadiness:
    def test_non_us_non_usd_claim_set_reaches_review_required_identically(self):
        claims = (
            _fact("Haneda RESA, Runway 05", "The RESA bed has reached its service life."),
            _request(
                "material procurement",
                "The Authority requests approval to procure replacement material.",
                financial=FinancialFact(amount=Decimal("450000000"), currency="JPY", semantic_role="advance_deposit"),
            ),
            _relationship_claim("material procurement", "Taiyo Safety Materials KK", "requested_sole_source_vendor"),
        )
        decision = evaluate_signal_candidate(claims, CONFIRMED)
        assert decision.outcome == SignalCandidateOutcome.REVIEW_REQUIRED
        assert "JPY" in decision.reason
        assert "advance_deposit" in decision.reason


# --- 19: deterministic reason ---

class TestDeterminism:
    def test_same_input_produces_identical_decision(self):
        claims = _real_msp_claims()
        first = evaluate_signal_candidate(claims, CONFIRMED)
        second = evaluate_signal_candidate(claims, CONFIRMED)
        assert first == second


# --- 20: purity / import boundary ---

class TestPurity:
    def test_no_forbidden_imports(self):
        source = inspect.getsource(scm)
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
        source = inspect.getsource(scm)
        assert "import Signal" not in source
        assert "models.signal" not in source


# --- 21: no current-time dependency ---

class TestNoCurrentTimeDependency:
    def test_no_today_now_utcnow_call_anywhere(self):
        source = inspect.getsource(scm)
        tree = ast.parse(source)
        forbidden_calls = {"today", "now", "utcnow"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_calls, f"forbidden call: {node.func.attr}"

    def test_planned_future_claim_unaffected_by_real_system_date(self):
        # System date at time of writing is 2026-08-19 - well past the
        # claim's own 2024/2025 dates. Outcome must not change.
        decision = evaluate_signal_candidate(_real_msp_claims(), CONFIRMED)
        assert decision.outcome == SignalCandidateOutcome.REVIEW_REQUIRED
