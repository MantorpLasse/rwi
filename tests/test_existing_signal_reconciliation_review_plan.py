"""Tests for app.services.existing_signal_reconciliation_review
(docs/architecture/existing-signal-reconciliation-r4a-review-plan-report.md,
R4A of docs/architecture/existing-signal-reconciliation-r4-human-resolution-design.md's
own S20 roadmap)."""
from __future__ import annotations

import ast
import dataclasses
import inspect
import itertools

import pytest

from app.services import existing_signal_reconciliation_review as r4a
from app.services.existing_signal_reconciliation import (
    ExistingSignalReconciliationDecision,
    ExistingSignalReconciliationOutcome,
    ExistingSignalReconciliationSubject,
    ReconciliationCandidateSignal,
    evaluate_existing_signal_reconciliation,
)
from app.services.existing_signal_reconciliation_review import (
    CURRENT_RECONCILIATION_PLAN_VERSION,
    ReconciliationReviewPlan,
    build_reconciliation_review_plan,
    compute_reconciliation_fingerprint,
)

CLEAR = ExistingSignalReconciliationOutcome.CLEAR_TO_CREATE
POSSIBLE = ExistingSignalReconciliationOutcome.POSSIBLE_EXISTING_SIGNAL_MATCH
LINKED = ExistingSignalReconciliationOutcome.ALREADY_LINKED


def subject(**kwargs) -> ExistingSignalReconciliationSubject:
    return ExistingSignalReconciliationSubject(**kwargs)


def candidate(signal_id: int, **kwargs) -> ReconciliationCandidateSignal:
    return ReconciliationCandidateSignal(signal_id=signal_id, **kwargs)


def plan_for(subj, candidates, *, source_assertion_id=222, version=None):
    decision = evaluate_existing_signal_reconciliation(subj, candidates)
    kwargs = dict(source_assertion_id=source_assertion_id, subject=subj, decision=decision)
    if version is not None:
        kwargs["reconciliation_plan_version"] = version
    return build_reconciliation_review_plan(**kwargs)


def fingerprint_for(subj, candidates, **kwargs):
    return compute_reconciliation_fingerprint(plan_for(subj, candidates, **kwargs))


# --- Single-anchor baseline used throughout ---

def _single_runway_anchor_scenario(runway_id=5, signal_id=99, airport_id=1):
    subj = subject(airport_id=airport_id, runway_id=runway_id)
    cand = candidate(signal_id, airport_id=airport_id, runway_id=runway_id)
    return subj, (cand,)


# --- 1. Basic construction ---

class TestBasicConstruction:
    def test_plan_built_from_possible_match(self):
        subj, cands = _single_runway_anchor_scenario()
        plan = plan_for(subj, cands)
        assert plan.candidate_signal_ids == (99,)
        assert plan.reconciliation_plan_version == CURRENT_RECONCILIATION_PLAN_VERSION
        assert plan.source_assertion_id == 222
        assert any("identity_anchor:runway_id" in r for r in plan.anchor_reasons)

    def test_fingerprint_is_64_char_lowercase_hex(self):
        subj, cands = _single_runway_anchor_scenario()
        fp = fingerprint_for(subj, cands)
        assert isinstance(fp, str)
        assert len(fp) == 64
        assert fp == fp.lower()
        int(fp, 16)  # raises ValueError if not valid hex


# --- A/B. Candidate / reason order permutation (case A, B) ---

