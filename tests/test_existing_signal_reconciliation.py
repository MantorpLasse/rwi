"""Tests for app.services.existing_signal_reconciliation
(docs/architecture/existing-signal-reconciliation-r1-core-report.md, R1 of
docs/architecture/existing-signal-reconciliation-guard-design.md's own S16
roadmap)."""
from __future__ import annotations

import ast
import dataclasses
import inspect
from datetime import date

import pytest

from app.services import existing_signal_reconciliation as esr
from app.services.existing_signal_reconciliation import (
    ExistingSignalReconciliationDecision,
    ExistingSignalReconciliationOutcome,
    ExistingSignalReconciliationSubject,
    ReconciliationCandidateSignal,
    evaluate_existing_signal_reconciliation,
)

CLEAR = ExistingSignalReconciliationOutcome.CLEAR_TO_CREATE
POSSIBLE = ExistingSignalReconciliationOutcome.POSSIBLE_EXISTING_SIGNAL_MATCH
LINKED = ExistingSignalReconciliationOutcome.ALREADY_LINKED


def subject(**kwargs) -> ExistingSignalReconciliationSubject:
    return ExistingSignalReconciliationSubject(**kwargs)


def candidate(signal_id: int, **kwargs) -> ReconciliationCandidateSignal:
    return ReconciliationCandidateSignal(signal_id=signal_id, **kwargs)


# --- 1. ALREADY_LINKED short-circuit ---

class TestAlreadyLinkedShortCircuit:
    def test_existing_signal_id_short_circuits_before_candidates(self):
        s = subject(existing_signal_id=67, airport_id=999)
        # deliberately empty candidate list AND a candidate list that would
        # otherwise anchor-match a totally different signal - must be ignored
        decision = evaluate_existing_signal_reconciliation(s, ())
        assert decision.outcome == LINKED
        assert decision.signal_id == 67
        assert decision.candidate_signal_ids == ()
        assert decision.advisory_candidate_signal_ids == ()

    def test_candidate_list_is_irrelevant_once_linked(self):
        s = subject(existing_signal_id=67, airport_id=45, runway_id=1, category="replacement")
        c = candidate(999, airport_id=45, runway_id=1, category="replacement")
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == LINKED
        assert decision.signal_id == 67
        assert decision.candidate_signal_ids == ()

    def test_already_linked_idempotent_across_repeated_calls(self):
        s = subject(existing_signal_id=41)
        first = evaluate_existing_signal_reconciliation(s, ())
        second = evaluate_existing_signal_reconciliation(s, ())
        assert first == second == ExistingSignalReconciliationDecision(outcome=LINKED, signal_id=41)


# --- 2. Bare CLEAR_TO_CREATE (no candidates, no compatibility) ---

class TestBareClearToCreate:
    def test_no_candidates_at_all(self):
        decision = evaluate_existing_signal_reconciliation(subject(airport_id=1), ())
        assert decision.outcome == CLEAR
        assert decision.advisory_candidate_signal_ids == ()
        assert decision.advisory_reasons == ()

    def test_airport_only_match_produces_no_advisory_metadata(self):
        # design doc S9 case G: airport alone is a near-universal
        # precondition, never a distinguishing signal - must not itself
        # produce an advisory line.
        s = subject(airport_id=1)
        c = candidate(5, airport_id=1)
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR
        assert decision.advisory_candidate_signal_ids == ()
        assert decision.advisory_reasons == ()


# --- 3. Compatibility evidence never blocks, however much accumulates ---

class TestCompatibilityNeverBlocks:
    def test_ten_compatibility_signals_across_candidates_still_clear_to_create(self):
        s = subject(
            airport_id=1, category="replacement", vendor_names=("Runway Safe", "Acme EMAS"),
            evidence_date=date(2024, 1, 1), reference_year=2024,
        )
        candidates = tuple(
            candidate(
                i, airport_id=1, category="replacement", confirmed_vendor="Runway Safe",
                likely_supplier="Acme EMAS", evidence_date=date(2025, 1, 1), reference_year=2024,
            )
            for i in range(1, 11)
        )
        decision = evaluate_existing_signal_reconciliation(s, candidates)
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()
        assert set(decision.advisory_candidate_signal_ids) == set(range(1, 11))
        # every reason present is compatibility-tier, never an anchor tag
        assert all("identity_anchor" not in r for r in decision.advisory_reasons)

    def test_single_candidate_with_every_compatibility_axis_agreeing_still_not_blocked(self):
        s = subject(
            airport_id=1, category="replacement", vendor_names=("Runway Safe",),
            evidence_date=date(2024, 1, 1), reference_year=2024,
        )
        c = candidate(
            1, airport_id=1, category="replacement", confirmed_vendor="Runway Safe",
            evidence_date=date(2025, 1, 1), reference_year=2024,
        )
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()
        assert decision.advisory_candidate_signal_ids == (1,)
        reasons = decision.advisory_reasons
        assert any("compatibility:category" in r for r in reasons)
        assert any("compatibility:vendor" in r for r in reasons)
        assert any("compatibility:temporal" in r for r in reasons)
        assert any("compatibility:year" in r for r in reasons)


