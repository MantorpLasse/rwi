"""RWI Mission #12B Part S - offline tests for
app.extraction.generic_pdf. No network, no database. Real PDF fixtures
only (existing repo fixtures + small synthetic ones built here) - no
scanned/adversarial input is downloaded or fabricated in a way that could
exhaust the test environment."""

from __future__ import annotations

import os
from io import BytesIO

import pdfplumber
import pytest

from app.extraction.generic_pdf import (
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    MAX_PAGE_COUNT,
    MAX_TEXT_CHARS,
    ExtractedDocument,
    ExtractedPage,
    ExtractionStatus,
    extract_pdf,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _read(name: str) -> bytes:
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


def _build_minimal_pdf(text: str | None) -> bytes:
    """Hand-built, minimal, valid one-page PDF - no PDF-generation library
    is installed in this environment (reportlab/pypdf/fpdf2 all absent;
    not added as a new dependency merely for a test fixture). `text`, if
    given, is encoded as Latin-1/WinAnsiEncoding (the standard Type1
    Helvetica font's encoding - supports accented Western-European
    characters as single bytes, a real, legitimate non-ASCII test
    without needing a full embedded-Unicode-font PDF). `text=None`
    produces a page with an empty content stream (genuinely no text
    objects at all) - the real "image-only/no-text" shape. Verified by
    direct round-trip against the real pdfplumber in this environment
    before being trusted here."""
    content = b"" if text is None else (b"BT /F1 12 Tf 72 700 Td (" + text.encode("latin-1") + b") Tj ET")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010} 00000 n \n".encode()
    out += b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\n"
    out += b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF"
    return bytes(out)


# --- MODEL --------------------------------------------------------------


def test_extracted_page_is_frozen():
    page = ExtractedPage(page_number=1, text="hello")
    with pytest.raises(Exception):
        page.text = "changed"  # type: ignore[misc]


def test_extracted_document_is_frozen():
    doc = ExtractedDocument(
        document_identity="x:y", media_type="application/pdf", extractor_name="a", extractor_version="1",
        pages=(), page_count=0, status=ExtractionStatus.NO_TEXT,
    )
    with pytest.raises(Exception):
        doc.status = ExtractionStatus.SUCCESS  # type: ignore[misc]


def test_page_number_must_be_1_based():
    with pytest.raises(ValueError):
        ExtractedPage(page_number=0, text="x")
    with pytest.raises(ValueError):
        ExtractedPage(page_number=-1, text="x")


def test_page_count_must_match_len_pages():
    with pytest.raises(ValueError):
        ExtractedDocument(
            document_identity="x:y", media_type="application/pdf", extractor_name="a", extractor_version="1",
            pages=(ExtractedPage(1, "a"),), page_count=2, status=ExtractionStatus.SUCCESS,
        )


def test_pages_must_be_contiguous_and_ordered():
    with pytest.raises(ValueError):
        ExtractedDocument(
            document_identity="x:y", media_type="application/pdf", extractor_name="a", extractor_version="1",
            pages=(ExtractedPage(1, "a"), ExtractedPage(3, "c")), page_count=2, status=ExtractionStatus.SUCCESS,
        )


def test_document_identity_required():
    with pytest.raises(ValueError):
        ExtractedDocument(
            document_identity="", media_type="application/pdf", extractor_name="a", extractor_version="1",
            pages=(), page_count=0, status=ExtractionStatus.NO_TEXT,
        )


# --- PDF: normal multi-page, real fixtures ------------------------------


def test_normal_multipage_fixture_extracts_success():
    payload = _read("mac_granicus_anoka_runway_18_36_bid_memo_sample.pdf")
    doc = extract_pdf(payload, document_identity="key:sha", media_type="application/pdf")
    assert doc.status in (ExtractionStatus.SUCCESS, ExtractionStatus.PARTIAL)
    assert doc.page_count >= 1


