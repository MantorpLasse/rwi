"""RWI Mission #13B Part P - offline tests for
app.selection.fragment_selection. No network, no database. Real
LCY-shaped and YTZ-shaped fixtures built directly as ExtractedDocument/
ExtractedPage objects (frozen dataclasses from Mission #12B), mirroring
the actual real Snapshot 7 findings from Mission #13A's recon (the real
"LCY EMAS ACP" repeated footer, the real page-16 bibliography EMAS
mention) - never a live fetch or live extraction."""

from __future__ import annotations

from app.extraction.generic_pdf import ExtractedDocument, ExtractedPage, ExtractionStatus
from app.selection.fragment_selection import (
    DEFAULT_MERGE_GAP_CHARS,
    DEFAULT_WINDOW_CHARS,
    AirportIdentityContext,
    SelectionReasonKind,
    SelectionVocabulary,
    detect_repeated_lines,
    select_fragments,
)

_LCY_IDENTITY = AirportIdentityContext(name="London City Airport", iata_code="LCY", icao_code="EGLC")


def _doc(pages: list[ExtractedPage], *, status: ExtractionStatus = ExtractionStatus.SUCCESS) -> ExtractedDocument:
    return ExtractedDocument(
        document_identity="test-source:testsha256",
        media_type="application/pdf",
        extractor_name="generic-pdf",
        extractor_version="0.1",
        pages=tuple(pages),
        page_count=len(pages),
        status=status,
    )


def _page(n: int, text: str) -> ExtractedPage:
    return ExtractedPage(page_number=n, text=text)


# --- 1/2/3: literal matching ------------------------------------------------


def test_exact_concept_match():
    doc = _doc([_page(1, "The airport is installing an EMAS system this year.")])
    result = select_fragments(doc)
    assert len(result.fragments) == 1
    reasons = result.fragments[0].reasons
    assert any(r.kind == SelectionReasonKind.STRONG_CONCEPT_TERM and r.matched_text == "EMAS" for r in reasons)


def test_case_insensitive_match():
    doc = _doc([_page(1, "the airport is installing an emas system this year.")])
    result = select_fragments(doc)
    assert len(result.fragments) == 1
    assert result.fragments[0].reasons[0].matched_text == "emas"  # literal casing preserved, not normalized


def test_boundary_safe_matching_rejects_embedded_substring():
    doc = _doc([_page(1, "This word is EMASCULATE and should not match the acronym.")])
    result = select_fragments(doc)
    assert result.fragments == ()


def test_boundary_safe_matching_accepts_bounded_token():
    doc = _doc([_page(1, "Reference to (EMAS) in parentheses.")])
    result = select_fragments(doc)
    assert len(result.fragments) == 1


# --- 4: short airport code boundary safety ------------------------------


def test_short_airport_code_does_not_match_inside_unrelated_word():
    doc = _doc([_page(1, "EMAS installation notice: see OLCYX for unrelated reference codes.")])
    result = select_fragments(doc, airport_identity=_LCY_IDENTITY)
    assert len(result.fragments) == 1
    assert not any(r.kind == SelectionReasonKind.AIRPORT_IDENTITY_MATCH for r in result.fragments[0].reasons)


def test_short_airport_code_matches_as_bounded_token():
    doc = _doc([_page(1, "EMAS installation at (LCY) confirmed for this year.")])
    result = select_fragments(doc, airport_identity=_LCY_IDENTITY)
    assert len(result.fragments) == 1
    assert any(r.kind == SelectionReasonKind.AIRPORT_IDENTITY_MATCH and r.matched_text == "LCY" for r in result.fragments[0].reasons)


# --- 5: airport identity is optional context, never a requirement ---------


def test_concept_match_selected_without_any_airport_identity():
    doc = _doc([_page(1, "EMAS system to be installed at the runway end.")])
    result = select_fragments(doc, airport_identity=None)
    assert len(result.fragments) == 1


def test_airport_identity_alone_never_creates_a_fragment():
    """Attention context only - a page mentioning only the airport name,
    with no concept/activity term anywhere, must not be selected."""
    doc = _doc([_page(1, "London City Airport (LCY) welcomes visitors daily.")])
    result = select_fragments(doc, airport_identity=_LCY_IDENTITY)
    assert result.fragments == ()