# --- 4. Runway-id identity anchor ---

class TestRunwayIdAnchor:
    def test_shared_canonical_runway_id_is_anchor(self):
        s = subject(airport_id=1, runway_id=123, category="replacement")
        c = candidate(10, airport_id=1, runway_id=123, category="replacement")
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == POSSIBLE
        assert decision.candidate_signal_ids == (10,)
        assert any("identity_anchor:runway_id" in r for r in decision.reasons)

    def test_same_runway_id_but_conflicting_airport_never_qualifies(self):
        s = subject(airport_id=1, runway_id=123)
        c = candidate(10, airport_id=2, runway_id=123)
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()
        assert decision.advisory_candidate_signal_ids == ()

    def test_null_runway_id_on_either_side_is_not_an_anchor(self):
        s = subject(airport_id=1, runway_id=None, category="replacement")
        c = candidate(10, airport_id=1, runway_id=999, category="replacement")
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()
        # category compatibility still surfaces (runway on only one side is
        # neither confirming nor disconfirming)
        assert decision.advisory_candidate_signal_ids == (10,)


# --- 5. Physical-installation identity anchor ---

class TestPhysicalInstallationAnchor:
    def test_shared_physical_installation_id_is_anchor(self):
        s = subject(airport_id=1, physical_installation_ids=(77,))
        c = candidate(10, airport_id=1, physical_installation_ids=(77,))
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == POSSIBLE
        assert decision.candidate_signal_ids == (10,)
        assert any("identity_anchor:physical_installation_identity" in r for r in decision.reasons)
        assert any("physical_installation_id=77" in r for r in decision.reasons)

    def test_different_physical_installation_id_does_not_qualify_despite_other_compatibility(self):
        s = subject(airport_id=1, physical_installation_ids=(77,), category="replacement", vendor_names=("Runway Safe",))
        c = candidate(10, airport_id=1, physical_installation_ids=(88,), category="replacement", confirmed_vendor="Runway Safe")
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()
        # vendor/category compatibility still surfaces as advisory only
        assert decision.advisory_candidate_signal_ids == (10,)
        assert all("identity_anchor" not in r for r in decision.advisory_reasons)


# --- 6. Provenance (source document) identity anchor ---

class TestProvenanceAnchor:
    def test_shared_source_id_already_backing_candidate_is_anchor(self):
        s = subject(airport_id=1, source_id=555)
        c = candidate(10, airport_id=1, supporting_source_ids=(555,))
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == POSSIBLE
        assert decision.candidate_signal_ids == (10,)
        assert any("identity_anchor:provenance" in r and "source_id=555" in r for r in decision.reasons)

    def test_shared_artifact_identity_already_backing_candidate_is_anchor(self):
        s = subject(airport_id=1, artifact_identity="acquisition.doc.4.999.1")
        c = candidate(10, airport_id=1, supporting_artifact_identities=("acquisition.doc.4.999.1",))
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == POSSIBLE
        assert any("identity_anchor:provenance" in r and "artifact_identity" in r for r in decision.reasons)

    def test_different_source_id_is_not_an_anchor_even_from_same_notional_publisher(self):
        # no "publisher" concept exists in this module at all - a different
        # source_id is simply a different document, never treated as related.
        s = subject(airport_id=1, source_id=555)
        c = candidate(10, airport_id=1, supporting_source_ids=(556,))
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()


# --- 7. Disconfirming evidence ---

