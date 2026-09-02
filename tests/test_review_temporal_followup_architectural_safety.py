"""RWI Mission #18C Part T - static safety checks for
scripts/review_temporal_followup.py.

This script composes existing Search/dedup/Triage/temporal-followup
machinery for human review only. It must never Fetch, never persist,
never mutate the database, never inspect governed lifecycle state
(Installation/Signal), never introduce a new domain contract or ranking
score, and never contain LCY/YTZ/domain-specific logic."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET = REPO_ROOT / "scripts" / "review_temporal_followup.py"
TEMPORAL_FOLLOWUP_MODULE = REPO_ROOT / "app" / "services" / "discovery_temporal_followup.py"

FORBIDDEN_IMPORT_SUBSTRINGS = (
    "app.acquisition",
    "generic_web_fetch",
    "discovery_evidence_persistence",
    "unknown_airport_discovery_integration",
    "unknown_airport_candidate",
    "evidence_attachment_guard",
    "governed_signal_creation",
    "candidate_fragment_adapter",
    "app.selection",
)

FORBIDDEN_IDENTIFIERS = (
    "fetch_discovered_url(",
    "Signal(",
    "Installation(",
    "Airport(",
    "EvidenceBag(",
    "evaluate_attachment_for_candidates",
    "persist_discovery_fragment",
    "resolve_or_persist_discovery_identity",
    "session.add(",
    "session.flush(",
    "session.commit(",
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
        assert not offenders, f"review_temporal_followup.py imports forbidden module(s) matching '{forbidden}': {offenders}"


def test_no_forbidden_identifiers():
    text = TARGET.read_text(encoding="utf-8")
    for identifier in FORBIDDEN_IDENTIFIERS:
        assert identifier not in text, f"review_temporal_followup.py references forbidden identifier '{identifier}'"


def test_imports_only_expected_app_modules():
    """Confirms the composition is exactly Search + dedup + Triage +
    temporal_followup + the one read-only SourceAssertion/CandidateFragment
    load - no new domain contract, no acquisition, no persistence, no
    IdentityGuard."""
    imports = _imported_module_names(TARGET)
    app_imports = {name for name in imports if name.startswith("app.")}
    assert app_imports == {
        "app.discovery.brave_search_provider",
        "app.discovery.dedup",
        "app.discovery.identity",
        "app.discovery.query",
        "app.discovery.search",
        "app.discovery.triage",
        "app.models",
        "app.services.discovery_candidate_fragment",
        "app.services.discovery_temporal_followup",
    }


def test_no_installation_or_signal_model_referenced():
    """Checks actual imports (AST), not prose - the module's own docstring
    legitimately mentions Installation/Signal by name to explain that
    neither is imported here."""
    imports = _imported_module_names(TARGET)
    assert not any("models.installation" in name.lower() for name in imports)
    assert not any("models.signal" in name.lower() for name in imports)
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.models":
            imported_names.update(alias.name for alias in node.names)
    assert "Installation" not in imported_names
    assert "Signal" not in imported_names


def test_no_lifecycle_or_score_vocabulary():
    """No new ranking/credibility score, no lifecycle-state vocabulary
    beyond what the frozen temporal_followup module itself already
    defines (which this script only ever displays, never computes)."""
    text = TARGET.read_text(encoding="utf-8")
    for forbidden in ("credibility_score", "confidence_score", "freshness_score", "rank_score", "installed_state", "lifecycle_state"):
        assert forbidden not in text


def test_no_lcy_or_ytz_or_domain_specific_literals():
    """The module docstring's own example invocation legitimately mentions
    London City Airport (matching every other script in this repository's
    own --help text, e.g. scripts/review_fragment_selection.py) - that is
    documentation, not a code-level special case. This check instead
    proves there is no CONDITIONAL/branching logic anywhere referencing a
    specific real-world source domain or airport - the true spaghetti risk
    Mission #18B/#18C warn against."""
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.Compare)):
            src = ast.dump(node)
            for forbidden in ("quantumcls", "runwaysafe", "blu-3", "Billy Bishop", "docklockandriver"):
                assert forbidden not in src, f"conditional/comparison references domain-specific literal {forbidden!r}"


def test_frozen_temporal_followup_module_defines_the_same_public_api():
    """Mission #18C Part D/G/H: the frozen app/services/discovery_temporal_followup.py
    must be reused, never recreated. This checks its public API surface is
    exactly what this script (and Mission #17C's freeze) expects - a
    change here would mean the frozen module was modified, which this
    mission must not do."""
    tree = ast.parse(TEMPORAL_FOLLOWUP_MODULE.read_text(encoding="utf-8"))
    top_level_names = {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    }
    assert {"detect_temporal_triggers", "plan_follow_up_queries", "DiscoveryTrigger", "AirportSearchContext", "TemporalTriggerKind"} <= top_level_names


def test_existing_search_dedup_triage_functions_reused_not_copied():
    """The review script must call the existing functions, not contain its
    own reimplementation of URL normalization or scoring logic."""
    text = TARGET.read_text(encoding="utf-8")
    assert "deduplicate_results(" in text
    assert "triage_results(" in text
    # No local reimplementation of URL normalization.
    assert "def normalize_url" not in text
    assert "urlsplit" not in text


def test_run_function_deduplicates_once_across_whole_plan():
    """Static proof that deduplicate_results() is called exactly once in
    run(), over the full accumulated results list - never inside the
    per-query loop (which would silently reintroduce per-query dedup)."""
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))
    run_func = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run")
    calls = [
        node.func.id for node in ast.walk(run_func)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "deduplicate_results"
    ]
    assert len(calls) == 1
