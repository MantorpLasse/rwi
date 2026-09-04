"""RWI HQ "Discovery Research Question Planner V1" - static safety checks
for app/services/research_question_planning.py.

This module is a pure, advisory research-planning capability: it must
perform zero database access, zero network access, zero LLM call, and zero
ORM mutation. It must never import ORM models, DB/session modules,
persistence services, governance services, or FH-D4/disposition logic -
only the minimum existing pure runtime types it genuinely needs
(SearchQuery, AirportSearchContext). Mirrors
tests/test_discovery_temporal_followup_architectural_safety.py's own
established discipline exactly."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET = REPO_ROOT / "app" / "services" / "research_question_planning.py"

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
    "known_airport_funding_signal_creation",
    "known_airport_funding_reviewer_action",
    "reviewer_action_persistence",
    "existing_signal_reconciliation",
    "signal_disposition",
    "fh_d4_disposition",
    "app.discovery.brave_search_provider",
    "app.discovery.search",
    "app.discovery.triage",
)

FORBIDDEN_IDENTIFIERS = (
    "Signal(",
    "Installation(",
    "Airport(",
    "ReviewerAction(",
    "SignalDisposition(",
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
        assert not offenders, f"research_question_planning.py imports forbidden module(s) matching '{forbidden}': {offenders}"


def test_imports_only_the_two_authorized_runtime_types():
    """This module may depend downward on the minimum existing runtime
    contracts it genuinely needs - SearchQuery and (reused, not
    reimplemented) AirportSearchContext - nothing else from the rest of
    the codebase."""
    imports = _imported_module_names(TARGET)
    app_imports = {name for name in imports if name.startswith("app.")}
    assert app_imports == {
        "app.discovery.query",
        "app.services.discovery_temporal_followup",
    }


def test_no_forbidden_identifiers():
    text = TARGET.read_text(encoding="utf-8")
    for identifier in FORBIDDEN_IDENTIFIERS:
        assert identifier not in text, f"research_question_planning.py references forbidden identifier '{identifier}'"


def test_no_llm_network_or_randomization_capability():
    text = TARGET.read_text(encoding="utf-8")
    for forbidden in ("openai", "anthropic", "random.", "uuid.uuid4", "datetime.now", "time.time", "requests.", "httpx."):
        assert forbidden not in text, f"research_question_planning.py references forbidden capability {forbidden!r}"


def test_does_not_modify_discovery_temporal_followup():
    """This mission reuses AirportSearchContext but must not touch that
    module's own frozen (Missions #17B/#18C) file at all."""
    target_source = TARGET.read_text(encoding="utf-8")
    followup_path = REPO_ROOT / "app" / "services" / "discovery_temporal_followup.py"
    assert followup_path.is_file()
    # This is a reuse-only check: the new module must import from, never
    # redefine, AirportSearchContext.
    assert "class AirportSearchContext" not in target_source


def test_existing_discovery_components_do_not_import_the_new_module():
    """Existing narrow, already-frozen components must NOT import this new
    module - a one-way reuse, never a two-way coupling."""
    existing_files = [
        REPO_ROOT / "app" / "discovery" / "search.py",
        REPO_ROOT / "app" / "discovery" / "triage.py",
        REPO_ROOT / "app" / "discovery" / "query.py",
        REPO_ROOT / "app" / "discovery" / "brave_search_provider.py",
        REPO_ROOT / "app" / "discovery" / "dedup.py",
        REPO_ROOT / "app" / "discovery" / "identity.py",
        REPO_ROOT / "app" / "services" / "discovery_temporal_followup.py",
        REPO_ROOT / "app" / "selection" / "fragment_selection.py",
        REPO_ROOT / "app" / "services" / "evidence_attachment_guard.py",
        REPO_ROOT / "app" / "services" / "discovery_evidence_persistence.py",
        REPO_ROOT / "app" / "services" / "governed_signal_creation.py",
        REPO_ROOT / "app" / "services" / "existing_signal_reconciliation.py",
    ]
    for path in existing_files:
        assert path.is_file(), f"expected existing file missing: {path}"
        text = path.read_text(encoding="utf-8")
        assert "research_question_planning" not in text, f"{path.name} must not import the new research planning module"


def test_no_score_confidence_or_acceptance_fields():
    """Checks the ACTUAL dataclass field names (AST), not prose - mirrors
    test_discovery_temporal_followup_architectural_safety.py's own
    equivalent check."""
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))
    field_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    field_names.add(stmt.target.id)
    forbidden = {
        "confidence", "probability", "score", "freshness", "resolved_state",
        "accepted_claim", "current_state", "lifecycle_state", "status",
    }
    assert not (field_names & forbidden), f"forbidden field(s) found: {field_names & forbidden}"