class TestDisconfirmingEvidence:
    def test_populated_differing_runway_id_excludes_candidate_entirely(self):
        s = subject(airport_id=1, runway_id=1, category="replacement", vendor_names=("Runway Safe",))
        c = candidate(10, airport_id=1, runway_id=2, category="replacement", confirmed_vendor="Runway Safe")
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()
        # design doc S9-A: disconfirming runway conflict suppresses the
        # advisory note too, not just the block
        assert decision.advisory_candidate_signal_ids == ()
        assert decision.advisory_reasons == ()

    def test_populated_differing_airport_excludes_candidate_entirely(self):
        s = subject(airport_id=1, category="replacement", vendor_names=("Runway Safe",))
        c = candidate(10, airport_id=2, category="replacement", confirmed_vendor="Runway Safe")
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()
        assert decision.advisory_candidate_signal_ids == ()

    def test_disconfirming_evidence_never_promotes_to_anchor(self):
        # a candidate disqualified by runway conflict must not somehow
        # "count" toward an anchor via any other axis in the same call.
        s = subject(airport_id=1, runway_id=1, physical_installation_ids=(77,))
        c = candidate(10, airport_id=1, runway_id=2, physical_installation_ids=(77,))
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()


# --- 8. Multiple anchor-backed candidates: fail closed ---

class TestMultipleAnchorBackedCandidates:
    def test_two_independently_anchored_candidates_both_returned(self):
        s = subject(airport_id=1, runway_id=5, physical_installation_ids=(99,))
        c_runway = candidate(20, airport_id=1, runway_id=5)
        c_installation = candidate(10, airport_id=1, physical_installation_ids=(99,))
        decision = evaluate_existing_signal_reconciliation(s, (c_runway, c_installation))
        assert decision.outcome == POSSIBLE
        # sorted ascending - never ranked, never "first", never "newest"
        assert decision.candidate_signal_ids == (10, 20)

    def test_three_anchor_backed_candidates_none_dropped(self):
        s = subject(airport_id=1, source_id=1, physical_installation_ids=(2,), runway_id=3)
        candidates = (
            candidate(30, airport_id=1, runway_id=3),
            candidate(10, airport_id=1, supporting_source_ids=(1,)),
            candidate(20, airport_id=1, physical_installation_ids=(2,)),
        )
        decision = evaluate_existing_signal_reconciliation(s, candidates)
        assert decision.outcome == POSSIBLE
        assert decision.candidate_signal_ids == (10, 20, 30)

    def test_ordering_is_stable_regardless_of_input_order(self):
        s = subject(airport_id=1, runway_id=5)
        forward = (candidate(1, airport_id=1, runway_id=5), candidate(2, airport_id=1, runway_id=5))
        backward = tuple(reversed(forward))
        assert evaluate_existing_signal_reconciliation(s, forward) == evaluate_existing_signal_reconciliation(s, backward)

    def test_duplicate_signal_id_rows_with_different_anchors_are_merged_not_overwritten(self):
        # Regression test (found during the R1 review checkpoint): two
        # candidate rows sharing the same signal_id but each carrying a
        # DIFFERENT anchor axis must have their reasons merged, never have
        # one silently dropped depending on which row happens to be
        # evaluated last.
        s = subject(airport_id=1, runway_id=5, physical_installation_ids=(9,))
        c_via_runway = candidate(10, airport_id=1, runway_id=5)
        c_via_installation = candidate(10, airport_id=1, physical_installation_ids=(9,))
        forward = evaluate_existing_signal_reconciliation(s, (c_via_runway, c_via_installation))
        backward = evaluate_existing_signal_reconciliation(s, (c_via_installation, c_via_runway))
        assert forward == backward
        assert forward.outcome == POSSIBLE
        assert forward.candidate_signal_ids == (10,)
        assert any("identity_anchor:runway_id" in r for r in forward.reasons)
        assert any("identity_anchor:physical_installation_identity" in r for r in forward.reasons)

    def test_duplicate_signal_id_rows_with_different_compatibility_axes_are_merged(self):
        s = subject(airport_id=1, category="replacement", vendor_names=("Runway Safe",))
        c_via_category = candidate(10, airport_id=1, category="replacement")
        c_via_vendor = candidate(10, airport_id=1, confirmed_vendor="Runway Safe")
        forward = evaluate_existing_signal_reconciliation(s, (c_via_category, c_via_vendor))
        backward = evaluate_existing_signal_reconciliation(s, (c_via_vendor, c_via_category))
        assert forward == backward
        assert forward.outcome == CLEAR
        assert forward.advisory_candidate_signal_ids == (10,)
        assert any("compatibility:category" in r for r in forward.advisory_reasons)
        assert any("compatibility:vendor" in r for r in forward.advisory_reasons)


