import ast
from datetime import date
from pathlib import Path

import pytest

from app.services import fleet_health_rules as fhr
from app.services.fleet_health_rules import (
    AirportCodeFact,
    FleetHardInvariantSnapshot,
    HealthClassification,
    HealthFinding,
    InstallationRunwayAirportFact,
    InstallationYearFact,
    PhysicalInstallationIdentityAirportFact,
    RunwayDesignationFact,
    RunwayEndCountFact,
    SignalConstructionDateFact,
    SignalRunwayAirportFact,
    SourceAssertionGovernanceFact,
    SourceAssertionSignalAirportFact,
    evaluate_fh_a2,
    evaluate_fh_b1,
    evaluate_fh_b2,
    evaluate_fh_c1,
    evaluate_fh_c2,
    evaluate_fh_c5,
    evaluate_fh_d1,
    evaluate_fh_d2,
    evaluate_fh_e3,
    evaluate_fh_g2,
    evaluate_fh_g3,
    evaluate_hard_invariants,
)

MODULE_PATH = Path(fhr.__file__)


def _all_identifiers(tree: ast.AST) -> set[str]:
    """All actual Python identifiers used in code - deliberately excludes
    docstrings/comments, so prose explaining what this module does NOT do
    (which necessarily mentions the banned words) never false-positives a
    scan intended to catch real usage.
    """
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
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
    return names


# ---------------------------------------------------------------------------
# Purity / registry sanity
# ---------------------------------------------------------------------------


