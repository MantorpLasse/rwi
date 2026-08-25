"""Tests for app.services.emas_relevance_evaluation (ERG1,
docs/architecture/rwi-erg1-emas-relevance-evaluator-report.md).

Attack matrix (mission's own S15, A-T) is covered by the test class names
below - each class docstring names which attack letter(s) it targets."""
from __future__ import annotations

import ast
import inspect

import pytest

from app.services import emas_relevance_evaluation as erm
from app.services.emas_relevance_evaluation import (
    EVALUATOR_VERSION,
    EmasEvidenceObservation,
    EmasRelevanceContext,
    EmasRelevanceInputError,
    EvidenceClass,
    ObservationPolarity,
    RelevanceOutcome,
    evaluate_emas_relevance,
)
from app.services.evidence_claim_semantics import TemporalQualifier

A = EvidenceClass.A_EXPLICIT_EMAS
B = EvidenceClass.B_RUNWAY_SAFETY_AREA_OR_ARRESTOR_NEED
C = EvidenceClass.C_PLANNING_OR_FEASIBILITY
D = EvidenceClass.D_FUNDING_OR_PROCUREMENT
E = EvidenceClass.E_EXISTING_INSTALLATION
F = EvidenceClass.F_INCIDENT_DRIVEN
G = EvidenceClass.G_GENERIC_RUNWAY_WORK

CONFIRMED = RelevanceOutcome.EMAS_CONFIRMED
STRONG = RelevanceOutcome.EMAS_STRONG_SIGNAL
PLAUSIBLE = RelevanceOutcome.EMAS_PLAUSIBLE_SIGNAL
RUNWAY_ONLY = RelevanceOutcome.RUNWAY_ONLY_NOT_EMAS_RELEVANT
INSUFFICIENT = RelevanceOutcome.INSUFFICIENT_EVIDENCE


def _obs(
    evidence_class: EvidenceClass, basis: str = "test basis",
    temporality: TemporalQualifier = TemporalQualifier.UNKNOWN,
    polarity: ObservationPolarity = ObservationPolarity.POSITIVE,
) -> EmasEvidenceObservation:
    return EmasEvidenceObservation(evidence_class=evidence_class, basis=basis, temporality=temporality, polarity=polarity)


def _evaluate(*observations: EmasEvidenceObservation):
    return evaluate_emas_relevance(tuple(observations))


def _source_without_docstrings(module) -> str:
    """Explanatory docstrings legitimately name FAA/MAC/app.models etc. as
    prose (explaining what this module deliberately does NOT do) - the
    firewall/source-neutral checks below must inspect executable code only,
    the same way test_promotion_policy_evaluation.py's own AST-based checks
    inspect import statements rather than raw substrings."""
    source = inspect.getsource(module)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                source = source.replace(doc, "")
    return source


# --- A. Anoka locked regression (mission S6, mandatory) ---

class TestAnokaLockedRegression:
    """Attack A. The real, permanent Anoka County-Blaine Airport evidence
    shape - Runway 18-36 pavement reconstruction + electrical vault
    improvements, no EMAS/RSA/arrestor/overrun language anywhere in the
    real evidence (5F/5G missions). Must never regress."""

    def test_anoka_evidence_is_runway_only_not_emas_relevant(self):
        decision = _evaluate(
            _obs(G, basis="Runway 18-36 Pavement Reconstruction"),
            _obs(G, basis="Electrical Vault Improvements"),
        )
        assert decision.outcome == RUNWAY_ONLY
        assert decision.is_watch_worthy is False
        assert decision.is_canonical_admission_relevant is False
        assert decision.evidence_classes_matched == frozenset({G})
        assert decision.contradicting_evidence_classes == frozenset()

    def test_anoka_evidence_alone_never_watch_worthy_or_admission_relevant(self):
        decision = _evaluate(_obs(G, basis="Runway 18-36 Pavement Reconstruction"))
        assert decision.is_watch_worthy is False
        assert decision.is_canonical_admission_relevant is False


# --- B/C/D/E/F/G. Early-signal, explicit-EMAS, and false-positive matrices ---