class TestOrderPermutation:
    def test_candidate_order_permutation_same_fingerprint(self):
        airport = 1
        subj = subject(airport_id=airport, source_id=10)
        c1 = candidate(1, airport_id=airport, supporting_source_ids=(10,))
        c2 = candidate(2, airport_id=airport, supporting_source_ids=(10,))
        fp_forward = fingerprint_for(subj, (c1, c2))
        fp_backward = fingerprint_for(subj, (c2, c1))
        assert fp_forward == fp_backward

    def test_reason_order_within_single_candidate_multiple_anchors(self):
        # Same candidate anchored via two independent axes at once (runway +
        # provenance) - the two resulting reason strings must combine into
        # the same fingerprint regardless of which one R1 happened to append
        # first.
        subj1 = subject(airport_id=1, runway_id=5, source_id=10)
        cand1 = candidate(99, airport_id=1, runway_id=5, supporting_source_ids=(10,))
        fp1 = fingerprint_for(subj1, (cand1,))

        # Re-derive independently via manual plan construction with reasons
        # supplied in the opposite literal order, to prove the fingerprint
        # function itself (not just R1's own construction order) is order-
        # invariant.
        decision = evaluate_existing_signal_reconciliation(subj1, (cand1,))
        reordered_decision = dataclasses.replace(decision, reasons=tuple(reversed(decision.reasons)))
        plan_reordered = build_reconciliation_review_plan(
            source_assertion_id=222, subject=subj1, decision=reordered_decision,
        )
        fp2 = compute_reconciliation_fingerprint(plan_reordered)
        assert fp1 == fp2


# --- C/D. Duplicate candidate rows / duplicate reasons ---

class TestDuplicateInputs:
    def test_duplicate_candidate_rows_same_fingerprint_as_single(self):
        subj, cands = _single_runway_anchor_scenario()
        c = cands[0]
        fp_single = fingerprint_for(subj, (c,))
        fp_duplicated = fingerprint_for(subj, (c, c, c))
        assert fp_single == fp_duplicated

    def test_duplicate_reasons_deduplicated_before_hashing(self):
        subj, cands = _single_runway_anchor_scenario()
        decision = evaluate_existing_signal_reconciliation(subj, cands)
        doubled_decision = dataclasses.replace(decision, reasons=decision.reasons + decision.reasons)
        plan_doubled = build_reconciliation_review_plan(
            source_assertion_id=222, subject=subj, decision=doubled_decision,
        )
        plan_single = build_reconciliation_review_plan(
            source_assertion_id=222, subject=subj, decision=decision,
        )
        assert compute_reconciliation_fingerprint(plan_doubled) == compute_reconciliation_fingerprint(plan_single)
        assert plan_doubled.anchor_reasons == plan_single.anchor_reasons


# --- E/F. Subject physical-installation / provenance ordering ---

class TestSubjectFieldOrderPermutation:
    def test_physical_installation_id_order_permutation_same_fingerprint(self):
        subj_a = subject(airport_id=1, physical_installation_ids=(3, 7, 1))
        subj_b = subject(airport_id=1, physical_installation_ids=(7, 1, 3))
        cand = candidate(99, airport_id=1, physical_installation_ids=(1,))
        fp_a = fingerprint_for(subj_a, (cand,))
        fp_b = fingerprint_for(subj_b, (cand,))
        assert fp_a == fp_b

    def test_duplicate_physical_installation_ids_deduplicated(self):
        subj_a = subject(airport_id=1, physical_installation_ids=(3, 3, 3))
        subj_b = subject(airport_id=1, physical_installation_ids=(3,))
        cand = candidate(99, airport_id=1, physical_installation_ids=(3,))
        assert fingerprint_for(subj_a, (cand,)) == fingerprint_for(subj_b, (cand,))


# --- G/H. Candidate added / removed ---