class TestPurity:
    def test_no_forbidden_imports_via_ast(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        forbidden_prefixes = (
            "sqlalchemy",
            "app.database",
            "app.models",
            "os",
            "pathlib",
            "socket",
            "random",
            "uuid",
            "requests",
            "httpx",
            "urllib",
        )
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.append(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.append(node.module)
        for name in found:
            for bad in forbidden_prefixes:
                assert not name.startswith(bad), f"forbidden import: {name}"

    def test_no_datetime_now_or_random_calls_via_ast(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        forbidden_calls = {"now", "utcnow", "today", "random", "uuid4"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in forbidden_calls:
                pytest.fail(f"forbidden clock/random call: .{node.attr}")

    def test_no_session_or_engine_symbols_referenced(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        identifiers = _all_identifiers(tree)
        for banned in ("Session", "create_engine", "sessionmaker", "Query"):
            assert banned not in identifiers

    def test_module_has_no_module_level_mutable_state_besides_registry(self):
        # The only module-level containers are the frozen rule registry and
        # small literal action-name constants - no cache, no counter. Only
        # top-level (ast.Module.body) statements count; local variables
        # inside functions are deliberately not inspected here.
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        assigned_names = set()
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigned_names.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                assigned_names.add(node.target.id)
        expected_allowed = {
            "_ACTION_MARK_DUPLICATE",
            "_ACTION_DEFER",
            "_ACTION_NEEDS_MORE_EVIDENCE",
            "_ACTION_REJECT_SIGNAL",
            "_TERMINAL_NON_CREATING_ACTIONS",
            "_RULE_EVALUATORS",
            "RULE_IDS",
        }
        # every module-level name assigned must be one we recognize (no
        # hidden extra mutable global state was added)
        assert assigned_names.issubset(expected_allowed | {"__all__"})


class TestRegistry:
    def test_exactly_11_rules(self):
        assert len(fhr.RULE_IDS) == 11

    def test_exact_rule_id_set(self):
        assert set(fhr.RULE_IDS) == {
            "FH-A2",
            "FH-B1",
            "FH-B2",
            "FH-C1",
            "FH-C2",
            "FH-C5",
            "FH-D1",
            "FH-D2",
            "FH-E3",
            "FH-G2",
            "FH-G3",
        }

    def test_no_duplicate_rule_ids(self):
        assert len(fhr.RULE_IDS) == len(set(fhr.RULE_IDS))

    def test_e1_e2_h2_absent(self):
        assert "FH-E1" not in fhr.RULE_IDS
        assert "FH-E2" not in fhr.RULE_IDS
        assert "FH-H2" not in fhr.RULE_IDS
        assert not hasattr(fhr, "evaluate_fh_e1")
        assert not hasattr(fhr, "evaluate_fh_e2")
        assert not hasattr(fhr, "evaluate_fh_h2")

    def test_registry_order_is_deterministic_and_stable(self):
        assert fhr.RULE_IDS == (
            "FH-A2",
            "FH-B1",
            "FH-B2",
            "FH-C1",
            "FH-C2",
            "FH-C5",
            "FH-D1",
            "FH-D2",
            "FH-E3",
            "FH-G2",
            "FH-G3",
        )

    def test_all_findings_classify_deterministic_error(self):
        snapshot = _violation_snapshot()
        findings = evaluate_hard_invariants(snapshot)
        assert findings
        for finding in findings:
            assert finding.classification == HealthClassification.DETERMINISTIC_ERROR


# ---------------------------------------------------------------------------
# Synthetic international fixtures (no FAA/MSP/MAC/Runway Safe/USAspending/USD)
# ---------------------------------------------------------------------------


def _healthy_snapshot() -> FleetHardInvariantSnapshot:
    """A synthetic, internally consistent multi-country fleet. Zero findings."""
    return FleetHardInvariantSnapshot(
        airport_codes=(
            AirportCodeFact(airport_id=1, iata_code="ARN", icao_code="ESSA", faa_code=None),
            AirportCodeFact(airport_id=2, iata_code="NRT", icao_code="RJAA", faa_code=None),
            AirportCodeFact(airport_id=3, iata_code=None, icao_code=None, faa_code=None),
        ),
        runway_end_counts=(
            RunwayEndCountFact(runway_id=10, airport_id=1, runway_end_count=2),
            RunwayEndCountFact(runway_id=11, airport_id=2, runway_end_count=2),
        ),
        runway_designations=(
            RunwayDesignationFact(runway_id=10, airport_id=1, designation="01/19"),
            RunwayDesignationFact(runway_id=11, airport_id=2, designation="16L/34R"),
            RunwayDesignationFact(runway_id=12, airport_id=2, designation="16R/34L"),
        ),
        installation_years=(
            InstallationYearFact(
                installation_id=100, airport_id=1, install_year=2015, replacement_year=2020
            ),
            InstallationYearFact(
                installation_id=101, airport_id=2, install_year=2018, replacement_year=None
            ),
        ),
        installation_runway_airports=(
            InstallationRunwayAirportFact(
                installation_id=100,
                installation_airport_id=1,
                runway_id=10,
                runway_airport_id=1,
            ),
            InstallationRunwayAirportFact(
                installation_id=101,
                installation_airport_id=2,
                runway_id=None,
                runway_airport_id=None,
            ),
        ),
        physical_installation_identity_airports=(
            PhysicalInstallationIdentityAirportFact(
                identity_id=200,
                identity_airport_id=1,
                runway_id=10,
                runway_airport_id=1,
                runway_end_id=None,
                runway_end_airport_id=None,
            ),
            PhysicalInstallationIdentityAirportFact(
                identity_id=201,
                identity_airport_id=2,
                runway_id=None,
                runway_airport_id=None,
                runway_end_id=None,
                runway_end_airport_id=None,
            ),
        ),
        signal_runway_airports=(
            SignalRunwayAirportFact(
                signal_id=300, signal_airport_id=1, runway_id=10, runway_airport_id=1
            ),
            SignalRunwayAirportFact(
                signal_id=301, signal_airport_id=2, runway_id=None, runway_airport_id=None
            ),
        ),
        source_assertion_signal_airports=(
            SourceAssertionSignalAirportFact(
                assertion_id=400,
                assertion_airport_id=1,
                signal_id=300,
                signal_airport_id=1,
            ),
            SourceAssertionSignalAirportFact(
                assertion_id=401,
                assertion_airport_id=None,
                signal_id=None,
                signal_airport_id=None,
            ),
        ),
        signal_construction_dates=(
            SignalConstructionDateFact(
                signal_id=300,
                airport_id=1,
                construction_start=date(2024, 1, 1),
                completion_date=date(2024, 6, 1),
            ),
            SignalConstructionDateFact(
                signal_id=301,
                airport_id=2,
                construction_start=None,
                completion_date=None,
            ),
        ),
        source_assertion_governance=(
            SourceAssertionGovernanceFact(
                assertion_id=400,
                signal_id=300,
                latest_action="APPROVE_SIGNAL",
                latest_action_duplicate_of_signal_id=None,
            ),
            SourceAssertionGovernanceFact(
                assertion_id=401,
                signal_id=None,
                latest_action=None,
                latest_action_duplicate_of_signal_id=None,
            ),
        ),
    )


def _violation_snapshot() -> FleetHardInvariantSnapshot:
    """One real violation per rule, injected on top of the healthy baseline."""
    healthy = _healthy_snapshot()
    return FleetHardInvariantSnapshot(
        airport_codes=healthy.airport_codes
        + (AirportCodeFact(airport_id=4, iata_code=None, icao_code="ESSA", faa_code=None),),
        runway_end_counts=healthy.runway_end_counts
        + (RunwayEndCountFact(runway_id=13, airport_id=1, runway_end_count=1),),
        runway_designations=healthy.runway_designations
        + (RunwayDesignationFact(runway_id=14, airport_id=1, designation="01/19"),),
        installation_years=healthy.installation_years
        + (
            InstallationYearFact(
                installation_id=102, airport_id=1, install_year=2020, replacement_year=2019
            ),
        ),
        installation_runway_airports=healthy.installation_runway_airports
        + (
            InstallationRunwayAirportFact(
                installation_id=103,
                installation_airport_id=1,
                runway_id=11,
                runway_airport_id=2,
            ),
        ),
        physical_installation_identity_airports=healthy.physical_installation_identity_airports
        + (
            PhysicalInstallationIdentityAirportFact(
                identity_id=202,
                identity_airport_id=1,
                runway_id=11,
                runway_airport_id=2,
                runway_end_id=None,
                runway_end_airport_id=None,
            ),
        ),
        signal_runway_airports=healthy.signal_runway_airports
        + (
            SignalRunwayAirportFact(
                signal_id=302, signal_airport_id=1, runway_id=11, runway_airport_id=2
            ),
        ),
        source_assertion_signal_airports=healthy.source_assertion_signal_airports
        + (
            SourceAssertionSignalAirportFact(
                assertion_id=402,
                assertion_airport_id=1,
                signal_id=301,
                signal_airport_id=2,
            ),
        ),
        signal_construction_dates=healthy.signal_construction_dates
        + (
            SignalConstructionDateFact(
                signal_id=303,
                airport_id=1,
                construction_start=date(2024, 6, 1),
                completion_date=date(2024, 1, 1),
            ),
        ),
        source_assertion_governance=healthy.source_assertion_governance
        + (
            SourceAssertionGovernanceFact(
                assertion_id=403,
                signal_id=300,
                latest_action="MARK_DUPLICATE",
                latest_action_duplicate_of_signal_id=999,
            ),
            SourceAssertionGovernanceFact(
                assertion_id=404,
                signal_id=300,
                latest_action="DEFER",
                latest_action_duplicate_of_signal_id=None,
            ),
        ),
    )


class TestHealthySyntheticBaseline:
    def test_zero_findings(self):
        assert evaluate_hard_invariants(_healthy_snapshot()) == ()

    def test_zero_findings_is_stable_across_repeated_calls(self):
        snapshot = _healthy_snapshot()
        assert evaluate_hard_invariants(snapshot) == evaluate_hard_invariants(snapshot)


class TestInjectedViolationsFireOnlyIntendedRule:
    """Each sub-test injects exactly one rule's violation onto the healthy
    baseline and asserts ONLY that rule fires - proving no hidden coupling.
    """

    def test_only_fh_a2_fires(self):
        healthy = _healthy_snapshot()
        snapshot = FleetHardInvariantSnapshot(
            **{**healthy.__dict__, "airport_codes": healthy.airport_codes + (
                AirportCodeFact(airport_id=4, iata_code=None, icao_code="ESSA", faa_code=None),
            )}
        )
        findings = evaluate_hard_invariants(snapshot)
        assert {f.rule_id for f in findings} == {"FH-A2"}

    def test_only_fh_b1_fires(self):
        healthy = _healthy_snapshot()
        snapshot = FleetHardInvariantSnapshot(
            **{**healthy.__dict__, "runway_end_counts": healthy.runway_end_counts + (
                RunwayEndCountFact(runway_id=13, airport_id=1, runway_end_count=1),
            )}
        )
        findings = evaluate_hard_invariants(snapshot)
        assert {f.rule_id for f in findings} == {"FH-B1"}

    def test_only_fh_b2_fires(self):
        healthy = _healthy_snapshot()
        snapshot = FleetHardInvariantSnapshot(
            **{**healthy.__dict__, "runway_designations": healthy.runway_designations + (
                RunwayDesignationFact(runway_id=14, airport_id=1, designation="01/19"),
            )}
        )
        findings = evaluate_hard_invariants(snapshot)
        assert {f.rule_id for f in findings} == {"FH-B2"}

    def test_only_fh_c1_fires(self):
        healthy = _healthy_snapshot()
        snapshot = FleetHardInvariantSnapshot(
            **{**healthy.__dict__, "installation_years": healthy.installation_years + (
                InstallationYearFact(
                    installation_id=102, airport_id=1, install_year=2020, replacement_year=2019
                ),
            )}
        )
        findings = evaluate_hard_invariants(snapshot)
        assert {f.rule_id for f in findings} == {"FH-C1"}

    def test_only_fh_c2_fires(self):
        healthy = _healthy_snapshot()
        snapshot = FleetHardInvariantSnapshot(
            **{**healthy.__dict__, "installation_runway_airports": healthy.installation_runway_airports + (
                InstallationRunwayAirportFact(
                    installation_id=103,
                    installation_airport_id=1,
                    runway_id=11,
                    runway_airport_id=2,
                ),
            )}
        )
        findings = evaluate_hard_invariants(snapshot)
        assert {f.rule_id for f in findings} == {"FH-C2"}

    def test_only_fh_c5_fires(self):
        healthy = _healthy_snapshot()
        snapshot = FleetHardInvariantSnapshot(
            **{**healthy.__dict__, "physical_installation_identity_airports": healthy.physical_installation_identity_airports + (
                PhysicalInstallationIdentityAirportFact(
                    identity_id=202,
                    identity_airport_id=1,
                    runway_id=11,
                    runway_airport_id=2,
                    runway_end_id=None,
                    runway_end_airport_id=None,
                ),
            )}
        )
        findings = evaluate_hard_invariants(snapshot)
        assert {f.rule_id for f in findings} == {"FH-C5"}

    def test_only_fh_d1_fires(self):
        healthy = _healthy_snapshot()
        snapshot = FleetHardInvariantSnapshot(
            **{**healthy.__dict__, "signal_runway_airports": healthy.signal_runway_airports + (
                SignalRunwayAirportFact(
                    signal_id=302, signal_airport_id=1, runway_id=11, runway_airport_id=2
                ),
            )}
        )
        findings = evaluate_hard_invariants(snapshot)
        assert {f.rule_id for f in findings} == {"FH-D1"}

    def test_only_fh_d2_fires(self):
        healthy = _healthy_snapshot()
        snapshot = FleetHardInvariantSnapshot(
            **{**healthy.__dict__, "source_assertion_signal_airports": healthy.source_assertion_signal_airports + (
                SourceAssertionSignalAirportFact(
                    assertion_id=402,
                    assertion_airport_id=1,
                    signal_id=301,
                    signal_airport_id=2,
                ),
            )}
        )
        findings = evaluate_hard_invariants(snapshot)
        assert {f.rule_id for f in findings} == {"FH-D2"}

    def test_only_fh_e3_fires(self):
        healthy = _healthy_snapshot()
        snapshot = FleetHardInvariantSnapshot(
            **{**healthy.__dict__, "signal_construction_dates": healthy.signal_construction_dates + (
                SignalConstructionDateFact(
                    signal_id=303,
                    airport_id=1,
                    construction_start=date(2024, 6, 1),
                    completion_date=date(2024, 1, 1),
                ),
            )}
        )
        findings = evaluate_hard_invariants(snapshot)
        assert {f.rule_id for f in findings} == {"FH-E3"}

    def test_only_fh_g2_fires(self):
        healthy = _healthy_snapshot()
        snapshot = FleetHardInvariantSnapshot(
            **{**healthy.__dict__, "source_assertion_governance": healthy.source_assertion_governance + (
                SourceAssertionGovernanceFact(
                    assertion_id=403,
                    signal_id=300,
                    latest_action="MARK_DUPLICATE",
                    latest_action_duplicate_of_signal_id=999,
                ),
            )}
        )
        findings = evaluate_hard_invariants(snapshot)
        assert {f.rule_id for f in findings} == {"FH-G2"}

    def test_only_fh_g3_fires(self):
        healthy = _healthy_snapshot()
        snapshot = FleetHardInvariantSnapshot(
            **{**healthy.__dict__, "source_assertion_governance": healthy.source_assertion_governance + (
                SourceAssertionGovernanceFact(
                    assertion_id=404,
                    signal_id=300,
                    latest_action="DEFER",
                    latest_action_duplicate_of_signal_id=None,
                ),
            )}
        )
        findings = evaluate_hard_invariants(snapshot)
        assert {f.rule_id for f in findings} == {"FH-G3"}

    def test_all_violations_together_fire_all_11_rules(self):
        findings = evaluate_hard_invariants(_violation_snapshot())
        assert {f.rule_id for f in findings} == set(fhr.RULE_IDS)


# ---------------------------------------------------------------------------
# FH-A2
# ---------------------------------------------------------------------------


class TestFhA2:
    def test_valid_non_trigger_distinct_codes(self):
        facts = (
            AirportCodeFact(1, "ARN", "ESSA", None),
            AirportCodeFact(2, "NRT", "RJAA", None),
        )
        assert evaluate_fh_a2(facts) == ()

    def test_true_violation_duplicate_icao(self):
        facts = (
            AirportCodeFact(1, None, "ESSA", None),
            AirportCodeFact(2, None, "ESSA", None),
        )
        findings = evaluate_fh_a2(facts)
        assert len(findings) == 1
        assert findings[0].entity_ids == (1, 2)
        assert findings[0].structured_evidence["code_value"] == "ESSA"

    def test_null_codes_never_collide(self):
        facts = (
            AirportCodeFact(1, None, None, None),
            AirportCodeFact(2, None, None, None),
            AirportCodeFact(3, None, None, None),
        )
        assert evaluate_fh_a2(facts) == ()

    def test_empty_string_codes_never_collide(self):
        facts = (
            AirportCodeFact(1, "", "", ""),
            AirportCodeFact(2, "", "", ""),
        )
        assert evaluate_fh_a2(facts) == ()

    def test_whitespace_codes_are_compared_literally_not_collapsed(self):
        # The reviewed design only authorizes excluding NULL and exact "".
        # A whitespace value is a real literal value and IS compared.
        facts = (
            AirportCodeFact(1, None, " ", None),
            AirportCodeFact(2, None, " ", None),
        )
        findings = evaluate_fh_a2(facts)
        assert len(findings) == 1
        assert findings[0].structured_evidence["code_value"] == " "

    def test_different_whitespace_values_do_not_collide(self):
        facts = (
            AirportCodeFact(1, None, " ", None),
            AirportCodeFact(2, None, "  ", None),
        )
        assert evaluate_fh_a2(facts) == ()

    def test_case_is_significant_not_folded(self):
        facts = (
            AirportCodeFact(1, None, "essa", None),
            AirportCodeFact(2, None, "ESSA", None),
        )
        assert evaluate_fh_a2(facts) == ()

    def test_conflicting_code_values_across_all_three_types_each_checked_independently(self):
        facts = (
            AirportCodeFact(1, "AAA", "KAAA", "AAA"),
            AirportCodeFact(2, "AAA", "KBBB", "BBB"),
        )
        findings = evaluate_fh_a2(facts)
        assert len(findings) == 1
        assert findings[0].structured_evidence["code_type"] == "iata_code"

    def test_three_way_collision_yields_one_finding_with_all_ids(self):
        facts = (
            AirportCodeFact(1, None, "ESSA", None),
            AirportCodeFact(2, None, "ESSA", None),
            AirportCodeFact(3, None, "ESSA", None),
        )
        findings = evaluate_fh_a2(facts)
        assert len(findings) == 1
        assert findings[0].entity_ids == (1, 2, 3)

    def test_deterministic_regardless_of_input_order(self):
        facts_a = (
            AirportCodeFact(1, None, "ESSA", None),
            AirportCodeFact(2, None, "ESSA", None),
        )
        facts_b = tuple(reversed(facts_a))
        findings_a = evaluate_fh_a2(facts_a)
        findings_b = evaluate_fh_a2(facts_b)
        assert findings_a[0].entity_ids == findings_b[0].entity_ids == (1, 2)


# ---------------------------------------------------------------------------
# FH-B1
# ---------------------------------------------------------------------------


class TestFhB1:
    def test_valid_two_ends_no_finding(self):
        assert evaluate_fh_b1((RunwayEndCountFact(1, 1, 2),)) == ()

    def test_true_violation_one_end(self):
        findings = evaluate_fh_b1((RunwayEndCountFact(1, 1, 1),))
        assert len(findings) == 1
        assert findings[0].rule_id == "FH-B1"

    def test_true_violation_three_ends(self):
        findings = evaluate_fh_b1((RunwayEndCountFact(1, 1, 3),))
        assert len(findings) == 1

    def test_true_violation_zero_ends(self):
        findings = evaluate_fh_b1((RunwayEndCountFact(1, 1, 0),))
        assert len(findings) == 1

    def test_zero_runway_legacy_airport_produces_no_facts_and_no_finding(self):
        # An airport with zero runways contributes NO RunwayEndCountFact at
        # all (there is no Runway row to describe) - this must never be
        # coerced into a fabricated violation.
        assert evaluate_fh_b1(()) == ()


# ---------------------------------------------------------------------------
# FH-B2
# ---------------------------------------------------------------------------


class TestFhB2:
    def test_valid_distinct_designations_no_finding(self):
        facts = (
            RunwayDesignationFact(1, 1, "01/19"),
            RunwayDesignationFact(2, 1, "10/28"),
        )
        assert evaluate_fh_b2(facts) == ()

    def test_true_violation_duplicate_designation_same_airport(self):
        facts = (
            RunwayDesignationFact(1, 1, "01/19"),
            RunwayDesignationFact(2, 1, "01/19"),
        )
        findings = evaluate_fh_b2(facts)
        assert len(findings) == 1
        assert findings[0].entity_ids == (1, 2)

    def test_same_designation_different_airports_does_not_collide(self):
        facts = (
            RunwayDesignationFact(1, 1, "01/19"),
            RunwayDesignationFact(2, 2, "01/19"),
        )
        assert evaluate_fh_b2(facts) == ()

    def test_case_is_significant_not_folded(self):
        facts = (
            RunwayDesignationFact(1, 1, "01/19"),
            RunwayDesignationFact(2, 1, "01/19 "),
        )
        # trailing whitespace makes this a different literal string - no text
        # normalization is performed
        assert evaluate_fh_b2(facts) == ()


# ---------------------------------------------------------------------------
# FH-C1
# ---------------------------------------------------------------------------


class TestFhC1:
    def test_valid_replacement_after_install(self):
        assert evaluate_fh_c1((InstallationYearFact(1, 1, 2015, 2020),)) == ()

    def test_valid_equal_years_not_a_violation(self):
        assert evaluate_fh_c1((InstallationYearFact(1, 1, 2015, 2015),)) == ()

    def test_true_violation_replacement_before_install(self):
        findings = evaluate_fh_c1((InstallationYearFact(1, 1, 2020, 2019),))
        assert len(findings) == 1

    def test_null_install_year_never_triggers(self):
        assert evaluate_fh_c1((InstallationYearFact(1, 1, None, 2019),)) == ()

    def test_null_replacement_year_never_triggers(self):
        # This is the real-data-dominant case: 0/149 real rows populate
        # replacement_year at all.
        assert evaluate_fh_c1((InstallationYearFact(1, 1, 2015, None),)) == ()

    def test_both_null_never_triggers(self):
        assert evaluate_fh_c1((InstallationYearFact(1, 1, None, None),)) == ()


# ---------------------------------------------------------------------------
# FH-C2
# ---------------------------------------------------------------------------


class TestFhC2:
    def test_valid_matching_airport(self):
        assert evaluate_fh_c2((InstallationRunwayAirportFact(1, 1, 10, 1),)) == ()

    def test_true_violation_mismatched_airport(self):
        findings = evaluate_fh_c2((InstallationRunwayAirportFact(1, 1, 10, 2),))
        assert len(findings) == 1

    def test_no_runway_link_is_absence_not_contradiction(self):
        assert evaluate_fh_c2((InstallationRunwayAirportFact(1, 1, None, None),)) == ()

    def test_multiple_legitimate_installation_rows_do_not_trigger(self):
        # The real 18-airport "two installation rows, both airport-only"
        # pattern must never become a hard error here - that belongs to
        # FHC3's FH-C3 warning, not this module.
        facts = (
            InstallationRunwayAirportFact(1, 5, None, None),
            InstallationRunwayAirportFact(2, 5, None, None),
        )
        assert evaluate_fh_c2(facts) == ()


# ---------------------------------------------------------------------------
# FH-C5
# ---------------------------------------------------------------------------


class TestFhC5:
    def test_valid_matching_runway_and_end(self):
        facts = (
            PhysicalInstallationIdentityAirportFact(1, 1, 10, 1, 20, 1),
        )
        assert evaluate_fh_c5(facts) == ()

    def test_valid_no_links_at_all_deliberate_design_state(self):
        # All real PhysicalInstallationIdentity.runway_id values are NULL by
        # deliberate design per the reviewed doc - must never trigger.
        facts = (PhysicalInstallationIdentityAirportFact(1, 1, None, None, None, None),)
        assert evaluate_fh_c5(facts) == ()

    def test_true_violation_runway_mismatch(self):
        facts = (PhysicalInstallationIdentityAirportFact(1, 1, 10, 2, None, None),)
        findings = evaluate_fh_c5(facts)
        assert len(findings) == 1

    def test_true_violation_runway_end_mismatch(self):
        facts = (PhysicalInstallationIdentityAirportFact(1, 1, None, None, 20, 2),)
        findings = evaluate_fh_c5(facts)
        assert len(findings) == 1

    def test_vendor_or_year_equality_is_structurally_impossible_to_use(self):
        # The dataclass has no vendor/year/name field at all - this is
        # enforced by the type itself, not by rule logic.
        field_names = set(PhysicalInstallationIdentityAirportFact.__dataclass_fields__)
        assert field_names == {
            "identity_id",
            "identity_airport_id",
            "runway_id",
            "runway_airport_id",
            "runway_end_id",
            "runway_end_airport_id",
        }


# ---------------------------------------------------------------------------
# FH-D1
# ---------------------------------------------------------------------------


class TestFhD1:
    def test_valid_matching_airport(self):
        assert evaluate_fh_d1((SignalRunwayAirportFact(1, 1, 10, 1),)) == ()

    def test_true_violation_mismatched_airport(self):
        findings = evaluate_fh_d1((SignalRunwayAirportFact(1, 1, 10, 2),))
        assert len(findings) == 1

    def test_missing_runway_is_absence_not_contradiction(self):
        assert evaluate_fh_d1((SignalRunwayAirportFact(1, 1, None, None),)) == ()

    def test_title_or_vendor_fields_are_structurally_absent(self):
        field_names = set(SignalRunwayAirportFact.__dataclass_fields__)
        assert "title" not in field_names
        assert "supplier" not in field_names
        assert "confirmed_vendor" not in field_names
        assert "estimated_total_value_usd" not in field_names


# ---------------------------------------------------------------------------
# FH-D2
# ---------------------------------------------------------------------------


class TestFhD2:
    def test_valid_matching_airport(self):
        assert evaluate_fh_d2((SourceAssertionSignalAirportFact(1, 1, 100, 1),)) == ()

    def test_true_violation_mismatched_airport(self):
        findings = evaluate_fh_d2((SourceAssertionSignalAirportFact(1, 1, 100, 2),))
        assert len(findings) == 1

    def test_no_linked_signal_is_legacy_informational_not_error(self):
        # 67 of 68 real Signals / 221 of 222 real SourceAssertions have no
        # governed link at all - this must never be treated as a hard error.
        assert evaluate_fh_d2((SourceAssertionSignalAirportFact(1, 1, None, None),)) == ()

    def test_no_attributed_airport_on_assertion_never_triggers(self):
        assert evaluate_fh_d2((SourceAssertionSignalAirportFact(1, None, 100, 1),)) == ()


# ---------------------------------------------------------------------------
# FH-E3
# ---------------------------------------------------------------------------


class TestFhE3:
    def test_valid_chronology(self):
        facts = (
            SignalConstructionDateFact(1, 1, date(2024, 1, 1), date(2024, 6, 1)),
        )
        assert evaluate_fh_e3(facts) == ()

    def test_equal_dates_allowed(self):
        facts = (
            SignalConstructionDateFact(1, 1, date(2024, 1, 1), date(2024, 1, 1)),
        )
        assert evaluate_fh_e3(facts) == ()

    def test_true_violation_impossible_chronology(self):
        facts = (
            SignalConstructionDateFact(1, 1, date(2024, 6, 1), date(2024, 1, 1)),
        )
        findings = evaluate_fh_e3(facts)
        assert len(findings) == 1

    def test_null_construction_start_never_triggers(self):
        facts = (SignalConstructionDateFact(1, 1, None, date(2024, 1, 1)),)
        assert evaluate_fh_e3(facts) == ()

    def test_null_completion_date_never_triggers(self):
        facts = (SignalConstructionDateFact(1, 1, date(2024, 1, 1), None),)
        assert evaluate_fh_e3(facts) == ()

    def test_planning_and_procurement_year_fields_are_structurally_absent(self):
        # FH-E1/E2 must never be reachable through this dataclass.
        field_names = set(SignalConstructionDateFact.__dataclass_fields__)
        assert "planning_year" not in field_names
        assert "procurement_year" not in field_names
        assert "target_year" not in field_names

    def test_no_current_date_inference(self):
        # There is no way to pass "today" into this evaluator at all - the
        # function signature accepts only the fact tuple.
        import inspect

        sig = inspect.signature(evaluate_fh_e3)
        assert list(sig.parameters) == ["facts"]


# ---------------------------------------------------------------------------
# FH-G2 / FH-G3
# ---------------------------------------------------------------------------


class TestFhG2:
    def test_valid_matching_duplicate_target(self):
        facts = (SourceAssertionGovernanceFact(1, 50, "MARK_DUPLICATE", 50),)
        assert evaluate_fh_g2(facts) == ()

    def test_true_violation_mismatched_duplicate_target(self):
        facts = (SourceAssertionGovernanceFact(1, 50, "MARK_DUPLICATE", 99),)
        findings = evaluate_fh_g2(facts)
        assert len(findings) == 1

    def test_true_violation_signal_id_null_while_duplicate_target_set(self):
        facts = (SourceAssertionGovernanceFact(1, None, "MARK_DUPLICATE", 99),)
        findings = evaluate_fh_g2(facts)
        assert len(findings) == 1

    def test_non_mark_duplicate_action_never_triggers(self):
        facts = (SourceAssertionGovernanceFact(1, 50, "APPROVE_SIGNAL", None),)
        assert evaluate_fh_g2(facts) == ()

    def test_no_action_recorded_yet_never_triggers(self):
        facts = (SourceAssertionGovernanceFact(1, None, None, None),)
        assert evaluate_fh_g2(facts) == ()


class TestFhG3:
    def test_valid_defer_with_null_signal(self):
        facts = (SourceAssertionGovernanceFact(1, None, "DEFER", None),)
        assert evaluate_fh_g3(facts) == ()

    def test_true_violation_defer_with_signal_set(self):
        facts = (SourceAssertionGovernanceFact(1, 50, "DEFER", None),)
        findings = evaluate_fh_g3(facts)
        assert len(findings) == 1

    def test_true_violation_needs_more_evidence_with_signal_set(self):
        facts = (SourceAssertionGovernanceFact(1, 50, "NEEDS_MORE_EVIDENCE", None),)
        findings = evaluate_fh_g3(facts)
        assert len(findings) == 1

    def test_true_violation_reject_signal_with_signal_set(self):
        facts = (SourceAssertionGovernanceFact(1, 50, "REJECT_SIGNAL", None),)
        findings = evaluate_fh_g3(facts)
        assert len(findings) == 1

    def test_approve_signal_action_never_triggers_g3(self):
        facts = (SourceAssertionGovernanceFact(1, 50, "APPROVE_SIGNAL", None),)
        assert evaluate_fh_g3(facts) == ()

    def test_mark_duplicate_action_is_g2_territory_not_g3(self):
        facts = (SourceAssertionGovernanceFact(1, 50, "MARK_DUPLICATE", 50),)
        assert evaluate_fh_g3(facts) == ()

    def test_g2_and_g3_are_evaluated_independently_not_conflated(self):
        # A single fact can be relevant to at most one of G2/G3 given the
        # real REVIEWER_ACTIONS vocabulary, but the two functions must never
        # share hidden state or call into one another.
        import inspect

        assert "evaluate_fh_g2" not in inspect.getsource(evaluate_fh_g3)
        assert "evaluate_fh_g3" not in inspect.getsource(evaluate_fh_g2)


# ---------------------------------------------------------------------------
# Cross-rule false-positive firewall attacks
# ---------------------------------------------------------------------------


class TestFalsePositiveFirewalls:
    def test_no_airport_name_field_anywhere_in_module(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for banned in ("name", "County", "Authority", "iata_code_lower"):
            # 'name' alone is too broad (matches e.g. "field_name"); check the
            # actual dataclasses instead of grepping loosely.
            pass
        all_fields: set[str] = set()
        for cls in (
            AirportCodeFact,
            RunwayEndCountFact,
            RunwayDesignationFact,
            InstallationYearFact,
            InstallationRunwayAirportFact,
            PhysicalInstallationIdentityAirportFact,
            SignalRunwayAirportFact,
            SourceAssertionSignalAirportFact,
            SignalConstructionDateFact,
            SourceAssertionGovernanceFact,
        ):
            all_fields |= set(cls.__dataclass_fields__)
        assert "name" not in all_fields
        assert "title" not in all_fields

    def test_no_title_similarity_or_keyword_logic(self):
        # AST-identifier check, not a raw substring scan (a raw scan would
        # false-positive on this module's own docstring prose describing
        # what it deliberately excludes).
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        identifiers = {name.lower() for name in _all_identifiers(tree)}
        for banned_token in ("difflib", "sequencematcher", "fuzz", "similarity"):
            assert banned_token not in identifiers
        # difflib/fuzzy-matching also can't have been imported at all
        imported_modules = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "difflib" not in imported_modules

    def test_no_financial_amount_fields(self):
        all_fields: set[str] = set()
        for cls in (
            SignalRunwayAirportFact,
            SignalConstructionDateFact,
        ):
            all_fields |= set(cls.__dataclass_fields__)
        assert not any("value" in f or "usd" in f.lower() or "amount" in f for f in all_fields)

    def test_no_vendor_or_supplier_fields(self):
        all_fields: set[str] = set()
        for cls in (
            InstallationYearFact,
            InstallationRunwayAirportFact,
            PhysicalInstallationIdentityAirportFact,
            SignalRunwayAirportFact,
        ):
            all_fields |= set(cls.__dataclass_fields__)
        assert not any("vendor" in f or "supplier" in f or "manufacturer" in f for f in all_fields)

    def test_no_category_status_confidence_fields(self):
        all_fields: set[str] = set()
        for cls in (SignalRunwayAirportFact, SignalConstructionDateFact):
            all_fields |= set(cls.__dataclass_fields__)
        assert not any(f in ("category", "status", "confidence") for f in all_fields)

    def test_no_row_age_or_created_at_fields(self):
        all_fields: set[str] = set()
        for cls in (
            AirportCodeFact,
            RunwayEndCountFact,
            InstallationYearFact,
            SourceAssertionGovernanceFact,
        ):
            all_fields |= set(cls.__dataclass_fields__)
        assert not any("created_at" in f or "age" in f for f in all_fields)

    def test_multiple_installation_rows_never_become_duplicate_error(self):
        # This is C3/D4 territory (FHC3, warning/review tier) - structurally
        # absent from FHC1. Confirm no such evaluator exists here.
        assert not hasattr(fhr, "evaluate_fh_c3")
        assert not hasattr(fhr, "evaluate_fh_d3")
        assert not hasattr(fhr, "evaluate_fh_d4")
        assert not hasattr(fhr, "evaluate_fh_a4")

    def test_legacy_provenance_only_signal_produces_no_hard_error(self):
        # Mirrors the real fleet: 67/68 Signals have source_id-only
        # provenance and no governed SourceAssertion link at all.
        facts = (
            SourceAssertionSignalAirportFact(
                assertion_id=1, assertion_airport_id=None, signal_id=None, signal_airport_id=None
            ),
        )
        assert evaluate_fh_d2(facts) == ()

    def test_no_d4_colocation_or_reconciliation_logic_present(self):
        # Checked against actual identifiers only (not docstring prose,
        # which legitimately explains what this module deliberately does
        # NOT do and therefore mentions these words in English sentences).
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        identifiers = {name.lower() for name in _all_identifiers(tree)}
        for banned_token in (
            "reconciliation",
            "fingerprint",
            "candidate",
            "possible_existing_signal_match",
        ):
            assert banned_token not in identifiers


# ---------------------------------------------------------------------------
# Duplicate-input-row defense (regression tests added at the FHC1 critical
# review checkpoint: a future FHC2 adapter's SQL JOIN could easily fan out
# and return the same entity twice; a repeated fact for one entity must
# never produce two findings, and must never make a batch/grouping rule
# report an entity as "colliding with itself").
# ---------------------------------------------------------------------------


class TestDuplicateInputRowDefense:
    def test_a2_duplicate_row_for_same_airport_produces_no_self_collision(self):
        facts = (
            AirportCodeFact(1, None, "ESSA", None),
            AirportCodeFact(1, None, "ESSA", None),
        )
        assert evaluate_fh_a2(facts) == ()

    def test_a2_duplicate_row_does_not_corrupt_a_genuine_collision(self):
        facts = (
            AirportCodeFact(1, None, "ESSA", None),
            AirportCodeFact(2, None, "ESSA", None),
            AirportCodeFact(1, None, "ESSA", None),
        )
        findings = evaluate_fh_a2(facts)
        assert len(findings) == 1
        assert findings[0].entity_ids == (1, 2)

    def test_b1_duplicate_row_for_same_runway_produces_exactly_one_finding(self):
        facts = (RunwayEndCountFact(1, 1, 1), RunwayEndCountFact(1, 1, 1))
        findings = evaluate_fh_b1(facts)
        assert len(findings) == 1

    def test_b2_duplicate_row_for_same_runway_produces_no_self_collision(self):
        facts = (
            RunwayDesignationFact(10, 1, "01/19"),
            RunwayDesignationFact(10, 1, "01/19"),
        )
        assert evaluate_fh_b2(facts) == ()

    def test_b2_duplicate_row_does_not_corrupt_a_genuine_collision(self):
        facts = (
            RunwayDesignationFact(10, 1, "01/19"),
            RunwayDesignationFact(11, 1, "01/19"),
            RunwayDesignationFact(10, 1, "01/19"),
        )
        findings = evaluate_fh_b2(facts)
        assert len(findings) == 1
        assert findings[0].entity_ids == (10, 11)

    def test_c1_duplicate_row_produces_exactly_one_finding(self):
        facts = (
            InstallationYearFact(1, 1, 2020, 2019),
            InstallationYearFact(1, 1, 2020, 2019),
        )
        assert len(evaluate_fh_c1(facts)) == 1

    def test_c2_duplicate_row_produces_exactly_one_finding(self):
        facts = (
            InstallationRunwayAirportFact(1, 1, 10, 2),
            InstallationRunwayAirportFact(1, 1, 10, 2),
        )
        assert len(evaluate_fh_c2(facts)) == 1

    def test_c5_duplicate_row_produces_exactly_one_finding(self):
        facts = (
            PhysicalInstallationIdentityAirportFact(1, 1, 10, 2, None, None),
            PhysicalInstallationIdentityAirportFact(1, 1, 10, 2, None, None),
        )
        assert len(evaluate_fh_c5(facts)) == 1

    def test_d1_duplicate_row_produces_exactly_one_finding(self):
        facts = (
            SignalRunwayAirportFact(1, 1, 10, 2),
            SignalRunwayAirportFact(1, 1, 10, 2),
        )
        assert len(evaluate_fh_d1(facts)) == 1

    def test_d2_duplicate_row_produces_exactly_one_finding(self):
        facts = (
            SourceAssertionSignalAirportFact(1, 1, 100, 2),
            SourceAssertionSignalAirportFact(1, 1, 100, 2),
        )
        assert len(evaluate_fh_d2(facts)) == 1

    def test_e3_duplicate_row_produces_exactly_one_finding(self):
        facts = (
            SignalConstructionDateFact(1, 1, date(2024, 6, 1), date(2024, 1, 1)),
            SignalConstructionDateFact(1, 1, date(2024, 6, 1), date(2024, 1, 1)),
        )
        assert len(evaluate_fh_e3(facts)) == 1

    def test_g2_duplicate_row_produces_exactly_one_finding(self):
        facts = (
            SourceAssertionGovernanceFact(1, 50, "MARK_DUPLICATE", 99),
            SourceAssertionGovernanceFact(1, 50, "MARK_DUPLICATE", 99),
        )
        assert len(evaluate_fh_g2(facts)) == 1

    def test_g3_duplicate_row_produces_exactly_one_finding(self):
        facts = (
            SourceAssertionGovernanceFact(1, 50, "DEFER", None),
            SourceAssertionGovernanceFact(1, 50, "DEFER", None),
        )
        assert len(evaluate_fh_g3(facts)) == 1

    def test_dedupe_keeps_first_occurrence_deterministically(self):
        # If an adapter bug ever produced two DIFFERENT facts for the same
        # entity id (not just an identical duplicate), dedup must still be
        # deterministic (first-seen wins) rather than order-dependent noise.
        facts_a = (
            RunwayEndCountFact(1, 1, 1),
            RunwayEndCountFact(1, 1, 2),
        )
        facts_b = (
            RunwayEndCountFact(1, 1, 2),
            RunwayEndCountFact(1, 1, 1),
        )
        assert len(evaluate_fh_b1(facts_a)) == 1  # first row (count=1) kept -> violation
        assert len(evaluate_fh_b1(facts_b)) == 0  # first row (count=2) kept -> no violation


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_snapshot_twice_is_equal(self):
        snapshot = _violation_snapshot()
        assert evaluate_hard_invariants(snapshot) == evaluate_hard_invariants(snapshot)

    def test_a2_finding_entity_ids_independent_of_input_order(self):
        facts_forward = (
            AirportCodeFact(3, None, "ESSA", None),
            AirportCodeFact(1, None, "ESSA", None),
            AirportCodeFact(2, None, "ESSA", None),
        )
        facts_reversed = tuple(reversed(facts_forward))
        findings_forward = evaluate_fh_a2(facts_forward)
        findings_reversed = evaluate_fh_a2(facts_reversed)
        assert findings_forward[0].entity_ids == findings_reversed[0].entity_ids == (1, 2, 3)

    def test_b2_finding_entity_ids_independent_of_input_order(self):
        facts_forward = (
            RunwayDesignationFact(2, 1, "01/19"),
            RunwayDesignationFact(1, 1, "01/19"),
        )
        facts_reversed = tuple(reversed(facts_forward))
        findings_forward = evaluate_fh_b2(facts_forward)
        findings_reversed = evaluate_fh_b2(facts_reversed)
        assert findings_forward[0].entity_ids == findings_reversed[0].entity_ids == (1, 2)


# ---------------------------------------------------------------------------
# HealthFinding shape
# ---------------------------------------------------------------------------


class TestHealthFindingShape:
    def test_finding_is_frozen(self):
        finding = evaluate_fh_b1((RunwayEndCountFact(1, 1, 1),))[0]
        with pytest.raises((AttributeError, TypeError)):
            finding.rule_id = "changed"  # type: ignore[misc]

    def test_no_score_or_confidence_percentage_field(self):
        field_names = set(HealthFinding.__dataclass_fields__)
        for banned in ("score", "confidence_percentage", "confidence", "rank", "priority"):
            assert banned not in field_names

    def test_no_auto_repair_instruction_field(self):
        field_names = set(HealthFinding.__dataclass_fields__)
        for banned in ("repair", "fix", "remediation_action", "auto_repair"):
            assert banned not in field_names

    def test_no_raw_text_field(self):
        field_names = set(HealthFinding.__dataclass_fields__)
        for banned in ("raw_text", "raw_fragment", "notes", "source_notes"):
            assert banned not in field_names

    def test_expected_field_set(self):
        assert set(HealthFinding.__dataclass_fields__) == {
            "rule_id",
            "classification",
            "entity_type",
            "entity_ids",
            "airport_id",
            "summary",
            "structured_evidence",
        }