# --- 6/7: window near page start/end ----------------------------------------


def test_match_near_start_of_page_clamps_window():
    text = "EMAS" + (" filler word" * 200)
    doc = _doc([_page(1, text)])
    result = select_fragments(doc, window_chars=300)
    assert len(result.fragments) == 1
    assert result.fragments[0].start_offset == 0  # clamped, never negative


def test_match_near_end_of_page_clamps_window():
    text = (" filler word" * 200) + " EMAS"
    doc = _doc([_page(1, text)])
    result = select_fragments(doc, window_chars=300)
    assert len(result.fragments) == 1
    assert result.fragments[0].end_offset == len(text)  # clamped, never past page end


# --- 8: exact substring reconstruction from offsets -------------------------


def test_fragment_text_is_exact_substring_of_page_text():
    text = "Some preamble text. EMAS installation planned. Some trailing text."
    doc = _doc([_page(1, text)])
    result = select_fragments(doc)
    for fragment in result.fragments:
        assert fragment.text == text[fragment.start_offset : fragment.end_offset]


# --- 9/10/11: overlap/merge behavior -----------------------------------------


def test_overlapping_windows_merge_into_one_fragment():
    text = "x " * 25 + "EMAS " + "y " * 5 + "RESA " + "z " * 25
    doc = _doc([_page(1, text)])
    result = select_fragments(doc, window_chars=300, merge_gap_chars=100)
    assert len(result.fragments) == 1
    kinds = {r.kind for r in result.fragments[0].reasons}
    assert SelectionReasonKind.STRONG_CONCEPT_TERM in kinds


def test_nearby_windows_within_merge_gap_merge():
    # Two matches whose ±window_chars windows do not overlap directly
    # (window1 ends at offset 54; window2 starts at offset 144 - a 90-char
    # gap) but ARE within merge_gap_chars=100 of each other, so they must
    # merge into one fragment. Exact offsets computed, not guessed.
    filler = ("x " * 95)[:189]
    text = "EMAS " + filler + "RESA"
    assert text.index("EMAS") == 0
    assert text.index("RESA") == 194
    doc = _doc([_page(1, text)])
    result = select_fragments(doc, window_chars=50, merge_gap_chars=100)
    assert len(result.fragments) == 1


def test_distant_matches_remain_separate_fragments():
    text = "EMAS " + ("x " * 2500) + "RESA"
    doc = _doc([_page(1, text)])
    result = select_fragments(doc, window_chars=100, merge_gap_chars=50)
    assert len(result.fragments) == 2


def test_merged_reasons_are_preserved_deterministically():
    text = "EMAS near RESA and also arresting system all together in one spot."
    doc = _doc([_page(1, text)])
    result = select_fragments(doc, window_chars=300, merge_gap_chars=100)
    assert len(result.fragments) == 1
    matched_texts = [r.matched_text for r in result.fragments[0].reasons]
    # Reasons are ordered by the position of the match that produced them.
    assert matched_texts == sorted(matched_texts, key=lambda t: text.index(t))
    # Run twice, confirm identical order.
    result2 = select_fragments(doc, window_chars=300, merge_gap_chars=100)
    assert result.fragments[0].reasons == result2.fragments[0].reasons


# --- 12: (folded into 9-11 above; duplicate reasons deduped) ---------------


def test_duplicate_identical_matches_do_not_duplicate_reasons():
    text = "EMAS mentioned here. EMAS mentioned again nearby."
    doc = _doc([_page(1, text)])
    result = select_fragments(doc, window_chars=300, merge_gap_chars=100)
    assert len(result.fragments) == 1
    emas_reasons = [r for r in result.fragments[0].reasons if r.matched_text == "EMAS"]
    assert len(emas_reasons) == 1


# --- 13/14/15/16: repeated header/footer suppression ------------------------


