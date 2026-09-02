"""RWI Mission #13B/#14B - static safety checks for app/selection/.

Three distinct dependency tiers now exist in this package, each with its
own, precise, tested boundary (Mission #14B Part O):

  PURE CORE (fragment_selection.py, structured_extraction.py) - must
  never know CandidateFragment or IdentityGuard exist at all. Depends
  only on app.extraction.generic_pdf and stdlib.

  ADAPTER (candidate_fragment_adapter.py, review.py) - MAY construct a
  runtime CandidateFragment (Mission #14B Part H explicitly authorizes
  this), but must NEVER import IdentityGuard/evidence_attachment_guard,
  and must never import any actual persistence write path.

  OPTIONAL DEMO (identity_guard_demo.py) - the ONLY file in this package
  permitted to import evidence_attachment_guard, and only for read-only
  evaluation (Mission #14B Part C/J) - it must still never import any
  actual persistence write path, Signal, Installation, or the database/
  network layer.

Nothing anywhere in this package may import Discovery/Triage, Brave,
generic web acquisition, the database, SQLAlchemy, or httpx - those
restrictions are universal, no exceptions, in every tier."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SELECTION_FILES = sorted((REPO_ROOT / "app" / "selection").glob("*.py"))

# Forbidden in EVERY file in app/selection/, no exceptions anywhere.
UNIVERSALLY_FORBIDDEN_IMPORT_SUBSTRINGS = (
    "app.discovery",
    "brave_search_provider",
    "app.acquisition",
    "generic_web_fetch",
    "discovery_evidence_persistence",  # the real persistence write path
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

UNIVERSALLY_FORBIDDEN_IDENTIFIERS = (
    "SearchResult(",
    "SearchQuery(",
    "TriagedResult(",
    "Source(",
    "SourceAssertion(",
    "Signal(",
    "Installation(",
    "Session(",
    "create_engine(",
    "record_manual_claim_evidence",
    "persist_candidate_linked_source_assertion",
    "persist_discovery_fragment",
    "create_airport_from_approved_candidate",
)

# Only the pure core must never know CandidateFragment/IdentityGuard
# exist - everything else in the package (the adapter, the review
# workflow, the optional demo) legitimately depends on at least one.
PURE_CORE_FILES = {"fragment_selection.py", "structured_extraction.py"}
PURE_CORE_ADDITIONAL_FORBIDDEN_IMPORTS = ("discovery_candidate_fragment", "evidence_attachment_guard")
PURE_CORE_ADDITIONAL_FORBIDDEN_IDENTIFIERS = ("CandidateFragment(", "EvidenceBag(")

# Only identity_guard_demo.py may import evidence_attachment_guard at all
# (Mission #14B Part O: "IdentityGuard may be imported only in the
# optional evaluation layer/CLI, not in the pure extraction function").
IDENTITY_GUARD_ALLOWED_FILES = {"identity_guard_demo.py"}


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
def test_no_universally_forbidden_imports(path: Path):
    if path.name == "__init__.py":
        return
    imports = _imported_module_names(path)
    for forbidden in UNIVERSALLY_FORBIDDEN_IMPORT_SUBSTRINGS:
        offenders = [name for name in imports if forbidden in name]
        assert not offenders, f"{path.name} imports forbidden module(s) matching '{forbidden}': {offenders}"


@pytest.mark.parametrize("path", SELECTION_FILES, ids=lambda p: p.name)
def test_no_universally_forbidden_identifiers(path: Path):
    if path.name == "__init__.py":
        return
    text = path.read_text(encoding="utf-8")
    for identifier in UNIVERSALLY_FORBIDDEN_IDENTIFIERS:
        pattern = r"(?<!\w)" + re.escape(identifier)
        assert not re.search(pattern, text), f"{path.name} references forbidden identifier '{identifier}'"


@pytest.mark.parametrize("path", [p for p in SELECTION_FILES if p.name in PURE_CORE_FILES], ids=lambda p: p.name)
def test_pure_core_never_imports_candidate_fragment_or_identity_guard(path: Path):
    imports = _imported_module_names(path)
    for forbidden in PURE_CORE_ADDITIONAL_FORBIDDEN_IMPORTS:
        offenders = [name for name in imports if forbidden in name]
        assert not offenders, f"{path.name} (pure core) imports forbidden module(s) matching '{forbidden}': {offenders}"
    text = path.read_text(encoding="utf-8")
    for identifier in PURE_CORE_ADDITIONAL_FORBIDDEN_IDENTIFIERS:
        pattern = r"(?<!\w)" + re.escape(identifier)
        assert not re.search(pattern, text), f"{path.name} (pure core) references forbidden identifier '{identifier}'"


@pytest.mark.parametrize(
    "path", [p for p in SELECTION_FILES if p.name not in IDENTITY_GUARD_ALLOWED_FILES], ids=lambda p: p.name
)
def test_only_identity_guard_demo_imports_evidence_attachment_guard(path: Path):
    if path.name == "__init__.py":
        return
    imports = _imported_module_names(path)
    offenders = [name for name in imports if "evidence_attachment_guard" in name]
    assert not offenders, f"{path.name} must not import evidence_attachment_guard - only identity_guard_demo.py may"


def test_fragment_selection_only_imports_from_app_extraction_and_stdlib():
    """The pure Selection core's only intra-RWI import is
    app.extraction.generic_pdf's own runtime types - the exact, intended,
    one-way dependency (Extraction -> Selection), unchanged since Mission
    #13B."""
    path = REPO_ROOT / "app" / "selection" / "fragment_selection.py"
    imports = _imported_module_names(path)
    app_imports = [name for name in imports if name.startswith("app.")]
    assert app_imports == ["app.extraction.generic_pdf"]


def test_structured_extraction_has_no_intra_rwi_imports_at_all():
    """Pure literal-text extraction - depends on nothing else in this
    codebase, only stdlib (re, dataclasses)."""
    path = REPO_ROOT / "app" / "selection" / "structured_extraction.py"
    imports = _imported_module_names(path)
    app_imports = [name for name in imports if name.startswith("app.")]
    assert app_imports == []


def test_candidate_fragment_adapter_imports_exactly_the_authorized_set():
    """Mission #14B Part H explicitly authorizes CandidateFragment
    construction here - and nothing more."""
    path = REPO_ROOT / "app" / "selection" / "candidate_fragment_adapter.py"
    imports = _imported_module_names(path)
    app_imports = {name for name in imports if name.startswith("app.")}
    assert app_imports == {
        "app.selection.fragment_selection",
        "app.selection.structured_extraction",
        "app.services.discovery_candidate_fragment",
    }


def test_no_network_or_raw_sql_capability_anywhere_in_selection():
    for path in SELECTION_FILES:
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "socket." not in text
        assert "requests." not in text
        assert ".execute(" not in text


def test_no_llm_or_subprocess_capability_anywhere_in_selection():
    for path in SELECTION_FILES:
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        for forbidden in ("openai", "anthropic", "subprocess", "os.system", "eval(", "exec("):
            assert forbidden not in text, f"{path.name} references forbidden capability {forbidden!r}"