class TestScalarIdentityFieldsAffectFingerprint:
    """Regression coverage added at the R4A review checkpoint for scalar
    fields the original 46 tests exercised only indirectly (through the
    serialization contract) rather than with a dedicated before/after
    comparison: subject airport identity, source_assertion_id, and plan
    version (Phase 3 items H, M, N)."""

    def test_source_assertion_id_change_changes_fingerprint(self):
        subj = subject(airport_id=1, runway_id=5)
        cand = candidate(99, airport_id=1, runway_id=5)
        fp_222 = fingerprint_for(subj, (cand,), source_assertion_id=222)
        fp_223 = fingerprint_for(subj, (cand,), source_assertion_id=223)
        assert fp_222 != fp_223

    def test_plan_version_change_changes_fingerprint(self):
        subj = subject(airport_id=1, runway_id=5)
        cand = candidate(99, airport_id=1, runway_id=5)
        fp_v1 = fingerprint_for(subj, (cand,), version=1)
        fp_v2 = fingerprint_for(subj, (cand,), version=2)
        assert fp_v1 != fp_v2

    def test_subject_airport_change_changes_fingerprint(self):
        # Both sides must stay internally self-consistent (R1 disqualifies a
        # candidate whose own airport differs from the subject's), so this
        # compares two otherwise-identical, independently valid scenarios
        # that differ only in which airport both subject and candidate share.
        cand_at_1 = candidate(99, airport_id=1, runway_id=5)
        subj_at_1 = subject(airport_id=1, runway_id=5)
        cand_at_2 = candidate(99, airport_id=2, runway_id=5)
        subj_at_2 = subject(airport_id=2, runway_id=5)
        assert fingerprint_for(subj_at_1, (cand_at_1,)) != fingerprint_for(subj_at_2, (cand_at_2,))

    def test_subject_artifact_identity_change_changes_fingerprint(self):
        cand = candidate(99, airport_id=1, supporting_artifact_identities=("art-A",))
        subj_a = subject(airport_id=1, artifact_identity="art-A")
        subj_b_candidate = candidate(99, airport_id=1, supporting_artifact_identities=("art-B",))
        subj_b = subject(airport_id=1, artifact_identity="art-B")
        assert fingerprint_for(subj_a, (cand,)) != fingerprint_for(subj_b, (subj_b_candidate,))


class TestCompleteBlockingSet:
    def test_candidate_added_changes_fingerprint(self):
        airport = 1
        subj = subject(airport_id=airport, runway_id=5)
        c67 = candidate(67, airport_id=airport, runway_id=5)
        c91 = candidate(91, airport_id=airport, runway_id=5)
        fp_one = fingerprint_for(subj, (c67,))
        fp_two = fingerprint_for(subj, (c67, c91))
        assert fp_one != fp_two

    def test_candidate_removed_changes_fingerprint(self):
        airport = 1
        subj = subject(airport_id=airport, runway_id=5)
        c67 = candidate(67, airport_id=airport, runway_id=5)
        c91 = candidate(91, airport_id=airport, runway_id=5)
        fp_two = fingerprint_for(subj, (c67, c91))
        fp_one = fingerprint_for(subj, (c67,))
        assert fp_two != fp_one

    def test_same_reasons_different_candidate_set_never_collide(self):
        # (67,) must never fingerprint the same as (67, 91) even though
        # signal 67's own reasons are byte-identical in both cases.
        airport = 1
        subj = subject(airport_id=airport, runway_id=5)
        c67 = candidate(67, airport_id=airport, runway_id=5)
        c91 = candidate(91, airport_id=airport, runway_id=5)
        plan_one = plan_for(subj, (c67,))
        plan_two = plan_for(subj, (c67, c91))
        assert "signal 67: identity_anchor:runway_id (runway_id=5)" in plan_one.anchor_reasons
        assert "signal 67: identity_anchor:runway_id (runway_id=5)" in plan_two.anchor_reasons
        assert compute_reconciliation_fingerprint(plan_one) != compute_reconciliation_fingerprint(plan_two)

    def test_candidate_signal_id_changed_changes_fingerprint(self):
        airport = 1
        subj = subject(airport_id=airport, runway_id=5)
        c67 = candidate(67, airport_id=airport, runway_id=5)
        c68 = candidate(68, airport_id=airport, runway_id=5)
        assert fingerprint_for(subj, (c67,)) != fingerprint_for(subj, (c68,))


# --- I. Candidate/reason association swapped - never pool ---