# --- 9. Compatibility split across candidates never pooled into a match ---

class TestCompatibilityNeverPooledAcrossCandidates:
    def test_two_candidates_each_weak_alone_stay_separately_weak(self):
        s = subject(airport_id=1, category="replacement", vendor_names=("Runway Safe",))
        c_category_only = candidate(1, airport_id=1, category="replacement")
        c_vendor_only = candidate(2, airport_id=1, confirmed_vendor="Runway Safe")
        decision = evaluate_existing_signal_reconciliation(s, (c_category_only, c_vendor_only))
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()
        assert set(decision.advisory_candidate_signal_ids) == {1, 2}

    def test_evidence_for_one_candidate_never_leaks_into_anothers_reasons(self):
        s = subject(airport_id=1, category="replacement", vendor_names=("Runway Safe",))
        c1 = candidate(1, airport_id=1, category="replacement")
        c2 = candidate(2, airport_id=1, confirmed_vendor="Runway Safe")
        decision = evaluate_existing_signal_reconciliation(s, (c1, c2))
        reasons_for_1 = [r for r in decision.advisory_reasons if r.startswith("signal 1:")]
        reasons_for_2 = [r for r in decision.advisory_reasons if r.startswith("signal 2:")]
        assert any("compatibility:category" in r for r in reasons_for_1)
        assert all("compatibility:vendor" not in r for r in reasons_for_1)
        assert any("compatibility:vendor" in r for r in reasons_for_2)
        assert all("compatibility:category" not in r for r in reasons_for_2)


# --- 10. MSP #222 golden case: pre- and post-resolution ---

class TestMSPGoldenCase:
    """Synthetic, DB-free fixtures faithfully reconstructing the STRUCTURED
    pre-resolution shapes discovered in the reviewed design checkpoint
    (existing-signal-reconciliation-guard-design.md S8) - not live DB rows.
    """

    def _pre_resolution_subject(self) -> ExistingSignalReconciliationSubject:
        return ExistingSignalReconciliationSubject(
            airport_id=45,
            runway_id=None,  # #222's own runway_id was null; only free-text '12R/30L'
            source_id=70,
            category="replacement",
            vendor_names=("Runway Safe",),  # requested, not-yet-confirmed sole-source claim
            evidence_date=date(2024, 8, 1),
        )

    def _signal_67(self) -> ReconciliationCandidateSignal:
        return ReconciliationCandidateSignal(
            signal_id=67, airport_id=45, runway_id=None, category="replacement",
            confirmed_vendor="Runway Safe", supporting_source_ids=(45,),
            evidence_date=date(2025, 5, 1),
        )

    def _signal_41(self) -> ReconciliationCandidateSignal:
        # USAspending grant for the OPPOSITE runway end (12R) of the same
        # 12R/30L pair - the distinguishing fact lives only in raw free text
        # ("AT THE RUNWAY 12R END"), which this module never sees.
        return ReconciliationCandidateSignal(
            signal_id=41, airport_id=45, runway_id=None, category="replacement",
            supporting_source_ids=(15,), reference_year=2025,
        )

    def test_pre_resolution_is_clear_to_create_not_a_block(self):
        decision = evaluate_existing_signal_reconciliation(
            self._pre_resolution_subject(), (self._signal_67(), self._signal_41()),
        )
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()
        assert decision.reasons == ()

    def test_pre_resolution_advisory_names_both_candidates(self):
        decision = evaluate_existing_signal_reconciliation(
            self._pre_resolution_subject(), (self._signal_67(), self._signal_41()),
        )
        assert decision.advisory_candidate_signal_ids == (41, 67)

    def test_pre_resolution_signal_67_has_three_independent_compatibility_axes(self):
        decision = evaluate_existing_signal_reconciliation(
            self._pre_resolution_subject(), (self._signal_67(), self._signal_41()),
        )
        reasons_67 = [r for r in decision.advisory_reasons if r.startswith("signal 67:")]
        assert any("compatibility:category" in r for r in reasons_67)
        assert any("compatibility:vendor" in r for r in reasons_67)
        assert any("compatibility:temporal" in r for r in reasons_67)

    def test_pre_resolution_signal_41_has_only_weak_compatibility(self):
        decision = evaluate_existing_signal_reconciliation(
            self._pre_resolution_subject(), (self._signal_67(), self._signal_41()),
        )
        reasons_41 = [r for r in decision.advisory_reasons if r.startswith("signal 41:")]
        assert any("compatibility:category" in r for r in reasons_41)
        assert all("compatibility:vendor" not in r for r in reasons_41)

    def test_no_anchor_axis_fires_for_either_candidate(self):
        decision = evaluate_existing_signal_reconciliation(
            self._pre_resolution_subject(), (self._signal_67(), self._signal_41()),
        )
        assert all("identity_anchor" not in r for r in decision.advisory_reasons)

    def test_post_resolution_already_linked(self):
        post_resolution_subject = dataclasses.replace(self._pre_resolution_subject(), existing_signal_id=67)
        decision = evaluate_existing_signal_reconciliation(
            post_resolution_subject, (self._signal_67(), self._signal_41()),
        )
        assert decision.outcome == LINKED
        assert decision.signal_id == 67
        assert decision.candidate_signal_ids == ()
        assert decision.advisory_candidate_signal_ids == ()