class TestEarlySignalsClassBAndC:
    """Attack sections 7 (design mission scenarios A-E): early signals must
    not require the literal word EMAS."""

    def test_runway_safety_area_alternatives_analysis(self):
        decision = _evaluate(_obs(C, basis="Runway Safety Area Alternatives Analysis"))
        assert decision.outcome == PLAUSIBLE
        assert decision.is_watch_worthy is True
        assert decision.is_canonical_admission_relevant is True

    def test_runway_end_safety_improvement_feasibility_study(self):
        decision = _evaluate(_obs(B, basis="Runway End Safety Improvement Feasibility Study"))
        assert decision.outcome == PLAUSIBLE

    def test_overrun_mitigation_alternatives(self):
        decision = _evaluate(_obs(B, basis="Overrun Mitigation Alternatives"))
        assert decision.outcome == PLAUSIBLE

    def test_insufficient_rsa_engineering_alternatives(self):
        decision = _evaluate(_obs(B, basis="Insufficient Runway Safety Area - Engineering Alternatives"))
        assert decision.outcome == PLAUSIBLE

    def test_arresting_system_feasibility(self):
        decision = _evaluate(_obs(B, basis="Arresting System Feasibility"))
        assert decision.outcome == PLAUSIBLE

    def test_rsa_wording_is_not_automatically_emas_positive_it_is_plausible_not_strong(self):
        """Explicit design instruction: do not make all RSA wording
        automatically EMAS-positive at the STRONG/CONFIRMED tier - class B/C
        alone (no A/D/E/F) must cap at PLAUSIBLE, never higher."""
        decision = _evaluate(_obs(B, basis="RSA deficiency"), _obs(C, basis="alternatives analysis"))
        assert decision.outcome == PLAUSIBLE


class TestExplicitEmasRegressions:
    """Attack section 8: explicit EMAS evidence must classify strongly."""

    def test_emas_feasibility_study_is_strong_not_merely_plausible(self):
        # Class C co-occurring with class A: naming EMAS in a feasibility
        # study contributes toward STRONG, not PLAUSIBLE (module docstring,
        # CLASS C'S OWN DUAL NATURE).
        decision = _evaluate(_obs(A, basis="EMAS Feasibility Study"), _obs(C, basis="EMAS Feasibility Study"))
        assert decision.outcome == STRONG

    def test_emas_procurement(self):
        decision = _evaluate(_obs(D, basis="EMAS Procurement"))
        assert decision.outcome == STRONG

    def test_emas_material_acquisition(self):
        decision = _evaluate(_obs(D, basis="EMAS material acquisition"))
        assert decision.outcome == STRONG

    def test_emas_installation_existing(self):
        decision = _evaluate(_obs(E, basis="EMAS installation"))
        assert decision.outcome == CONFIRMED

    def test_emas_replacement_of_existing_bed(self):
        decision = _evaluate(_obs(E, basis="EMAS replacement"))
        assert decision.outcome == CONFIRMED

    def test_emas_maintenance(self):
        decision = _evaluate(_obs(E, basis="EMAS maintenance"))
        assert decision.outcome == CONFIRMED

    def test_engineered_materials_arresting_system_design(self):
        decision = _evaluate(_obs(A, basis="Engineered Materials Arresting System design"))
        assert decision.outcome == STRONG

    @pytest.mark.parametrize("basis", [
        "EMAS", "emas", "E.M.A.S.", "Engineered Materials Arresting System",
        "ENGINEERED MATERIALS ARRESTING SYSTEM", "Engineered Material Arresting System",
        "  EMAS  bed  ",
    ])
    def test_capitalization_and_punctuation_variants_of_basis_never_change_classification(self, basis):
        """The evaluator classifies by EvidenceClass, never by parsing
        `basis` text - basis is audit-only (module docstring). Proves
        outcome is stable across any capitalization/punctuation of the
        already-extracted basis string."""
        decision = _evaluate(_obs(A, basis=basis))
        assert decision.outcome == STRONG
        assert decision.evidence_classes_matched == frozenset({A})