class TestCandidateReasonAssociationNeverPooled:
    def test_swapped_anchor_types_between_two_candidates_differ(self):
        airport = 1
        # Scenario 1: signal 67 anchored via runway, signal 91 via provenance.
        subj1 = subject(airport_id=airport, runway_id=5, source_id=10)
        c67_runway = candidate(67, airport_id=airport, runway_id=5)
        c91_provenance = candidate(91, airport_id=airport, supporting_source_ids=(10,))
        fp1 = fingerprint_for(subj1, (c67_runway, c91_provenance))

        # Scenario 2: signal 67 anchored via provenance, signal 91 via runway
        # - the reverse association.
        subj2 = subject(airport_id=airport, runway_id=5, source_id=10)
        c67_provenance = candidate(67, airport_id=airport, supporting_source_ids=(10,))
        c91_runway = candidate(91, airport_id=airport, runway_id=5)
        fp2 = fingerprint_for(subj2, (c67_provenance, c91_runway))

        assert fp1 != fp2

    def test_reason_strings_carry_their_own_candidate_identity(self):
        # The structural property that makes flat pooling safe: every reason
        # string is self-describing (prefixed with its own candidate id), so
        # a flat sorted tuple never actually loses candidate/reason
        # association.
        airport = 1
        subj = subject(airport_id=airport, runway_id=5, source_id=10)
        c67 = candidate(67, airport_id=airport, runway_id=5)
        c91 = candidate(91, airport_id=airport, supporting_source_ids=(10,))
        plan = plan_for(subj, (c67, c91))
        assert any(r.startswith("signal 67: ") for r in plan.anchor_reasons)
        assert any(r.startswith("signal 91: ") for r in plan.anchor_reasons)
        assert not any(r.startswith("signal 67: ") and "provenance" in r for r in plan.anchor_reasons)
        assert not any(r.startswith("signal 91: ") and "runway_id" in r for r in plan.anchor_reasons)

    def test_numeric_prefix_collision_between_candidate_ids_does_not_false_positive(self):
        # Regression coverage added at the R4A review checkpoint: candidate
        # ids 1, 10, and 11 could in principle collide under a naive prefix
        # check ("signal 1" is a literal string-prefix of "signal 11") if the
        # reason-prefix template did not include the delimiting ": " right
        # after the number. All three, each anchored via a DIFFERENT family,
        # must build a valid plan with each reason attributed to exactly its
        # own candidate - no false "candidate has no reason" or "orphaned
        # reason" error should ever fire here.
        airport = 1
        subj = subject(airport_id=airport, runway_id=5, physical_installation_ids=(7,), source_id=99)
        c1 = candidate(1, airport_id=airport, runway_id=5)
        c11 = candidate(11, airport_id=airport, physical_installation_ids=(7,))
        c10 = candidate(10, airport_id=airport, supporting_source_ids=(99,))
        plan = plan_for(subj, (c1, c11, c10))
        assert plan.candidate_signal_ids == (1, 10, 11)
        assert any(r == "signal 1: identity_anchor:runway_id (runway_id=5)" for r in plan.anchor_reasons)
        assert any(r.startswith("signal 10: ") and "provenance" in r for r in plan.anchor_reasons)
        assert any(r.startswith("signal 11: ") and "physical_installation_identity" in r for r in plan.anchor_reasons)


# --- J. Anchor reason changed ---

class TestAnchorReasonChanged:
    def test_additional_anchor_axis_on_same_candidate_changes_fingerprint(self):
        airport = 1
        subj_runway_only = subject(airport_id=airport, runway_id=5)
        subj_runway_and_provenance = subject(airport_id=airport, runway_id=5, source_id=10)
        cand = candidate(99, airport_id=airport, runway_id=5, supporting_source_ids=(10,))
        fp_one_axis = fingerprint_for(subj_runway_only, (cand,))
        fp_two_axes = fingerprint_for(subj_runway_and_provenance, (cand,))
        assert fp_one_axis != fp_two_axes

    def test_lost_anchor_axis_changes_fingerprint(self):
        airport = 1
        identity_a = 7
        identity_b = 8
        subj_a = subject(airport_id=airport, physical_installation_ids=(identity_a,))
        subj_b = subject(airport_id=airport, physical_installation_ids=(identity_b,))
        cand = candidate(99, airport_id=airport, physical_installation_ids=(identity_a,))
        fp_a = fingerprint_for(subj_a, (cand,))
        # subj_b no longer shares the same installation id - not an anchor at
        # all any more for this candidate, so this must raise (no blocking
        # decision left to build a plan from) rather than silently differ.
        decision_b = evaluate_existing_signal_reconciliation(subj_b, (cand,))
        assert decision_b.outcome == CLEAR
        with pytest.raises(ValueError):
            build_reconciliation_review_plan(source_assertion_id=222, subject=subj_b, decision=decision_b)


# --- K. Advisory metadata changed only ---

