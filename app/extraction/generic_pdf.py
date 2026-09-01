"""Generic PDF page-text extraction (RWI Mission #12B).

Pure, DB-free, network-free: `extract_pdf(payload, ...)` takes raw bytes
plus caller-supplied provenance and returns an ExtractedDocument. Never
opens a session, never fetches, never imports CandidateFragment/Source/
SourceAssertion/Signal/Installation or any governance-mutation code.

TEXT FIDELITY (Mission #12B Part F): `ExtractedPage.text` is pdfplumber's
own reconstructed text - a real transformation of PDF drawing/text
objects, not a byte-for-byte copy of anything in `Snapshot.payload`. It
is never called "raw text" for that reason. No RWI normalization layer
(no whitespace-joining, case-folding, punctuation stripping, translation)
is applied - this module deliberately preserves pdfplumber's own output
verbatim, since a destructive/opinionated cleanup step with no real
consumer yet would be exactly the kind of speculative addition this
codebase's own conventions warn against (see
app.services.discovery_candidate_fragment's own docstring on why
`organizations` was never added as a duplicate of `issuers`).

WALL-CLOCK/CPU SAFETY (Mission #12B Part J - investigated, not assumed):
this environment is Windows, where `signal.SIGALRM`/`signal.alarm` do not
exist (confirmed empirically) - there is no portable, reliable way in
this runtime to interrupt a single pathological pdfplumber page-parse
already in progress. A real hard timeout would require process isolation
(e.g. concurrent.futures.ProcessPoolExecutor with future.result(timeout=..)),
but robustly guaranteeing a runaway worker process is actually terminated
(not merely abandoned, still consuming CPU) on this platform requires
real process-lifecycle engineering beyond this V1 slice's scope - exactly
the "substantial process-isolation architecture" this mission's own brief
says not to casually implement. V1 therefore uses an HONEST, WEAKER
protection instead: a wall-clock BUDGET checked BETWEEN pages (never
during a single page's own extract_text() call). This bounds the common
case (many pages, cumulative time) but does NOT protect against a single
pathological page taking arbitrarily long - stated here plainly, not
glossed over. See the Mission #12B HQ report's own Part 11 for the full
reasoning and the residual risk this leaves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from io import BytesIO
from time import perf_counter

import pdfminer.psparser
import pdfplumber
import pdfplumber.utils.exceptions

EXTRACTOR_NAME = "generic-pdf"
EXTRACTOR_VERSION = "0.1"

_SUPPORTED_MEDIA_TYPE = "application/pdf"

# Conservative V1 limits (Mission #12B Part I), justified against real
# measurements, not guessed:
#   - The one real document RWI has actually acquired so far (the CAA
#     LCY ACP-2022-090 PDF, Snapshot 7) is 16 pages, ~35,400 extracted
#     characters (~2,200 chars/page). Real regulatory PDFs (staff
#     reports, environmental assessments) plausibly run larger.
#   - MAX_PAGE_COUNT=200 is generous headroom over every real document
#     seen this session (4 existing test fixtures are 1-4 pages each;
#     the real CAA document is 16) while still bounding worst-case time:
#     at the empirically measured ~0.33s/page (5.2s for 16 real pages),
#     200 pages is roughly 66s worst-case - noticeable but bounded for a
#     human-authorized, one-document-at-a-time CLI operation.
#   - MAX_TEXT_CHARS=2,000,000 assumes up to ~10,000 chars/page (~4.5x
#     the real LCY document's own density) across the full 200-page
#     limit - comfortable headroom for a denser real document without
#     leaving pathological text-expansion unbounded.
#   - EXTRACTION_WALL_CLOCK_BUDGET_SECONDS=120.0 gives ~2x headroom over
#     the 200-page worst-case estimate above. Checked BETWEEN pages only
#     (see module docstring) - not a true interrupting timeout.
MAX_PAGE_COUNT = 200
MAX_TEXT_CHARS = 2_000_000
EXTRACTION_WALL_CLOCK_BUDGET_SECONDS = 120.0

# The real, empirically-validated (Mission #12A: feeding garbage bytes to
# pdfplumber.open() raised exactly PdfminerException) exception set for
# "this is not a valid/readable PDF", never a bare `except Exception`.
# PSException is pdfminer's own common base for PDFPasswordIncorrect/
# PDFEncryptionError/PDFSyntaxError/PDFTypeError/PDFKeyError (confirmed
# via MRO inspection) - catching it covers all of them without needing
# five separate except clauses that would silently miss a future
# addition to that family.
_PDF_PARSE_EXCEPTIONS = (
    pdfplumber.utils.exceptions.MalformedPDFException,
    pdfplumber.utils.exceptions.PdfminerException,
    pdfminer.psparser.PSException,
)


class ExtractionStatus(str, Enum):
    """Extraction-specific outcomes - deliberately NOT AcquisitionRunStatus
    (Mission #12B Part C): a different semantic layer entirely. None of
    these values, nor anything derived from them, implies truth,
    relevance, or evidence."""

    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    NO_TEXT = "NO_TEXT"
    UNSUPPORTED_CONTENT = "UNSUPPORTED_CONTENT"
    PARSE_FAILURE = "PARSE_FAILURE"


@dataclass(frozen=True)
class ExtractedPage:
    """One page's parser-extracted text. `text` is pdfplumber's own
    reconstructed text, verbatim - never renormalized here."""

    page_number: int  # 1-based
    text: str
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise ValueError(f"ExtractedPage.page_number must be 1-based (>=1), got {self.page_number}")


@dataclass(frozen=True)
class ExtractedDocument:
    """One document's full (or partially/un-)extracted page text, bound
    unambiguously to one exact Snapshot via `document_identity`
    (Mission #12B Part D/M) - never to a SearchResult, query, airport
    seed, triage band, or provider rank."""

    document_identity: str
    media_type: str
    extractor_name: str
    extractor_version: str
    pages: tuple[ExtractedPage, ...]
    page_count: int
    status: ExtractionStatus
    warnings: tuple[str, ...] = field(default_factory=tuple)
    parser_library_version: str | None = None

    def __post_init__(self) -> None:
        if not self.document_identity or not self.document_identity.strip():
            raise ValueError("ExtractedDocument.document_identity is required and cannot be empty")
        if self.page_count != len(self.pages):
            raise ValueError(
                f"ExtractedDocument.page_count ({self.page_count}) must equal len(pages) ({len(self.pages)})"
            )
        for index, page in enumerate(self.pages):
            expected_number = index + 1
            if page.page_number != expected_number:
                raise ValueError(
                    f"ExtractedDocument.pages is not contiguous/1-based/ordered: expected page_number "
                    f"{expected_number} at position {index}, got {page.page_number}"
                )


def _empty_result(
    status: ExtractionStatus,
    *,
    document_identity: str,
    media_type: str,
    warnings: tuple[str, ...],
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


def extract_pdf(
    payload: bytes,
    *,
    document_identity: str,
    media_type: str | None,
    max_pages: int = MAX_PAGE_COUNT,
    max_text_chars: int = MAX_TEXT_CHARS,
    wall_clock_budget_seconds: float = EXTRACTION_WALL_CLOCK_BUDGET_SECONDS,
) -> ExtractedDocument:
    """Pure function: PDF bytes + provenance -> ExtractedDocument. Never
    raises for an expected hostile/malformed/encrypted/oversized
    document - all such cases return a non-SUCCESS ExtractedDocument
    instead (Mission #12B Part H). A genuine programming error in this
    function's own code is NOT caught here and will propagate normally,
    remaining distinguishable from a bad document.

    Never flattens pages into one string, never invents line numbers or
    bounding boxes, never keyword-matches EMAS/RESA, never interprets
    headings - see the module docstring for the full non-goals list.
    """
    if not document_identity or not document_identity.strip():
        raise ValueError("extract_pdf requires a non-empty document_identity")

    normalized_media_type = (media_type or "").partition(";")[0].strip().lower()
    if normalized_media_type != _SUPPORTED_MEDIA_TYPE:
        return _empty_result(
            ExtractionStatus.UNSUPPORTED_CONTENT,
            document_identity=document_identity,
            media_type=media_type or "",
            warnings=(f"Unsupported media type for PDF extraction: {media_type!r}",),
        )

    try:
        pdf = pdfplumber.open(BytesIO(payload))
    except _PDF_PARSE_EXCEPTIONS as exc:
        return _empty_result(
            ExtractionStatus.PARSE_FAILURE,
            document_identity=document_identity,
            media_type=media_type or "",
            warnings=(f"PDF could not be opened/parsed: {type(exc).__name__}: {exc}",),
        )

    try:
        total_pages = len(pdf.pages)
        if total_pages == 0:
            return _empty_result(
                ExtractionStatus.NO_TEXT,
                document_identity=document_identity,
                media_type=media_type or "",
                warnings=("PDF parsed successfully but contains zero pages.",),
            )

        pages_to_process = min(total_pages, max_pages)
        document_warnings: list[str] = []
        pages: list[ExtractedPage] = []
        cumulative_chars = 0
        started = perf_counter()
        any_page_failed = False
        # Truncated by the page-count limit counts as "stopped early" for
        # status purposes too, exactly like the wall-clock/text-size
        # limits below - a truncated result must never be reported as
        # SUCCESS (Mission #12B Part I: "Do NOT silently truncate text
        # and report SUCCESS").
        stopped_early = total_pages > max_pages
        if stopped_early:
            document_warnings.append(
                f"Document has {total_pages} pages, exceeding the {max_pages}-page extraction limit; "
                f"only the first {max_pages} pages were extracted."
            )

        for index in range(pages_to_process):
            if perf_counter() - started > wall_clock_budget_seconds:
                document_warnings.append(
                    f"Extraction stopped after {len(pages)} of {total_pages} pages: "
                    f"the {wall_clock_budget_seconds}s wall-clock budget (checked between pages, "
                    "not during a single page's own parse) was exceeded."
                )
                stopped_early = True
                break

            page_number = index + 1
            try:
                raw_text = pdf.pages[index].extract_text()
            except _PDF_PARSE_EXCEPTIONS as exc:
                pages.append(
                    ExtractedPage(
                        page_number=page_number,
                        text="",
                        warnings=(f"page could not be parsed: {type(exc).__name__}: {exc}",),
                    )
                )
                any_page_failed = True
                continue

            page_warnings: list[str] = []
            text = raw_text if raw_text is not None else ""
            if not text:
                page_warnings.append("page produced no text")

            if cumulative_chars + len(text) > max_text_chars:
                document_warnings.append(
                    f"Extraction stopped after {len(pages)} of {total_pages} pages: cumulative extracted "
                    f"text would exceed the {max_text_chars}-character limit."
                )
                stopped_early = True
                break

            cumulative_chars += len(text)
            pages.append(ExtractedPage(page_number=page_number, text=text, warnings=tuple(page_warnings)))

        if not pages:
            status = ExtractionStatus.NO_TEXT if not stopped_early else ExtractionStatus.PARTIAL
        elif stopped_early or any_page_failed:
            status = ExtractionStatus.PARTIAL
        elif all(not p.text for p in pages):
            status = ExtractionStatus.NO_TEXT
        else:
            status = ExtractionStatus.SUCCESS

        return ExtractedDocument(
            document_identity=document_identity,
            media_type=media_type or "",
            extractor_name=EXTRACTOR_NAME,
            extractor_version=EXTRACTOR_VERSION,
            pages=tuple(pages),
            page_count=len(pages),
            status=status,
            warnings=tuple(document_warnings),
            parser_library_version=getattr(pdfplumber, "__version__", None),
        )
    finally:
        pdf.close()
