"""RWI HQ "Bounded Official-Hub Follow-Up Discovery" mission - static
safety checks for app/services/official_hub_followup.py.

This module performs a real, bounded, read-only HTTP fetch - unlike a
pure app/discovery/*.py module, it legitimately needs the safe transport
(app.acquisition.generic_web) and the robots.txt check
(app.services.generic_web_fetch.check_robots_txt_allows). What it must
NEVER do is cross into governed persistence: no Session/engine
construction, no ORM model import, no AcquisitionService, no Snapshot/
Source/SourceAssertion/Signal/Installation/Airport/ReviewerAction
creation, and no publication/governance-mutation code of any kind."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET = REPO_ROOT / "app" / "services" / "official_hub_followup.py"

FORBIDDEN_IMPORT_SUBSTRINGS = (
    "app.models",
    "app.database",
    "app.services.acquisition",
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
    "signal_publication",
    "sqlalchemy",
)

FORBIDDEN_IDENTIFIERS = (
    "Session(",
    "create_engine(",
    "session.add(",
    "session.commit(",
    "session.flush(",
    "AcquisitionService(",
    "Snapshot(",
    "Source(",
    "SourceAssertion(",
    "Signal(",
    "Installation(",
    "Airport(",
    "ReviewerAction(",
    "SignalDisposition(",
    "subprocess",
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
        assert not offenders, f"official_hub_followup.py imports forbidden module(s) matching '{forbidden}': {offenders}"


def test_no_forbidden_identifiers():
    text = TARGET.read_text(encoding="utf-8")
    for identifier in FORBIDDEN_IDENTIFIERS:
        assert identifier not in text, f"official_hub_followup.py references forbidden identifier '{identifier}'"


def test_does_not_construct_a_second_search_or_dedup_or_triage_type():
    """No parallel SearchResult/SearchQuery/DedupedResult/TriagedResult
    class is defined here - only reused."""
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))
    class_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    forbidden_class_names = {"SearchResult", "SearchOutcome", "TriagedResult", "DedupedResult", "SearchQuery"}
    assert not (class_names & forbidden_class_names), f"must not redefine: {class_names & forbidden_class_names}"


def test_no_recursive_fetch_function_defined():
    """No second-round/recursive fetch entry point exists as actual code
    (function/class names, not docstring prose, which legitimately
    explains what this module does NOT do - e.g. "not a crawler"). The
    real behavioral guarantee (fetching a hub's own extracted links is
    never attempted) is proven functionally in
    tests/test_official_hub_followup.py's own
    test_fixture1_no_recursive_fetch_only_one_stream_call."""
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))
    defined_names = {
        node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    }
    forbidden_names = {"crawl", "spider", "sitemap_fetch", "recursive_fetch", "second_hop_fetch"}
    assert not (defined_names & forbidden_names), f"forbidden capability defined: {defined_names & forbidden_names}"


def test_no_js_or_browser_automation():
    text = TARGET.read_text(encoding="utf-8")
    for forbidden in ("selenium", "playwright", "puppeteer", "webdriver", "execute_script"):
        assert forbidden.lower() not in text.lower(), f"official_hub_followup.py references forbidden capability {forbidden!r}"


def test_reuses_existing_safe_transport_not_a_new_one():
    """Must import the existing safe primitives, never hand-roll a new
    unpinned httpx.Client for an arbitrary destination."""
    imports = _imported_module_names(TARGET)
    assert "app.acquisition.generic_web" in imports
    text = TARGET.read_text(encoding="utf-8")
    assert "httpx.Client()" not in text.replace(" ", "")  # never a bare, unpinned client construction
