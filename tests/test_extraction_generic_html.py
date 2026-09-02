"""RWI Mission #20B - offline tests for app.extraction.generic_html.
Pure function tests only: no network, no database, no filesystem."""

from __future__ import annotations

from app.extraction.generic_pdf import ExtractionStatus
from app.extraction.generic_html import EXTRACTOR_NAME, EXTRACTOR_VERSION, extract_html

DOC_ID = "artifact:test-html:1"


def _extract(html: str, media_type: str = "text/html; charset=UTF-8"):
    return extract_html(html.encode("utf-8"), document_identity=DOC_ID, media_type=media_type)


# --- 1. basic visible HTML text ---


def test_basic_visible_text_extracted():
    doc = _extract("<html><body><p>Hello world.</p></body></html>")
    assert doc.status == ExtractionStatus.SUCCESS
    assert doc.pages[0].text == "Hello world."
    assert doc.extractor_name == EXTRACTOR_NAME
    assert doc.extractor_version == EXTRACTOR_VERSION


# --- 2/3. script/style excluded ---


def test_script_content_excluded():
    doc = _extract("<html><body><p>Visible.</p><script>var x = 'not visible';</script></body></html>")
    assert "not visible" not in doc.pages[0].text
    assert "Visible." in doc.pages[0].text


def test_style_content_excluded():
    doc = _extract("<html><head><style>body { color: red; }</style></head><body><p>Text.</p></body></html>")
    assert "color: red" not in doc.pages[0].text
    assert "Text." in doc.pages[0].text


# --- 4. nav/header/footer retained ---


def test_nav_header_footer_text_retained():
    doc = _extract(
        "<html><body><header>Site Header</header><nav>Home | About</nav>"
        "<main><p>Main content.</p></main><footer>Copyright 2026</footer></body></html>"
    )
    text = doc.pages[0].text
    assert "Site Header" in text
    assert "Home | About" in text
    assert "Main content." in text
    assert "Copyright 2026" in text


# --- 5. entities decoded ---


def test_entities_decoded():
    doc = _extract("<p>Value: &pound;17m &amp; rising.</p>")
    assert "£17m & rising" in doc.pages[0].text


# --- 6. Unicode preserved ---


def test_unicode_preserved():
    doc = _extract("<p>Zurich Flughafen – café naïve résumé 日本語</p>")
    assert "café naïve résumé 日本語" in doc.pages[0].text


# --- 7. NBSP normalized ---


def test_nbsp_normalized_to_ordinary_space():
    doc = _extract("<p>Word1&nbsp;Word2</p>")
    text = doc.pages[0].text
    assert text == "Word1 Word2"
    assert "\xa0" not in text


# --- 8. adjacent block elements do not concatenate words ---


def test_adjacent_block_elements_do_not_concatenate_words():
    doc = _extract("<div>First</div><div>Second</div>")
    text = doc.pages[0].text
    assert "FirstSecond" not in text
    assert "First" in text and "Second" in text


# --- 9. deterministic newline policy ---


def test_deterministic_newline_policy_no_double_blank_lines():
    doc = _extract("<p>One</p>\n\n\n<p>Two</p>")
    text = doc.pages[0].text
    assert "\n\n" not in text
    assert text == "One\nTwo"


# --- 10. lists ---


def test_lists_extracted_with_boundaries():
    doc = _extract("<ul><li>Apple</li><li>Banana</li></ul>")
    text = doc.pages[0].text
    assert "Apple" in text
    assert "Banana" in text
    assert "AppleBanana" not in text


# --- 11. tables ---


def test_tables_extracted_with_boundaries():
    doc = _extract("<table><tr><td>Row1Col1</td><td>Row1Col2</td></tr></table>")
    text = doc.pages[0].text
    assert "Row1Col1" in text
    assert "Row1Col2" in text
    assert "Row1Col1Row1Col2" not in text


# --- 12. malformed/unclosed HTML ---


def test_malformed_unclosed_html_does_not_raise():
    doc = _extract("<html><body><p>Unclosed paragraph <div>Nested without closing p")
    assert doc.status in (ExtractionStatus.SUCCESS, ExtractionStatus.PARTIAL)
    assert "Unclosed paragraph" in doc.pages[0].text
    assert "Nested without closing p" in doc.pages[0].text


# --- 13. no visible text -> NO_TEXT ---


