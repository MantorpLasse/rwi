"""Media-type extraction dispatch (RWI Mission #20B Part M).

Routes a Snapshot's payload/media_type to the correct pure extractor:

    application/pdf  -> app.extraction.generic_pdf.extract_pdf
    text/html        -> app.extraction.generic_html.extract_html
    anything else     -> an UNSUPPORTED_CONTENT ExtractedDocument

This module contains NO parsing logic of its own - it only chooses which
existing, frozen extractor to call, exactly once, based on the media type
already recorded on the Snapshot. It never modifies generic_pdf.py or
generic_html.py, and neither of those modules knows the other, or this
dispatcher, exists - each remains a single-format, independently pure
extractor. This module never touches a database, network, or governance
code of any kind.
"""
from __future__ import annotations

from app.extraction.generic_html import extract_html
from app.extraction.generic_pdf import ExtractedDocument, ExtractionStatus, extract_pdf

_PDF_MEDIA_TYPE = "application/pdf"
_HTML_MEDIA_TYPE = "text/html"

_DISPATCH_EXTRACTOR_NAME = "generic-dispatch"
_DISPATCH_EXTRACTOR_VERSION = "0.1"


def extract_document(payload: bytes, *, document_identity: str, media_type: "str | None") -> ExtractedDocument:
    """Pure dispatch: normalizes `media_type` (stripping any `;charset=...`
    parameter, matching each extractor's own internal normalization) and
    routes to the one matching extractor, called exactly once. An
    unsupported/unrecognized media type never reaches either extractor -
    it returns its own UNSUPPORTED_CONTENT ExtractedDocument directly,
    with an honest `extractor_name` reflecting that neither real extractor
    ran."""
    if not document_identity or not document_identity.strip():
        raise ValueError("extract_document requires a non-empty document_identity")

    normalized = (media_type or "").partition(";")[0].strip().lower()
    if normalized == _PDF_MEDIA_TYPE:
        return extract_pdf(payload, document_identity=document_identity, media_type=media_type)
    if normalized == _HTML_MEDIA_TYPE:
        return extract_html(payload, document_identity=document_identity, media_type=media_type)

    return ExtractedDocument(
        document_identity=document_identity,
        media_type=media_type or "",
        extractor_name=_DISPATCH_EXTRACTOR_NAME,
        extractor_version=_DISPATCH_EXTRACTOR_VERSION,
        pages=(),
        page_count=0,
        status=ExtractionStatus.UNSUPPORTED_CONTENT,
        warnings=(f"Unsupported media type for extraction: {media_type!r}",),
    )
