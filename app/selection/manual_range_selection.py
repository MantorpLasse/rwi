"""Human exact-range fragment selection (RWI Mission #25J2).

Machine Selection (app.selection.fragment_selection.select_fragments())
is a recommendation, not authority - Mission #25H's own design analysis.
This module is the provenance-safe answer to "Selection missed a useful
passage; inspect this exact preserved range instead": a human supplies
ONLY coordinates into an already-extracted, immutable document -
`page_number`, `start_char`, `end_char` - and this module derives the
exact text itself. There is no parameter anywhere in this module's public
function for human-supplied raw text; `select_manual_range()`'s own
signature makes that structurally impossible, not merely disciplined by
convention.

Returns the EXACT SAME `FragmentSelection` type machine Selection itself
produces (app.selection.fragment_selection.FragmentSelection) - not a
parallel shape - so a manually-selected fragment converges on the
identical downstream contract (app.selection.candidate_fragment_adapter.
build_candidate_fragment(), app.selection.review.apply_keep_decisions())
with zero special-casing anywhere below this module. Nothing downstream
of a FragmentSelection object can tell, or needs to tell, whether it came
from machine Selection or a human exact range.

`text` is always exactly `page.text[start_char:end_char]` - no
`.strip()`, no Unicode normalization, no translation, no whitespace
rewriting. `FragmentSelection.__post_init__` itself re-verifies
`len(text) == end_offset - start_offset`, so any accidental slicing bug
here would fail loud, not silently produce mismatched provenance.

A new `SelectionReasonKind.HUMAN_MANUAL_RANGE` member (added in
app.selection.fragment_selection, not a Selection VOCABULARY change -
no new STRONG_CONCEPT_TERMS/WEAK_ACTIVITY_TERMS string was added; this is
a different axis entirely, the CATEGORY of why a fragment exists, never
read by select_fragments()'s own term-matching logic) is used because
`FragmentSelection` itself requires at least one real `SelectionReason` -
fabricating a `strong_concept_term`/`weak_activity_term` match that did
not actually occur would misrepresent provenance; a human choosing this
range is the actual, honest reason.
"""
from __future__ import annotations

from app.extraction.generic_pdf import ExtractedDocument, ExtractionStatus
from app.selection.fragment_selection import FragmentSelection, SelectionReason, SelectionReasonKind

__all__ = ["ManualRangeSelectionError", "MAX_MANUAL_RANGE_CHARS", "select_manual_range"]

# Conservative, explicit V1 bound (Mission #25J2 Part F): no existing
# repository constant already means "maximum single reviewable fragment
# size" - DEFAULT_WINDOW_CHARS (300) is machine Selection's own
# context-window sizing, a different concept, not a hard cap on a whole
# fragment. Real machine-selected fragments observed this session ranged
# 285-1258 chars; 4000 is generously above that (room for a genuinely
# longer multi-paragraph human-reviewed passage) while still refusing an
# accidental "select nearly the whole document" range.
MAX_MANUAL_RANGE_CHARS = 4000

_SELECTABLE_STATUSES = frozenset({ExtractionStatus.SUCCESS, ExtractionStatus.PARTIAL})


class ManualRangeSelectionError(ValueError):
    """Raised for any invalid manual-range request. Always raised BEFORE
    any FragmentSelection is constructed - fails closed, exactly like
    every other validation boundary in this pipeline."""


def select_manual_range(
    document: ExtractedDocument, *, page_number: int, start_char: int, end_char: int
) -> FragmentSelection:
    """The one function in this module. Validates, slices, and returns a
    real FragmentSelection - never a second, parallel fragment shape.

    Validation order (fails closed at the first violation):
      1. document.status must be SUCCESS or PARTIAL (mirrors
         select_fragments()'s own _SELECTABLE_STATUSES gate exactly - a
         human cannot manually select from a document extraction itself
         already marked unusable).
      2. page_number must refer to a real page in `document.pages`.
      3. start_char/end_char must be non-negative integers with
         start_char < end_char (rejects negative, zero-length, and
         reversed ranges in one comparison).
      4. end_char must not exceed the page's own text length.
      5. The derived text must not exceed MAX_MANUAL_RANGE_CHARS.
    """
    if document.status not in _SELECTABLE_STATUSES:
        raise ManualRangeSelectionError(
            f"Document status {document.status.value!r} is not selectable "
            f"(must be one of {sorted(s.value for s in _SELECTABLE_STATUSES)})"
        )

    page = next((p for p in document.pages if p.page_number == page_number), None)
    if page is None:
        available = sorted(p.page_number for p in document.pages)
        raise ManualRangeSelectionError(f"No page {page_number!r} in this document (available pages: {available})")

    if start_char < 0:
        raise ManualRangeSelectionError(f"start_char must be >= 0, got {start_char}")
    if end_char < 0:
        raise ManualRangeSelectionError(f"end_char must be >= 0, got {end_char}")
    if start_char >= end_char:
        raise ManualRangeSelectionError(
            f"start_char must be strictly less than end_char, got start_char={start_char}, end_char={end_char}"
        )
    if end_char > len(page.text):
        raise ManualRangeSelectionError(
            f"end_char={end_char} exceeds page {page_number} text length ({len(page.text)})"
        )

    text = page.text[start_char:end_char]
    if not text:
        raise ManualRangeSelectionError("Derived text is empty")
    if len(text) > MAX_MANUAL_RANGE_CHARS:
        raise ManualRangeSelectionError(
            f"Derived text is {len(text)} chars, exceeding the MAX_MANUAL_RANGE_CHARS bound ({MAX_MANUAL_RANGE_CHARS})"
        )

    return FragmentSelection(
        document_identity=document.document_identity,
        page_number=page_number,
        start_offset=start_char,
        end_offset=end_char,
        text=text,
        reasons=(
            SelectionReason(
                kind=SelectionReasonKind.HUMAN_MANUAL_RANGE,
                matched_text=f"page:{page_number};chars:{start_char}-{end_char}",
            ),
        ),
    )
