"""RWI Mission #25J1 (architecture sign-off: Mission #25I) - static safety
checks.

Stage-only evidence persistence must write ONLY Source/SourceAssertion
rows. It must never import or call anything from the identity-guard/UAC/
EvidenceBag governance layer - mirroring the exact AST-inspection
discipline tests/test_generic_web_fetch_architectural_safety.py already
established for the human-Fetch boundary (Mission #11B)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

STAGE_ONLY_FILES = [
    REPO_ROOT / "app" / "services" / "stage_only_evidence_persistence.py",
]

FORBIDDEN_IMPORT_SUBSTRINGS = (
    "evidence_attachment_guard",
    "unknown_airport_discovery_integration",
    "unknown_airport_candidate",
    "governed_signal_creation",
    "reviewer_action",
    "source_assertion_evidence_bag",
    "evidence_bag_serialization",
    "discovery_candidate_fragment",
    "signal_lifecycle",
)

FORBIDDEN_IDENTIFIERS = (
    "evaluate_attachment_for_candidates(",
    "resolve_or_persist_discovery_identity(",
    "candidate_fragment_to_evidence_bag(",
    "persist_discovery_fragment(",
    "persist_candidate_linked_source_assertion(",
    "_verify_evidence_bag_schema_ready(",
    "SourceAssertionEvidenceBag(",
    "UnknownAirportCandidate(",
    "ReviewerAction(",
    "Signal(",
    "Installation(",
    "Airport(",
    "Runway(",
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


@pytest.mark.parametrize("path", STAGE_ONLY_FILES, ids=lambda p: p.name)
def test_no_forbidden_governance_imports(path: Path):
    imports = _imported_module_names(path)
    for forbidden in FORBIDDEN_IMPORT_SUBSTRINGS:
        offenders = [name for name in imports if forbidden in name]
        assert not offenders, f"{path.name} imports forbidden module(s) matching '{forbidden}': {offenders}"


@pytest.mark.parametrize("path", STAGE_ONLY_FILES, ids=lambda p: p.name)
def test_no_forbidden_identifiers_referenced(path: Path):
    """Word-boundary-safe: 'Signal(' must not match as a substring of
    'DiscoverySignal(' - a real identifier reference only."""
    text = path.read_text(encoding="utf-8")
    for identifier in FORBIDDEN_IDENTIFIERS:
        pattern = r"(?<!\w)" + re.escape(identifier)
        assert not re.search(pattern, text), f"{path.name} references forbidden identifier '{identifier}'"


def test_persist_selected_fragments_stage_only_branch_never_calls_governance():
    """The CLI script itself: the stage-only branch (bounded by the
    literal markers below) must not contain any of the forbidden
    identifiers - a coarser, text-scoped check since this file also
    legitimately contains the full-governed path that DOES call them."""
    path = REPO_ROOT / "scripts" / "persist_selected_fragments.py"
    text = path.read_text(encoding="utf-8")
    start = text.index("if config.stage_only:")
    end = text.index("if kept and not config.candidate_airport_ids and not config.no_known_candidates:")
    stage_only_branch = text[start:end]
    for identifier in FORBIDDEN_IDENTIFIERS:
        pattern = r"(?<!\w)" + re.escape(identifier)
        assert not re.search(pattern, stage_only_branch), (
            f"stage-only branch references forbidden identifier '{identifier}'"
        )