class TestFalsePositiveDefense:
    """Attack section 9: no contextual leakage. Generic runway work, in any
    quantity or combination, never escalates past RUNWAY_ONLY_NOT_EMAS_RELEVANT."""

    @pytest.mark.parametrize("basis", [
        "runway pavement reconstruction", "runway lighting replacement", "PAPI replacement",
        "electrical vault", "taxiway reconstruction", "terminal construction", "generic safety improvements",
    ])
    def test_single_generic_runway_work_item_is_runway_only(self, basis):
        decision = _evaluate(_obs(G, basis=basis))
        assert decision.outcome == RUNWAY_ONLY

    def test_many_generic_runway_work_items_never_accumulate_to_a_positive_outcome(self):
        decision = _evaluate(*[
            _obs(G, basis=f"generic runway work item {i}") for i in range(12)
        ])
        assert decision.outcome == RUNWAY_ONLY
        assert decision.is_watch_worthy is False

    def test_no_observations_extracted_for_unrelated_mentions_is_insufficient_not_positive(self):
        """'Generic RSA acronym in unrelated context', ''arresting' in
        unrelated prose', 'another airport's EMAS mention', and 'funding
        document listing EMAS for a different airport' are all
        EXTRACTION-LAYER precision concerns (design doc S15/S24 threat C) -
        this evaluator has no raw text to misclassify. When extraction
        correctly declines to produce any observation for such unrelated
        text, the safe default is INSUFFICIENT_EVIDENCE, never a positive
        outcome."""
        decision = _evaluate()
        assert decision.outcome == INSUFFICIENT
        assert decision.is_watch_worthy is False
        assert decision.is_canonical_admission_relevant is False


# --- Adversarial-review addition: RSA false-positive boundary (mission's
# own ERG1 review S9, "one of the most important ERG1 review questions") ---

class TestRsaFalsePositiveDefense:
    """Attack section 9 (review mission): 'do not treat every appearance of
    runway safety area as equivalent.' Class B is a flat bucket - the
    evaluator itself has no raw text to distinguish a genuine deficiency
    from routine RSA upkeep, so this is fundamentally an EXTRACTION-LAYER
    precision requirement (module docstring, EvidenceClass.B's own NOTE).
    These tests document and lock the boundary: when extraction correctly
    declines to tag routine work as B (tagging it G or producing no
    observation at all, exactly as it must), the evaluator behaves safely
    - it does NOT and cannot retroactively fix a mis-tagged B."""

    @pytest.mark.parametrize("basis", [
        "RSA mowing contract", "RSA signage replacement", "routine RSA inspection",
        "generic Part 139 compliance checklist mentioning runway safety area",
    ])
    def test_routine_rsa_administrative_work_correctly_extracted_as_generic_is_runway_only(self, basis):
        """If extraction correctly recognizes routine/administrative RSA
        work carries no deficiency signal and tags it G (not B), the
        evaluator stays at RUNWAY_ONLY - never escalates."""
        decision = _evaluate(_obs(G, basis=basis))
        assert decision.outcome == RUNWAY_ONLY

    @pytest.mark.parametrize("basis", [
        "RSA mowing contract", "RSA signage replacement", "routine RSA inspection",
    ])
    def test_routine_rsa_administrative_work_with_no_observation_at_all_is_insufficient(self, basis):
        """The safer extraction behavior for genuinely non-signal routine
        work is to produce no observation at all - proven safe (never
        positive) by construction."""
        decision = _evaluate()
        assert decision.outcome == INSUFFICIENT

    def test_a_genuine_rsa_deficiency_correctly_tagged_b_is_plausible(self):
        """The positive control: when extraction correctly identifies an
        actual deficiency/need (not routine upkeep), class B produces
        EMAS_PLAUSIBLE_SIGNAL exactly as intended."""
        decision = _evaluate(_obs(B, basis="RSA deficiency requiring mitigation"))
        assert decision.outcome == PLAUSIBLE

    def test_declared_distance_reduction_from_runway_end_constraint(self):
        decision = _evaluate(_obs(B, basis="Declared distance reduction due to runway-end constraint"))
        assert decision.outcome == PLAUSIBLE

    def test_safety_area_specific_compliance_study_is_plausible(self):
        """A compliance STUDY specific to the runway safety area (not a
        generic Part 139 checklist mention) is a genuine class-B signal per
        the design doc's own S16 line-drawing."""
        decision = _evaluate(_obs(B, basis="Runway safety area compliance study, no explicit EMAS mention"))
        assert decision.outcome == PLAUSIBLE

    def test_mixed_title_generic_safety_words_do_not_elevate_generic_runway_work(self):
        """'Runway 18-36 Reconstruction and Safety Improvements' - if
        extraction correctly treats generic 'safety improvements' marketing
        language as G (it names no specific RSA/arrestor deficiency), the
        result must stay RUNWAY_ONLY, never escalate merely because the
        word 'safety' appears in the title."""
        decision = _evaluate(
            _obs(G, basis="Runway 18-36 Reconstruction"), _obs(G, basis="Safety Improvements (generic, no RSA/EMAS specificity)"),
        )
        assert decision.outcome == RUNWAY_ONLY