def test_no_visible_text_produces_no_text_status():
    # <title> content is deliberately treated as ordinary text (kept, not
    # excluded - only script/style content is excluded, see module
    # docstring), so this fixture must omit it to produce a genuine
    # zero-text document.
    doc = _extract("<html><head><script>var x=1;</script></head><body></body></html>")
    assert doc.status == ExtractionStatus.NO_TEXT
    assert doc.pages[0].text == ""
    assert doc.page_count == 1


def test_title_text_is_kept_not_excluded():
    """Documents the deliberate design choice: <title> is NOT in
    _SKIP_TAGS, so its text is retained as ordinary content - only
    script/style are excluded (Mission #20B Part H)."""
    doc = _extract("<html><head><title>Page Title Text</title></head><body></body></html>")
    assert "Page Title Text" in doc.pages[0].text


# --- 14. text/html charset parameter accepted ---


def test_charset_parameter_variants_accepted():
    doc = _extract("<p>Hi.</p>", media_type="text/html; charset=UTF-8")
    assert doc.status == ExtractionStatus.SUCCESS
    doc2 = _extract("<p>Hi.</p>", media_type="text/html")
    assert doc2.status == ExtractionStatus.SUCCESS


# --- 15. unknown charset warning/fallback ---


def test_unknown_declared_charset_falls_back_with_warning():
    payload = "<p>Café</p>".encode("utf-8")
    doc = extract_html(payload, document_identity=DOC_ID, media_type="text/html; charset=not-a-real-charset")
    assert doc.status == ExtractionStatus.SUCCESS
    assert any("not-a-real-charset" in w for w in doc.warnings)
    assert "Café" in doc.pages[0].text


# --- 16. replacement decode warning ---


def test_undecodable_bytes_trigger_replacement_warning():
    # Bytes that are invalid in the (correctly declared) UTF-8 charset.
    payload = b"<p>Broken \xff\xfe byte</p>"
    doc = extract_html(payload, document_identity=DOC_ID, media_type="text/html; charset=UTF-8")
    # utf-8 decode of invalid bytes raises UnicodeDecodeError -> falls back to utf-8 replace with a warning.
    assert any("replacement" in w.lower() or "could not decode" in w.lower() for w in doc.warnings)


# --- 17. wrong media type -> UNSUPPORTED_CONTENT ---


def test_wrong_media_type_returns_unsupported_content():
    doc = extract_html(b"%PDF-1.4 not html", document_identity=DOC_ID, media_type="application/pdf")
    assert doc.status == ExtractionStatus.UNSUPPORTED_CONTENT
    assert doc.page_count == 0
    assert doc.pages == ()


# --- 18. one-page representation ---


def test_one_page_representation():
    doc = _extract("<html><body><h1>A</h1><p>B</p><h2>C</h2></body></html>")
    assert doc.page_count == 1
    assert len(doc.pages) == 1
    assert doc.pages[0].page_number == 1


# --- 19. deterministic replay ---


def test_deterministic_replay_same_bytes_same_output():
    html = "<html><body><p>Repeatable &amp; stable.</p></body></html>"
    doc1 = _extract(html)
    doc2 = _extract(html)
    assert doc1.pages[0].text == doc2.pages[0].text
    assert doc1.status == doc2.status


# --- 20. no network/resource loading (behavioral proof) ---


def test_extraction_never_touches_network_even_with_remote_looking_tags():
    html = (
        "<html><head><link rel=\"stylesheet\" href=\"https://example.invalid/should-not-be-fetched.css\">"
        "<script src=\"https://example.invalid/should-not-run.js\"></script></head>"
        "<body><img src=\"https://example.invalid/no.png\"><p>Local text only.</p></body></html>"
    )
    doc = _extract(html)  # would hang/raise if it attempted any network access
    assert "Local text only." in doc.pages[0].text


# --- Provenance ---


def test_document_identity_preserved_verbatim():
    doc = _extract("<p>x</p>")
    assert doc.document_identity == DOC_ID


def test_missing_document_identity_raises():
    import pytest

    with pytest.raises(ValueError):
        extract_html(b"<p>x</p>", document_identity="", media_type="text/html")


# --- Exact wording preservation (no rewriting) ---


def test_wording_not_rewritten_tense_or_wording():
    doc = _extract("<p>The project is scheduled for completion mid-summer 2023.</p>")
    text = doc.pages[0].text
    assert "scheduled for completion mid-summer 2023" in text
    assert "completed mid-summer 2023" not in text
