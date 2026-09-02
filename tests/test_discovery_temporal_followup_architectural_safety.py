"""RWI Mission #17B Part R - static safety checks for
app/services/discovery_temporal_followup.py.

This module is a pure, advisory DISCOVERY capability: it must perform
zero database access, zero network access, zero file writes, and zero ORM
mutation. It must never import ORM models, DB/session modules,
persistence services, IdentityGuard, or Fetch providers - only the
minimum existing pure runtime types it genuinely needs
(CandidateFragment, SearchQuery)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET = REPO_ROOT / "app" / "services" / "discovery_temporal_followup.py"

FORBIDDEN_IMPORT_SUBSTRINGS = (
    "app.models",
    "app.database",
    "sqlalchemy",
    "httpx",
    "requests",
    "app.acquisition",
    "evidence_attachment_guard",
    "discovery_evidence_persistence",
    "unknown_airport_discovery_integration",
    "unknown_airport_candidate",
    "governed_signal_creation",
    "app.discovery.brave_search_provider",
    "app.discovery.search",
    "app.discovery.triage",
)

FORBIDDEN_IDENTIFIERS = (
    "Signal(",
    "Installation(",
    "Airport(",
    "Session(",
    "create_engine(",
    "evaluate_attachment_for_candidates",
    "resolve_or_persist_discovery_identity",
    "persist_discovery_fragment",
    "open(",
    "subprocess",
    "socket.",
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
        assert not offenders, f"discovery_temporal_followup.py imports forbidden module(s) matching '{forbidden}': {offenders}"


def test_imports_only_the_two_authorized_runtime_types():
    """Mission #17B Part C: may depend downward on the minimum existing
    runtime contracts needed - CandidateFragment and SearchQuery -
    nothing else from the rest of the codebase."""
    imports = _imported_module_names(TARGET)
    app_imports = {name for name in imports if name.startswith("app.")}
    assert app_imports == {
        "app.discovery.query",
        "app.services.discovery_candidate_fragment",
    }


def test_no_forbidden_identifiers():
    text = TARGET.read_text(encoding="utf-8")
    for identifier in FORBIDDEN_IDENTIFIERS:
        assert identifier not in text, f"discovery_temporal_followup.py references forbidden identifier '{identifier}'"


def test_no_llm_or_randomization_capability():
    text = TARGET.read_text(encoding="utf-8")
    for forbidden in ("openai", "anthropic", "random.", "uuid.uuid4", "datetime.now", "time.time"):
        assert forbidden not in text, f"discovery_temporal_followup.py references forbidden capability {forbidden!r}"


def test_existing_discovery_components_do_not_import_the_new_module():
    """Existing narrow components must NOT import the new orchestration
    module (Mission #17B Part C)."""
    existing_files = [
        REPO_ROOT / "app" / "discovery" / "search.py",
        REPO_ROOT / "app" / "discovery" / "triage.py",
        REPO_ROOT / "app" / "discovery" / "query.py",
        REPO_ROOT / "app" / "discovery" / "brave_search_provider.py",
        REPO_ROOT / "app" / "discovery" / "dedup.py",
        REPO_ROOT / "app" / "discovery" / "identity.py",
        REPO_ROOT / "app" / "extraction" / "generic_pdf.py",
        REPO_ROOT / "app" / "selection" / "fragment_selection.py",
        REPO_ROOT / "app" / "selection" / "structured_extraction.py",
        REPO_ROOT / "app" / "selection" / "candidate_fragment_adapter.py",
        REPO_ROOT / "app" / "services" / "evidence_attachment_guard.py",
        REPO_ROOT / "app" / "services" / "discovery_evidence_persistence.py",
        REPO_ROOT / "scripts" / "persist_selected_fragments.py",
    ]
    for path in existing_files:
        assert path.is_file(), f"expected existing file missing: {path}"
        text = path.read_text(encoding="utf-8")
        assert "discovery_temporal_followup" not in text, f"{path.name} must not import the new orchestration module"


def test_no_lifecycle_or_confidence_fields_on_trigger():
    """Checks the ACTUAL dataclass field names (AST), not prose - the
    module's own docstrings legitimately use words like "confidence" and
    "freshness" only to explain what is deliberately NOT a field."""
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))
    field_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    field_names.add(stmt.target.id)
    forbidden = {"confidence", "probability", "freshness", "resolved_state", "accepted_claim", "current_state", "lifecycle_state"}
    assert not (field_names & forbidden), f"forbidden field(s) found: {field_names & forbidden}"