def _lcy_shaped_pages(n_pages: int = 16) -> list[ExtractedPage]:
    pages = []
    body_pages = {3, 4, 5, 6, 7, 9}
    for i in range(1, n_pages + 1):
        header = f"Page {i} of {n_pages}\n"
        footer = "\nLCY EMAS ACP"
        if i in body_pages:
            body = "This section discusses the EMAS installation and RESA compliance in detail."
        elif i == n_pages:
            body = "20. Appendices\nNATS Design Submission Package titled: Enhanced Material Arrester System (EMAS) Implementation - Phase 1."
        else:
            body = "General administrative content with no runway safety technology discussion."
        pages.append(_page(i, header + body + footer))
    return pages


def test_repeated_footer_is_suppressed_from_matching():
    doc = _doc(_lcy_shaped_pages())
    result = select_fragments(doc)
    assert "LCY EMAS ACP" in result.suppressed_lines
    selected_pages = {f.page_number for f in result.fragments}
    # Pages with no genuine body-text concept mention must NOT be selected
    # merely because of the repeated "LCY EMAS ACP" footer.
    assert 1 not in selected_pages
    assert 2 not in selected_pages


def test_body_pages_still_selected_after_suppression():
    doc = _doc(_lcy_shaped_pages())
    result = select_fragments(doc)
    selected_pages = {f.page_number for f in result.fragments}
    for page_number in (3, 4, 5, 6, 7, 9):
        assert page_number in selected_pages


def test_occasional_repeated_meaningful_line_is_not_over_suppressed():
    """A line repeated on only 2 of many pages (well under the 50%
    fraction and 3-occurrence-adjusted threshold interactions) must not
    be suppressed - it is not a running header/footer."""
    pages = [_page(i, f"Filler page {i}.") for i in range(1, 10)]
    pages[2] = _page(3, "Filler page 3.\nEMAS installation discussed here.")
    pages[6] = _page(7, "Filler page 7.\nEMAS installation discussed here.")
    doc = _doc(pages)
    suppressed = detect_repeated_lines(doc)
    assert "EMAS installation discussed here." not in suppressed


def test_suppression_never_rewrites_original_page_text():
    pages = _lcy_shaped_pages()
    original_texts = [p.text for p in pages]
    doc = _doc(pages)
    select_fragments(doc)
    # The ExtractedDocument/ExtractedPage objects themselves are frozen
    # and therefore structurally cannot be mutated - re-assert their text
    # is unchanged as a behavioral proof, not just a type guarantee.
    assert [p.text for p in doc.pages] == original_texts


def test_context_window_spanning_suppressed_line_preserves_exact_text():
    """A genuine match whose context window happens to extend into a
    suppressed footer line must still include that footer's exact
    original text verbatim in the final fragment - suppression only
    affects which matches TRIGGER selection, never what text is kept.
    Each page's body sentence is distinct (varies by page number) so ONLY
    the standalone "LCY EMAS ACP" line - not the body sentence - is
    repeated across pages and eligible for suppression."""
    pages = [
        _page(i, f"Page header {i}.\nEMAS installation confirmed on page {i}.\nLCY EMAS ACP")
        for i in range(1, 5)
    ]
    doc = _doc(pages)
    result = select_fragments(doc, window_chars=100, merge_gap_chars=50)
    assert "LCY EMAS ACP" in result.suppressed_lines
    assert result.fragments
    fragment = result.fragments[0]
    original_page = doc.pages[fragment.page_number - 1]
    assert fragment.text == original_page.text[fragment.start_offset : fragment.end_offset]
    # The window is generous enough to sweep in the suppressed footer -
    # its exact original text is preserved verbatim when that happens.
    if fragment.end_offset == len(original_page.text):
        assert fragment.text.endswith("LCY EMAS ACP")


# --- 17/18: YTZ-shaped negative fixture --------------------------------------


def test_ytz_shaped_negation_context_preserved_no_conclusion():
    text = (
        "The federal review considered several RESA compliance options. "
        "EMAS was considered as one possible solution, but the landmass "
        "alternative was ultimately approved by the Board."
    )
    doc = _doc([_page(1, text)])
    result = select_fragments(doc)
    assert len(result.fragments) == 1
    fragment = result.fragments[0]
    assert "EMAS was considered" in fragment.text
    assert "landmass" in fragment.text and "approved" in fragment.text
    # No field/reason anywhere states a conclusion.
    for reason in fragment.reasons:
        assert reason.kind in (
            SelectionReasonKind.STRONG_CONCEPT_TERM,
            SelectionReasonKind.WEAK_ACTIVITY_TERM,
            SelectionReasonKind.AIRPORT_IDENTITY_MATCH,
        )


