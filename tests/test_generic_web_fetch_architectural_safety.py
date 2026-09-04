"""RWI Mission #11B Part M (Architecture) - static safety checks.

Generic web fetch must write ONLY PublishingSource/AcquisitionSource/
AcquisitionRun/Snapshot rows. It must never import or call anything from
the governed evidence/identity/review layer, and must never be triggered
automatically by Discovery/Triage (one explicit human URL = one fetch,
always)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FETCH_FILES = [
    REPO_ROOT / "app" / "acquisition" / "generic_web.py",
    REPO_ROOT / "app" / "services" / "generic_web_fetch.py",
    REPO_ROOT / "scripts" / "fetch_discovered_url.py",
    REPO_ROOT / "scripts" / "fetch_research_candidate.py",
]

FORBIDDEN_IMPORT_SUBSTRINGS = (
    "discovery_evidence_persistence",
    "discovery_candidate_fragment",
    "evidence_attachment_guard",
    "unknown_airport_candidate",
    "manual_claim_evidence",
    "governed_signal_creation",
    "signal_lifecycle",
    "emas_relevance_evaluation",
)

FORBIDDEN_IDENTIFIERS = (
    "Source(",
    "SourceAssertion(",
    "Signal(",
    "Installation(",
    "CandidateFragment(",
    "create_airport_from_approved_candidate",
    "persist_candidate_linked_source_assertion",
    "persist_discovery_fragment",
    "record_manual_claim_evidence",
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


@pytest.mark.parametrize("path", FETCH_FILES, ids=lambda p: p.name)
def test_no_forbidden_governance_imports(path: Path):
    imports = _imported_module_names(path)
    for forbidden in FORBIDDEN_IMPORT_SUBSTRINGS:
        offenders = [name for name in imports if forbidden in name]
        assert not offenders, f"{path.name} imports forbidden module(s) matching '{forbidden}': {offenders}"


@pytest.mark.parametrize("path", FETCH_FILES, ids=lambda p: p.name)
def test_no_forbidden_identifiers_referenced(path: Path):
    """Word-boundary-safe: 'Source(' must not match as a substring of
    'PublishingSource(' - a real identifier reference only, not any
    identifier that happens to end with the forbidden text."""
    import re

    text = path.read_text(encoding="utf-8")
    for identifier in FORBIDDEN_IDENTIFIERS:
        pattern = r"(?<!\w)" + re.escape(identifier)
        assert not re.search(pattern, text), f"{path.name} references forbidden identifier '{identifier}'"


def test_generic_web_provider_imports_nothing_from_app_models():
    """The provider itself (app.acquisition.generic_web) is pure network
    I/O - it should not know about any ORM model at all, matching
    FAAAcquisitionProvider/MACGranicusAcquisitionProvider's own existing
    convention."""
    path = REPO_ROOT / "app" / "acquisition" / "generic_web.py"
    imports = _imported_module_names(path)
    assert not any(name.startswith("app.models") for name in imports)
    assert not any(name.startswith("app.database") for name in imports)
    assert not any("sqlalchemy" in name for name in imports)


def test_discovery_module_never_imports_generic_web_fetch():
    """One-way dependency only: generic_web_fetch may (in a future
    mission) read Discovery's output, but Discovery/Triage must never
    import or call the fetch layer - there is no "auto-fetch HIGH
    results" path anywhere (Mission #11B: "one explicit URL invocation =
    one human FETCH authorization")."""
    discovery_files = list((REPO_ROOT / "app" / "discovery").glob("*.py"))
    cli_file = REPO_ROOT / "scripts" / "discover_airport_sources.py"
    for path in discovery_files + [cli_file]:
        imports = _imported_module_names(path)
        assert not any("generic_web" in name for name in imports), f"{path.name} must not import the fetch layer"
