"""RWI Mission #15B Part R - static safety checks for
scripts/persist_selected_fragments.py and
app/services/selection_source_metadata.py.

This runner is an adapter/orchestrator over already-committed persistence
services - it must never gain the ability to create a Signal, an
Installation, a governed fact, or an Airport (outside the existing,
separately-authorized UAC-resolution workflow), never promote evidence or
perform intelligence acceptance, never change IdentityGuard/SelectionReason
semantics, and never touch the network/HTTP/LLM layer during replay."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET_FILES = (
    REPO_ROOT / "scripts" / "persist_selected_fragments.py",
    REPO_ROOT / "app" / "services" / "selection_source_metadata.py",
)

FORBIDDEN_IMPORT_SUBSTRINGS = (
    "governed_signal_creation",
    "signal_lifecycle",
    "promotion_policy_evaluation",
    "promotion_policy_persistence",
    "intelligence_review_persistence",
    "signal_candidate_evaluation",
    "unknown_airport_candidate_resolution",  # create_airport_from_approved_candidate lives here
    "httpx",
    "requests",
    "app.acquisition",  # no live provider/network fetch during replay
)

FORBIDDEN_IDENTIFIERS = (
    "Signal(",
    "Installation(",
    "create_airport_from_approved_candidate",
    "create_signal_from_approved_review",
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


@pytest.mark.parametrize("path", TARGET_FILES, ids=lambda p: p.name)
def test_no_forbidden_imports(path: Path):
    imports = _imported_module_names(path)
    for forbidden in FORBIDDEN_IMPORT_SUBSTRINGS:
        offenders = [name for name in imports if forbidden in name]
        assert not offenders, f"{path.name} imports forbidden module(s) matching '{forbidden}': {offenders}"


@pytest.mark.parametrize("path", TARGET_FILES, ids=lambda p: p.name)
def test_no_forbidden_identifiers(path: Path):
    text = path.read_text(encoding="utf-8")
    for identifier in FORBIDDEN_IDENTIFIERS:
        assert identifier not in text, f"{path.name} references forbidden identifier '{identifier}'"


def test_runner_never_imports_session_local_or_default_engine():
    """Checks actual imports (AST), not the module's own docstring prose
    describing this very safety property (which mentions the names
    'SessionLocal'/'app.database.engine' by way of explaining what it does
    NOT do - exactly like scripts/capture_mac_discovery.py's own module
    docstring does)."""
    path = REPO_ROOT / "scripts" / "persist_selected_fragments.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.database":
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
    assert "app.database" not in imported_names
    assert "SessionLocal" not in imported_names
    assert "engine" not in imported_names


def test_no_llm_or_subprocess_capability():
    for path in TARGET_FILES:
        text = path.read_text(encoding="utf-8")
        for forbidden in ("openai", "anthropic", "subprocess", "os.system", "eval(", "exec("):
            assert forbidden not in text, f"{path.name} references forbidden capability {forbidden!r}"


def test_selection_source_metadata_has_no_governance_side_effects():
    """The adapter reads Snapshot/AcquisitionSource/PublishingSource only
    and constructs a plain dataclass - it must never import Session-write
    helpers, Signal, or Installation."""
    path = REPO_ROOT / "app" / "services" / "selection_source_metadata.py"
    imports = _imported_module_names(path)
    forbidden_modules = ("app.models.signal", "app.models.installation")
    for forbidden in forbidden_modules:
        assert not any(forbidden in name for name in imports)