def test_no_semantic_rejection_or_confirmation_field_exists():
    import dataclasses

    from app.selection.fragment_selection import FragmentSelection, SelectionReason

    fragment_fields = {f.name for f in dataclasses.fields(FragmentSelection)}
    reason_fields = {f.name for f in dataclasses.fields(SelectionReason)}
    forbidden = {"confirmed", "rejected", "status", "verified", "conclusion", "truth"}
    assert not (fragment_fields & forbidden)
    assert not (reason_fields & forbidden)


# --- 19: bibliography/reference EMAS mention remains an acceptable selection


def test_bibliography_reference_mention_is_still_selected():
    doc = _doc(_lcy_shaped_pages())
    result = select_fragments(doc)
    last_page_fragments = [f for f in result.fragments if f.page_number == 16]
    assert last_page_fragments
    assert "EMAS" in last_page_fragments[0].text or "Enhanced Material Arrester System" in last_page_fragments[0].text


# --- 20/21: extraction-status gating -----------------------------------------


def test_partial_status_still_selects_from_present_pages():
    doc = _doc([_page(1, "EMAS discussion here.")], status=ExtractionStatus.PARTIAL)
    result = select_fragments(doc)
    assert len(result.fragments) == 1


def test_no_text_status_never_selects():
    doc = _doc([_page(1, "")], status=ExtractionStatus.NO_TEXT)
    result = select_fragments(doc)
    assert result.fragments == ()


def test_unsupported_content_status_never_selects():
    doc = _doc([], status=ExtractionStatus.UNSUPPORTED_CONTENT)
    result = select_fragments(doc)
    assert result.fragments == ()


def test_parse_failure_status_never_selects():
    doc = _doc([], status=ExtractionStatus.PARSE_FAILURE)
    result = select_fragments(doc)
    assert result.fragments == ()


# --- 22: determinism ----------------------------------------------------------


def test_same_input_twice_produces_identical_selection():
    doc = _doc(_lcy_shaped_pages())
    result1 = select_fragments(doc, airport_identity=_LCY_IDENTITY)
    result2 = select_fragments(doc, airport_identity=_LCY_IDENTITY)
    assert result1 == result2


# --- 23/24: configurable vocabulary, local-language, no translation --------


def test_configurable_vocabulary_replaces_default():
    custom_vocab = SelectionVocabulary(strong_terms=("bespoke-term",), weak_terms=())
    doc = _doc([_page(1, "This page discusses a bespoke-term of interest.")])
    result_default = select_fragments(doc)
    result_custom = select_fragments(doc, vocabulary=custom_vocab)
    assert result_default.fragments == ()
    assert len(result_custom.fragments) == 1


def test_local_language_caller_supplied_term_works_without_translation():
    """A caller-supplied non-English term matches literally - no
    translation occurs anywhere in this module."""
    swedish_vocab = SelectionVocabulary(strong_terms=("bromsbädd",), weak_terms=())  # Swedish for "arrestor bed"
    doc = _doc([_page(1, "Flygplatsen installerar en bromsbädd i år.")])
    result = select_fragments(doc, vocabulary=swedish_vocab)
    assert len(result.fragments) == 1
    assert result.fragments[0].reasons[0].matched_text == "bromsbädd"


# --- Determinism of DocumentSelection top-level fields -----------------------


def test_document_selection_carries_version_and_identity():
    doc = _doc([_page(1, "EMAS discussion.")])
    result = select_fragments(doc)
    assert result.document_identity == doc.document_identity
    assert result.selection_version == "0.1"


def test_default_window_and_merge_gap_constants_are_explicit():
    assert DEFAULT_WINDOW_CHARS == 300
    assert DEFAULT_MERGE_GAP_CHARS == 100