def test_page_numbering_is_1_based_and_contiguous():
    payload = _read("faa_construction_report_sample.pdf")
    doc = extract_pdf(payload, document_identity="key:sha", media_type="application/pdf")
    assert [p.page_number for p in doc.pages] == list(range(1, doc.page_count + 1))


def test_extractor_name_and_version_exposed():
    payload = _read("aip_grant_sample.pdf")
    doc = extract_pdf(payload, document_identity="key:sha", media_type="application/pdf")
    assert doc.extractor_name == EXTRACTOR_NAME
    assert doc.extractor_version == EXTRACTOR_VERSION
    assert doc.parser_library_version == pdfplumber.__version__


def test_parser_text_matches_pdfplumber_directly():
    """Fidelity check: ExtractedPage.text must be byte-for-byte identical
    to calling pdfplumber directly - no RWI renormalization layer."""
    payload = _read("aip_grant_sample.pdf")
    doc = extract_pdf(payload, document_identity="key:sha", media_type="application/pdf")
    with pdfplumber.open(BytesIO(payload)) as pdf:
        expected = [(p.extract_text() or "") for p in pdf.pages]
    assert [p.text for p in doc.pages] == expected


def test_deterministic_repeat_extraction():
    payload = _read("faa_construction_report_sample.pdf")
    doc1 = extract_pdf(payload, document_identity="key:sha", media_type="application/pdf")
    doc2 = extract_pdf(payload, document_identity="key:sha", media_type="application/pdf")
    assert doc1 == doc2


# --- PDF: non-English/accented text (hand-built, verified fixture) -------


def test_accented_non_ascii_text_preserved_exactly():
    """Real, non-ASCII (Western-European accented) text through a
    genuinely valid PDF, round-trip-verified against pdfplumber directly
    before being trusted in this test (see _build_minimal_pdf's own
    docstring for why WinAnsiEncoding, not full CJK, is used here)."""
    payload = _build_minimal_pdf("café münchen")
    doc = extract_pdf(payload, document_identity="key:sha", media_type="application/pdf")
    assert doc.status == ExtractionStatus.SUCCESS
    assert doc.pages[0].text == "café münchen"


# --- PDF: empty page ------------------------------------------------------


def test_none_from_extract_text_becomes_empty_string_with_warning(monkeypatch: pytest.MonkeyPatch):
    class _FakePage:
        def extract_text(self):
            return None

    class _FakePdf:
        pages = [_FakePage()]

        def close(self):
            pass

    monkeypatch.setattr("app.extraction.generic_pdf.pdfplumber.open", lambda *_a, **_k: _FakePdf())
    doc = extract_pdf(b"irrelevant", document_identity="key:sha", media_type="application/pdf")
    assert doc.status == ExtractionStatus.NO_TEXT
    assert doc.pages[0].text == ""
    assert "page produced no text" in doc.pages[0].warnings


# --- PDF: malformed ---------------------------------------------------------


def test_malformed_pdf_bytes_do_not_crash_caller():
    doc = extract_pdf(b"not a real pdf at all", document_identity="key:sha", media_type="application/pdf")
    assert doc.status == ExtractionStatus.PARSE_FAILURE
    assert doc.pages == ()
    assert doc.warnings


def test_malformed_pdf_result_explains_what_went_wrong():
    doc = extract_pdf(b"garbage garbage garbage", document_identity="key:sha", media_type="application/pdf")
    assert doc.status == ExtractionStatus.PARSE_FAILURE
    assert any("could not be opened" in w for w in doc.warnings)


# --- PDF: encrypted/password-protected --------------------------------
#
# No encryption library is installed in this environment (pypdf absent;
# not added as a new dependency merely for a test fixture) to build a
# genuinely encrypted PDF from scratch. Real, honest coverage achieved
# instead: mock pdfplumber.open() to raise the REAL pdfminer exception
# class (imported from the actual installed library, not a fake) that
# #12A empirically confirmed exists for this exact case
# (pdfminer.pdfdocument.PDFPasswordIncorrect, a real PSException
# subclass) - proving the extractor's own exception-handling path
# genuinely catches it via the PSException base class. This does NOT
# prove pdfplumber correctly detects every real-world encrypted PDF
# shape (that would require a real encrypted fixture) - stated honestly
# as a residual test-coverage gap, not glossed over.


