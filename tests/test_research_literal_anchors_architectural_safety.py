"""RWI HQ "Discovery Research Loop V1 - Slice 5E" - static safety checks
for app/services/research_literal_anchors.py.

This module extracts literal search anchors from preserved evidence text
and produces additional, bounded search queries - it must perform zero
database access, zero network access, zero LLM call, and zero
randomization, and must never import ORM models, DB/session modules,
persistence services, governance services, Fetch/acquisition code, or
Selection/KEEP code. It must never be imported by app.services.research_loop
or scripts/research_airport_clue.py in this slice (Slice 5E deliberately
does not wire it into the live research CLI)."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET = REPO_ROOT / "app" / "services" / "research_literal_anchors.py"

FORBIDDEN_IMPORT_SUBSTRINGS = (
    "app.models",
    "app.database",
    "sqlalchemy",
    "httpx",
    "requests",
    "app.acquisition",
    "app.discovery.search",
    "app.discovery.triage",
    "app.discovery.brave_search_provider",
    "generic_web_fetch",
    "snapshot_extraction",
    "app.selection",
    "evidence_attachment_guard",
    "discovery_evidence_persistence",
    "unknown_airport_discovery_integration",
    "governed_signal_creation",
    "known_airport_funding_signal_creation",
    "known_airport_funding_reviewer_action",
    "reviewer_action_persistence",
    "existing_signal_reconciliation",
    "signal_disposition",
    "fh_d4_disposition",
    "app.services.research_loop",
)

FORBIDDEN_IDENTIFIERS = (
    "Signal(",
    "Installation(",
    "Airport(",
    "ReviewerAction(",
    "SignalDisposition(",
    "SourceAssertion(",
    "CandidateFragment(",
    "Session(",
    "create_engine(",
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
        assert not offenders, f"research_literal_anchors.py imports forbidden module(s) matching '{forbidden}': {offenders}"


def test_imports_only_authorized_runtime_types():
    imports = _imported_module_names(TARGET)
    app_imports = {name for name in imports if name.startswith("app.")}
    assert app_imports == {
        "app.discovery.query",
        "app.services.discovery_temporal_followup",
        "app.services.research_question_planning",
    }


def test_no_forbidden_identifiers():
    text = TARGET.read_text(encoding="utf-8")
    for identifier in FORBIDDEN_IDENTIFIERS:
        assert identifier not in text, f"research_literal_anchors.py references forbidden identifier '{identifier}'"


def test_no_llm_or_randomization_capability():
    text = TARGET.read_text(encoding="utf-8")
    for forbidden in ("openai", "anthropic", "random.", "uuid.uuid4", "datetime.now", "time.time"):
        assert forbidden not in text, f"research_literal_anchors.py references forbidden capability {forbidden!r}"


def test_exactly_three_anchor_kinds_no_quoted_phrase():
    from app.services.research_literal_anchors import AnchorKind

    assert {k.value for k in AnchorKind} == {
        "DIRECTIONAL_RUNWAY_NAME", "NUMBERED_RUNWAY_DESIGNATION", "PHASE_LITERAL",
    }
    assert "QUOTED_PHRASE" not in {k.value for k in AnchorKind}


def test_research_anchor_has_no_resolution_or_confidence_field():
    """AST-based (not prose): ResearchAnchor must carry text/kind/
    dimension_hint only - never a normalized/inferred/resolved value,
    confidence, or fact-state field."""
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))
    field_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ResearchAnchor":
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    field_names.add(stmt.target.id)
    assert field_names == {"text", "kind", "dimension_hint"}


def test_existing_frozen_modules_do_not_import_the_new_module():
    """One-way dependency only: this new module may depend downward on
    research_question_planning, but nothing pre-existing depends on it -
    especially not research_loop.py or research_airport_clue.py (Slice
    5E's own explicit "does NOT wire the new planner into the live
    research CLI" instruction)."""
    existing_files = [
        REPO_ROOT / "app" / "services" / "research_question_planning.py",
        REPO_ROOT / "app" / "services" / "research_loop.py",
        REPO_ROOT / "app" / "services" / "discovery_temporal_followup.py",
        REPO_ROOT / "scripts" / "research_airport_clue.py",
        REPO_ROOT / "app" / "discovery" / "query.py",
        REPO_ROOT / "app" / "discovery" / "search.py",
        REPO_ROOT / "app" / "discovery" / "triage.py",
    ]
    for path in existing_files:
        assert path.is_file(), f"expected existing file missing: {path}"
        imports = _imported_module_names(path)
        offenders = [name for name in imports if "research_literal_anchors" in name]
        assert not offenders, f"{path.name} must not import the new anchors module in Slice 5E: {offenders}"


def test_research_question_planning_untouched_import_set():
    """Regression pin: research_question_planning.py's own frozen
    architectural-safety test (test_imports_only_the_two_authorized_runtime_types)
    must still pass unmodified - this new module was deliberately placed
    to avoid a circular import that would have broken it. Re-asserted
    here, directly, as an extra guard."""
    target = REPO_ROOT / "app" / "services" / "research_question_planning.py"
    imports = _imported_module_names(target)
    app_imports = {name for name in imports if name.startswith("app.")}
    assert app_imports == {"app.discovery.query", "app.services.discovery_temporal_followup"}


def test_no_query_explosion_constants_are_small():
    from app.services.research_literal_anchors import (
        MAX_ANCHOR_DERIVED_QUERIES_PER_DIMENSION,
        MAX_ANCHORS_PER_DIMENSION,
        MAX_ANCHORS_TOTAL,
    )

    assert MAX_ANCHORS_TOTAL == 5
    assert MAX_ANCHORS_PER_DIMENSION == 1
    assert MAX_ANCHOR_DERIVED_QUERIES_PER_DIMENSION == 1
