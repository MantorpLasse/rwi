"""RWI Mission #12B Part P - static safety checks.

Generic PDF extraction must remain upstream and non-evidentiary: nothing
in app/extraction/ (or its DB-loader/CLI wrappers) may import or
construct CandidateFragment, Source, SourceAssertion, ManualClaimEvidence/
Claim, Signal, Installation, or any evidence-promotion/governance
service. Nothing may import SearchResult/SearchQuery/Triage. Nothing may
open a network connection or a database session from the pure parsing
core."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRACTION_FILES = sorted((REPO_ROOT / "app" / "extraction").glob("*.py"))
LOADER_FILE = REPO_ROOT / "app" / "services" / "snapshot_extraction.py"
CLI_FILE = REPO_ROOT / "scripts" / "extract_snapshot_text.py"
ALL_FILES = EXTRACTION_FILES + [LOADER_FILE, CLI_FILE]

FORBIDDEN_IMPORT_SUBSTRINGS = (
    "discovery_candidate_fragment",
    "discovery_evidence_persistence",
    "evidence_attachment_guard",
    "unknown_airport_candidate",
    "manual_claim_evidence",
    "manual_identity_evidence",
    "governed_signal_creation",
    "signal_lifecycle",
    "emas_relevance_evaluation",
    "app.discovery",
    "brave_search_provider",
    "httpx",
)

FORBIDDEN_IDENTIFIERS = (
    "CandidateFragment(",
    "Source(",
    "SourceAssertion(",
    "Signal(",
    "Installation(",
    "ManualClaimEvidence(",
    "record_manual_claim_evidence",
    "persist_candidate_linked_source_assertion",
    "persist_discovery_fragment",
    "create_airport_from_approved_candidate",
    "SearchResult(",
    "SearchQuery(",
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


@pytest.mark.parametrize("path", ALL_FILES, ids=lambda p: p.name)
def test_no_forbidden_imports(path: Path):
    imports = _imported_module_names(path)
    for forbidden in FORBIDDEN_IMPORT_SUBSTRINGS:
        offenders = [name for name in imports if forbidden in name]
        assert not offenders, f"{path.name} imports forbidden module(s) matching '{forbidden}': {offenders}"


@pytest.mark.parametrize("path", ALL_FILES, ids=lambda p: p.name)
def test_no_forbidden_identifiers_referenced(path: Path):
    import re

    text = path.read_text(encoding="utf-8")
    for identifier in FORBIDDEN_IDENTIFIERS:
        pattern = r"(?<!\w)" + re.escape(identifier)
        assert not re.search(pattern, text), f"{path.name} references forbidden identifier '{identifier}'"


def test_extraction_core_never_imports_sqlalchemy_or_app_models():
    """The pure parsing core (app/extraction/*.py) must never touch a
    database at all - that boundary belongs entirely to
    app.services.snapshot_extraction."""
    for path in EXTRACTION_FILES:
        if path.name == "__init__.py":
            continue
        imports = _imported_module_names(path)
        assert not any("sqlalchemy" in name for name in imports), f"{path.name} imports sqlalchemy"
        assert not any(name.startswith("app.models") for name in imports), f"{path.name} imports app.models"
        assert not any(name.startswith("app.database") for name in imports), f"{path.name} imports app.database"


def test_loader_never_imports_network_libraries():
    imports = _imported_module_names(LOADER_FILE)
    assert not any("httpx" in name for name in imports)


def test_extraction_module_has_no_network_dependency():
    """No httpx/socket-based network capability anywhere in the
    extraction package - it operates purely on already-preserved bytes."""
    for path in EXTRACTION_FILES:
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "httpx" not in text
        assert "socket.create_connection" not in text