def test_encrypted_pdf_is_rejected_safely(monkeypatch: pytest.MonkeyPatch):
    import pdfminer.pdfdocument

    def fake_open(*_a, **_k):
        raise pdfminer.pdfdocument.PDFPasswordIncorrect("no password supplied")

    monkeypatch.setattr("app.extraction.generic_pdf.pdfplumber.open", fake_open)
    doc = extract_pdf(b"irrelevant", document_identity="key:sha", media_type="application/pdf")
    assert doc.status == ExtractionStatus.PARSE_FAILURE
    assert doc.pages == ()
    assert any("PDFPasswordIncorrect" in w for w in doc.warnings)


# --- PDF: unsupported content type ----------------------------------------


def test_non_pdf_media_type_is_unsupported_content():
    doc = extract_pdf(b"<html></html>", document_identity="key:sha", media_type="text/html")
    assert doc.status == ExtractionStatus.UNSUPPORTED_CONTENT
    assert doc.pages == ()


def test_missing_media_type_is_unsupported_content():
    doc = extract_pdf(_read("aip_grant_sample.pdf"), document_identity="key:sha", media_type=None)
    assert doc.status == ExtractionStatus.UNSUPPORTED_CONTENT


def test_media_type_with_charset_suffix_still_recognized_as_pdf():
    payload = _read("aip_grant_sample.pdf")
    doc = extract_pdf(payload, document_identity="key:sha", media_type="application/pdf; charset=binary")
    assert doc.status != ExtractionStatus.UNSUPPORTED_CONTENT


# --- PDF: image-only / no-text (genuinely empty content stream) -----------


def test_valid_pdf_with_no_extractable_text_is_no_text_not_failure():
    """A page with a genuinely empty content stream (no text objects at
    all - the real shape a scanned/image-only PDF's text layer has) must
    produce NO_TEXT, never PARSE_FAILURE - the document itself is
    perfectly valid, it just has nothing pdfplumber can read as text."""
    payload = _build_minimal_pdf(None)
    doc = extract_pdf(payload, document_identity="key:sha", media_type="application/pdf")
    assert doc.status == ExtractionStatus.NO_TEXT
    assert doc.pages != ()  # the page WAS parsed - it just had no text
    assert doc.pages[0].text == ""
    assert "page produced no text" in doc.pages[0].warnings


# --- PDF: page-count limit -------------------------------------------------


def test_page_count_limit_stops_deterministically(monkeypatch: pytest.MonkeyPatch):
    class _FakePage:
        def __init__(self, i):
            self._i = i

        def extract_text(self):
            return f"page {self._i}"

    class _FakePdf:
        pages = [_FakePage(i) for i in range(10)]

        def close(self):
            pass

    monkeypatch.setattr("app.extraction.generic_pdf.pdfplumber.open", lambda *_a, **_k: _FakePdf())
    doc = extract_pdf(b"irrelevant", document_identity="key:sha", media_type="application/pdf", max_pages=3)
    assert doc.status == ExtractionStatus.PARTIAL
    assert doc.page_count == 3
    assert any("extraction limit" in w for w in doc.warnings)


# --- PDF: cumulative text-size limit ---------------------------------------


