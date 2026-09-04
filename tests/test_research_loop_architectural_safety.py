"""RWI HQ "Discovery Research Loop V1, Slice 2" - static safety checks for
app/services/research_loop.py.

This module executes ONE bounded search round over an existing
SearchProvider and reports results - it must perform zero database access,
zero persistence, zero LLM call, and zero ORM mutation. It must never
import ORM models, DB/session modules, persistence services, governance
services, reconciliation, or FH-D4/disposition logic - only the minimum
existing pure runtime types it genuinely needs. Mirrors
tests/test_discovery_temporal_followup_architectural_safety.py and
tests/test_research_question_planning_architectural_safety.py's own
established discipline exactly."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET = REPO_ROOT / "app" / "services" / "research_loop.py"

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
    "generic_web_fetch",
    "snapshot_extraction",
)

FORBIDDEN_IDENTIFIERS = (
    "Signal(",
    "Installation(",
    "Airport(",
    "ReviewerAction(",
    "SignalDisposition(",
    "SourceAssertion(",
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
        assert not offenders, f"research_loop.py imports forbidden module(s) matching '{forbidden}': {offenders}"


def test_imports_only_authorized_runtime_types():
    imports = _imported_module_names(TARGET)
    app_imports = {name for name in imports if name.startswith("app.")}
    assert app_imports == {
        "app.discovery.dedup",
        "app.discovery.identity",
        "app.discovery.search",
        "app.discovery.triage",
        "app.services.research_question_planning",
        # RWI HQ "Discovery Research Loop V1 - Slice 5F": the explicit,
        # default-False opt-in seam for literal-anchor-aware query
        # planning (Slice 5E). Reused unmodified - see
        # run_research_loop's own docstring for the exact contract.
        "app.services.research_literal_anchors",
    }


def test_no_forbidden_identifiers():
    text = TARGET.read_text(encoding="utf-8")
    for identifier in FORBIDDEN_IDENTIFIERS:
        assert identifier not in text, f"research_loop.py references forbidden identifier '{identifier}'"


def test_no_llm_or_randomization_capability():
    text = TARGET.read_text(encoding="utf-8")
    for forbidden in ("openai", "anthropic", "random.", "uuid.uuid4", "datetime.now", "time.time"):
        assert forbidden not in text, f"research_loop.py references forbidden capability {forbidden!r}"


def test_does_not_construct_a_second_search_or_dedup_or_triage_type():
    """No parallel SearchResult/SearchOutcome/TriagedResult class is
    defined here - only reused (mission Part 2's own explicit
    instruction)."""
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))
    class_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    forbidden_class_names = {"SearchResult", "SearchOutcome", "TriagedResult", "DedupedResult", "SearchQuery"}
    assert not (class_names & forbidden_class_names), f"must not redefine: {class_names & forbidden_class_names}"


def test_no_round_two_or_query_refinement_capability():
    """Slice 2 is deliberately ONE round only - no title-derived
    refinement, no recursive planning, no second-round query generation."""
    text = TARGET.read_text(encoding="utf-8")
    for forbidden in ("round_two", "round2", "refine_quer", "extract_proper_noun", "second_round"):
        assert forbidden not in text.lower(), f"research_loop.py references forbidden capability {forbidden!r}"


def test_existing_discovery_components_do_not_import_the_new_module():
    existing_files = [
        REPO_ROOT / "app" / "discovery" / "search.py",
        REPO_ROOT / "app" / "discovery" / "triage.py",
        REPO_ROOT / "app" / "discovery" / "query.py",
        REPO_ROOT / "app" / "discovery" / "dedup.py",
        REPO_ROOT / "app" / "discovery" / "identity.py",
        REPO_ROOT / "app" / "discovery" / "brave_search_provider.py",
        REPO_ROOT / "app" / "services" / "discovery_temporal_followup.py",
        REPO_ROOT / "app" / "services" / "research_question_planning.py",
        REPO_ROOT / "app" / "services" / "governed_signal_creation.py",
        REPO_ROOT / "app" / "services" / "existing_signal_reconciliation.py",
    ]
    for path in existing_files:
        assert path.is_file(), f"expected existing file missing: {path}"
        # AST-based (not a raw text/substring search): research_question_planning.py
        # legitimately MENTIONS app.services.research_loop in prose (explaining
        # that module is one of its consumers) without importing it - only an
        # actual import statement is a real coupling violation.
        imports = _imported_module_names(path)
        offenders = [name for name in imports if "research_loop" in name]
        assert not offenders, f"{path.name} must not import the new research loop module: {offenders}"


def test_no_acceptance_or_fact_state_fields():
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))
    field_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    field_names.add(stmt.target.id)
    forbidden = {
        "confidence", "probability", "score", "freshness", "resolved_state",
        "accepted_claim", "current_state", "lifecycle_state", "status", "signal_id",
    }
    assert not (field_names & forbidden), f"forbidden field(s) found: {field_names & forbidden}"


def test_dimension_search_status_vocabulary_avoids_resolution_words():
    """RWI HQ 'Discovery Research Loop V1 - Slice 3', Part 2's own explicit
    instruction: DimensionSearchStatus is SEARCH DISCOVERY STATUS only,
    never evidence-resolution status - its own member VALUES (never merely
    variable/docstring prose) must avoid RESOLVED/CONFIRMED/VERIFIED/
    ANSWERED/ESTABLISHED or any synonym."""
    from app.services.research_loop import DimensionSearchStatus

    banned_substrings = ("RESOLVED", "CONFIRMED", "VERIFIED", "ANSWERED", "ESTABLISHED")
    for member in DimensionSearchStatus:
        for banned in banned_substrings:
            assert banned not in member.value, f"DimensionSearchStatus.{member.name} contains banned word {banned!r}"
    assert {m.value for m in DimensionSearchStatus} == {
        "CANDIDATES_FOUND", "NO_CANDIDATES_FOUND", "SEARCH_FAILED",
    }
