"""RWI Mission #14B Part J/P - offline tests proving the existing,
unmodified IdentityGuard behaves correctly against Selection-produced
CandidateFragments. No network, no database - CandidateAirport is
constructed directly (its own contract: "never touches a database")."""

from __future__ import annotations

from app.extraction.generic_pdf import ExtractedDocument, ExtractedPage, ExtractionStatus
from app.selection.candidate_fragment_adapter import build_candidate_fragment
from app.selection.fragment_selection import select_fragments
from app.selection.identity_guard_demo import evaluate_candidate_fragment_identity
from app.services.evidence_attachment_guard import AttachmentOutcome, CandidateAirport

_LCY = CandidateAirport(id=1, name="London City Airport", identifiers=frozenset({"LCY", "EGLC"}))
_YTZ = CandidateAirport(id=2, name="Billy Bishop Toronto City Airport", identifiers=frozenset({"YTZ", "CYTZ"}))


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


def test_raw_text_only_candidate_fragment_is_insufficient_identity():
    """A CandidateFragment with no structured airport_names/identifiers
    (a page that never literally names an airport) must fail closed."""
    doc = _doc([_page(1, "EMAS discussed generally, no airport named.")])
    selection = select_fragments(doc)
    cf = build_candidate_fragment(selection.fragments[0])
    assert cf.airport_names == frozenset()
    decisions = evaluate_candidate_fragment_identity(cf, [_LCY])
    assert decisions[_LCY.id].outcome == AttachmentOutcome.INSUFFICIENT_IDENTITY


def test_extracted_airport_name_produces_meaningful_evaluation():
    doc = _doc([_page(1, "EMAS installed at London City Airport this year.")])
    selection = select_fragments(doc)
    cf = build_candidate_fragment(selection.fragments[0])
    assert cf.airport_names == frozenset({"London City Airport"})
    decisions = evaluate_candidate_fragment_identity(cf, [_LCY])
    assert decisions[_LCY.id].outcome == AttachmentOutcome.ATTACH_PROVISIONAL


def test_ambiguous_identity_remains_ambiguous():
    """Two candidates sharing the exact same literal name both
    independently qualify -> the existing guard's own real ambiguity
    resolution downgrades both to REVIEW_REQUIRED - unmodified,
    pre-existing behavior, not new logic."""
    twin_a = CandidateAirport(id=10, name="Springfield Municipal Airport")
    twin_b = CandidateAirport(id=11, name="Springfield Municipal Airport")
    doc = _doc([_page(1, "EMAS installed at Springfield Municipal Airport this year.")])
    selection = select_fragments(doc)
    cf = build_candidate_fragment(selection.fragments[0])
    decisions = evaluate_candidate_fragment_identity(cf, [twin_a, twin_b])
    assert decisions[10].outcome == AttachmentOutcome.REVIEW_REQUIRED
    assert decisions[11].outcome == AttachmentOutcome.REVIEW_REQUIRED


def test_unknown_airport_not_forced_onto_existing_candidate():
    """A literal name for an airport NOT in the supplied candidate list
    must never be silently attached to an unrelated existing candidate -
    it simply yields no positive evidence for that candidate."""
    doc = _doc([_page(1, "EMAS installed at Some Unlisted Regional Airport this year.")])
    selection = select_fragments(doc)
    cf = build_candidate_fragment(selection.fragments[0])
    assert cf.airport_names == frozenset({"Some Unlisted Regional Airport"})
    decisions = evaluate_candidate_fragment_identity(cf, [_LCY, _YTZ])
    assert decisions[_LCY.id].outcome == AttachmentOutcome.INSUFFICIENT_IDENTITY
    assert decisions[_YTZ.id].outcome == AttachmentOutcome.INSUFFICIENT_IDENTITY


def test_selection_reason_alone_has_zero_effect_on_identity_guard():
    """A fragment selected purely via airport_identity_match on a bare
    code (no literal "<Name> Airport" pattern in its own text) has
    identical, empty structured identity - IdentityGuard cannot tell
    this fragment apart from one selected via a concept term alone."""
    from app.selection.fragment_selection import AirportIdentityContext

    identity = AirportIdentityContext(name="London City Airport", iata_code="LCY")
    doc_with_seed = _doc([_page(1, "EMAS work near LCY continues.")])
    selection_with_seed = select_fragments(doc_with_seed, airport_identity=identity)
    cf_with_seed = build_candidate_fragment(selection_with_seed.fragments[0])

    doc_without_seed = _doc([_page(1, "EMAS work near LCY continues.")])
    selection_without_seed = select_fragments(doc_without_seed, airport_identity=None)
    cf_without_seed = build_candidate_fragment(selection_without_seed.fragments[0])

    assert cf_with_seed.airport_names == cf_without_seed.airport_names == frozenset()
    decisions_with = evaluate_candidate_fragment_identity(cf_with_seed, [_LCY])
    decisions_without = evaluate_candidate_fragment_identity(cf_without_seed, [_LCY])
    assert decisions_with[_LCY.id].outcome == decisions_without[_LCY.id].outcome == AttachmentOutcome.INSUFFICIENT_IDENTITY
