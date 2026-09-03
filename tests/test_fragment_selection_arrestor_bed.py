"""RWI Mission #25J3 - offline tests for the "arrestor bed"/"arresting
bed" Selection vocabulary addition. No network, no database.

The Roland Garros regression fixture mirrors the REAL Snapshot 10 wording
captured during Mission #25J2's manual-range review (the airport
operator's own history page, https://www.reunion.aeroport.fr/en/aeroport/
histoire) - not a live fetch, matching test_fragment_selection.py's own
established "real findings, reconstructed as a fixture" convention."""

from __future__ import annotations

from app.extraction.generic_pdf import ExtractedDocument, ExtractedPage, ExtractionStatus
from app.selection.fragment_selection import SelectionReasonKind, select_fragments

FILLER = "Unrelated filler paragraph text about airport history and operations. " * 15  # ~1100 chars

# The real 2017 timeline entry text as extracted from Snapshot 10 (Mission
# #25J2's own manual-range review), verbatim.
ROLAND_GARROS_2017_PASSAGE = (
    "2017\n"
    "Arrestor bed fitted at the end of the longest runway\n"
    "Installation of an arrestor bed at point 30 of the airport’s longest runway, "
    "supplied by the Swedish company Runway Safe. Roland Garros became the first "
    "European Airport to adopt this system in order to comply with regulations."
)

# An earlier, unrelated timeline entry - the real 2013 sentence machine
# Selection previously (mis)selected, kept here to prove it is STILL
# found (as a weak_activity_term match, via the literal word
# "construction" - the real trigger #25J2's live run showed) without
# becoming the ONLY result.
ROLAND_GARROS_2013_PASSAGE = (
    "2013\nRunways and taxiways\nWork began on construction to reinforce and expand runways and taxiways"
)


def _doc(pages: list[ExtractedPage], *, status: ExtractionStatus = ExtractionStatus.SUCCESS) -> ExtractedDocument:
    return ExtractedDocument(
        document_identity="test-source:testsha256",
        media_type="text/html",
        extractor_name="generic-html",
        extractor_version="0.1",
        pages=tuple(pages),
        page_count=len(pages),
        status=status,
    )


def _page(n: int, text: str) -> ExtractedPage:
    return ExtractedPage(page_number=n, text=text)


# --- Positive matches -----------------------------------------------------


def test_arrestor_bed_matches():
    doc = _doc([_page(1, "The airport installed an arrestor bed at the runway end this year.")])
    result = select_fragments(doc)
    assert len(result.fragments) == 1
    reasons = result.fragments[0].reasons
    assert any(
        r.kind == SelectionReasonKind.STRONG_CONCEPT_TERM and r.matched_text.lower() == "arrestor bed"
        for r in reasons
    )


def test_arresting_bed_matches():
    doc = _doc([_page(1, "A new arresting bed was commissioned at the airport last month.")])
    result = select_fragments(doc)
    assert len(result.fragments) == 1
    reasons = result.fragments[0].reasons
    assert any(
        r.kind == SelectionReasonKind.STRONG_CONCEPT_TERM and r.matched_text.lower() == "arresting bed"
        for r in reasons
    )


def test_arrestor_bed_case_insensitive():
    doc = _doc([_page(1, "installation of an ARRESTOR BED at point 30 of the runway.")])
    result = select_fragments(doc)
    assert len(result.fragments) == 1
    assert any(r.matched_text == "ARRESTOR BED" for r in result.fragments[0].reasons)  # literal casing preserved


# --- False-positive discipline --------------------------------------------


def test_bare_unrelated_arrestor_does_not_match_new_term():
    """A bare 'arrestor' reference (e.g. a generic mechanical-braking
    context, not the two-word compound phrase) must not trigger the new
    strong_concept_term via this specific addition - bare 'arrestor' was
    deliberately NOT added to the vocabulary."""
    doc = _doc([_page(1, "The vehicle's tail arrestor system was inspected during routine maintenance.")])
    result = select_fragments(doc)
    matched_arrestor_bed = any(
        r.matched_text.lower() in ("arrestor bed", "arresting bed")
        for f in result.fragments
        for r in f.reasons
    )
    assert not matched_arrestor_bed


def test_existing_vocabulary_behavior_intact():
    """Regression: the pre-existing strong term 'EMAS' still matches
    exactly as before - the addition did not disturb the matching engine
    or existing terms."""
    doc = _doc([_page(1, "The airport is installing an EMAS system this year.")])
    result = select_fragments(doc)
    assert len(result.fragments) == 1
    assert any(
        r.kind == SelectionReasonKind.STRONG_CONCEPT_TERM and r.matched_text == "EMAS"
        for r in result.fragments[0].reasons
    )


def test_aircraft_arresting_not_in_vocabulary():
    """Mission #25J3 explicitly evaluated and declined to add "aircraft
    arresting" - no real evidence text observed this session actually
    uses that exact phrase. Confirm it does NOT independently trigger a
    strong_concept_term match (distinct from "arresting system", which
    already did before this mission and still does)."""
    doc = _doc([_page(1, "Discussion of aircraft arresting technology in general aviation safety.")])
    result = select_fragments(doc)
    # "arresting system" is NOT present here, so no PRE-EXISTING term
    # should fire either - if this fails, it means some other term
    # coincidentally matches, not that "aircraft arresting" was added.
    strong_matches = [
        r.matched_text.lower()
        for f in result.fragments
        for r in f.reasons
        if r.kind == SelectionReasonKind.STRONG_CONCEPT_TERM
    ]
    assert "aircraft arresting" not in strong_matches


# --- Roland Garros regression (Mission #25J2's real acceptance case) -----


def test_roland_garros_2017_passage_now_selected():
    """The real Snapshot 10 wording machine Selection previously missed
    entirely (Mission #25J1/#25J2's own finding) must now be captured."""
    text = FILLER + ROLAND_GARROS_2013_PASSAGE + FILLER + ROLAND_GARROS_2017_PASSAGE + FILLER
    doc = _doc([_page(1, text)])
    result = select_fragments(doc)

    combined_fragment_text = " ".join(f.text for f in result.fragments)
    assert "Arrestor bed fitted" in combined_fragment_text
    assert "point 30" in combined_fragment_text
    assert "Runway Safe" in combined_fragment_text  # captured naturally by the existing window, not forced

    # The selection reason for the fragment containing the target passage
    # must be the new strong term, not a coincidental weak-term match.
    target_fragment = next(f for f in result.fragments if "Arrestor bed fitted" in f.text)
    assert any(
        r.kind == SelectionReasonKind.STRONG_CONCEPT_TERM and r.matched_text.lower() == "arrestor bed"
        for r in target_fragment.reasons
    )


def test_roland_garros_old_2013_fragment_still_present_not_exclusive():
    """The old 2013 construction sentence is still legitimately found (a
    real weak_activity_term match, unchanged) - it must no longer be the
    ONLY result now that the real 2017 passage is also captured."""
    text = FILLER + ROLAND_GARROS_2013_PASSAGE + FILLER + ROLAND_GARROS_2017_PASSAGE + FILLER
    doc = _doc([_page(1, text)])
    result = select_fragments(doc)

    combined_fragment_text = " ".join(f.text for f in result.fragments)
    assert "reinforce and expand runways" in combined_fragment_text  # still found
    assert "Arrestor bed fitted" in combined_fragment_text  # now ALSO found
    assert len(result.fragments) >= 2  # no longer collapsed into a single, wrong-passage result
