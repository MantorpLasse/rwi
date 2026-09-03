"""RWI Mission #26D - static safety checks.

Known-Airport evidence persistence must write ONLY Source/SourceAssertion
rows. It must never import or call anything from the identity-guard/UAC/
EvidenceBag governance layer, and must never construct an Airport/Runway/
Installation/Signal row - mirroring the exact AST-inspection discipline
tests/test_stage_only_evidence_persistence_architectural_safety.py already
established for Mission #25J1's sibling module."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

KNOWN_AIRPORT_FILES = [
    REPO_ROOT / "app" / "services" / "known_airport_evidence_persistence.py",
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


@pytest.mark.parametrize("path", KNOWN_AIRPORT_FILES, ids=lambda p: p.name)
def test_no_forbidden_governance_imports(path: Path):
    imports = _imported_module_names(path)
    for forbidden in FORBIDDEN_IMPORT_SUBSTRINGS:
        offenders = [name for name in imports if forbidden in name]
        assert not offenders, f"{path.name} imports forbidden module(s) matching '{forbidden}': {offenders}"


@pytest.mark.parametrize("path", KNOWN_AIRPORT_FILES, ids=lambda p: p.name)
def test_no_forbidden_identifiers_referenced(path: Path):
    """Word-boundary-safe: 'Airport(' must not match 'session.get(Airport,'
    (a lookup, not a construction call) - a real construction-call
    reference only."""
    text = path.read_text(encoding="utf-8")
    for identifier in FORBIDDEN_IDENTIFIERS:
        pattern = r"(?<!\w)" + re.escape(identifier)
        assert not re.search(pattern, text), f"{path.name} references forbidden identifier '{identifier}'"


def test_persist_selected_fragments_known_airport_branch_never_calls_governance():
    """The CLI script itself: the known-airport branch must not contain
    any of the forbidden identifiers - a coarser, text-scoped check since
    this file also legitimately contains the full-governed path that DOES
    call them."""
    path = REPO_ROOT / "scripts" / "persist_selected_fragments.py"
    text = path.read_text(encoding="utf-8")
    start = text.index("if config.known_airport_id is not None:")
    end = text.index("if kept and not config.candidate_airport_ids and not config.no_known_candidates:")
    known_airport_branch = text[start:end]
    for identifier in FORBIDDEN_IDENTIFIERS:
        # "session.get(Airport, ...)" is an intentional, safe lookup call
        # in this branch and never produces the contiguous substring
        # "Airport(" (there is a comma, not an open paren, right after the
        # name) - so the plain word-boundary pattern already only flags a
        # real "Airport("/"Runway(" construction call, exactly like
        # test_stage_only_evidence_persistence_architectural_safety.py's
        # own identical check.
        pattern = r"(?<!\w)" + re.escape(identifier)
        assert not re.search(pattern, known_airport_branch), (
            f"known-airport branch references forbidden identifier '{identifier}'"
        )
