"""RWI Mission #20B - offline tests for app.extraction.dispatch.
Pure function tests only: no network, no database."""

from __future__ import annotations

import pytest

from app.extraction.dispatch import extract_document
from app.extraction.generic_pdf import ExtractionStatus

DOC_ID = "artifact:dispatch-test:1"


def _minimal_pdf() -> bytes:
    content = b"BT /F1 12 Tf 72 700 Td (Hello PDF) Tj ET"
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


# --- 21. dispatch PDF -> existing PDF extractor ---


def test_dispatch_routes_pdf_to_pdf_extractor():
    doc = extract_document(_minimal_pdf(), document_identity=DOC_ID, media_type="application/pdf")
    assert doc.extractor_name == "generic-pdf"
    assert doc.status == ExtractionStatus.SUCCESS
    assert "Hello PDF" in doc.pages[0].text


# --- 22. dispatch HTML -> HTML extractor ---


def test_dispatch_routes_html_to_html_extractor():
    doc = extract_document(
        b"<html><body><p>Hello HTML</p></body></html>",
        document_identity=DOC_ID, media_type="text/html; charset=UTF-8",
    )
    assert doc.extractor_name == "generic-html"
    assert doc.status == ExtractionStatus.SUCCESS
    assert "Hello HTML" in doc.pages[0].text


# --- 23. dispatch unsupported -> UNSUPPORTED_CONTENT ---


def test_dispatch_unsupported_media_type():
    doc = extract_document(b"{}", document_identity=DOC_ID, media_type="application/json")
    assert doc.status == ExtractionStatus.UNSUPPORTED_CONTENT
    assert doc.page_count == 0
    assert doc.extractor_name == "generic-dispatch"


def test_dispatch_missing_media_type_unsupported():
    doc = extract_document(b"whatever", document_identity=DOC_ID, media_type=None)
    assert doc.status == ExtractionStatus.UNSUPPORTED_CONTENT


def test_dispatch_missing_document_identity_raises():
    with pytest.raises(ValueError):
        extract_document(b"<p>x</p>", document_identity="", media_type="text/html")


def test_dispatch_never_double_invokes_both_extractors():
    """Static/behavioral proof: HTML input never reaches the PDF parser
    (which would raise/degrade on non-PDF bytes) and vice versa."""
    html_doc = extract_document(b"<p>text</p>", document_identity=DOC_ID, media_type="text/html")
    assert html_doc.extractor_name != "generic-pdf"
    pdf_doc = extract_document(_minimal_pdf(), document_identity=DOC_ID, media_type="application/pdf")
    assert pdf_doc.extractor_name != "generic-html"
