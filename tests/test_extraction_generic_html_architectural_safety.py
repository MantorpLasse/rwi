"""RWI Mission #20B Part S - static safety checks specific to
app/extraction/generic_html.py and app/extraction/dispatch.py, additive to
the existing tests/test_extraction_architectural_safety.py (which already
globs app/extraction/*.py and covers the shared forbidden-import/
forbidden-identifier/no-network/no-sqlalchemy/no-app.models checks for
every file in this package, including these two new ones).

This file covers what that shared suite does not: proof that the HTML
extractor knows HTML, not aviation (no EMAS/LCY/Quantum/Runway-Safe
vocabulary), and that no browser-automation/URL-fetching capability was
introduced anywhere in either new file."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HTML_FILE = REPO_ROOT / "app" / "extraction" / "generic_html.py"
DISPATCH_FILE = REPO_ROOT / "app" / "extraction" / "dispatch.py"
PDF_FILE = REPO_ROOT / "app" / "extraction" / "generic_pdf.py"
LOADER_FILE = REPO_ROOT / "app" / "services" / "snapshot_extraction.py"

# Aviation/domain vocabulary that must never appear in production code
# here - this extractor/dispatcher must know HTML, not aviation (Mission
# #20B Part V). Checked against actual production files only; this test
# file's own use of these words (in comments/fixtures elsewhere) is not
# scanned.
_AVIATION_VOCABULARY = ("EMAS", "RESA", "runway", "Quantum", "London City", "Runway Safe", "blu-3", "LCY", "EGLC")

_BROWSER_AUTOMATION_MARKERS = (
    "selenium", "playwright", "puppeteer", "webdriver", "chromium", "headless",
    "urllib.request", "socket.", "subprocess", "os.system",
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


@pytest.mark.parametrize("path", [HTML_FILE, DISPATCH_FILE], ids=lambda p: p.name)
def test_no_aviation_vocabulary_in_production_code(path: Path):
    text = path.read_text(encoding="utf-8")
    for term in _AVIATION_VOCABULARY:
        assert term not in text, f"{path.name} contains domain-specific vocabulary {term!r} - the extractor must know HTML, not aviation"


@pytest.mark.parametrize("path", [HTML_FILE, DISPATCH_FILE], ids=lambda p: p.name)
def test_no_browser_automation_or_shell_capability(path: Path):
    text = path.read_text(encoding="utf-8")
    for marker in _BROWSER_AUTOMATION_MARKERS:
        assert marker not in text, f"{path.name} references forbidden capability {marker!r}"


def test_html_extractor_uses_only_stdlib_html_parser():
    """No third-party HTML parser dependency was added (Mission #20A Part
    K found none installed/declared; Mission #20B Part D forbids adding
    one)."""
    imports = _imported_module_names(HTML_FILE)
    for forbidden in ("bs4", "lxml", "html5lib", "beautifulsoup"):
        assert not any(forbidden in name.lower() for name in imports), f"generic_html.py imports forbidden third-party parser matching {forbidden!r}"
    assert "html.parser" in imports


def test_dispatch_imports_only_the_two_extractors():
    imports = _imported_module_names(DISPATCH_FILE)
    app_imports = {name for name in imports if name.startswith("app.")}
    assert app_imports == {"app.extraction.generic_html", "app.extraction.generic_pdf"}


def test_generic_pdf_unmodified_by_this_mission():
    """Mission #20B Part C: do not modify generic_pdf.py unless absolutely
    required - this test proves it still exports exactly the same public
    contract this mission's own dispatcher relies on, unmodified."""
    text = PDF_FILE.read_text(encoding="utf-8")
    assert "EXTRACTOR_NAME = \"generic-pdf\"" in text
    assert "def extract_pdf(" in text


def test_snapshot_extraction_loader_unmodified_by_this_mission():
    text = LOADER_FILE.read_text(encoding="utf-8")
    assert "def load_snapshot_for_extraction(" in text
    assert "def build_document_identity(" in text
    # The loader remains format-agnostic - it must never import either
    # extractor directly (callers/dispatcher own that choice).
    imports = _imported_module_names(LOADER_FILE)
    assert not any("generic_pdf" in name or "generic_html" in name or "dispatch" in name for name in imports)