def test_text_size_limit_stops_deterministically(monkeypatch: pytest.MonkeyPatch):
    class _FakePage:
        def extract_text(self):
            return "x" * 1000

    class _FakePdf:
        pages = [_FakePage() for _ in range(10)]

        def close(self):
            pass

    monkeypatch.setattr("app.extraction.generic_pdf.pdfplumber.open", lambda *_a, **_k: _FakePdf())
    doc = extract_pdf(b"irrelevant", document_identity="key:sha", media_type="application/pdf", max_text_chars=2500)
    assert doc.status == ExtractionStatus.PARTIAL
    assert doc.page_count <= 3  # 2 full pages = 2000 chars fits; 3rd would exceed 2500
    assert any("character limit" in w for w in doc.warnings)
    assert not doc.pages or doc.page_count * 1000 <= 2500


def test_no_silent_truncation_reported_as_success(monkeypatch: pytest.MonkeyPatch):
    """A limit-truncated result must never claim SUCCESS."""
    class _FakePage:
        def extract_text(self):
            return "y" * 100

    class _FakePdf:
        pages = [_FakePage() for _ in range(5)]

        def close(self):
            pass

    monkeypatch.setattr("app.extraction.generic_pdf.pdfplumber.open", lambda *_a, **_k: _FakePdf())
    doc = extract_pdf(b"irrelevant", document_identity="key:sha", media_type="application/pdf", max_pages=2)
    assert doc.status != ExtractionStatus.SUCCESS


# --- PDF: wall-clock budget (between-page check, mocked clock) ------------


def test_wall_clock_budget_stops_between_pages_not_mid_page(monkeypatch: pytest.MonkeyPatch):
    class _FakePage:
        def extract_text(self):
            return "text"

    class _FakePdf:
        pages = [_FakePage() for _ in range(5)]

        def close(self):
            pass

    monkeypatch.setattr("app.extraction.generic_pdf.pdfplumber.open", lambda *_a, **_k: _FakePdf())

    calls = [0.0]

    def fake_perf_counter():
        calls[0] += 100.0
        return calls[0]

    monkeypatch.setattr("app.extraction.generic_pdf.perf_counter", fake_perf_counter)
    doc = extract_pdf(
        b"irrelevant", document_identity="key:sha", media_type="application/pdf", wall_clock_budget_seconds=5.0
    )
    assert doc.status in (ExtractionStatus.PARTIAL, ExtractionStatus.NO_TEXT)
    assert any("wall-clock budget" in w for w in doc.warnings)


# --- PARTIAL: mixed per-page failure ---------------------------------------


def test_one_bad_page_among_good_pages_yields_partial_not_total_failure(monkeypatch: pytest.MonkeyPatch):
    import pdfminer.psparser

    class _GoodPage:
        def extract_text(self):
            return "good text"

    class _BadPage:
        def extract_text(self):
            raise pdfminer.psparser.PSException("boom")

    class _FakePdf:
        pages = [_GoodPage(), _BadPage(), _GoodPage()]

        def close(self):
            pass

    monkeypatch.setattr("app.extraction.generic_pdf.pdfplumber.open", lambda *_a, **_k: _FakePdf())
    doc = extract_pdf(b"irrelevant", document_identity="key:sha", media_type="application/pdf")
    assert doc.status == ExtractionStatus.PARTIAL
    assert doc.page_count == 3
    assert doc.pages[1].text == ""
    assert doc.pages[1].warnings
    assert doc.pages[0].text == "good text"
    assert doc.pages[2].text == "good text"


# --- Architecture-adjacent: does not raise for programming-unrelated cases -


def test_unexpected_programming_error_is_not_swallowed(monkeypatch: pytest.MonkeyPatch):
    """A genuine bug (e.g. TypeError from RWI's own code, not from
    pdfplumber's parse exceptions) must remain visible, never silently
    turned into a PARSE_FAILURE result."""

    class _FakePdf:
        @property
        def pages(self):
            raise TypeError("not a parser failure - a real bug")

        def close(self):
            pass

    monkeypatch.setattr("app.extraction.generic_pdf.pdfplumber.open", lambda *_a, **_k: _FakePdf())
    with pytest.raises(TypeError):
        extract_pdf(b"irrelevant", document_identity="key:sha", media_type="application/pdf")
