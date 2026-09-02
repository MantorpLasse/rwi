"""RWI Mission #14B Part P - offline tests for
app.selection.candidate_fragment_adapter and app.selection.review. No
network, no database."""

from __future__ import annotations

import dataclasses

from app.extraction.generic_pdf import ExtractedDocument, ExtractedPage, ExtractionStatus
from app.selection.candidate_fragment_adapter import build_candidate_fragment
from app.selection.fragment_selection import select_fragments
from app.selection.review import FragmentReview, ReviewDecision, apply_keep_decisions
from app.services.discovery_candidate_fragment import CandidateFragment


def _doc(pages):
    return ExtractedDocument(
        document_identity="test-source:testsha",
        media_type="application/pdf",
        extractor_name="generic-pdf",
        extractor_version="0.1",
        pages=tuple(pages),
        page_count=len(pages),
        status=ExtractionStatus.SUCCESS,
    )


def _page(n, text):
    return ExtractedPage(page_number=n, text=text)


# --- Field mapping ------------------------------------------------------


def test_artifact_identity_maps_from_document_identity():
    doc = _doc([_page(1, "EMAS installed at Example Municipal Airport.")])
    selection = select_fragments(doc)
    cf = build_candidate_fragment(selection.fragments[0])
    assert cf.artifact_identity == doc.document_identity == "test-source:testsha"


def test_source_locator_maps_exactly():
    doc = _doc([_page(1, "EMAS installed at Example Municipal Airport.")])
    selection = select_fragments(doc)
    fragment = selection.fragments[0]
    cf = build_candidate_fragment(fragment)
    assert cf.source_locator == f"page:{fragment.page_number};chars:{fragment.start_offset}-{fragment.end_offset}"


def test_raw_text_preserved_exactly():
    doc = _doc([_page(1, "EMAS installed at Example Municipal Airport.")])
    selection = select_fragments(doc)
    fragment = selection.fragments[0]
    cf = build_candidate_fragment(fragment)
    assert cf.raw_text == fragment.text


def test_no_airport_name_present_yields_empty_structured_identity():
    doc = _doc([_page(1, "EMAS discussed generally with no specific airport named.")])
    selection = select_fragments(doc)
    cf = build_candidate_fragment(selection.fragments[0])
    assert cf.airport_names == frozenset()
    assert cf.airport_identifiers == frozenset()


def test_literal_airport_name_populates_airport_names():
    doc = _doc([_page(1, "EMAS installed at Example Municipal Airport this year.")])
    selection = select_fragments(doc)
    cf = build_candidate_fragment(selection.fragments[0])
    assert cf.airport_names == frozenset({"Example Municipal Airport"})


def test_selection_reason_never_populates_evidence_fields():
    """A fragment selected via airport_identity_match alone (search-seed
    context) whose fragment text does NOT literally contain a
    "<Name> Airport" pattern must not have that context leak into
    airport_names."""
    from app.selection.fragment_selection import AirportIdentityContext

    doc = _doc([_page(1, "EMAS work is ongoing near LCY this month.")])
    identity = AirportIdentityContext(name="London City Airport", iata_code="LCY")
    selection = select_fragments(doc, airport_identity=identity)
    assert selection.fragments  # confirm a fragment was selected via the identity_match reason
    cf = build_candidate_fragment(selection.fragments[0])
    # "LCY" alone (a bare code, no "<Name> Airport" pattern) must not
    # become a structured airport name merely because it caused attention.
    assert cf.airport_names == frozenset()


def test_all_non_authorized_fields_remain_default():
    doc = _doc([_page(1, "EMAS installed at Example Municipal Airport this year, costing $5 million on 2024-01-01.")])
    selection = select_fragments(doc)
    cf = build_candidate_fragment(selection.fragments[0])
    assert cf.issuers == frozenset()
    assert cf.locations == frozenset()
    assert cf.runway_ends == frozenset()
    assert cf.runway_pairs == frozenset()
    assert cf.contradicting_names == frozenset()
    assert cf.contradicting_issuers == frozenset()
    assert cf.contradicting_locations == frozenset()
    assert cf.alternate_airport_runway_ends == frozenset()
    assert cf.alternate_airport_runway_pairs == frozenset()
    assert cf.project_identifiers == frozenset()
    assert cf.contract_identifiers == frozenset()
    assert cf.money_values == ()
    assert cf.dates == ()
    assert cf.terminology_hits == frozenset()
    assert cf.language is None


def test_document_title_and_url_only_included_when_supplied():
    doc = _doc([_page(1, "EMAS installed at Example Municipal Airport.")])
    selection = select_fragments(doc)
    cf_without = build_candidate_fragment(selection.fragments[0])
    assert cf_without.document_title is None
    assert cf_without.url is None
    cf_with = build_candidate_fragment(selection.fragments[0], document_title="Real Title", url="https://example.com/x")
    assert cf_with.document_title == "Real Title"
    assert cf_with.url == "https://example.com/x"


def test_deterministic_repeated_construction():
    doc = _doc([_page(1, "EMAS installed at Example Municipal Airport.")])
    selection = select_fragments(doc)
    cf1 = build_candidate_fragment(selection.fragments[0])
    cf2 = build_candidate_fragment(selection.fragments[0])
    assert cf1 == cf2


# --- KEEP/SKIP gating (Part D/P #1/#2) ---------------------------------


def test_keep_required_before_candidate_fragment_creation():
    doc = _doc([_page(1, "EMAS installed at Example Municipal Airport.")])
    selection = select_fragments(doc)
    assert selection.fragments
    reviews = apply_keep_decisions(selection, keep_indices=frozenset())
    assert len(reviews) == len(selection.fragments)
    assert all(r.decision == ReviewDecision.SKIP for r in reviews)
    assert all(r.candidate_fragment is None for r in reviews)


def test_skip_produces_no_candidate_fragment():
    doc = _doc([_page(1, "EMAS at Example Municipal Airport."), _page(2, "RESA at Sample Regional Airport.")])
    selection = select_fragments(doc)
    assert len(selection.fragments) == 2
    reviews = apply_keep_decisions(selection, keep_indices=frozenset({1}))
    assert reviews[0].decision == ReviewDecision.KEEP
    assert reviews[0].candidate_fragment is not None
    assert reviews[1].decision == ReviewDecision.SKIP
    assert reviews[1].candidate_fragment is None


def test_keep_indices_are_1_based_matching_reviewer_display():
    doc = _doc([_page(1, "EMAS at Example Municipal Airport.")])
    selection = select_fragments(doc)
    reviews = apply_keep_decisions(selection, keep_indices=frozenset({1}))
    assert reviews[0].decision == ReviewDecision.KEEP


def test_every_fragment_accounted_for_in_review_output():
    doc = _doc([_page(1, "EMAS at Example Municipal Airport."), _page(2, "RESA at Sample Regional Airport.")])
    selection = select_fragments(doc)
    reviews = apply_keep_decisions(selection, keep_indices=frozenset({2}))
    assert len(reviews) == 2
    assert isinstance(reviews[0], FragmentReview)
