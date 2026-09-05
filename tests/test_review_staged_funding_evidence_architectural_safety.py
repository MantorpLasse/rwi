"""RWI HQ "Commander Staged Funding Review CLI" - static safety checks for
scripts/review_staged_funding_evidence.py.

This CLI must orchestrate the existing lightweight funding services only -
never reimplement eligibility, never widen the funding-provenance
namespace, never accept a runway_id/supplier/dollar-value Signal
parameter, and never import a heavy-pipeline governance module (those
remain the exclusive domain of app.services.reviewer_action_persistence /
app.services.governed_signal_creation for genuinely governed rows).
Mirrors the exact AST-inspection discipline
tests/test_known_airport_funding_review_and_promotion_architectural_safety.py
already established for this pipeline's sibling services."""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET = REPO_ROOT / "scripts" / "review_staged_funding_evidence.py"

FORBIDDEN_IMPORT_SUBSTRINGS = (
    "signal_disposition",
    "identity_guard",
    "intelligence_review_persistence",
    "promotion_policy_persistence",
    "import_usaspending_grants",
    "import_faa_aip_grants",
)

FORBIDDEN_IDENTIFIERS = (
    "Airport(",
    "Runway(",
    "Installation(",
    "SignalDisposition(",
    "SignalDispositionMember(",
)

FORBIDDEN_CLI_PARAMETER_NAMES = (
    "runway_id",
    "supplier",
    "likely_supplier",
    "estimated_total_value_usd",
    "estimated_emas_value_usd",
    "published",
)


def _imported_module_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_no_forbidden_imports():
    imports = _imported_module_names(TARGET)
    for forbidden in FORBIDDEN_IMPORT_SUBSTRINGS:
        offenders = [name for name in imports if forbidden in name]
        assert not offenders, f"review_staged_funding_evidence.py imports forbidden module(s) matching '{forbidden}': {offenders}"


def test_no_forbidden_identifiers_referenced():
    text = TARGET.read_text(encoding="utf-8")
    for identifier in FORBIDDEN_IDENTIFIERS:
        pattern = r"(?<!\w)" + re.escape(identifier)
        assert not re.search(pattern, text), f"references forbidden identifier '{identifier}'"


def test_no_forbidden_cli_flags():
    """No --runway-id/--supplier/--likely-supplier/--estimated-*-value-usd/
    --published flag is DEFINED on this CLI's own parser - those
    parameters are structurally absent from
    create_signal_from_lightweight_funding_review() itself, and this CLI
    must not reintroduce them. AST-based, over actual add_argument(...)
    call sites only - a docstring merely explaining that these fields are
    absent (as this module's own docstring does) must not be flagged."""
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))
    flag_strings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    flag_strings.add(arg.value)
    for forbidden in FORBIDDEN_CLI_PARAMETER_NAMES:
        flag = "--" + forbidden.replace("_", "-")
        assert flag not in flag_strings, f"forbidden CLI flag defined: {flag!r}"


def test_only_reuses_the_shared_eligibility_function_never_reimplements_it():
    """This CLI must call check_lightweight_funding_path_eligibility() -
    never define its own parallel eligibility predicate."""
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))
    imports = _imported_module_names(TARGET)
    assert "app.services.known_airport_funding_lightweight_path_guard" in imports
    function_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert not any("eligib" in name.lower() and "check_lightweight" not in name.lower() for name in function_names)


def test_only_reuses_the_shared_reviewer_action_and_signal_functions():
    imports = _imported_module_names(TARGET)
    assert "app.services.known_airport_funding_reviewer_action" in imports
    assert "app.services.known_airport_funding_signal_creation" in imports


def test_does_not_import_heavy_pipeline_reviewer_action_or_signal_creation():
    """DEFER/CONFIRM_DISTINCT_SIGNAL and the heavy Signal-creation path
    remain exclusively app.services.reviewer_action_persistence /
    app.services.governed_signal_creation's own domain - this CLI must
    never import record_reviewer_action or
    create_signal_from_approved_review."""
    text = TARGET.read_text(encoding="utf-8")
    assert "record_reviewer_action(" not in text
    assert "create_signal_from_approved_review(" not in text


def test_action_choices_are_exactly_the_lightweight_subset():
    import scripts.review_staged_funding_evidence as cli
    from app.services.known_airport_funding_reviewer_action import LIGHTWEIGHT_FUNDING_REVIEWER_ACTIONS

    parser = cli._parser()
    action_action = next(a for a in parser._actions if a.dest == "action")
    assert set(action_action.choices) == set(LIGHTWEIGHT_FUNDING_REVIEWER_ACTIONS)


def test_commits_at_most_once_per_apply_path():
    """Reviewer inspection aid, not a strict AST proof: counts literal
    `.commit(` call sites in the apply branch - this CLI's own design
    requires exactly one commit per successful apply (never a commit
    between recording the ReviewerAction and creating the Signal)."""
    text = TARGET.read_text(encoding="utf-8")
    assert text.count("session.commit()") <= 2  # one on the APPROVE_SIGNAL success path, one on the non-APPROVE path
