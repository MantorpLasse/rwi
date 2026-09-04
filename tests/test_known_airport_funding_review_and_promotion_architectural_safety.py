"""RWI HQ "Funding Human Review Gate - Slice B" - static safety checks.

The lightweight known-Airport funding review/promotion services must never
construct an Airport/Runway/Installation, never touch FH-D4
(SignalDisposition), never import a funding importer, never accept a
dollar-value parameter, and never publish a Signal. Mirrors the exact
AST-inspection discipline
tests/test_known_airport_evidence_persistence_architectural_safety.py and
tests/test_stage_only_evidence_persistence_architectural_safety.py already
established for this pipeline's other narrow sibling services."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

LIGHTWEIGHT_FUNDING_FILES = [
    REPO_ROOT / "app" / "services" / "known_airport_funding_lightweight_path_guard.py",
    REPO_ROOT / "app" / "services" / "known_airport_funding_reviewer_action.py",
    REPO_ROOT / "app" / "services" / "known_airport_funding_signal_creation.py",
]

FORBIDDEN_IMPORT_SUBSTRINGS = (
    "signal_disposition",
    "import_usaspending_grants",
    "import_faa_aip_grants",
    "import_faa_construction_report",
    "usaspending_grants",
    "faa_aip_grants",
    "faa_construction_report",
)

FORBIDDEN_IDENTIFIERS = (
    "Airport(",
    "Runway(",
    "Installation(",
    "SignalDisposition(",
    "SignalDispositionMember(",
)

FORBIDDEN_PARAMETER_NAMES = (
    "estimated_total_value_usd",
    "estimated_emas_value_usd",
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


@pytest.mark.parametrize("path", LIGHTWEIGHT_FUNDING_FILES, ids=lambda p: p.name)
def test_no_forbidden_imports(path: Path):
    imports = _imported_module_names(path)
    for forbidden in FORBIDDEN_IMPORT_SUBSTRINGS:
        offenders = [name for name in imports if forbidden in name]
        assert not offenders, f"{path.name} imports forbidden module(s) matching '{forbidden}': {offenders}"


@pytest.mark.parametrize("path", LIGHTWEIGHT_FUNDING_FILES, ids=lambda p: p.name)
def test_no_forbidden_identifiers_referenced(path: Path):
    """Word-boundary-safe: 'Airport(' must not match 'session.get(Airport,'
    (a lookup, not a construction call) - a real construction-call
    reference only, matching the existing KAR precedent's own technique."""
    text = path.read_text(encoding="utf-8")
    for identifier in FORBIDDEN_IDENTIFIERS:
        pattern = r"(?<!\w)" + re.escape(identifier)
        assert not re.search(pattern, text), f"{path.name} references forbidden identifier '{identifier}'"


def test_signal_creation_never_accepts_a_dollar_value_or_publication_parameter():
    """AST-level check of create_signal_from_lightweight_funding_review()'s
    own function signature - never a caller-suppliable `published` argument
    (it must remain hardcoded False) and never either dollar-value field."""
    path = REPO_ROOT / "app" / "services" / "known_airport_funding_signal_creation.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    target = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "create_signal_from_lightweight_funding_review"
    )
    arg_names = {a.arg for a in target.args.args + target.args.kwonlyargs}
    for forbidden in FORBIDDEN_PARAMETER_NAMES:
        assert forbidden not in arg_names, f"{forbidden!r} must never be a caller-suppliable parameter"
    assert "published" not in arg_names, "published must remain hardcoded False, never caller-suppliable"


def test_signal_creation_hardcodes_published_false():
    path = REPO_ROOT / "app" / "services" / "known_airport_funding_signal_creation.py"
    text = path.read_text(encoding="utf-8")
    assert "published=False" in text


def test_reviewer_action_module_does_not_widen_vocabulary():
    """The lightweight reviewer-action module must accept a STRICT SUBSET of
    app.models.reviewer_action.REVIEWER_ACTIONS - never a superset, never a
    value outside it."""
    from app.models.reviewer_action import REVIEWER_ACTIONS
    from app.services.known_airport_funding_reviewer_action import (
        LIGHTWEIGHT_FUNDING_REVIEWER_ACTIONS,
    )

    assert set(LIGHTWEIGHT_FUNDING_REVIEWER_ACTIONS) <= set(REVIEWER_ACTIONS)
    assert LIGHTWEIGHT_FUNDING_REVIEWER_ACTIONS == (
        "APPROVE_SIGNAL", "MARK_DUPLICATE", "NEEDS_MORE_EVIDENCE", "REJECT_SIGNAL",
    )