class TestContextLeakageDefense:
    """Attack section 10 (review mission): arresting/EMAS context leakage -
    military arresting gear, another airport's EMAS, historical background
    mentions, multi-project procurement documents. All are extraction-layer
    scoping concerns (module docstring, EvidenceClass.A's own NOTE; design
    doc's own per-candidate evidence scoping principle) - documented here as
    safe-by-construction when extraction correctly abstains, not solved by
    new evaluator logic."""

    def test_no_observation_for_unrelated_military_arresting_gear_mention_is_insufficient(self):
        decision = _evaluate()
        assert decision.outcome == INSUFFICIENT

    def test_another_airports_emas_mention_is_out_of_scope_for_per_candidate_evaluation(self):
        """Cross-airport identity attribution is UAC3/the guard's job, not
        ERG1's - this evaluator only ever sees observations ALREADY scoped
        to one candidate (design doc S13/S15). A document mentioning a
        DIFFERENT airport's EMAS must simply never produce an observation
        for THIS candidate; if extraction does its job, no test inside this
        evaluator can or should represent 'a different airport's EMAS' as
        input to this candidate's own evaluation."""
        decision = _evaluate(_obs(G, basis="this candidate's own generic runway work only"))
        assert decision.outcome == RUNWAY_ONLY

    def test_historical_background_only_emas_mention_is_discounted_per_temporal_rules(self):
        """'Airport X installed EMAS in 2008' as pure background prose
        about a DIFFERENT, unrelated topic - if extraction still produces
        an observation for it (rather than correctly recognizing it is
        off-topic and producing none), class E is exempt from temporal
        discount regardless (see TestHistoricalVsCurrentSignal) - documented
        here as the same known, accepted behavior, not a new case."""
        decision = _evaluate(_obs(E, basis="background: Airport X installed EMAS in 2008", temporality=TemporalQualifier.HISTORICAL_FACT))
        assert decision.outcome == CONFIRMED


# --- Adversarial-review addition: contradiction scenario D (mission S13) ---

class TestContradictionScenarioD:
    def test_rsa_deficiency_contradicted_by_standard_rsa_already_constructed(self):
        """'weak RSA signal + explicit evidence that full standard RSA was
        constructed instead' - contradiction is surfaced, never silently
        resolves the outcome to negative (module docstring, CONTRADICTION
        SEMANTICS) - a human must review this, not the evaluator."""
        decision = _evaluate(
            _obs(B, basis="RSA deficiency identified"),
            _obs(B, basis="full standard RSA subsequently constructed, deficiency resolved", polarity=ObservationPolarity.CONTRADICTING),
        )
        assert decision.outcome == PLAUSIBLE
        assert decision.contradicting_evidence_classes == frozenset({B})


# --- Adversarial-review addition: EMAS repair, additional explicit-EMAS attacks ---

class TestAdditionalExplicitEmasRegressions:
    def test_emas_repair(self):
        decision = _evaluate(_obs(E, basis="EMAS repair"))
        assert decision.outcome == CONFIRMED

    def test_emas_bed_replacement(self):
        decision = _evaluate(_obs(E, basis="EMAS bed replacement"))
        assert decision.outcome == CONFIRMED

    def test_arresting_system_design_explicitly_tied_to_runway_safety(self):
        decision = _evaluate(_obs(A, basis="Arresting system design explicitly tied to runway end safety"))
        assert decision.outcome == STRONG


# --- Adversarial-review addition: flagged, not-fixed design tension (mission
# S12/S16) - documented, not silently accepted ---

