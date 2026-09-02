"""Generic HTML page-text extraction (RWI Mission #20B, following the
recon in Mission #20A).

Pure, DB-free, network-free: `extract_html(payload, ...)` takes raw bytes
plus caller-supplied provenance and returns an ExtractedDocument - the
exact same frozen contract app.extraction.generic_pdf.extract_pdf()
already returns (imported here, never redefined). Never opens a session,
never fetches, never imports CandidateFragment/Source/SourceAssertion/
Signal/Installation/EvidenceBag or any governance-mutation code. This
module knows HTML, not any particular subject-matter domain: it contains
no airport/aviation-specific vocabulary of any kind and no source-specific
selector logic (see tests/test_extraction_generic_html_architectural_safety.py,
which enforces this by scanning for the exact terms this docstring is
deliberately not naming here).

PARSER: Python stdlib `html.parser.HTMLParser` only (Mission #20A Part K -
no beautifulsoup4/lxml/html5lib is installed or declared anywhere in this
repository, and stdlib's tokenizer is fully sufficient for this V1's
narrow "deterministic source-text extraction" goal). `HTMLParser` never
executes JavaScript, never loads external resources, never follows links,
and never performs a network request - it operates purely on the
in-memory decoded string already handed to `feed()`.

PAGE MODEL: one HTML document -> exactly one ExtractedPage (page_number=1).
HTML has no native pagination; inventing one from sections/headings would
be speculative complexity this module deliberately avoids (Mission #20A
Part G).

TEXT FIDELITY: ExtractedPage.text is this module's own deterministic
text-node reconstruction - never a byte-for-byte copy of Snapshot.payload
(same non-claim generic_pdf.py already makes about pdfplumber's output).
Wording, tense, capitalization, numbers, and currency are never rewritten
- only whitespace is normalized (see _WHITESPACE POLICY below) and HTML
entities are decoded (via HTMLParser's own convert_charrefs=True) exactly
per Mission #20B Part I's frozen contract.

WHITESPACE POLICY (Mission #20B Part I, verbatim contract - do not modify
without a fresh mission establishing a new one):
  1. Text-node content is entity-decoded before final normalization
     (HTMLParser's own convert_charrefs=True does this automatically).
  2. Runs of horizontal whitespace within emitted text collapse to one
     ASCII space.
  3. Non-breaking spaces (U+00A0) are treated as ordinary spaces.
  4. Structural block-tag boundaries (see _BLOCK_TAGS) emit a newline
     separator.
  5. Repeated separators collapse so the final text contains no more than
     one newline between adjacent non-empty text blocks.
  6. Leading/trailing whitespace is stripped from the final document text.
  7. Adjacent inline elements are never force-concatenated into one word -
     this V1 relies on the source HTML's own literal inter-element
     whitespace (real-world prose HTML reliably has this; verified
     against the real Snapshot 8 specimen) rather than inserting
     synthetic spacing at every inline-tag boundary - see the module's
     own "KNOWN LIMITATIONS" note below for the one disclosed edge case
     this does not solve.
  8. No punctuation is introduced.
  9. No tense/capitalization/number/currency rewriting occurs anywhere.

KNOWN LIMITATION (disclosed, not silently omitted): two inline elements
with literally zero whitespace between them in the source markup (e.g.
`<b>Fast</b><i>Car</i>`) may extract as "FastCar" - this V1 does not
insert synthetic spacing at inline-tag boundaries, only at the fixed
block-tag list. No case requiring this was found in the real Snapshot 8
specimen this module was built and tested against.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

from app.extraction.generic_pdf import ExtractedDocument, ExtractedPage, ExtractionStatus

EXTRACTOR_NAME = "generic-html"
EXTRACTOR_VERSION = "0.1"

_SUPPORTED_MEDIA_TYPE = "text/html"

# Mission #20B Part I's own fixed block-tag list, verbatim.
_BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "div", "dl", "dt", "dd",
        "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
        "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table",
        "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    }
)

# Content of these elements is never emitted (Mission #20B Part H) -
# html.parser's own CDATA handling for script/style hands their content to
# handle_data() as opaque text, never re-scanned for nested tags, so
# tracking start/end here is sufficient - no manual nested-tag bookkeeping
# needed.
_SKIP_TAGS = frozenset({"script", "style"})

_META_CHARSET_RE = re.compile(rb'<meta[^>]+charset=["\']?([a-zA-Z0-9_\-]+)', re.IGNORECASE)
_HORIZONTAL_WHITESPACE_RE = re.compile(r"[ \t ]+")
_NEWLINE_SPACING_RE = re.compile(r" *\n *")
_REPEATED_NEWLINE_RE = re.compile(r"\n{2,}")


class _HtmlTextParser(HTMLParser):
    """Deterministic, local, pure text-node collector. Never executes
    JavaScript, never loads a resource, never performs I/O of any kind -
    operates only on the in-memory string handed to feed()."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag in _BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag in _BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        self.chunks.append(data)


