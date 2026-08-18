"""Tests for app/acquisition/mac_granicus_extractor.py
(docs/product/msp-authoritative-discovery-provider-pilot.md).

Text-level extraction logic is tested directly with plain strings (no PDF
fixture needed for most cases - the pure-text/PDF-bytes split is
deliberate, see the module's own docstring). Exactly one test exercises
the real, recorded PDF fixture (a genuine Metropolitan Airports
Commission Planning, Development and Environment Committee memo,
Consent Item 2.3.2, 2024-08-28) to prove the pdfplumber wrapper works on
real content - this is the one real MSP document identified during this
pilot's live research.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.acquisition.mac_granicus_extractor import (
    MACGranicusExtractionError,
    _extract_text,
    _fragment_from_text,
    extract_candidate_fragment,
    is_relevant_text,
)
from app.services.discovery_candidate_fragment import DiscoveryContext

FIXTURE_PDF = (Path(__file__).parent / "fixtures" / "mac_granicus_emas_procurement_memo_sample.pdf").read_bytes()

REAL_SHAPED_TEXT = """PD&E 09/03/2024
Consent Item 2.3.2.
TO: Planning, Development and Environment Committee
FROM: Angela Enroth, Senior Project Manager
SUBJECT: Engineered Material Arresting Systems (EMAS) Procurement Advance Deposit
DATE: August 28, 2024
Summary
The Runway 30L Engineered Material Arresting System (EMAS) has reached its life expectancy
and requires replacement.
Fiscal Impact
The funds for the advance deposit to secure production in the amount of $1,590,000.00 will
come from the approved 2025 EMAS Replacement CIP project.
Action Requested
1. Recommend that the full Commission authorize staff to enter into a sole source
procurement with Runway Safe for the purchase, delivery and installation-oversight
of EMAS materials;
2. Authorize staff to issue a Purchase Order to Runway Safe in the amount of
$1,590,000.00 to cover the cost of the advance deposit.
Background
On December 18, 2023, the Commission approved the 2024-2030 CIP listing which
included the work associated with the 2025 30L EMAS Replacement in the amount of
$19,000,000.00.
Analysis
The EMAS bed at the end of Runway 30L has deteriorated and needs to be replaced. MAC
is requesting the authority to enter a contract directly with Runway Safe for the EMAS
replacement materials as a sole source purchase order."""

NON_RELEVANT_TEXT = """TO: Operations, Finance and Administration Committee
FROM: Troy Tomlinson, Manager - IT Infrastructure
SUBJECT: Reliever Radio Purchase
DATE: August 20, 2024
Summary
Staff requests authorization to purchase replacement handheld radios for reliever airport
operations staff. The current radios are past end-of-life and no longer supported by the
manufacturer.
Fiscal Impact
The funds in the amount of $42,500.00 will come from the approved Operating Budget.
Action Requested
Authorize staff to issue a Purchase Order to Motorola Solutions in the amount of $42,500.00."""


def _fragment(**overrides):
    kwargs = dict(artifact_identity="mac.granicus.document.4.2349.105406", source_locator="item-2.3.2")
    kwargs.update(overrides)
    return _fragment_from_text(REAL_SHAPED_TEXT, **kwargs)


# --- 4. extractor produces CandidateFragment ---


def test_extractor_produces_a_candidate_fragment_for_relevant_text():
    result = _fragment()
    assert result is not None
    fragment, _vendors = result
    assert fragment.artifact_identity == "mac.granicus.document.4.2349.105406"
    assert fragment.source_locator == "item-2.3.2"


# --- 19. non-relevant document produces no CandidateFragment ---


def test_non_relevant_text_produces_no_candidate_fragment():
    assert is_relevant_text(NON_RELEVANT_TEXT) is False
    assert _fragment_from_text(
        NON_RELEVANT_TEXT, artifact_identity="mac.granicus.document.4.2350.999999", source_locator="item-2.3.1",
    ) is None


def test_empty_text_produces_no_candidate_fragment():
    assert _fragment_from_text("", artifact_identity="x", source_locator="y") is None
    assert _fragment_from_text("   \n  ", artifact_identity="x", source_locator="y") is None


# --- 5. raw text preserved ---


def test_raw_text_is_preserved_verbatim():
    fragment, _vendors = _fragment()
    assert fragment.raw_text == REAL_SHAPED_TEXT


# --- 6. runway extraction ---


def test_runway_end_and_pair_extraction():
    fragment, _vendors = _fragment()
    assert fragment.runway_ends == frozenset({"30L"})
    assert fragment.runway_pairs == frozenset()


def test_hyphenated_runway_pair_is_normalized_to_slash_form():
    text = REAL_SHAPED_TEXT + "\nRunway 12R-30L closure is planned for the 2025 construction season."
    fragment, _vendors = _fragment_from_text(text, artifact_identity="a", source_locator="b")
    assert "12R/30L" in fragment.runway_pairs
    assert {"12R", "30L"} <= fragment.runway_ends


# --- 7. issuer extraction ---


def test_issuer_extraction_recognizes_bare_mac_self_reference():
    """The real memo never spells out 'Metropolitan Airports Commission' -
    it only ever refers to itself as 'MAC' (confirmed during this pilot's
    own real-document inspection) - this is the genuine finding this test
    locks in."""
    fragment, _vendors = _fragment()
    assert fragment.issuers == frozenset({"Metropolitan Airports Commission"})


def test_issuer_extraction_also_recognizes_the_full_spelled_out_name():
    text = "A Metropolitan Airports Commission memo regarding Runway Safety Area work."
    fragment, _vendors = _fragment_from_text(text, artifact_identity="a", source_locator="b")
    assert fragment.issuers == frozenset({"Metropolitan Airports Commission"})


def test_lowercase_mac_word_is_not_mistaken_for_the_issuer():
    text = "This mac address is unrelated. Engineered Material Arresting System work is planned."
    fragment, _vendors = _fragment_from_text(text, artifact_identity="a", source_locator="b")
    assert fragment.issuers == frozenset()


# --- 8. vendor/org extraction ---


def test_vendor_extraction_from_sole_source_and_purchase_order_phrasing():
    fragment, vendors = _fragment()
    assert "Runway Safe" in vendors


def test_vendor_extraction_is_not_hardcoded_to_runway_safe():
    """Same structural phrasing, different vendor name - proves the
    pattern is generic, not a literal 'Runway Safe' search (task S9)."""
    text = (
        "Engineered Material Arresting Systems (EMAS) work. Recommend that the full "
        "Commission authorize staff to enter into a sole source procurement with "
        "Zodiac Arresting Systems for the purchase of EMAS materials."
    )
    fragment, vendors = _fragment_from_text(text, artifact_identity="a", source_locator="b")
    assert "Zodiac Arresting Systems" in vendors
    assert "Runway Safe" not in vendors


# --- 9. money extraction ---


def test_money_extraction_with_context_labels():
    fragment, _vendors = _fragment()
    values = [m.numeric_value for m in fragment.money_values]
    labels_by_value = {v: [m.context_label for m in fragment.money_values if m.numeric_value == v] for v in values}
    assert Decimal("1590000.00") in values
    assert "advance_deposit" in labels_by_value[Decimal("1590000.00")]
    assert Decimal("19000000.00") in values
    assert "cip_project_ceiling" in labels_by_value[Decimal("19000000.00")]


# --- 10. date extraction ---


def test_date_extraction_with_semantic_roles():
    fragment, _vendors = _fragment()
    by_role = {d.semantic_role: d.normalized_date for d in fragment.dates}
    assert by_role.get("memo_date") is not None
    assert by_role["memo_date"].isoformat() == "2024-08-28"
    assert by_role.get("prior_approval_date") is not None
    assert by_role["prior_approval_date"].isoformat() == "2023-12-18"


# --- 11. project/contract id extraction ---


def test_contract_and_project_identifier_extraction():
    fragment, _vendors = _fragment()
    assert fragment.contract_identifiers == frozenset({"Consent Item 2.3.2"})
    assert fragment.project_identifiers == frozenset({"2024-2030 CIP"})


# --- discovery_context is audit-only, never read for extraction ---


def test_discovery_context_never_affects_extraction_output():
    without_context = _fragment_from_text(
        REAL_SHAPED_TEXT, artifact_identity="a", source_locator="b",
    )
    with_context = _fragment_from_text(
        REAL_SHAPED_TEXT, artifact_identity="a", source_locator="b",
        discovery_context=DiscoveryContext(search_query="SFO EMAS Runway Safe contract", seed_airport="SFO"),
    )
    fragment_without, vendors_without = without_context
    fragment_with, vendors_with = with_context
    assert fragment_without.issuers == fragment_with.issuers
    assert fragment_without.runway_ends == fragment_with.runway_ends
    assert vendors_without == vendors_with
    assert fragment_with.discovery_context is not None  # stored for audit only


# --- real PDF fixture: end-to-end pdfplumber-backed extraction ---


def test_real_fixture_pdf_extracts_the_genuine_msp_evidence():
    result = extract_candidate_fragment(
        FIXTURE_PDF, "application/pdf",
        artifact_identity="mac.granicus.document.4.2349.105406",
        source_locator="pd&e-2024-09-03-item-2.3.2",
    )
    assert result is not None
    fragment, vendors = result
    assert fragment.runway_ends == frozenset({"12R", "30L"})
    assert fragment.runway_pairs == frozenset({"12R/30L"})
    assert fragment.issuers == frozenset({"Metropolitan Airports Commission"})
    assert vendors == ("Runway Safe",)
    assert fragment.contract_identifiers == frozenset({"Consent Item 2.3.2"})
    assert Decimal("1590000.00") in {m.numeric_value for m in fragment.money_values}
    assert Decimal("19000000.00") in {m.numeric_value for m in fragment.money_values}


def test_real_fixture_pdf_text_extraction_is_deterministic():
    text_a = _extract_text(FIXTURE_PDF, "application/pdf")
    text_b = _extract_text(FIXTURE_PDF, "application/pdf")
    assert text_a == text_b
    assert "Runway 30L" in text_a
    assert "2.3.2" in text_a


def test_empty_pdf_bytes_fails_closed():
    try:
        _extract_text(b"", "application/pdf")
        assert False, "expected MACGranicusExtractionError"
    except MACGranicusExtractionError as exc:
        assert exc.code == "empty_payload"


def test_unsupported_media_type_fails_closed():
    try:
        _extract_text(b"some bytes", "text/html")
        assert False, "expected MACGranicusExtractionError"
    except MACGranicusExtractionError as exc:
        assert exc.code == "unsupported_media_type"
