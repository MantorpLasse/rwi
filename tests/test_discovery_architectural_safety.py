"""RWI Mission #9D Part L - static architectural safety checks.

Discovery Search Foundation must be upstream and read-only: nothing in
app/discovery/ (or its CLI wrapper) may import or call governed Signal
creation, Installation creation, Airport creation, SourceAssertion
persistence, or any review-mutation service. It must also never import a
database Session/engine constructor.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DISCOVERY_FILES = sorted((REPO_ROOT / "app" / "discovery").glob("*.py"))
CLI_FILE = REPO_ROOT / "scripts" / "discover_airport_sources.py"
ALL_FILES = DISCOVERY_FILES + [CLI_FILE]

FORBIDDEN_IMPORT_SUBSTRINGS = (
    "unknown_airport_candidate_resolution",  # create_airport_from_approved_candidate
    "unknown_airport_candidate_persistence",
    "unknown_airport_candidate_relevance_persistence",
    "unknown_airport_candidate_relevance_review_persistence",
    "discovery_evidence_persistence",  # persist_candidate_linked_source_assertion
    "manual_claim_evidence",
    "governed_signal_creation",
    "signal_lifecycle",
    "app.database",
    "sqlalchemy.orm",  # no Session import anywhere in this package
    "sqlalchemy.create_engine",
)


def _imported_module_names(path: Path, *, exclude_type_checking_guard: bool = True) -> list[str]:
    """Real (non-type-hint) imports only, by default: a `Session` type hint
    imported inside `if TYPE_CHECKING:` never executes at runtime and
    carries no capability, so it is excluded here - the runtime-executed
    identifier checks below (`Session(`, `create_engine(`) are what
    actually guard against a real database dependency being introduced."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if exclude_type_checking_guard and _is_inside_type_checking_guard(tree, node):
            continue
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
def test_no_forbidden_identifiers_referenced_anywhere_in_source(path: Path):
    """Belt-and-braces textual check (catches e.g. `import app.models` then
    `app.models.Signal(...)` which the import-name check above would miss)."""
    text = path.read_text(encoding="utf-8")
    forbidden_identifiers = (
        "create_airport_from_approved_candidate",
        "record_unknown_airport_candidate_review",
        "record_manual_claim_evidence",
        "persist_candidate_linked_source_assertion",
        "Session(",
        "create_engine(",
    )
    for identifier in forbidden_identifiers:
        assert identifier not in text, f"{path.name} references forbidden identifier '{identifier}'"


def test_discovery_package_has_no_model_imports():
    """app/discovery/ itself (excluding the CLI wrapper) must not import
    any ORM model at all - identity.py's Airport type hint is TYPE_CHECKING
    -only and therefore does not appear as a real ast.Import/ImportFrom at
    module scope outside that guard being satisfied."""
    for path in DISCOVERY_FILES:
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app.models"):
                # Only allowed inside a `if TYPE_CHECKING:` guard.
                parent_is_type_checking = _is_inside_type_checking_guard(tree, node)
                assert parent_is_type_checking, (
                    f"{path.name} imports {node.module} outside a TYPE_CHECKING guard"
                )


def _is_inside_type_checking_guard(tree: ast.AST, target: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            is_type_checking = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
            )
            if is_type_checking and target in ast.walk(node):
                return True
    return False
