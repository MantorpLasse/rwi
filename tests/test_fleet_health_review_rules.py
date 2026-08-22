import ast
from datetime import date
from pathlib import Path

import pytest

from app.services import fleet_health_review_rules as fhr3
from app.services.fleet_health_rules import AirportCodeFact, HealthClassification, SignalRunwayAirportFact
from app.services.fleet_health_review_rules import (
    AirportRunwayCountFact,
    FleetReviewSnapshot,
    InstallationAirportLinkageFact,
    InstallationAssertionLinkRetractionFact,
    SignalLifecycleFact,
    SignalProvenanceFact,
    SourceAssertionGovernanceDecisionFact,
    SourceAssertionReviewStateFact,
    evaluate_fh_a1,
    evaluate_fh_a3,
    evaluate_fh_c3,
    evaluate_fh_c4,
    evaluate_fh_d3,
    evaluate_fh_d4,
    evaluate_fh_e1,
    evaluate_fh_e2,
    evaluate_fh_e4,
    evaluate_fh_f1,
    evaluate_fh_f2,
    evaluate_fh_f3,
    evaluate_fh_g1,
    evaluate_review_findings,
)

MODULE_PATH = Path(fhr3.__file__)


def _all_identifiers(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name)
    return names


# ---------------------------------------------------------------------------
# Purity / registry
# ---------------------------------------------------------------------------