# --- 11. Synthetic anchor-bearing positive case (proves the block is reachable) ---

class TestSyntheticAnchorPositiveCase:
    def test_runway_id_anchor_reaches_possible_match(self):
        s = subject(airport_id=200, runway_id=555, category="replacement", vendor_names=("Acme EMAS",))
        c = candidate(300, airport_id=200, runway_id=555, category="replacement", confirmed_vendor="Acme EMAS")
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == POSSIBLE
        assert decision.candidate_signal_ids == (300,)

    def test_same_runway_id_conflicting_airport_invalidates(self):
        s = subject(airport_id=200, runway_id=555)
        c = candidate(300, airport_id=201, runway_id=555)
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()


# --- 12. Adversarial cases A-K (design doc S9 / R1 task S18) ---

class TestAdversarialCases:
    def test_case_a_same_airport_category_vendor_different_runway_advisory_only(self):
        s = subject(airport_id=1, category="replacement", vendor_names=("Runway Safe",))
        c = candidate(10, airport_id=1, category="replacement", confirmed_vendor="Runway Safe")
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()
        assert decision.advisory_candidate_signal_ids == (10,)

    def test_case_b_same_airport_vendor_temporal_unrelated_project_advisory_only(self):
        s = subject(airport_id=1, vendor_names=("Runway Safe",), evidence_date=date(2024, 1, 1))
        c = candidate(10, airport_id=1, confirmed_vendor="Runway Safe", evidence_date=date(2025, 1, 1))
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()
        assert decision.advisory_candidate_signal_ids == (10,)

    def test_case_c_same_airport_category_year_different_runway_ends_clear(self):
        s = subject(airport_id=1, category="replacement", reference_year=2025, runway_id=None)
        c = candidate(10, airport_id=1, category="replacement", reference_year=2025, runway_id=None)
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()

    def test_case_d_busy_airport_multiple_same_vendor_projects_never_blocks(self):
        s = subject(airport_id=1, category="replacement", vendor_names=("Runway Safe",), reference_year=2025)
        candidates = tuple(
            candidate(i, airport_id=1, category="replacement", confirmed_vendor="Runway Safe", reference_year=2025)
            for i in range(1, 6)
        )
        decision = evaluate_existing_signal_reconciliation(s, candidates)
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()
        assert set(decision.advisory_candidate_signal_ids) == {1, 2, 3, 4, 5}

    def test_case_e_financial_fields_structurally_impossible_to_supply(self):
        # No financial/dollar field exists anywhere on either dataclass -
        # attempting to pass one raises TypeError, not merely "ignored".
        with pytest.raises(TypeError):
            ExistingSignalReconciliationSubject(estimated_total_value_usd=19_000_000)  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            ReconciliationCandidateSignal(signal_id=1, estimated_total_value_usd=19_000_000)  # type: ignore[call-arg]

    def test_case_f_planning_evidence_no_structural_anchor_advisory_only(self):
        s = subject(airport_id=1, category="replacement")
        c = candidate(10, airport_id=1, category="replacement")
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()
        assert decision.advisory_candidate_signal_ids == (10,)

    def test_case_g_ambiguous_amount_coincidence_cannot_influence_result(self):
        # Same structural point as case E: amounts cannot be represented at
        # all, so two independently-true dollar figures that happen to share
        # a numeral have literally no code path into this module's decision.
        # (Field-level and literal-figure checks live in TestFinancialFirewall;
        # this asserts the behavioral consequence: nothing here can even
        # attempt to compare two candidates by a shared numeral.)
        s = subject(airport_id=1, category="replacement")
        c = candidate(10, airport_id=1, category="replacement")
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()
        assert not any("19000000" in r or "40000000" in r for r in decision.advisory_reasons)

    def test_case_h_international_non_usd_identical_structural_path(self):
        s = subject(airport_id=900, runway_id=42, category="replacement", vendor_names=("Taiyo Safety Materials KK",))
        c = candidate(901, airport_id=900, runway_id=42, category="replacement", confirmed_vendor="Taiyo Safety Materials KK")
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == POSSIBLE
        assert decision.candidate_signal_ids == (901,)

    def test_case_i_multiple_anchor_backed_candidates_all_ids_returned(self):
        s = subject(airport_id=1, runway_id=5, physical_installation_ids=(9,))
        candidates = (candidate(10, airport_id=1, runway_id=5), candidate(20, airport_id=1, physical_installation_ids=(9,)))
        decision = evaluate_existing_signal_reconciliation(s, candidates)
        assert decision.outcome == POSSIBLE
        assert decision.candidate_signal_ids == (10, 20)

    def test_case_j_compatibility_split_across_candidates_never_pooled(self):
        s = subject(airport_id=1, category="replacement", vendor_names=("Runway Safe",))
        candidates = (candidate(1, airport_id=1, category="replacement"), candidate(2, airport_id=1, confirmed_vendor="Runway Safe"))
        decision = evaluate_existing_signal_reconciliation(s, candidates)
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()

    def test_case_k_disconfirming_evidence_cannot_promote_to_anchor(self):
        s = subject(airport_id=1, runway_id=1, category="replacement", vendor_names=("Runway Safe",))
        c = candidate(10, airport_id=1, runway_id=2, category="replacement", confirmed_vendor="Runway Safe")
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR
        assert decision.candidate_signal_ids == ()
        assert decision.advisory_candidate_signal_ids == ()

    def test_empty_and_whitespace_only_optional_fields_never_produce_spurious_compatibility(self):
        s = subject(airport_id=1, category="", vendor_names=("   ", ""))
        c = candidate(10, airport_id=1, category="", confirmed_vendor="   ")
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR
        assert decision.advisory_candidate_signal_ids == ()
        assert decision.advisory_reasons == ()

    def test_four_candidate_permutations_all_produce_identical_decision(self):
        import itertools
        s = subject(airport_id=1, category="replacement", vendor_names=("Runway Safe",))
        candidates = (
            candidate(1, airport_id=1, category="replacement"),
            candidate(2, airport_id=1, confirmed_vendor="Runway Safe"),
            candidate(3, airport_id=1, category="replacement", confirmed_vendor="Runway Safe"),
            candidate(4, airport_id=2, category="replacement"),  # disconfirmed - different airport
        )
        decisions = {
            evaluate_existing_signal_reconciliation(s, perm)
            for perm in itertools.permutations(candidates)
        }
        assert len(decisions) == 1
        only_decision = decisions.pop()
        assert only_decision.candidate_signal_ids == ()
        assert set(only_decision.advisory_candidate_signal_ids) == {1, 2, 3}