def _normalize_text(raw: str) -> str:
    """Applies the module's own frozen whitespace policy (rules 2-6)."""
    text = raw.replace(" ", " ")
    text = _HORIZONTAL_WHITESPACE_RE.sub(" ", text)
    text = _NEWLINE_SPACING_RE.sub("\n", text)
    text = _REPEATED_NEWLINE_RE.sub("\n", text)
    return text.strip()


def _charset_from_media_type(media_type: "str | None") -> "str | None":
    if not media_type:
        return None
    for part in media_type.split(";")[1:]:
        part = part.strip()
        if part.lower().startswith("charset="):
            value = part.split("=", 1)[1].strip().strip('"').strip("'")
            return value or None
    return None


def _sniff_html_charset(payload: bytes) -> "str | None":
    """Best-effort declared-charset sniff from the raw bytes' own <meta>
    tag, scanning only the first 2048 bytes (charset declarations always
    appear early in a well-formed document's <head>) - never a full parse
    before the charset needed to perform that parse is even known."""
    head = payload[:2048]
    match = _META_CHARSET_RE.search(head)
    if match is None:
        return None
    try:
        return match.group(1).decode("ascii")
    except UnicodeDecodeError:  # pragma: no cover - a charset name is never non-ASCII in practice
        return None


def _decode_payload(payload: bytes, media_type: "str | None") -> "tuple[str, tuple[str, ...]]":
    """Deterministic decode priority (Mission #20B Part F): (1) explicit
    charset parameter from media_type, (2) HTML-declared charset via a
    <meta> sniff, (3) UTF-8 fallback. An unknown/invalid declared charset
    falls through to UTF-8 with replacement, never silently discarding
    bytes without a warning."""
    warnings: list[str] = []
    charset = _charset_from_media_type(media_type)
    source = "HTTP Content-Type charset parameter"
    if not charset:
        charset = _sniff_html_charset(payload)
        source = "HTML-declared <meta> charset"
    if not charset:
        charset = "utf-8"
        source = "fallback default"

    try:
        return payload.decode(charset), tuple(warnings)
    except (LookupError, UnicodeDecodeError) as exc:
        warnings.append(
            f"Declared charset {charset!r} ({source}) could not decode the payload "
            f"({type(exc).__name__}); fell back to UTF-8 with replacement characters."
        )
        return payload.decode("utf-8", errors="replace"), tuple(warnings)


def _empty_result(
    status: ExtractionStatus,
    *,
    document_identity: str,
    media_type: str,
    warnings: "tuple[str, ...]",
) -> ExtractedDocument:
    return ExtractedDocument(
        document_identity=document_identity,
        media_type=media_type or "",
        extractor_name=EXTRACTOR_NAME,
        extractor_version=EXTRACTOR_VERSION,
        pages=(),
        page_count=0,
        status=status,
        warnings=warnings,
    )


def extract_html(payload: bytes, *, document_identity: str, media_type: "str | None") -> ExtractedDocument:
    """Pure function: HTML bytes + provenance -> ExtractedDocument, using
    the exact same frozen contract app.extraction.generic_pdf.extract_pdf()
    returns. Never raises for an expected hostile/malformed/undecodable
    document - all such cases return a non-SUCCESS ExtractedDocument
    instead, matching generic_pdf.py's own discipline. A genuine
    programming error in this function's own code is NOT caught here.

    Never invents pagination, never keyword-matches any subject-matter
    vocabulary, never summarizes/translates/rewrites wording - see the
    module docstring for the full non-goals list.
    """
    if not document_identity or not document_identity.strip():
        raise ValueError("extract_html requires a non-empty document_identity")

    normalized_media_type = (media_type or "").partition(";")[0].strip().lower()
    if normalized_media_type != _SUPPORTED_MEDIA_TYPE:
        return _empty_result(
            ExtractionStatus.UNSUPPORTED_CONTENT,
            document_identity=document_identity,
            media_type=media_type or "",
            warnings=(f"Unsupported media type for HTML extraction: {media_type!r}",),
        )

    decoded_text, decode_warnings = _decode_payload(payload, media_type)

    parser = _HtmlTextParser()
    try:
        parser.feed(decoded_text)
        parser.close()
    except Exception as exc:  # html.parser is extremely tolerant; a real exception here is rare but honestly covered
        return _empty_result(
            ExtractionStatus.PARSE_FAILURE,
            document_identity=document_identity,
            media_type=media_type or "",
            warnings=(f"HTML could not be parsed: {type(exc).__name__}: {exc}",) + decode_warnings,
        )

    text = _normalize_text("".join(parser.chunks))

    page_warnings: tuple[str, ...] = () if text else ("page produced no text",)
    status = ExtractionStatus.SUCCESS if text else ExtractionStatus.NO_TEXT

    page = ExtractedPage(page_number=1, text=text, warnings=page_warnings)

    return ExtractedDocument(
        document_identity=document_identity,
        media_type=media_type or "",
        extractor_name=EXTRACTOR_NAME,
        extractor_version=EXTRACTOR_VERSION,
        pages=(page,),
        page_count=1,
        status=status,
        warnings=decode_warnings,
        parser_library_version=None,  # stdlib html.parser has no separate distributable version
    )
