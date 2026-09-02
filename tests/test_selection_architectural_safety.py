"""RWI Mission #13B Part O - static safety checks.

Fragment Selection must depend on extracted text only - never upstream
ranking (Discovery/Triage), never downstream governance (Source/
SourceAssertion/Signal/Installation/CandidateFragment), never the
database, network, or acquisition HTTP logic."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SELECTION_FILES = sorted((REPO_ROOT / "app" / "selection").glob("*.py"))

FORBIDDEN_IMPORT_SUBSTRINGS = (
    "app.discovery",
    "brave_search_provider",
    "app.acquisition",
    "generic_web_fetch",
    "discovery_candidate_fragment",
    "discovery_evidence_persistence",
    "evidence_attachment_guard",
    "unknown_airport_candidate",
    "manual_claim_evidence",
    "governed_signal_creation",
    "signal_lifecycle",
    "emas_relevance_evaluation",
    "app.models",
    "app.database",
    "sqlalchemy",
    "httpx",
)

FORBIDDEN_IDENTIFIERS = (
    "SearchResult(",
    "SearchQuery(",
    "TriagedResult(",
    "CandidateFragment(",
    "Source(",
    "SourceAssertion(",
    "Signal(",
    "Installation(",
    "Session(",
    "create_engine(",
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


@pytest.mark.parametrize("path", SELECTION_FILES, ids=lambda p: p.name)
def test_no_forbidden_imports(path: Path):
    if path.name == "__init__.py":
        return
    imports = _imported_module_names(path)
    for forbidden in FORBIDDEN_IMPORT_SUBSTRINGS:
        offenders = [name for name in imports if forbidden in name]
        assert not offenders, f"{path.name} imports forbidden module(s) matching '{forbidden}': {offenders}"


@pytest.mark.parametrize("path", SELECTION_FILES, ids=lambda p: p.name)
def test_no_forbidden_identifiers_referenced(path: Path):
    if path.name == "__init__.py":
        return
    text = path.read_text(encoding="utf-8")
    for identifier in FORBIDDEN_IDENTIFIERS:
        pattern = r"(?<!\w)" + re.escape(identifier)
        assert not re.search(pattern, text), f"{path.name} references forbidden identifier '{identifier}'"


def test_selection_only_imports_from_app_extraction_and_stdlib():
    """The only intra-RWI import Selection is permitted is
    app.extraction.generic_pdf's own runtime types - the exact,
    intended, one-way dependency (Extraction -> Selection)."""
    path = REPO_ROOT / "app" / "selection" / "fragment_selection.py"
    imports = _imported_module_names(path)
    app_imports = [name for name in imports if name.startswith("app.")]
    assert app_imports == ["app.extraction.generic_pdf"]


def test_no_network_or_database_capability_anywhere_in_selection():
    for path in SELECTION_FILES:
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "socket." not in text
        assert "requests." not in text
        assert ".execute(" not in text