# --- 13. Financial firewall ---

class TestFinancialFirewall:
    _FORBIDDEN_TOKENS = ("usd", "amount", "value", "cost", "price", "financial", "dollar", "money")

    def test_no_money_field_on_subject(self):
        names = {f.name.lower() for f in dataclasses.fields(ExistingSignalReconciliationSubject)}
        assert not any(token in name for name in names for token in self._FORBIDDEN_TOKENS)

    def test_no_money_field_on_candidate(self):
        names = {f.name.lower() for f in dataclasses.fields(ReconciliationCandidateSignal)}
        assert not any(token in name for name in names for token in self._FORBIDDEN_TOKENS)

    def test_no_money_field_on_decision(self):
        names = {f.name.lower() for f in dataclasses.fields(ExistingSignalReconciliationDecision)}
        assert not any(token in name for name in names for token in self._FORBIDDEN_TOKENS)

    def test_module_source_never_mentions_dollar_figures(self):
        source = inspect.getsource(esr)
        assert "$" not in source
        assert "19,000,000" not in source
        assert "19000000" not in source


# --- 14. Title / raw-text firewall ---

class TestTitleRawTextFirewall:
    _FORBIDDEN_TOKENS = ("title", "raw_text", "raw_relevant", "notes", "similarity", "keyword", "embedding", "fuzzy", "nlp", "llm")

    def test_no_raw_text_field_on_subject(self):
        names = {f.name.lower() for f in dataclasses.fields(ExistingSignalReconciliationSubject)}
        assert not any(token in name for name in names for token in self._FORBIDDEN_TOKENS)

    def test_no_raw_text_field_on_candidate(self):
        names = {f.name.lower() for f in dataclasses.fields(ReconciliationCandidateSignal)}
        assert not any(token in name for name in names for token in self._FORBIDDEN_TOKENS)

    def test_cannot_construct_subject_with_title(self):
        with pytest.raises(TypeError):
            ExistingSignalReconciliationSubject(title="MSP EMAS order")  # type: ignore[call-arg]

    def test_module_source_has_no_fuzzy_or_nlp_machinery(self):
        source = inspect.getsource(esr)
        lowered = source.lower()
        for token in ("difflib", "levenshtein", "fuzzywuzzy", "embedding", "cosine", "similarity", "nlp", " llm", "openai", "anthropic"):
            assert token not in lowered, f"forbidden token found: {token!r}"