class TestDormantInstallationFlaggedFinding:
    """A dormant, decades-old confirmed installation with zero corroborating
    current activity is, TODAY, both watch-worthy and canonical-admission-
    relevant - identical to an active EMAS_STRONG_SIGNAL opportunity. This
    matches the parent design doc's own literal, unconditional Section 7
    definition of watch-worthiness (EMAS_CONFIRMED/STRONG/PLAUSIBLE, no
    'dormant' carve-out) - this module implements that locked definition
    exactly, and does not invent a third boolean/vocabulary member outside
    this mission's scope. Locked as a KNOWN, FLAGGED, NOT-FIXED-HERE
    finding (module docstring) for a future design-level decision before
    ERG4 is built - this test documents current behavior honestly rather
    than silently asserting it is fully resolved."""

    def test_dormant_confirmed_installation_is_watch_and_admission_relevant_today(self):
        decision = _evaluate(_obs(E, basis="EMAS installed 2008, no subsequent activity", temporality=TemporalQualifier.HISTORICAL_FACT))
        assert decision.outcome == CONFIRMED
        assert decision.is_watch_worthy is True
        assert decision.is_canonical_admission_relevant is True


# --- Adversarial-review addition: evaluator version seam (mission S22) ---

class TestEvaluatorVersionSeam:
    def test_evaluator_version_is_a_nonempty_string_constant(self):
        assert isinstance(EVALUATOR_VERSION, str) and EVALUATOR_VERSION.strip()


# --- H/I/J/K. Historical vs. current signal ---

class TestHistoricalVsCurrentSignal:
    """Attack sections 10/16 item M: a historical article must not read as
    a current opportunity equivalent to fresh evidence."""

    def test_installed_in_2008_existing_installation_is_still_confirmed(self):
        """E is exempt from the temporal discount - an install's existence
        is a present-tense structural fact regardless of install date."""
        decision = _evaluate(_obs(E, basis="Airport installed EMAS in 2008", temporality=TemporalQualifier.HISTORICAL_FACT))
        assert decision.outcome == CONFIRMED

    def test_2027_planned_replacement_project_is_strong_signal(self):
        decision = _evaluate(_obs(D, basis="2027 EMAS replacement project", temporality=TemporalQualifier.PLANNED_FUTURE_ACTION))
        assert decision.outcome == STRONG

    def test_historical_overrun_with_no_current_followon_is_insufficient_not_plausible(self):
        """Design doc S8 scenario 5: a historical (years-old) incident with
        no current follow-on evidence is INSUFFICIENT_EVIDENCE, not
        EMAS_PLAUSIBLE_SIGNAL."""
        decision = _evaluate(_obs(B, basis="decades-old overrun incident", temporality=TemporalQualifier.HISTORICAL_FACT))
        assert decision.outcome == INSUFFICIENT
        assert decision.evidence_classes_matched == frozenset()

    def test_historical_overrun_plus_generic_runway_work_is_runway_only_not_plausible(self):
        decision = _evaluate(
            _obs(B, basis="decades-old overrun incident", temporality=TemporalQualifier.HISTORICAL_FACT),
            _obs(G, basis="runway resurfacing"),
        )
        assert decision.outcome == RUNWAY_ONLY

    def test_fresh_overrun_incident_is_plausible_signal(self):
        """Design doc S8 scenario 5: a FRESH incident is EMAS_PLAUSIBLE_SIGNAL."""
        decision = _evaluate(_obs(B, basis="overrun incident", temporality=TemporalQualifier.CURRENT_STATE_AS_OF_DOCUMENT_DATE))
        assert decision.outcome == PLAUSIBLE

    def test_historical_incident_corroborated_by_a_current_followon_is_not_discounted(self):
        decision = _evaluate(
            _obs(B, basis="overrun incident 10 years ago", temporality=TemporalQualifier.HISTORICAL_FACT),
            _obs(C, basis="current alternatives analysis following the incident", temporality=TemporalQualifier.CURRENT_STATE_AS_OF_DOCUMENT_DATE),
        )
        assert B in decision.evidence_classes_matched
        assert decision.outcome == PLAUSIBLE

    def test_unknown_temporality_is_never_discounted(self):
        """Missing temporal information must not silently lose evidence
        weight - only an EXPLICIT HISTORICAL_FACT tag discounts."""
        decision = _evaluate(_obs(B, basis="RSA deficiency", temporality=TemporalQualifier.UNKNOWN))
        assert decision.outcome == PLAUSIBLE
        assert B in decision.evidence_classes_matched


