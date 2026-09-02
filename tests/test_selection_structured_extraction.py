"""RWI Mission #14B Part P - offline tests for
app.selection.structured_extraction. No network, no database."""

from __future__ import annotations

from app.selection.structured_extraction import ExtractedIdentity, extract_airport_names, extract_identity


def test_literal_airport_name_extracted():
    names = extract_airport_names("EMAS installed at London City Airport this year.")
    assert names == frozenset({"London City Airport"})


def test_no_airport_name_present_yields_empty():
    names = extract_airport_names("EMAS discussed generally with no specific location named.")
    assert names == frozenset()


def test_bare_iata_or_icao_code_never_extracted_as_identifier():
    """No existing repository-safe pattern for free-text code extraction
    was found (Mission #14B recon) - airport_identifiers always empty."""
    identity = extract_identity(fragment_text="EMAS installed at LCY (EGLC) this year.")
    assert identity.airport_identifiers == frozenset()


def test_matching_never_crosses_a_line_break():
    """Regression for a real bug found and fixed during this mission's
    own development: real pdfplumber page text is heavily newline-
    delimited, and a naive \\s+ separator would merge two unrelated,
    visually-adjacent PDF lines into one bogus name."""
    text = "Proposal\nLondon City Airport"
    names = extract_airport_names(text)
    assert names == frozenset({"London City Airport"})
    assert "Proposal" not in "".join(names)


def test_two_consecutive_airport_names_not_merged():
    text = "The proposal serves both Example Regional Airport and Sample Municipal Airport jointly."
    names = extract_airport_names(text)
    assert names == frozenset({"Example Regional Airport", "Sample Municipal Airport"})


def test_airport_road_not_mistaken_for_airport_name():
    names = extract_airport_names("The site is located on Example Airport Road near the terminal.")
    assert names == frozenset()


def test_selection_reason_never_influences_extraction():
    """Extraction reads ONLY the literal text - a SelectionReason object
    is never consulted (structurally impossible: this function doesn't
    even accept one as a parameter)."""
    import inspect

    sig = inspect.signature(extract_identity)
    assert "reason" not in sig.parameters
    assert "reasons" not in sig.parameters


def test_airport_identity_context_search_seed_never_becomes_evidence():
    """Same structural proof: no AirportIdentityContext/search-seed
    parameter exists on the extraction function at all."""
    import inspect

    sig = inspect.signature(extract_identity)
    assert "airport_identity" not in sig.parameters
    assert "identity" not in sig.parameters


def test_bibliography_shaped_text_extracts_only_what_is_literally_there():
    """Real, frozen text from the actual preserved CAA LCY Snapshot 7,
    page 16 (Mission #14A/#14B recon) - a genuine literal match
    ("London City ACC Airport") is extracted, honestly imperfect,
    never silently corrected to "London City Airport" and never
    suppressed merely because this is a reference-list page."""
    text = (
        "20. Appendices\n20.1 References\nRef No Document Number Hyperlink\n"
        "1 CAP1616 Link\n2 London City Statement of Need Link\n"
        "3 AIP changes in support of EMAS ACP Supplied directly to CAA\n"
        "4 NATS Design Submission Package titled: London Supplied directly to CAA\n"
        "City Enhanced Material Arrester System (EMAS)\nImplementation - Phase 1 v1.0 March 2023\n"
        "5 London City ACC Airport Report 9th March 2023 Link\n"
        "6 London City ACC Minutes 9th March 2023 Link\n"
        "7 Aircraft Noise Levels with EMAS development report Link\nEnd of document"
    )
    names = extract_airport_names(text)
    assert names == frozenset({"London City ACC Airport"})
    assert "London City Airport" not in names  # honest: this exact clean string is NOT literally present


def test_ytz_shaped_negative_fixture_no_semantic_fields():
    text = (
        "The federal review considered several RESA compliance options at Example Municipal Airport. "
        "EMAS was considered as one possible solution, but the landmass alternative was ultimately "
        "approved by the Board."
    )
    identity = extract_identity(fragment_text=text)
    assert identity.airport_names == frozenset({"Example Municipal Airport"})
    # No field anywhere on ExtractedIdentity could hold a conclusion.
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(ExtractedIdentity)}
    assert field_names == {"airport_names", "airport_identifiers"}
    forbidden = {"emas_present", "emas_confirmed", "emas_rejected", "project_status", "opportunity", "conclusion"}
    assert not (field_names & forbidden)


def test_unicode_local_language_airport_name_not_supported_by_ascii_pattern_is_honestly_absent():
    """V1's pattern is ASCII-capitalization-based (matching MAC's own
    real precedent exactly) - a non-Latin-script name is honestly NOT
    extracted (no false structured claim), while the original text
    itself remains fully preserved in raw_text elsewhere (Mission #14B
    Part M: this module never translates or invents multilingual
    matching, and never corrupts what it cannot extract)."""
    text = "새 활주로 안전 시스템이 김포공항에 설치되었습니다."  # Korean: "installed at Gimpo Airport"
    names = extract_airport_names(text)
    assert names == frozenset()  # honestly empty, not fabricated, not corrupted


def test_unicode_text_with_ascii_airport_name_survives_unchanged():
    """A real, literal ASCII airport-name match embedded in a
    non-English sentence is extracted with its exact original
    characters preserved - no transliteration, no case change."""
    text = "Ett meddelande om EMAS vid London City Airport skickades igår."  # Swedish sentence
    names = extract_airport_names(text)
    assert names == frozenset({"London City Airport"})


def test_deterministic_repeated_extraction():
    text = "EMAS installed at London City Airport (LCY) this year."
    identity1 = extract_identity(fragment_text=text)
    identity2 = extract_identity(fragment_text=text)
    assert identity1 == identity2


def test_document_title_names_merged_not_replacing_fragment_names():
    identity = extract_identity(
        fragment_text="EMAS discussed at Example Municipal Airport.",
        document_title="Report for Sample Regional Airport",
    )
    assert identity.airport_names == frozenset({"Example Municipal Airport", "Sample Regional Airport"})


def test_no_document_title_does_not_error():
    identity = extract_identity(fragment_text="EMAS discussed generally.", document_title=None)
    assert identity.airport_names == frozenset()