# --- 15. Source-family / provider firewall ---

class TestSourceFamilyProviderFirewall:
    def test_no_provider_or_airport_specific_names_in_production_source(self):
        source = inspect.getsource(esr)
        for token in ("MAC", "MSP", "FAA", "Runway Safe", "USAspending", "Granicus"):
            assert token not in source, f"forbidden source-specific token found: {token!r}"

    def test_no_currency_specific_branching(self):
        source = inspect.getsource(esr)
        for token in ("USD", "EUR", "JPY", "currency"):
            assert token not in source


# --- 16. Purity / import boundary ---

class TestPurity:
    def test_no_forbidden_imports(self):
        source = inspect.getsource(esr)
        tree = ast.parse(source)
        forbidden_modules = {"sqlalchemy", "httpx", "requests", "app.database", "app.models", "app.acquisition"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(alias.name == m or alias.name.startswith(m + ".") for m in forbidden_modules)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not any(module == m or module.startswith(m + ".") for m in forbidden_modules)

    def test_no_model_imports_at_all(self):
        source = inspect.getsource(esr)
        tree = ast.parse(source)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
        for forbidden_name in ("Signal", "SourceAssertion", "ReviewerAction"):
            assert forbidden_name not in imported_names
        assert "app.models" not in source

    def test_no_random_import(self):
        source = inspect.getsource(esr)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(alias.name == "random" for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.module != "random"

    def test_no_current_time_dependency(self):
        source = inspect.getsource(esr)
        tree = ast.parse(source)
        forbidden_calls = {"today", "now", "utcnow"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_calls, f"forbidden call: {node.func.attr}"

    def test_no_filesystem_or_open_calls(self):
        source = inspect.getsource(esr)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "open"

    def test_all_three_public_dataclasses_are_frozen(self):
        for cls in (ExistingSignalReconciliationSubject, ReconciliationCandidateSignal, ExistingSignalReconciliationDecision):
            assert cls.__dataclass_params__.frozen is True

    def test_subject_is_immutable_after_construction(self):
        s = subject(airport_id=1)
        with pytest.raises(dataclasses.FrozenInstanceError):
            s.airport_id = 2  # type: ignore[misc]

    def test_candidate_is_immutable_after_construction(self):
        c = candidate(1, airport_id=1)
        with pytest.raises(dataclasses.FrozenInstanceError):
            c.airport_id = 2  # type: ignore[misc]

    def test_decision_is_immutable_after_construction(self):
        decision = evaluate_existing_signal_reconciliation(subject(airport_id=1), ())
        with pytest.raises(dataclasses.FrozenInstanceError):
            decision.outcome = POSSIBLE  # type: ignore[misc]


# --- 17. Determinism ---

class TestDeterminism:
    def test_repeated_calls_produce_identical_decision(self):
        s = subject(airport_id=1, runway_id=5, category="replacement", vendor_names=("Acme",))
        c = candidate(10, airport_id=1, runway_id=5, category="replacement", confirmed_vendor="Acme")
        first = evaluate_existing_signal_reconciliation(s, (c,))
        second = evaluate_existing_signal_reconciliation(s, (c,))
        assert first == second

    def test_inputs_are_not_mutated_by_evaluation(self):
        s = subject(airport_id=1, category="replacement")
        candidates = (candidate(1, airport_id=1, category="replacement"),)
        s_before, candidates_before = s, candidates
        evaluate_existing_signal_reconciliation(s, candidates)
        assert s == s_before
        assert candidates == candidates_before

    def test_reason_ordering_is_stable_across_repeated_calls(self):
        s = subject(airport_id=1, category="replacement", vendor_names=("Runway Safe",), evidence_date=date(2024, 1, 1))
        c = candidate(10, airport_id=1, category="replacement", confirmed_vendor="Runway Safe", evidence_date=date(2025, 1, 1))
        first = evaluate_existing_signal_reconciliation(s, (c,))
        second = evaluate_existing_signal_reconciliation(s, (c,))
        assert first.advisory_reasons == second.advisory_reasons


# --- 18. Reason explainability ---

class TestReasonExplainability:
    def test_anchor_reasons_are_tagged_identity_anchor(self):
        s = subject(airport_id=1, runway_id=5)
        c = candidate(10, airport_id=1, runway_id=5)
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert all("identity_anchor:" in r for r in decision.reasons)

    def test_compatibility_reasons_are_tagged_compatibility(self):
        s = subject(airport_id=1, category="replacement")
        c = candidate(10, airport_id=1, category="replacement")
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert all("compatibility:" in r for r in decision.advisory_reasons)

    def test_reasons_never_mixed_between_anchor_and_advisory(self):
        s = subject(airport_id=1, runway_id=5, category="replacement")
        c = candidate(10, airport_id=1, runway_id=5, category="replacement")
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        # candidate has both an anchor and compatibility evidence - only the
        # anchor-bearing outcome/branch is populated
        assert decision.outcome == POSSIBLE
        assert decision.advisory_candidate_signal_ids == ()
        assert decision.advisory_reasons == ()
        assert all("compatibility:" not in r for r in decision.reasons)

    def test_reasons_name_the_specific_candidate(self):
        s = subject(airport_id=1, category="replacement")
        candidates = (candidate(1, airport_id=1, category="replacement"), candidate(2, airport_id=1, category="replacement"))
        decision = evaluate_existing_signal_reconciliation(s, candidates)
        assert any(r.startswith("signal 1:") for r in decision.advisory_reasons)
        assert any(r.startswith("signal 2:") for r in decision.advisory_reasons)

    def test_no_numeric_score_or_confidence_in_any_reason(self):
        s = subject(airport_id=1, category="replacement", vendor_names=("Runway Safe",), evidence_date=date(2024, 1, 1))
        c = candidate(10, airport_id=1, category="replacement", confirmed_vendor="Runway Safe", evidence_date=date(2025, 1, 1))
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        for reason in decision.advisory_reasons:
            assert "confidence" not in reason.lower()
            assert "score" not in reason.lower()
            assert "%" not in reason


# --- 19. The central hardened rule: no count of compatibility axes substitutes for an anchor ---

class TestNoCompatibilityCountSubstitutesForAnchor:
    def test_every_compatibility_axis_at_once_still_does_not_block(self):
        s = subject(
            airport_id=1, category="replacement", vendor_names=("Runway Safe",),
            evidence_date=date(2024, 1, 1), reference_year=2024,
        )
        c = candidate(
            10, airport_id=1, category="replacement", confirmed_vendor="Runway Safe",
            evidence_date=date(2025, 1, 1), reference_year=2024,
        )
        decision = evaluate_existing_signal_reconciliation(s, (c,))
        assert decision.outcome == CLEAR
        assert len(decision.advisory_reasons) >= 4
        assert decision.candidate_signal_ids == ()

    def test_source_module_has_no_numeric_threshold_constant(self):
        # guards against the exact defect the design doc's own review
        # checkpoint found and removed ("2+ independent supporting
        # categories") ever quietly returning as a magic number.
        source = inspect.getsource(esr)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for comparator in node.comparators:
                    if isinstance(comparator, ast.Constant) and isinstance(comparator.value, int):
                        assert comparator.value in (0,), (
                            f"unexpected integer threshold constant in comparison: {comparator.value!r}"
                        )