class TestPurity:
    def test_no_forbidden_imports(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.append(node.module)
        forbidden = ("sqlalchemy", "app.database", "app.models", "os", "pathlib", "random", "uuid")
        for name in found:
            for bad in forbidden:
                assert not name.startswith(bad)

    def test_no_clock_or_random_calls(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        forbidden_calls = {"now", "utcnow", "today", "random", "uuid4"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in forbidden_calls:
                pytest.fail(f"forbidden clock/random call: .{node.attr}")

    def test_no_scoring_or_ranking_identifiers(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        identifiers = {name.lower() for name in _all_identifiers(tree)}
        for banned in ("score", "rank", "weight", "probability", "threshold"):
            assert banned not in identifiers

    def test_no_write_path_imports_or_calls(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        identifiers = _all_identifiers(tree)
        for banned in (
            "record_reviewer_action",
            "create_signal_from_approved_review",
            "link_source_assertion_to_duplicate_signal",
            "record_reconciliation_decision",
            "Session",
        ):
            assert banned not in identifiers

    def test_e2_e1_e4_never_reference_e3_fields(self):
        # E3 (construction_start/completion_date-vs-itself) stays FHC1's own
        # hard rule; SignalLifecycleFact must not smuggle construction_start
        # into this module.
        field_names = set(SignalLifecycleFact.__dataclass_fields__)
        assert "construction_start" not in field_names


class TestRegistry:
    def test_exactly_13_rules(self):
        assert len(fhr3.REVIEW_RULE_IDS) == 13

    def test_exact_rule_id_set(self):
        assert set(fhr3.REVIEW_RULE_IDS) == {
            "FH-A1", "FH-A3", "FH-C3", "FH-C4", "FH-D3", "FH-D4",
            "FH-E1", "FH-E2", "FH-E4", "FH-F1", "FH-F2", "FH-F3", "FH-G1",
        }

    def test_no_duplicate_rule_ids(self):
        assert len(fhr3.REVIEW_RULE_IDS) == len(set(fhr3.REVIEW_RULE_IDS))

    def test_deferred_rules_absent(self):
        for rule_id in ("FH-A4", "FH-B3", "FH-B4", "FH-B5", "FH-H1", "FH-H2", "FH-I1", "FH-I2"):
            assert rule_id not in fhr3.REVIEW_RULE_IDS
        for fn_name in ("evaluate_fh_a4", "evaluate_fh_b3", "evaluate_fh_b4", "evaluate_fh_b5"):
            assert not hasattr(fhr3, fn_name)

    def test_fhc1_hard_rules_absent(self):
        for rule_id in ("FH-A2", "FH-B1", "FH-B2", "FH-C1", "FH-C2", "FH-C5", "FH-D1", "FH-D2", "FH-E3", "FH-G2", "FH-G3"):
            assert rule_id not in fhr3.REVIEW_RULE_IDS

    def test_classifications_match_reviewed_design(self):
        expected = {
            "FH-A1": HealthClassification.INFORMATIONAL,
            "FH-A3": HealthClassification.DETERMINISTIC_WARNING,
            "FH-C3": HealthClassification.DETERMINISTIC_WARNING,
            "FH-C4": HealthClassification.REVIEW_REQUIRED,
            "FH-D3": HealthClassification.REVIEW_REQUIRED,
            "FH-D4": HealthClassification.REVIEW_REQUIRED,
            "FH-E1": HealthClassification.DETERMINISTIC_WARNING,
            "FH-E2": HealthClassification.DETERMINISTIC_WARNING,
            "FH-E4": HealthClassification.REVIEW_REQUIRED,
            "FH-F1": HealthClassification.INFORMATIONAL,
            "FH-F2": HealthClassification.INFORMATIONAL,
            "FH-F3": HealthClassification.REVIEW_REQUIRED,
            "FH-G1": HealthClassification.DETERMINISTIC_WARNING,
        }
        findings = evaluate_review_findings(_violation_snapshot())
        seen = {f.rule_id: f.classification for f in findings}
        for rule_id, classification in expected.items():
            assert seen[rule_id] == classification, rule_id

    def test_no_finding_ever_classified_deterministic_error(self):
        findings = evaluate_review_findings(_violation_snapshot())
        assert all(f.classification != HealthClassification.DETERMINISTIC_ERROR for f in findings)

    def test_no_finding_ever_classified_not_currently_detectable(self):
        findings = evaluate_review_findings(_violation_snapshot())
        assert all(f.classification != HealthClassification.NOT_CURRENTLY_DETECTABLE for f in findings)


# ---------------------------------------------------------------------------
# Wording discipline
# ---------------------------------------------------------------------------


class TestWordingDiscipline:
    def test_no_alarmist_or_conclusive_words_in_summaries(self):
        findings = evaluate_review_findings(_violation_snapshot())
        banned_words = ("corrupt", "duplicate", "wrong", "invalid", "broken")
        for finding in findings:
            lowered = finding.summary.lower()
            for word in banned_words:
                assert word not in lowered, (finding.rule_id, finding.summary)

    def test_review_required_findings_mention_review_or_candidate(self):
        findings = evaluate_review_findings(_violation_snapshot())
        for finding in findings:
            if finding.classification == HealthClassification.REVIEW_REQUIRED:
                lowered = finding.summary.lower()
                assert "review" in lowered or "candidate" in lowered


# ---------------------------------------------------------------------------
# Healthy / violation snapshot builders
# ---------------------------------------------------------------------------


def _healthy_snapshot() -> FleetReviewSnapshot:
    return FleetReviewSnapshot(
        airport_runway_counts=(
            AirportRunwayCountFact(airport_id=1, runway_count=2),
            AirportRunwayCountFact(airport_id=2, runway_count=1),
        ),
        airport_codes=(
            AirportCodeFact(airport_id=1, iata_code="ARN", icao_code="ESSA", faa_code=None),
            AirportCodeFact(airport_id=2, iata_code="NRT", icao_code="RJAA", faa_code=None),
        ),
        installation_airport_linkages=(
            InstallationAirportLinkageFact(installation_id=100, airport_id=1, runway_id=10, runway_end=None),
            InstallationAirportLinkageFact(installation_id=101, airport_id=2, runway_id=None, runway_end="1"),
        ),
        installation_assertion_link_retractions=(
            InstallationAssertionLinkRetractionFact(
                assertion_id=400, latest_outcome="SAME_PHYSICAL_INSTALLATION",
                had_earlier_same_physical_installation=True,
            ),
        ),
        signal_runway_airports=(
            SignalRunwayAirportFact(signal_id=300, signal_airport_id=1, runway_id=10, runway_airport_id=1),
            SignalRunwayAirportFact(signal_id=301, signal_airport_id=2, runway_id=None, runway_airport_id=None),
        ),
        signal_lifecycles=(
            SignalLifecycleFact(
                signal_id=300, airport_id=1, status="identified", planning_year=2020,
                procurement_year=2021, target_year=2022, completion_date=None,
            ),
            SignalLifecycleFact(
                signal_id=301, airport_id=2, status="completed", planning_year=None,
                procurement_year=None, target_year=None, completion_date=date(2023, 1, 1),
            ),
        ),
        signal_provenance=(
            SignalProvenanceFact(signal_id=300, source_id=500, has_governed_supporting_assertion=True),
            SignalProvenanceFact(signal_id=301, source_id=None, has_governed_supporting_assertion=False),
        ),
        source_assertion_review_states=(),
        source_assertion_governance_decisions=(
            SourceAssertionGovernanceDecisionFact(
                assertion_id=400, identity_guard_decision="ATTACH_CONFIRMED",
                intelligence_review_decision="REVIEW_REQUIRED",
                promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
            ),
        ),
    )


def _violation_snapshot() -> FleetReviewSnapshot:
    healthy = _healthy_snapshot()
    return FleetReviewSnapshot(
        airport_runway_counts=healthy.airport_runway_counts + (
            AirportRunwayCountFact(airport_id=3, runway_count=0),
        ),
        airport_codes=healthy.airport_codes + (
            AirportCodeFact(airport_id=4, iata_code=None, icao_code=None, faa_code=None),
        ),
        installation_airport_linkages=healthy.installation_airport_linkages + (
            InstallationAirportLinkageFact(installation_id=102, airport_id=5, runway_id=None, runway_end=None),
            InstallationAirportLinkageFact(installation_id=103, airport_id=5, runway_id=None, runway_end=None),
        ),
        installation_assertion_link_retractions=healthy.installation_assertion_link_retractions + (
            InstallationAssertionLinkRetractionFact(
                assertion_id=401, latest_outcome="UNRESOLVED",
                had_earlier_same_physical_installation=True,
            ),
        ),
        signal_runway_airports=healthy.signal_runway_airports + (
            SignalRunwayAirportFact(signal_id=302, signal_airport_id=6, runway_id=60, runway_airport_id=6),
            SignalRunwayAirportFact(signal_id=303, signal_airport_id=6, runway_id=60, runway_airport_id=6),
            SignalRunwayAirportFact(signal_id=304, signal_airport_id=7, runway_id=None, runway_airport_id=None),
            SignalRunwayAirportFact(signal_id=305, signal_airport_id=7, runway_id=None, runway_airport_id=None),
        ),
        signal_lifecycles=healthy.signal_lifecycles + (
            SignalLifecycleFact(
                signal_id=306, airport_id=1, status=None, planning_year=2026,
                procurement_year=2025, target_year=None, completion_date=None,
            ),
            SignalLifecycleFact(
                signal_id=307, airport_id=1, status=None, planning_year=None,
                procurement_year=2025, target_year=2024, completion_date=None,
            ),
            SignalLifecycleFact(
                signal_id=308, airport_id=1, status="completed", planning_year=None,
                procurement_year=None, target_year=2026, completion_date=None,
            ),
        ),
        signal_provenance=healthy.signal_provenance + (
            SignalProvenanceFact(signal_id=309, source_id=501, has_governed_supporting_assertion=False),
        ),
        source_assertion_review_states=(
            SourceAssertionReviewStateFact(assertion_id=402, review_state="unreviewed"),
            SourceAssertionReviewStateFact(assertion_id=403, review_state="reviewed"),
        ),
        source_assertion_governance_decisions=healthy.source_assertion_governance_decisions + (
            SourceAssertionGovernanceDecisionFact(
                assertion_id=404, identity_guard_decision="PENDING",
                intelligence_review_decision="REVIEW_REQUIRED",
                promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
            ),
        ),
    )


class TestHealthySnapshot:
    def test_zero_findings(self):
        assert evaluate_review_findings(_healthy_snapshot()) == ()

    def test_zero_findings_stable(self):
        s = _healthy_snapshot()
        assert evaluate_review_findings(s) == evaluate_review_findings(s)


class TestViolationSnapshot:
    def test_all_13_rules_fire(self):
        findings = evaluate_review_findings(_violation_snapshot())
        assert {f.rule_id for f in findings} == set(fhr3.REVIEW_RULE_IDS)


# ---------------------------------------------------------------------------
# FH-A1
# ---------------------------------------------------------------------------


class TestFhA1:
    def test_valid_nonzero_runway_count(self):
        assert evaluate_fh_a1((AirportRunwayCountFact(1, 2),)) == ()

    def test_zero_runway_count_fires(self):
        findings = evaluate_fh_a1((AirportRunwayCountFact(1, 0),))
        assert len(findings) == 1
        assert findings[0].classification == HealthClassification.INFORMATIONAL
        assert findings[0].entity_ids == (1,)

    def test_duplicate_row_produces_one_finding(self):
        findings = evaluate_fh_a1((AirportRunwayCountFact(1, 0), AirportRunwayCountFact(1, 0)))
        assert len(findings) == 1


# ---------------------------------------------------------------------------
# FH-A3
# ---------------------------------------------------------------------------


class TestFhA3:
    def test_valid_has_a_code(self):
        assert evaluate_fh_a3((AirportCodeFact(1, "ARN", None, None),)) == ()

    def test_no_code_at_all_fires(self):
        findings = evaluate_fh_a3((AirportCodeFact(1, None, None, None),))
        assert len(findings) == 1
        assert findings[0].classification == HealthClassification.DETERMINISTIC_WARNING

    def test_empty_string_codes_also_fire(self):
        findings = evaluate_fh_a3((AirportCodeFact(1, "", "", ""),))
        assert len(findings) == 1


# ---------------------------------------------------------------------------
# FH-C3
# ---------------------------------------------------------------------------


class TestFhC3:
    def test_valid_single_installation_no_finding(self):
        facts = (InstallationAirportLinkageFact(1, 5, None, None),)
        assert evaluate_fh_c3(facts) == ()

    def test_valid_multiple_installations_with_runway_links_no_finding(self):
        # Legitimate multi-installation examples that must NOT escalate.
        facts = (
            InstallationAirportLinkageFact(1, 5, 10, None),
            InstallationAirportLinkageFact(2, 5, 11, None),
        )
        assert evaluate_fh_c3(facts) == ()

    def test_valid_multiple_installations_with_free_text_runway_end_no_finding(self):
        facts = (
            InstallationAirportLinkageFact(1, 5, None, "22L"),
            InstallationAirportLinkageFact(2, 5, None, "04R"),
        )
        assert evaluate_fh_c3(facts) == ()

    def test_two_unlinked_installations_same_airport_fires(self):
        facts = (
            InstallationAirportLinkageFact(1, 5, None, None),
            InstallationAirportLinkageFact(2, 5, None, None),
        )
        findings = evaluate_fh_c3(facts)
        assert len(findings) == 1
        assert findings[0].classification == HealthClassification.DETERMINISTIC_WARNING
        assert findings[0].entity_ids == (1, 2)

    def test_unlinked_installations_different_airports_do_not_collide(self):
        facts = (
            InstallationAirportLinkageFact(1, 5, None, None),
            InstallationAirportLinkageFact(2, 6, None, None),
        )
        assert evaluate_fh_c3(facts) == ()

    def test_summary_does_not_assert_duplication(self):
        facts = (
            InstallationAirportLinkageFact(1, 5, None, None),
            InstallationAirportLinkageFact(2, 5, None, None),
        )
        findings = evaluate_fh_c3(facts)
        assert "duplicate" not in findings[0].summary.lower()


# ---------------------------------------------------------------------------
# FH-C4
# ---------------------------------------------------------------------------


class TestFhC4:
    def test_valid_no_retraction_no_finding(self):
        facts = (InstallationAssertionLinkRetractionFact(1, "SAME_PHYSICAL_INSTALLATION", True),)
        assert evaluate_fh_c4(facts) == ()

    def test_no_earlier_same_physical_installation_no_finding(self):
        facts = (InstallationAssertionLinkRetractionFact(1, "UNRESOLVED", False),)
        assert evaluate_fh_c4(facts) == ()

    def test_retraction_fires(self):
        facts = (InstallationAssertionLinkRetractionFact(1, "DIFFERENT_PHYSICAL_INSTALLATION", True),)
        findings = evaluate_fh_c4(facts)
        assert len(findings) == 1
        assert findings[0].classification == HealthClassification.REVIEW_REQUIRED


# ---------------------------------------------------------------------------
# FH-D3 / FH-D4
# ---------------------------------------------------------------------------


class TestFhD3:
    def test_valid_single_signal_no_finding(self):
        facts = (SignalRunwayAirportFact(1, 1, 10, 1),)
        assert evaluate_fh_d3(facts) == ()

    def test_shared_runway_fires(self):
        facts = (
            SignalRunwayAirportFact(1, 1, 10, 1),
            SignalRunwayAirportFact(2, 1, 10, 1),
        )
        findings = evaluate_fh_d3(facts)
        assert len(findings) == 1
        assert findings[0].classification == HealthClassification.REVIEW_REQUIRED
        assert findings[0].entity_ids == (1, 2)

    def test_different_runway_same_airport_does_not_fire(self):
        facts = (
            SignalRunwayAirportFact(1, 1, 10, 1),
            SignalRunwayAirportFact(2, 1, 11, 1),
        )
        assert evaluate_fh_d3(facts) == ()

    def test_null_runway_never_considered_by_d3(self):
        facts = (
            SignalRunwayAirportFact(1, 1, None, None),
            SignalRunwayAirportFact(2, 1, None, None),
        )
        assert evaluate_fh_d3(facts) == ()


class TestFhD4:
    def test_valid_single_signal_no_finding(self):
        facts = (SignalRunwayAirportFact(1, 1, None, None),)
        assert evaluate_fh_d4(facts) == ()

    def test_colocation_fires(self):
        facts = (
            SignalRunwayAirportFact(1, 1, None, None),
            SignalRunwayAirportFact(2, 1, None, None),
        )
        findings = evaluate_fh_d4(facts)
        assert len(findings) == 1
        assert findings[0].classification == HealthClassification.REVIEW_REQUIRED
        assert findings[0].entity_ids == (1, 2)

    def test_summary_never_asserts_duplicate(self):
        facts = (
            SignalRunwayAirportFact(1, 1, None, None),
            SignalRunwayAirportFact(2, 1, None, None),
        )
        findings = evaluate_fh_d4(facts)
        assert "duplicate" not in findings[0].summary.lower()

    def test_msp_shaped_two_signal_colocation_generic_wording(self):
        # Reproduces the structural shape of the real Signal #41/#67
        # co-location (same airport, both runway_id NULL) with fully
        # synthetic IDs - production logic must never special-case these.
        facts = (
            SignalRunwayAirportFact(9001, 45, None, None),
            SignalRunwayAirportFact(9002, 45, None, None),
        )
        findings = evaluate_fh_d4(facts)
        assert len(findings) == 1
        assert findings[0].entity_ids == (9001, 9002)
        for banned in ("MSP", "41", "67", "FAA", "Runway Safe"):
            assert banned not in findings[0].summary

    def test_signal_with_runway_id_excluded_from_d4(self):
        facts = (
            SignalRunwayAirportFact(1, 1, 10, 1),
            SignalRunwayAirportFact(2, 1, None, None),
        )
        assert evaluate_fh_d4(facts) == ()

    def test_no_production_provider_constants(self):
        # AST-identifier check, not raw substring - "FAA" legitimately
        # appears in this module's own docstring prose ("IATA/ICAO/FAA
        # code"), so a naive text scan false-positives on real code that
        # merely explains the general vocabulary rather than special-casing
        # a specific provider.
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        identifiers = _all_identifiers(tree)
        for banned in ("MSP", "MAC", "FAA", "USAspending", "RunwaySafe"):
            assert banned not in identifiers


# ---------------------------------------------------------------------------
# FH-E1 / FH-E2 / FH-E4
# ---------------------------------------------------------------------------


class TestFhE1:
    def test_valid_ordering_no_finding(self):
        facts = (SignalLifecycleFact(1, 1, None, 2020, 2021, None, None),)
        assert evaluate_fh_e1(facts) == ()

    def test_equal_years_no_finding(self):
        facts = (SignalLifecycleFact(1, 1, None, 2020, 2020, None, None),)
        assert evaluate_fh_e1(facts) == ()

    def test_planning_after_procurement_fires_warning(self):
        facts = (SignalLifecycleFact(1, 1, None, 2026, 2025, None, None),)
        findings = evaluate_fh_e1(facts)
        assert len(findings) == 1
        assert findings[0].classification == HealthClassification.DETERMINISTIC_WARNING

    def test_null_either_side_never_fires(self):
        assert evaluate_fh_e1((SignalLifecycleFact(1, 1, None, None, 2021, None, None),)) == ()
        assert evaluate_fh_e1((SignalLifecycleFact(1, 1, None, 2020, None, None, None),)) == ()

    def test_multiphase_regression_fixture_from_real_signal_3_shape(self):
        # Reproduces Signal #3's own real shape (planning_year after
        # procurement_year on a multi-phase project) with synthetic IDs -
        # must classify WARNING, never ERROR.
        facts = (SignalLifecycleFact(3, 1, "under construction", 2026, 2025, None, None),)
        findings = evaluate_fh_e1(facts)
        assert len(findings) == 1
        assert findings[0].classification == HealthClassification.DETERMINISTIC_WARNING


class TestFhE2:
    def test_valid_ordering_no_finding(self):
        assert evaluate_fh_e2((SignalLifecycleFact(1, 1, None, None, 2021, 2022, None),)) == ()

    def test_procurement_after_target_fires(self):
        findings = evaluate_fh_e2((SignalLifecycleFact(1, 1, None, None, 2025, 2024, None),))
        assert len(findings) == 1
        assert findings[0].classification == HealthClassification.DETERMINISTIC_WARNING

    def test_null_either_side_never_fires(self):
        assert evaluate_fh_e2((SignalLifecycleFact(1, 1, None, None, None, 2022, None),)) == ()


class TestFhE4:
    def test_valid_completed_with_date_no_finding(self):
        facts = (SignalLifecycleFact(1, 1, "completed", None, None, None, date(2024, 1, 1)),)
        assert evaluate_fh_e4(facts) == ()

    def test_non_completed_status_never_fires(self):
        facts = (SignalLifecycleFact(1, 1, "identified", None, None, None, None),)
        assert evaluate_fh_e4(facts) == ()

    def test_null_status_never_fires(self):
        facts = (SignalLifecycleFact(1, 1, None, None, None, None, None),)
        assert evaluate_fh_e4(facts) == ()

    def test_completed_without_date_fires_review_required(self):
        facts = (SignalLifecycleFact(65, 1, "completed", None, None, 2026, None),)
        findings = evaluate_fh_e4(facts)
        assert len(findings) == 1
        assert findings[0].classification == HealthClassification.REVIEW_REQUIRED

    def test_case_sensitive_exact_match_only(self):
        facts = (SignalLifecycleFact(1, 1, "Completed", None, None, None, None),)
        assert evaluate_fh_e4(facts) == ()


# ---------------------------------------------------------------------------
# FH-F1 / FH-F2 / FH-F3
# ---------------------------------------------------------------------------


class TestFhF1:
    def test_valid_governed_signal_not_included(self):
        facts = (SignalProvenanceFact(1, 500, True),)
        assert evaluate_fh_f1(facts) == ()

    def test_no_source_id_not_included(self):
        facts = (SignalProvenanceFact(1, None, False),)
        assert evaluate_fh_f1(facts) == ()

    def test_legacy_signal_produces_one_bucketed_finding(self):
        facts = (
            SignalProvenanceFact(1, 500, False),
            SignalProvenanceFact(2, 501, False),
            SignalProvenanceFact(3, 502, True),
        )
        findings = evaluate_fh_f1(facts)
        assert len(findings) == 1
        assert findings[0].classification == HealthClassification.INFORMATIONAL
        assert findings[0].entity_ids == (1, 2)

    def test_never_one_finding_per_signal(self):
        # 67-signal-shaped real pattern must never become 67 findings.
        facts = tuple(SignalProvenanceFact(i, 500 + i, False) for i in range(1, 68))
        findings = evaluate_fh_f1(facts)
        assert len(findings) == 1
        assert len(findings[0].entity_ids) == 67


class TestFhF2:
    def test_unreviewed_fires_informational(self):
        findings = evaluate_fh_f2((SourceAssertionReviewStateFact(1, "unreviewed"),))
        assert len(findings) == 1
        assert findings[0].classification == HealthClassification.INFORMATIONAL

    def test_reviewed_does_not_fire_f2(self):
        assert evaluate_fh_f2((SourceAssertionReviewStateFact(1, "reviewed"),)) == ()

    def test_unreviewed_candidate_linked_does_not_fire_f2(self):
        """UAC3 correction: unreviewed + unknown_airport_candidate_id set
        is governed candidate-linked evidence, not raw evidence pending
        identity-guard processing - must not fire FH-F2."""
        fact = SourceAssertionReviewStateFact(assertion_id=1, review_state="unreviewed", unknown_airport_candidate_id=7)
        assert evaluate_fh_f2((fact,)) == ()

    def test_unreviewed_truly_unattributed_still_fires_f2(self):
        """Genuinely unattributed evidence (unknown_airport_candidate_id
        also None) must remain completely unaffected by the UAC3
        correction - the pre-existing finding is not weakened."""
        fact = SourceAssertionReviewStateFact(assertion_id=1, review_state="unreviewed", unknown_airport_candidate_id=None)
        findings = evaluate_fh_f2((fact,))
        assert len(findings) == 1
        assert findings[0].classification == HealthClassification.INFORMATIONAL

    def test_mixed_candidate_linked_and_unattributed_only_unattributed_fires_f2(self):
        facts = (
            SourceAssertionReviewStateFact(assertion_id=1, review_state="unreviewed", unknown_airport_candidate_id=7),
            SourceAssertionReviewStateFact(assertion_id=2, review_state="unreviewed", unknown_airport_candidate_id=None),
        )
        findings = evaluate_fh_f2(facts)
        assert len(findings) == 1
        assert findings[0].entity_ids == (2,)


class TestFhF3:
    def test_reviewed_fires_review_required(self):
        findings = evaluate_fh_f3((SourceAssertionReviewStateFact(1, "reviewed"),))
        assert len(findings) == 1
        assert findings[0].classification == HealthClassification.REVIEW_REQUIRED

    def test_unreviewed_does_not_fire_f3(self):
        assert evaluate_fh_f3((SourceAssertionReviewStateFact(1, "unreviewed"),)) == ()

    def test_reviewed_candidate_linked_does_not_fire_f3(self):
        """UAC3 correction: reviewed + unknown_airport_candidate_id set is
        already under a separate, already-governed UAC candidate-review
        workflow - must not fire FH-F3's REVIEW_REQUIRED finding."""
        fact = SourceAssertionReviewStateFact(assertion_id=1, review_state="reviewed", unknown_airport_candidate_id=7)
        assert evaluate_fh_f3((fact,)) == ()

    def test_reviewed_truly_unattributed_still_fires_f3(self):
        """Genuinely unattributed, reviewed evidence must remain
        completely unaffected by the UAC3 correction."""
        fact = SourceAssertionReviewStateFact(assertion_id=1, review_state="reviewed", unknown_airport_candidate_id=None)
        findings = evaluate_fh_f3((fact,))
        assert len(findings) == 1
        assert findings[0].classification == HealthClassification.REVIEW_REQUIRED

    def test_mixed_candidate_linked_and_unattributed_only_unattributed_fires_f3(self):
        facts = (
            SourceAssertionReviewStateFact(assertion_id=1, review_state="reviewed", unknown_airport_candidate_id=7),
            SourceAssertionReviewStateFact(assertion_id=2, review_state="reviewed", unknown_airport_candidate_id=None),
        )
        findings = evaluate_fh_f3(facts)
        assert len(findings) == 1
        assert findings[0].entity_ids == (2,)


# ---------------------------------------------------------------------------
# FH-G1
# ---------------------------------------------------------------------------


class TestFhG1:
    def test_valid_matching_decisions_no_finding(self):
        facts = (
            SourceAssertionGovernanceDecisionFact(
                1, "ATTACH_CONFIRMED", "REVIEW_REQUIRED", "HUMAN_REVIEW_REQUIRED"
            ),
        )
        assert evaluate_fh_g1(facts) == ()

    def test_identity_mismatch_fires_warning(self):
        facts = (
            SourceAssertionGovernanceDecisionFact(
                1, "PENDING", "REVIEW_REQUIRED", "HUMAN_REVIEW_REQUIRED"
            ),
        )
        findings = evaluate_fh_g1(facts)
        assert len(findings) == 1
        assert findings[0].classification == HealthClassification.DETERMINISTIC_WARNING

    def test_intelligence_mismatch_fires_warning(self):
        facts = (
            SourceAssertionGovernanceDecisionFact(
                1, "ATTACH_CONFIRMED", "PENDING", "HUMAN_REVIEW_REQUIRED"
            ),
        )
        findings = evaluate_fh_g1(facts)
        assert len(findings) == 1

    def test_not_human_review_required_never_fires(self):
        facts = (
            SourceAssertionGovernanceDecisionFact(1, "PENDING", "PENDING", "AUTO_ELIGIBLE"),
        )
        assert evaluate_fh_g1(facts) == ()

    def test_g1_can_fire_independently_of_g2_g3(self):
        # G1 operates on entirely different fact fields than FHC1's G2/G3
        # (identity/intelligence decisions, not latest ReviewerAction) - a
        # G1 finding carries no ReviewerAction-shaped evidence at all.
        facts = (
            SourceAssertionGovernanceDecisionFact(
                1, "PENDING", "REVIEW_REQUIRED", "HUMAN_REVIEW_REQUIRED"
            ),
        )
        findings = evaluate_fh_g1(facts)
        assert "latest_action" not in findings[0].structured_evidence
        assert "duplicate_of_signal_id" not in findings[0].structured_evidence


# ---------------------------------------------------------------------------
# Dedup defense
# ---------------------------------------------------------------------------


class TestDuplicateInputRowDefense:
    def test_c3_duplicate_row_does_not_self_collide(self):
        facts = (
            InstallationAirportLinkageFact(1, 5, None, None),
            InstallationAirportLinkageFact(1, 5, None, None),
        )
        assert evaluate_fh_c3(facts) == ()

    def test_d3_duplicate_row_does_not_self_collide(self):
        facts = (
            SignalRunwayAirportFact(1, 1, 10, 1),
            SignalRunwayAirportFact(1, 1, 10, 1),
        )
        assert evaluate_fh_d3(facts) == ()

    def test_d4_duplicate_row_does_not_self_collide(self):
        facts = (
            SignalRunwayAirportFact(1, 1, None, None),
            SignalRunwayAirportFact(1, 1, None, None),
        )
        assert evaluate_fh_d4(facts) == ()

    def test_a1_duplicate_row_produces_one_finding(self):
        assert len(evaluate_fh_a1((AirportRunwayCountFact(1, 0), AirportRunwayCountFact(1, 0)))) == 1

    def test_f1_duplicate_row_does_not_double_count(self):
        facts = (
            SignalProvenanceFact(1, 500, False),
            SignalProvenanceFact(1, 500, False),
        )
        findings = evaluate_fh_f1(facts)
        assert len(findings) == 1
        assert findings[0].entity_ids == (1,)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_c3_group_entity_ids_independent_of_input_order(self):
        forward = (
            InstallationAirportLinkageFact(2, 5, None, None),
            InstallationAirportLinkageFact(1, 5, None, None),
        )
        reversed_ = tuple(reversed(forward))
        assert evaluate_fh_c3(forward)[0].entity_ids == evaluate_fh_c3(reversed_)[0].entity_ids == (1, 2)

    def test_d4_group_entity_ids_independent_of_input_order(self):
        forward = (
            SignalRunwayAirportFact(2, 1, None, None),
            SignalRunwayAirportFact(1, 1, None, None),
        )
        reversed_ = tuple(reversed(forward))
        assert evaluate_fh_d4(forward)[0].entity_ids == evaluate_fh_d4(reversed_)[0].entity_ids == (1, 2)

    def test_repeated_full_evaluation_is_equal(self):
        snapshot = _violation_snapshot()
        assert evaluate_review_findings(snapshot) == evaluate_review_findings(snapshot)


# ---------------------------------------------------------------------------
# False-positive attacks
# ---------------------------------------------------------------------------


class TestFalsePositiveAttacks:
    def test_legitimate_county_named_airport_not_flagged_by_any_rule(self):
        # A4 (name-keyword rule) is deliberately not implemented at all in
        # this module - proven structurally by field absence.
        all_field_names: set[str] = set()
        for cls in (
            AirportRunwayCountFact, AirportCodeFact, InstallationAirportLinkageFact,
            InstallationAssertionLinkRetractionFact, SignalRunwayAirportFact,
            SignalLifecycleFact, SignalProvenanceFact, SourceAssertionReviewStateFact,
            SourceAssertionGovernanceDecisionFact,
        ):
            all_field_names |= set(cls.__dataclass_fields__)
        assert "name" not in all_field_names

    def test_multiple_legitimate_installations_with_runway_links_no_warning(self):
        facts = (
            InstallationAirportLinkageFact(1, 5, 10, None),
            InstallationAirportLinkageFact(2, 5, 11, None),
            InstallationAirportLinkageFact(3, 5, 12, None),
        )
        assert evaluate_fh_c3(facts) == ()

    def test_separate_signals_same_airport_different_runway_no_review(self):
        facts = (
            SignalRunwayAirportFact(1, 1, 10, 1),
            SignalRunwayAirportFact(2, 1, 20, 1),
        )
        assert evaluate_fh_d3(facts) == ()

    def test_multiphase_project_dates_produce_warning_not_error(self):
        facts = (SignalLifecycleFact(1, 1, "under construction", 2026, 2025, None, None),)
        findings = evaluate_fh_e1(facts)
        assert len(findings) == 1
        assert findings[0].classification != HealthClassification.DETERMINISTIC_ERROR

    def test_legacy_signal_only_legacy_provenance_is_informational_not_error(self):
        facts = (SignalProvenanceFact(1, 500, False),)
        findings = evaluate_fh_f1(facts)
        assert findings[0].classification == HealthClassification.INFORMATIONAL

    def test_zero_runway_airport_is_informational_not_error(self):
        findings = evaluate_fh_a1((AirportRunwayCountFact(1, 0),))
        assert findings[0].classification == HealthClassification.INFORMATIONAL

    def test_same_title_field_does_not_exist_anywhere(self):
        # H: "same title but no structural relationship" - title is
        # structurally absent from every FHC3 fact type.
        all_field_names: set[str] = set()
        for cls in (SignalRunwayAirportFact, SignalLifecycleFact, SignalProvenanceFact):
            all_field_names |= set(cls.__dataclass_fields__)
        assert "title" not in all_field_names

    def test_same_vendor_category_year_fields_absent(self):
        # I: same vendor/category/year but no anchor must be structurally
        # impossible to evaluate - no such fields exist anywhere.
        all_field_names: set[str] = set()
        for cls in (
            SignalRunwayAirportFact, SignalLifecycleFact, SignalProvenanceFact,
            InstallationAirportLinkageFact,
        ):
            all_field_names |= set(cls.__dataclass_fields__)
        for banned in ("vendor", "category", "confirmed_vendor", "likely_supplier"):
            assert banned not in all_field_names

    def test_international_airport_no_special_handling(self):
        facts = (
            AirportCodeFact(1, "ARN", "ESSA", None),
            AirportCodeFact(2, "NRT", "RJAA", None),
        )
        assert evaluate_fh_a3(facts) == ()

    def test_unicode_name_irrelevant_since_no_name_field_exists(self):
        # Structural proof: no dataclass here even has a place to put a
        # Unicode airport name, so Unicode handling is a non-issue by
        # construction rather than by a passing string test.
        all_field_names: set[str] = set()
        for cls in (AirportRunwayCountFact, AirportCodeFact):
            all_field_names |= set(cls.__dataclass_fields__)
        assert not any("name" in f for f in all_field_names)