class TestAdvisoryChurnNeverStales:
    def test_advisory_only_candidate_never_enters_the_plan(self):
        airport = 1
        subj = subject(airport_id=airport, runway_id=5, category="replacement", vendor_names=("Acme",))
        blocking = candidate(99, airport_id=airport, runway_id=5)
        advisory_only = candidate(41, airport_id=airport, category="replacement", confirmed_vendor="Acme")
        plan = plan_for(subj, (blocking, advisory_only))
        assert plan.candidate_signal_ids == (99,)
        assert all("41" not in str(r) or "signal 41" not in r for r in plan.anchor_reasons)

    def test_fingerprint_unchanged_when_only_advisory_evidence_changes(self):
        airport = 1
        subj_v1 = subject(airport_id=airport, runway_id=5, category="replacement", vendor_names=("Acme",))
        subj_v2 = subject(airport_id=airport, runway_id=5, category="different-category", vendor_names=("Someone Else",))
        blocking = candidate(99, airport_id=airport, runway_id=5, category="replacement", confirmed_vendor="Acme")
        fp_v1 = fingerprint_for(subj_v1, (blocking,))
        fp_v2 = fingerprint_for(subj_v2, (blocking,))
        assert fp_v1 == fp_v2

    def test_extra_advisory_only_candidate_appearing_does_not_change_fingerprint(self):
        airport = 1
        subj = subject(airport_id=airport, runway_id=5)
        blocking = candidate(99, airport_id=airport, runway_id=5)
        fp_before = fingerprint_for(subj, (blocking,))
        advisory_newcomer = candidate(41, airport_id=airport, category="replacement")
        subj_with_category = subject(airport_id=airport, runway_id=5, category="replacement")
        fp_after = fingerprint_for(subj_with_category, (blocking, advisory_newcomer))
        assert fp_before == fp_after


# --- L. Financial/title/raw-text structurally unavailable ---