# --- N. Contradiction semantics ---

class TestContradictionSemantics:
    """Attack N: positive EMAS evidence + contradictory evidence. Design
    derivation (module docstring, CONTRADICTION SEMANTICS): contradiction
    never auto-suppresses positive evidence and never auto-confirms
    anything - it is always surfaced for human review, never hidden."""

    def test_contradicting_evidence_never_downgrades_a_positive_outcome(self):
        decision = _evaluate(
            _obs(A, basis="EMAS procurement announced", polarity=ObservationPolarity.POSITIVE),
            _obs(A, basis="EMAS project later cancelled", polarity=ObservationPolarity.CONTRADICTING),
        )
        assert decision.outcome == STRONG
        assert decision.contradicting_evidence_classes == frozenset({A})
        assert "CONTRADICTING" in decision.reason

    def test_contradicting_evidence_alone_never_produces_a_positive_outcome(self):
        decision = _evaluate(_obs(A, basis="EMAS was studied and rejected as unwarranted", polarity=ObservationPolarity.CONTRADICTING))
        assert decision.outcome == INSUFFICIENT
        assert decision.evidence_classes_matched == frozenset()
        assert decision.contradicting_evidence_classes == frozenset({A})
        assert decision.is_watch_worthy is False

    def test_contradiction_is_always_surfaced_never_silently_absorbed(self):
        decision = _evaluate(
            _obs(B, basis="RSA alternatives analysis"),
            _obs(B, basis="RSA deficiency claim later withdrawn", polarity=ObservationPolarity.CONTRADICTING),
        )
        assert decision.contradicting_evidence_classes == frozenset({B})
        assert decision.outcome == PLAUSIBLE  # unaffected by the contradiction


# --- O. Multi-evidence aggregation ---

class TestMultiEvidenceAggregation:
    """Attack O, mission S11 items A-D."""

    def test_generic_runway_work_only_is_irrelevant(self):
        decision = _evaluate(_obs(G, basis="pavement reconstruction"))
        assert decision.outcome == RUNWAY_ONLY

    def test_generic_runway_work_plus_later_emas_feasibility_evidence_raises_relevance_deterministically(self):
        decision = _evaluate(_obs(G, basis="pavement reconstruction"), _obs(C, basis="EMAS feasibility study added later"))
        assert decision.outcome == PLAUSIBLE
        assert decision.is_watch_worthy is True

    def test_multiple_weak_plausible_signals_do_not_combine_into_strong_or_confirmed(self):
        """No probabilistic accumulation - three PLAUSIBLE-tier observations
        together are still only PLAUSIBLE, never magically STRONG."""
        decision = _evaluate(
            _obs(B, basis="RSA deficiency 1"), _obs(B, basis="RSA deficiency 2"), _obs(C, basis="alternatives analysis"),
        )
        assert decision.outcome == PLAUSIBLE

    def test_positive_and_contradictory_evidence_is_covered_by_contradiction_semantics(self):
        # see TestContradictionSemantics - cross-referenced here per mission S11 item C.
        decision = _evaluate(_obs(D, basis="EMAS funding grant"), _obs(D, basis="funding later withdrawn", polarity=ObservationPolarity.CONTRADICTING))
        assert decision.outcome == STRONG
        assert decision.contradicting_evidence_classes == frozenset({D})


# --- P. International / source-neutral ---

