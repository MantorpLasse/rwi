"""RWI Mission #25J2 - offline tests for
app.selection.manual_range_selection.select_manual_range(). Pure function,
no database, no network."""

from __future__ import annotations

import hashlib

import pytest

from app.extraction.generic_pdf import ExtractedDocument, ExtractedPage, ExtractionStatus
from app.selection.candidate_fragment_adapter import build_candidate_fragment
from app.selection.fragment_selection import SelectionReasonKind
from app.selection.manual_range_selection import (
    MAX_MANUAL_RANGE_CHARS,
    ManualRangeSelectionError,
    select_manual_range,
)

PAGE_TEXT = "Alpha bravo charlie delta echo foxtrot golf 日本語テキスト unicode preserved exactly."


def _doc(text: str = PAGE_TEXT, status: ExtractionStatus = ExtractionStatus.SUCCESS) -> ExtractedDocument:
    return ExtractedDocument(
        document_identity="generic_web:example-key:examplesha256",
        media_type="text/html",
        extractor_name="generic-html",
        extractor_version="0.1",
        pages=(ExtractedPage(page_number=1, text=text, warnings=()),),
        page_count=1,
        status=status,
        warnings=(),
        parser_library_version=None,
    )


# --- 1-6: exact slice / determinism / Unicode / no rewriting -----------


def test_exact_slice_equality():
    doc = _doc()
    fs = select_manual_range(doc, page_number=1, start_char=6, end_char=13)
    assert fs.text == PAGE_TEXT[6:13] == "bravo c"


def test_deterministic_locator():
    doc = _doc()
    fs = select_manual_range(doc, page_number=1, start_char=6, end_char=13)
    assert fs.reasons[0].matched_text == "page:1;chars:6-13"


def test_deterministic_hash_via_candidate_fragment():
    doc = _doc()
    fs = select_manual_range(doc, page_number=1, start_char=6, end_char=13)
    cf1 = build_candidate_fragment(fs)
    cf2 = build_candidate_fragment(fs)
    expected_hash = hashlib.sha256(fs.text.encode("utf-8")).hexdigest()
    assert cf1.identity[2] == expected_hash
    assert cf1.identity == cf2.identity  # same range, same document -> same identity every time


def test_artifact_identity_preserved():
    doc = _doc()
    fs = select_manual_range(doc, page_number=1, start_char=0, end_char=5)
    assert fs.document_identity == doc.document_identity
    cf = build_candidate_fragment(fs)
    assert cf.artifact_identity == doc.document_identity


def test_unicode_preserved_exactly():
    doc = _doc()
    idx = PAGE_TEXT.index("日本語テキスト")
    fs = select_manual_range(doc, page_number=1, start_char=idx, end_char=idx + len("日本語テキスト"))
    assert fs.text == "日本語テキスト"


def test_no_strip_behavior():
    text = "   leading and trailing whitespace preserved   "
    doc = _doc(text=text)
    fs = select_manual_range(doc, page_number=1, start_char=0, end_char=len(text))
    assert fs.text == text  # not .strip()'d
    assert fs.text[0] == " " and fs.text[-1] == " "


# --- 7-13: validation ----------------------------------------------------


def test_negative_start_rejected():
    with pytest.raises(ManualRangeSelectionError):
        select_manual_range(_doc(), page_number=1, start_char=-1, end_char=5)


def test_negative_end_rejected():
    with pytest.raises(ManualRangeSelectionError):
        select_manual_range(_doc(), page_number=1, start_char=0, end_char=-5)


def test_zero_length_rejected():
    with pytest.raises(ManualRangeSelectionError):
        select_manual_range(_doc(), page_number=1, start_char=5, end_char=5)


def test_reversed_range_rejected():
    with pytest.raises(ManualRangeSelectionError):
        select_manual_range(_doc(), page_number=1, start_char=10, end_char=5)


def test_out_of_bounds_rejected():
    doc = _doc()
    with pytest.raises(ManualRangeSelectionError):
        select_manual_range(doc, page_number=1, start_char=0, end_char=len(doc.pages[0].text) + 1)


def test_invalid_page_rejected():
    with pytest.raises(ManualRangeSelectionError):
        select_manual_range(_doc(), page_number=2, start_char=0, end_char=5)


def test_over_limit_range_rejected():
    long_text = "x" * (MAX_MANUAL_RANGE_CHARS + 100)
    doc = _doc(text=long_text)
    with pytest.raises(ManualRangeSelectionError):
        select_manual_range(doc, page_number=1, start_char=0, end_char=len(long_text))


def test_at_limit_range_accepted():
    long_text = "x" * MAX_MANUAL_RANGE_CHARS
    doc = _doc(text=long_text)
    fs = select_manual_range(doc, page_number=1, start_char=0, end_char=MAX_MANUAL_RANGE_CHARS)
    assert len(fs.text) == MAX_MANUAL_RANGE_CHARS


def test_unselectable_document_status_rejected():
    doc = _doc(status=ExtractionStatus.NO_TEXT)
    with pytest.raises(ManualRangeSelectionError):
        select_manual_range(doc, page_number=1, start_char=0, end_char=5)


def test_reason_kind_is_human_manual_range():
    doc = _doc()
    fs = select_manual_range(doc, page_number=1, start_char=0, end_char=5)
    assert fs.reasons[0].kind == SelectionReasonKind.HUMAN_MANUAL_RANGE


# --- 20: no arbitrary raw-text parameter exists --------------------------


def test_no_raw_text_parameter_exists():
    """The function signature itself makes raw-text injection structurally
    impossible - proven by introspection, not merely by convention."""
    import inspect

    sig = inspect.signature(select_manual_range)
    param_names = set(sig.parameters)
    assert param_names == {"document", "page_number", "start_char", "end_char"}
    assert "text" not in param_names
    assert "raw_text" not in param_names