class TestStructuralFirewalls:
    def test_no_financial_title_or_raw_text_fields_on_plan(self):
        field_names = {f.name for f in dataclasses.fields(ReconciliationReviewPlan)}
        forbidden_tokens = ("financial", "amount", "cost", "price", "usd", "title", "notes", "text", "confidence", "score", "similarity")
        for name in field_names:
            lowered = name.lower()
            for token in forbidden_tokens:
                assert token not in lowered, f"forbidden token {token!r} found in field name {name!r}"

    def test_cannot_construct_plan_with_financial_or_title_kwarg(self):
        with pytest.raises(TypeError):
            ReconciliationReviewPlan(
                reconciliation_plan_version=1, source_assertion_id=1,
                subject_airport_id=None, subject_runway_id=None,
                subject_physical_installation_ids=(), subject_source_id=None,
                subject_artifact_identity=None, candidate_signal_ids=(1,),
                anchor_reasons=("x",), title="smuggled",  # type: ignore[call-arg]
            )

    def test_module_source_never_mentions_dollar_figures_or_provider_names(self):
        source_text = inspect.getsource(r4a)
        assert "$" not in source_text
        for token in ("MAC", "MSP", "FAA", "Runway Safe", "USAspending", "Granicus"):
            assert token not in source_text, f"forbidden token found: {token!r}"

    def test_module_never_imports_acquisition_or_orm(self):
        source_text = inspect.getsource(r4a)
        tree = ast.parse(source_text)
        forbidden_modules = {"sqlalchemy", "httpx", "requests", "app.database", "app.models", "app.acquisition"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(alias.name == m or alias.name.startswith(m + ".") for m in forbidden_modules)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not any(module == m or module.startswith(m + ".") for m in forbidden_modules)

    def test_no_reviewer_action_import(self):
        source_text = inspect.getsource(r4a)
        assert "ReviewerAction" not in source_text
        assert "reviewer_action" not in source_text.lower()


# --- M/N. CLEAR_TO_CREATE / ALREADY_LINKED plan construction attempt ---

class TestOutcomeGuard:
    def test_clear_to_create_raises(self):
        subj = subject(airport_id=1, category="replacement")
        cand = candidate(1, airport_id=1, category="replacement")
        decision = evaluate_existing_signal_reconciliation(subj, (cand,))
        assert decision.outcome == CLEAR
        with pytest.raises(ValueError, match="POSSIBLE_EXISTING_SIGNAL_MATCH"):
            build_reconciliation_review_plan(source_assertion_id=1, subject=subj, decision=decision)

    def test_bare_clear_to_create_no_candidates_raises(self):
        subj = subject(airport_id=1)
        decision = evaluate_existing_signal_reconciliation(subj, ())
        assert decision.outcome == CLEAR
        with pytest.raises(ValueError):
            build_reconciliation_review_plan(source_assertion_id=1, subject=subj, decision=decision)

    def test_already_linked_raises(self):
        subj = subject(existing_signal_id=67)
        decision = evaluate_existing_signal_reconciliation(subj, ())
        assert decision.outcome == LINKED
        with pytest.raises(ValueError, match="POSSIBLE_EXISTING_SIGNAL_MATCH"):
            build_reconciliation_review_plan(source_assertion_id=222, subject=subj, decision=decision)


# --- O. Malformed empty blocking decision ---

class TestMalformedDecisionFailsClosed:
    def test_empty_candidate_signal_ids_raises(self):
        malformed = ExistingSignalReconciliationDecision(
            outcome=POSSIBLE, candidate_signal_ids=(), reasons=("signal 1: identity_anchor:runway_id",),
        )
        subj = subject(airport_id=1)
        with pytest.raises(ValueError):
            build_reconciliation_review_plan(source_assertion_id=1, subject=subj, decision=malformed)

    def test_empty_reasons_raises(self):
        malformed = ExistingSignalReconciliationDecision(
            outcome=POSSIBLE, candidate_signal_ids=(1,), reasons=(),
        )
        subj = subject(airport_id=1)
        with pytest.raises(ValueError):
            build_reconciliation_review_plan(source_assertion_id=1, subject=subj, decision=malformed)

    def test_candidate_with_no_attributed_reason_raises(self):
        malformed = ExistingSignalReconciliationDecision(
            outcome=POSSIBLE, candidate_signal_ids=(1, 2),
            reasons=("signal 1: identity_anchor:runway_id (runway_id=5)",),  # signal 2 has none
        )
        subj = subject(airport_id=1)
        with pytest.raises(ValueError, match="signal 2"):
            build_reconciliation_review_plan(source_assertion_id=1, subject=subj, decision=malformed)

    def test_reason_attributed_to_a_non_blocking_candidate_raises(self):
        # Regression test for a real defect found at the R4A review
        # checkpoint: a reason string attributed to signal_id 2, even though
        # candidate_signal_ids only declares signal_id 1, must never be
        # silently accepted into the plan - the plan must represent exactly
        # what a human is shown, not one extra candidate's worth of hidden
        # evidence. Before the fix, this exact input produced a valid plan
        # whose `anchor_reasons` silently included the signal-2 reason.
        malformed = ExistingSignalReconciliationDecision(
            outcome=POSSIBLE, candidate_signal_ids=(1,),
            reasons=(
                "signal 1: identity_anchor:runway_id (runway_id=5)",
                "signal 2: identity_anchor:provenance (source_id=10)",  # not a declared candidate
            ),
        )
        subj = subject(airport_id=1)
        with pytest.raises(ValueError, match="signal 2"):
            build_reconciliation_review_plan(source_assertion_id=1, subject=subj, decision=malformed)

    def test_reason_referring_to_unrelated_candidate_with_otherwise_valid_set_raises(self):
        # A variant where the DECLARED candidates are fully, validly covered
        # (so the forward check alone would pass) but one extra, orphaned
        # reason for an undeclared candidate is also present.
        malformed = ExistingSignalReconciliationDecision(
            outcome=POSSIBLE, candidate_signal_ids=(1, 2),
            reasons=(
                "signal 1: identity_anchor:runway_id (runway_id=5)",
                "signal 2: identity_anchor:runway_id (runway_id=5)",
                "signal 999: identity_anchor:provenance (source_id=10)",  # orphaned
            ),
        )
        subj = subject(airport_id=1)
        with pytest.raises(ValueError, match="signal 999"):
            build_reconciliation_review_plan(source_assertion_id=1, subject=subj, decision=malformed)


# --- MSP historical/current regression (synthetic, R1/R2/R3 semantics unmodified) ---

class TestMSPRegression:
    def _pre_resolution_subject(self):
        import datetime
        return subject(
            airport_id=45, runway_id=None, source_id=70,
            category="replacement", vendor_names=("Runway Safe",),
            evidence_date=datetime.date(2024, 8, 1),
        )

    def _signal_67(self):
        import datetime
        return candidate(
            67, airport_id=45, runway_id=None, category="replacement",
            confirmed_vendor="Runway Safe", supporting_source_ids=(45,),
            evidence_date=datetime.date(2025, 5, 1),
        )

    def _signal_41(self):
        return candidate(
            41, airport_id=45, runway_id=None, category="replacement",
            supporting_source_ids=(15,), reference_year=2025,
        )

    def test_pre_resolution_msp_shape_never_produces_a_blocking_plan(self):
        subj = self._pre_resolution_subject()
        decision = evaluate_existing_signal_reconciliation(subj, (self._signal_67(), self._signal_41()))
        assert decision.outcome == CLEAR
        assert set(decision.advisory_candidate_signal_ids) == {41, 67}
        with pytest.raises(ValueError):
            build_reconciliation_review_plan(source_assertion_id=222, subject=subj, decision=decision)

    def test_current_msp_already_linked_never_produces_a_blocking_plan(self):
        subj = dataclasses.replace(self._pre_resolution_subject(), existing_signal_id=67)
        decision = evaluate_existing_signal_reconciliation(subj, (self._signal_67(), self._signal_41()))
        assert decision.outcome == LINKED
        with pytest.raises(ValueError):
            build_reconciliation_review_plan(source_assertion_id=222, subject=subj, decision=decision)

    def test_synthetic_msp_shaped_anchor_backed_case_produces_a_blocking_plan(self):
        # Same airport/category/vendor shape as the real MSP case, but WITH a
        # genuine structural anchor added (shared canonical runway_id) -
        # proving the review-plan path is reachable without altering R1/R2
        # semantics or fabricating an anchor for the real, unresolved case.
        import datetime
        runway_id = 30
        subj = subject(
            airport_id=45, runway_id=runway_id, source_id=70,
            category="replacement", vendor_names=("Runway Safe",),
            evidence_date=datetime.date(2024, 8, 1),
        )
        signal_67_anchored = candidate(
            67, airport_id=45, runway_id=runway_id, category="replacement",
            confirmed_vendor="Runway Safe", evidence_date=datetime.date(2025, 5, 1),
        )
        decision = evaluate_existing_signal_reconciliation(subj, (signal_67_anchored,))
        assert decision.outcome == POSSIBLE
        plan = build_reconciliation_review_plan(source_assertion_id=999, subject=subj, decision=decision)
        assert plan.candidate_signal_ids == (67,)
        fp = compute_reconciliation_fingerprint(plan)
        assert len(fp) == 64


# --- P. Repeated calls produce identical fingerprint ---

class TestDeterminismAcrossRepeatedCalls:
    def test_repeated_calls_identical_fingerprint(self):
        subj, cands = _single_runway_anchor_scenario()
        fp1 = fingerprint_for(subj, cands)
        fp2 = fingerprint_for(subj, cands)
        fp3 = fingerprint_for(subj, cands)
        assert fp1 == fp2 == fp3

    def test_repeated_plan_construction_produces_equal_plans(self):
        subj, cands = _single_runway_anchor_scenario()
        plan1 = plan_for(subj, cands)
        plan2 = plan_for(subj, cands)
        assert plan1 == plan2

    def test_inputs_not_mutated_by_plan_or_fingerprint_construction(self):
        subj, cands = _single_runway_anchor_scenario()
        decision = evaluate_existing_signal_reconciliation(subj, cands)
        decision_before = decision
        plan = build_reconciliation_review_plan(source_assertion_id=222, subject=subj, decision=decision)
        compute_reconciliation_fingerprint(plan)
        assert decision == decision_before
        assert subj == _single_runway_anchor_scenario()[0]


# --- Q. International / provider-neutral synthetic case ---

class TestInternationalCase:
    def test_non_us_airport_and_vendor_reach_identical_semantics(self):
        subj = subject(
            airport_id=900, runway_id=42, category="replacement",
            vendor_names=("Taiyo Safety Materials KK",),
        )
        cand = candidate(
            901, airport_id=900, runway_id=42, category="replacement",
            confirmed_vendor="Taiyo Safety Materials KK",
        )
        plan = plan_for(subj, (cand,))
        fp = compute_reconciliation_fingerprint(plan)
        assert len(fp) == 64
        assert plan.candidate_signal_ids == (901,)


# --- R. Large candidate set remains deterministic ---

class TestLargeCandidateSetDeterminism:
    def test_large_candidate_set_deterministic_across_permutations(self):
        airport = 1
        runway_id = 5
        subj = subject(airport_id=airport, runway_id=runway_id)
        candidates = tuple(candidate(i, airport_id=airport, runway_id=runway_id) for i in range(1, 21))
        fp_forward = fingerprint_for(subj, candidates)
        fp_reversed = fingerprint_for(subj, tuple(reversed(candidates)))
        assert fp_forward == fp_reversed

        # a handful of random-ish permutations, not exhaustive (20! is
        # intractable) but enough to catch any accidental order-dependence
        import random
        rng = random.Random(1234)
        for _ in range(5):
            shuffled = list(candidates)
            rng.shuffle(shuffled)
            assert fingerprint_for(subj, tuple(shuffled)) == fp_forward

    def test_all_permutations_of_three_candidates_identical(self):
        airport = 1
        runway_id = 5
        subj = subject(airport_id=airport, runway_id=runway_id)
        candidates = tuple(candidate(i, airport_id=airport, runway_id=runway_id) for i in (10, 20, 30))
        fingerprints = {fingerprint_for(subj, perm) for perm in itertools.permutations(candidates)}
        assert len(fingerprints) == 1


# --- Purity ---

class TestPurity:
    def test_no_forbidden_imports(self):
        source_text = inspect.getsource(r4a)
        tree = ast.parse(source_text)
        forbidden_modules = {"sqlalchemy", "httpx", "requests", "app.database", "app.models", "app.acquisition"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(alias.name == m or alias.name.startswith(m + ".") for m in forbidden_modules)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not any(module == m or module.startswith(m + ".") for m in forbidden_modules)

    def test_no_random_or_uuid_import(self):
        source_text = inspect.getsource(r4a)
        tree = ast.parse(source_text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in ("random", "uuid")
            elif isinstance(node, ast.ImportFrom):
                assert node.module not in ("random", "uuid")

    def test_no_current_time_dependency(self):
        source_text = inspect.getsource(r4a)
        tree = ast.parse(source_text)
        forbidden_calls = {"today", "now", "utcnow"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_calls, f"forbidden call: {node.func.attr}"

    def test_no_filesystem_or_network_calls(self):
        source_text = inspect.getsource(r4a)
        tree = ast.parse(source_text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in ("open", "socket")

    def test_plan_is_frozen(self):
        assert ReconciliationReviewPlan.__dataclass_params__.frozen is True

    def test_plan_is_immutable_after_construction(self):
        subj, cands = _single_runway_anchor_scenario()
        plan = plan_for(subj, cands)
        with pytest.raises(dataclasses.FrozenInstanceError):
            plan.source_assertion_id = 999  # type: ignore[misc]

    def test_serialization_uses_json_not_repr_or_pickle(self):
        # AST-based: checks actual CALLS in the code, not prose mentioning
        # these functions to explain why they are not used.
        source_text = inspect.getsource(r4a)
        tree = ast.parse(source_text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    assert node.func.id not in ("repr", "hash"), f"forbidden call: {node.func.id}(...)"
                if isinstance(node.func, ast.Attribute):
                    assert node.func.attr not in ("dumps",) or (
                        isinstance(node.func.value, ast.Name) and node.func.value.id == "json"
                    ), "a non-json .dumps(...) call was found"
        assert "pickle" not in source_text.lower()
        # str(dataclass)/f"{plan}" would rely on the dataclass's own
        # implementation-dependent __repr__ - confirm json.dumps is what's
        # actually used for the hashed payload.
        assert "json.dumps" in source_text
        assert "hashlib.sha256" in source_text