class TestInternationalSourceNeutral:
    """Attack P: no MAC/FAA/USA-only vocabulary dependency. The evaluator
    consumes only EvidenceClass tags - basis text content, in any language
    or script, never participates in classification."""

    def test_unicode_non_us_basis_text_classifies_identically(self):
        decision = _evaluate(_obs(
            A, basis="Estudo de viabilidade do Sistema de Retenção de Materiais de Engenharia (EMAS) "
            "- Aeroporto Internacional de São Paulo",
        ))
        assert decision.outcome == STRONG

    def test_unicode_plausible_signal_basis_text(self):
        decision = _evaluate(_obs(B, basis="不足な滑走路安全区域 (Insufficient runway safety area)"))
        assert decision.outcome == PLAUSIBLE

    def test_no_us_specific_terminology_referenced_anywhere_in_executable_source(self):
        import re
        source = _source_without_docstrings(erm)
        forbidden_terms = ["MAC", "FAA", "USAspending", "AIP", "BIL", "MSP", "ANE", "Granicus"]
        for term in forbidden_terms:
            assert not re.search(rf"\b{term}\b", source), (
                f"source-specific term {term!r} must not appear in the evaluator's executable code"
            )


# --- Q/R. Determinism and duplicate evidence ---

class TestDeterminism:
    """Attack Q, R: identical evidence in different order produces an
    identical result; duplicate evidence never inflates the result."""

    def test_same_evidence_different_order_produces_identical_decision(self):
        a = _obs(B, basis="RSA deficiency")
        b = _obs(C, basis="alternatives analysis")
        first = _evaluate(a, b)
        second = _evaluate(b, a)
        assert first == second

    def test_duplicate_identical_observation_does_not_change_result(self):
        one = _evaluate(_obs(A, basis="EMAS procurement"))
        many = _evaluate(*[_obs(A, basis="EMAS procurement") for _ in range(5)])
        assert one.outcome == many.outcome == STRONG
        assert one.evidence_classes_matched == many.evidence_classes_matched

    def test_duplicate_nonidentical_same_class_observations_do_not_escalate_outcome(self):
        decision = _evaluate(
            _obs(B, basis="RSA deficiency memo 1"), _obs(B, basis="RSA deficiency memo 2"), _obs(B, basis="RSA deficiency memo 3"),
        )
        assert decision.outcome == PLAUSIBLE

    def test_same_input_produces_equal_decision_objects(self):
        obs = (_obs(D, basis="EMAS grant"),)
        assert evaluate_emas_relevance(obs) == evaluate_emas_relevance(obs)


# --- S. Empty evidence ---

class TestEmptyEvidence:
    """Attack S."""

    def test_empty_observation_tuple_is_insufficient_evidence(self):
        decision = evaluate_emas_relevance(())
        assert decision.outcome == INSUFFICIENT
        assert decision.is_watch_worthy is False
        assert decision.is_canonical_admission_relevant is False
        assert decision.evidence_classes_matched == frozenset()
        assert decision.contradicting_evidence_classes == frozenset()


# --- T. Malformed / unsupported evidence ---

class TestMalformedEvidence:
    """Attack T: fail-closed, typed errors for malformed input - never a
    silent misclassification."""

    def test_evidence_class_must_be_enum_member_not_raw_string(self):
        with pytest.raises(EmasRelevanceInputError):
            EmasEvidenceObservation(evidence_class="A_EXPLICIT_EMAS", basis="x")  # type: ignore[arg-type]

    def test_temporality_must_be_enum_member(self):
        with pytest.raises(EmasRelevanceInputError):
            EmasEvidenceObservation(evidence_class=A, basis="x", temporality="historical_fact")  # type: ignore[arg-type]

    def test_polarity_must_be_enum_member(self):
        with pytest.raises(EmasRelevanceInputError):
            EmasEvidenceObservation(evidence_class=A, basis="x", polarity="POSITIVE")  # type: ignore[arg-type]

    def test_basis_cannot_be_empty(self):
        with pytest.raises(EmasRelevanceInputError):
            EmasEvidenceObservation(evidence_class=A, basis="")

    def test_basis_cannot_be_whitespace_only(self):
        with pytest.raises(EmasRelevanceInputError):
            EmasEvidenceObservation(evidence_class=A, basis="   ")

    def test_evidence_class_cannot_be_none(self):
        with pytest.raises(EmasRelevanceInputError):
            EmasEvidenceObservation(evidence_class=None, basis="x")  # type: ignore[arg-type]

    def test_temporality_cannot_be_none(self):
        with pytest.raises(EmasRelevanceInputError):
            EmasEvidenceObservation(evidence_class=A, basis="x", temporality=None)  # type: ignore[arg-type]

    def test_polarity_cannot_be_none(self):
        with pytest.raises(EmasRelevanceInputError):
            EmasEvidenceObservation(evidence_class=A, basis="x", polarity=None)  # type: ignore[arg-type]

    def test_basis_cannot_be_none(self):
        with pytest.raises((EmasRelevanceInputError, TypeError)):
            EmasEvidenceObservation(evidence_class=A, basis=None)  # type: ignore[arg-type]

    def test_wrong_object_type_entirely_raises_at_construction(self):
        with pytest.raises(EmasRelevanceInputError):
            EmasEvidenceObservation(evidence_class="not-an-enum-member", basis="x", temporality=TemporalQualifier.UNKNOWN)  # type: ignore[arg-type]


