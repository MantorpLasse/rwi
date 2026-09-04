"""RWI HQ "Discovery Research Loop V1 - Slice 4" - static safety checks
for scripts/fetch_research_candidate.py.

This script is a thin Commander-handoff orchestrator over two already-
existing, already-tested pieces (fetch_discovered_url, extract_document)
- it must not duplicate HTTP I/O, must not duplicate parsing, must not
select/rank a candidate URL on its own, and must not create any governed
row (Source/SourceAssertion/CandidateFragment/Signal/Installation/
ReviewerAction/SignalDisposition). tests/test_generic_web_fetch_architectural_safety.py
already covers the shared forbidden-import/forbidden-identifier checks by
including this file in its own FETCH_FILES list - this file adds the
checks specific to Slice 4's own new concerns only."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET = REPO_ROOT / "scripts" / "fetch_research_candidate.py"


def _imported_module_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_uses_the_generic_dispatcher_not_the_pdf_only_extractor():
    """Must call app.extraction.dispatch.extract_document - never
    app.extraction.generic_pdf.extract_pdf directly (that path cannot
    handle HTML, which is what most Research Loop candidates will be)."""
    imports = _imported_module_names(TARGET)
    assert "app.extraction.dispatch" in imports
    assert "app.extraction.generic_pdf" not in imports
    assert "app.extraction.generic_html" not in imports


def test_reuses_existing_fetch_layer_no_second_http_client():
    """No second HTTP implementation: must import fetch_discovered_url
    from the existing generic_web_fetch service, never construct its own
    httpx/requests client."""
    imports = _imported_module_names(TARGET)
    assert "app.services.generic_web_fetch" in imports
    text = TARGET.read_text(encoding="utf-8")
    for forbidden in ("httpx.Client(", "httpx.get(", "requests.get(", "urllib.request"):
        assert forbidden not in text, f"must not construct a second HTTP client: {forbidden!r} found"


def test_no_forbidden_governance_construction():
    text = TARGET.read_text(encoding="utf-8")
    import re

    for identifier in (
        "Source(", "SourceAssertion(", "Signal(", "Installation(", "CandidateFragment(",
        "ReviewerAction(", "SignalDisposition(", "FragmentSelection(",
    ):
        pattern = r"(?<!\w)" + re.escape(identifier)
        assert not re.search(pattern, text), f"must not construct governed row {identifier!r}"


def test_no_candidate_selection_heuristic():
    """The URL is always the human's own explicit positional argument -
    this script must never rank, score, or auto-pick among multiple
    candidates (Slice 4 Part 2's core constraint)."""
    text = TARGET.read_text(encoding="utf-8")
    for forbidden in ("priority_band ==", "domain_category ==", "sorted(", "max(", "auto_select", "best_candidate"):
        assert forbidden not in text, f"must not rank/auto-select a candidate: {forbidden!r} found"


def test_no_second_round_search_capability():
    text = TARGET.read_text(encoding="utf-8").lower()
    for forbidden in ("searchprovider", "brave", "run_research_loop", "plan_research_search_queries", "round_two", "round2"):
        assert forbidden not in text, f"must not perform a new search round: {forbidden!r} found"


def test_does_not_invoke_selection_or_keep_boundary_directly():
    """Must not bypass the existing human-KEEP boundary by calling
    Selection/CandidateFragment construction itself - that remains a
    separate, later, explicitly human-driven step via
    scripts/review_fragment_selection.py."""
    imports = _imported_module_names(TARGET)
    assert not any("selection" in name.lower() for name in imports)
    assert not any("candidate_fragment" in name.lower() for name in imports)