# --- Context seam ---

class TestContextSeam:
    def test_default_context_is_accepted_implicitly(self):
        decision = evaluate_emas_relevance((_obs(A, basis="EMAS procurement"),))
        assert decision.outcome == STRONG

    def test_explicit_context_instance_is_accepted(self):
        decision = evaluate_emas_relevance((_obs(A, basis="EMAS procurement"),), EmasRelevanceContext())
        assert decision.outcome == STRONG

    def test_context_instances_are_equal_regardless_of_construction_site(self):
        assert EmasRelevanceContext() == EmasRelevanceContext()


# --- Output contract ---

class TestOutputContract:
    def test_decision_exposes_exactly_the_documented_fields(self):
        decision = _evaluate(_obs(G, basis="pavement reconstruction"))
        for field_name in (
            "outcome", "reason", "evidence_classes_matched", "contradicting_evidence_classes",
            "is_watch_worthy", "is_canonical_admission_relevant",
        ):
            assert hasattr(decision, field_name)

    def test_reason_is_a_nonempty_string_and_never_contains_the_word_score(self):
        for observations in (
            (), (_obs(G, basis="x"),), (_obs(A, basis="x"),), (_obs(B, basis="x"),),
            (_obs(E, basis="x"),),
        ):
            decision = evaluate_emas_relevance(observations)
            assert isinstance(decision.reason, str) and decision.reason.strip()
            assert "score" not in decision.reason.lower()
            assert "probability" not in decision.reason.lower()


# --- Information firewall (AST-based, mirrors test_promotion_policy_evaluation.py) ---

class TestInformationFirewall:
    """Attack section 13: ERG1 must not create/update/query any ORM object,
    hit the database, or reach the network - AST-based, not substring
    matching, to avoid false positives on legitimately-similar names
    (e.g. RelevanceOutcome vs. a hypothetical ORM class)."""

    def test_no_forbidden_imports(self):
        source = inspect.getsource(erm)
        tree = ast.parse(source)
        forbidden_modules = {
            "sqlalchemy", "httpx", "requests", "urllib", "socket",
            "app.database", "app.models",
            "app.services.unknown_airport_candidate_resolution",
            "app.services.unknown_airport_candidate_persistence",
            "app.services.discovery_evidence_persistence",
            "app.services.governed_signal_creation",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(alias.name == m or alias.name.startswith(m + ".") for m in forbidden_modules)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not any(module == m or module.startswith(m + ".") for m in forbidden_modules)

    def test_no_orm_or_governance_type_names_imported(self):
        source = inspect.getsource(erm)
        tree = ast.parse(source)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
        forbidden_names = {
            "Session", "Airport", "SourceAssertion", "UnknownAirportCandidate",
            "UnknownAirportCandidateReview", "Signal", "SessionLocal",
        }
        assert imported_names.isdisjoint(forbidden_names)
        executable_source = _source_without_docstrings(erm)
        assert "app.models" not in executable_source
        assert "app.database" not in executable_source

    def test_no_current_time_dependency(self):
        source = inspect.getsource(erm)
        tree = ast.parse(source)
        forbidden_calls = {"today", "now", "utcnow"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_calls, f"forbidden call: {node.func.attr}"

    def test_no_network_or_filesystem_calls(self):
        source = inspect.getsource(erm)
        assert "open(" not in source
        assert "requests." not in source
        assert "httpx." not in source
        assert "socket." not in source

    def test_no_commit_or_session_method_calls(self):
        source = inspect.getsource(erm)
        for token in (".commit(", ".flush(", ".query(", ".add(", "session."):
            assert token not in source
