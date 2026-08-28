"""Tests for app/acquisition/usaspending_grant_claims.py
(docs/architecture/rwi-usaspending-legacy-claims-extractor-design.md).

Pure-function tests only - no database, no session, no persistence. Text
fixtures below are either verbatim excerpts of real, already-persisted
SourceAssertion rows (SA81, SA84's own "MATERIALS" spelling, SA78's own
letter-suffix runway) or deliberately synthetic negative controls, since the
real 25-row usaspending_grant population contains no genuine RSA-only
negative example (every real row explicitly names EMAS).
"""
from __future__ import annotations

from datetime import date

from app.acquisition.usaspending_grant_claims import extract_usaspending_grant_claims
from app.services.evidence_claim_semantics import ClaimCategory, TemporalQualifier

SA81_TEXT = (
    "PURPOSE: CONSTRUCT/EXTEND/IMPROVE SAFETY AREA. THIS GRANT IS FUNDED BY THE CORONAVIRUS AID, "
    "RELIEF, AND ECONOMIC SECURITY ACT TO INCREASE THE FEDERAL SHARE TO 100 PERCENT FOR THE AIRPORT "
    "IMPROVEMENT PROGRAM (AIP). ACTIVITIES TO BE PERFORMED/EXPECTED OUTCOMES: THIS PROJECT EXTENDS "
    "THE RUNWAY 1/19 SAFETY AREAS TO 600 FEET TO MEET FEDERAL AVIATION ADMINISTRATION DESIGN "
    "STANDARDS. THIS IMPROVEMENT WILL ENHANCE SAFETY AT THE AIRPORT. THIS GRANT FUNDS THE SECOND "
    "PHASE, WHICH CONSISTS OF DESIGN AND CONSTRUCTION OF THE RUNWAY 1 ENGINEERED MATERIAL ARRESTING "
    "SYSTEM (EMAS). INTENDED BENEFICIARY: THIS GRANT WILL PROVIDE FEDERAL FUNDING FOR AIRPORTS "
    "ASSOCIATED WITH GREENVILLE, SOUTH CAROLINA."
)

_DEFAULT_KWARGS = dict(
    source_type="usaspending_grant",
    artifact_identity="https://www.usaspending.gov/award/ASST_NON_TEST",
    source_locator="source.external_id=usaspending:ASST_NON_TEST",
    raw_fragment_hash="deadbeef",
    published_date=date(2022, 9, 13),
)


def _extract(text, **overrides):
    kwargs = dict(_DEFAULT_KWARGS, raw_relevant_text=text)
    kwargs.update(overrides)
    return extract_usaspending_grant_claims(**kwargs)


class TestEmasDetection:
    def test_explicit_material_singular(self):
        claims = _extract("CONSTRUCTS AN ENGINEERED MATERIAL ARRESTING SYSTEM AT THE RUNWAY 22 END.")
        assert len(claims) == 1
        assert claims[0].category == ClaimCategory.EXPLICIT_DOCUMENT_FACT

    def test_explicit_materials_plural(self):
        claims = _extract("PROCUREMENT OF ENGINEERED MATERIALS ARRESTING SYSTEM (EMAS) BLOCKS.")
        assert len(claims) == 1

    def test_case_insensitive(self):
        claims = _extract("this project constructs an engineered material arresting system.")
        assert len(claims) == 1

    def test_optional_emas_parenthetical_not_required(self):
        claims = _extract("RECONSTRUCT ENGINEERED MATERIAL ARRESTING SYSTEM SAFETY AREA.")
        assert len(claims) == 1

    def test_bare_acronym_alone_is_not_sufficient(self):
        """A bare 'EMAS' with no expanded phrase nearby is ambiguous -
        fail closed rather than guess (design doc S7)."""
        claims = _extract("THIS PROJECT RELATES TO EMAS FUNDING PRIORITIES.")
        assert claims == ()

    def test_sa81_real_text_produces_emas_claim(self):
        claims = _extract(SA81_TEXT)
        assert any(c.category == ClaimCategory.EXPLICIT_DOCUMENT_FACT for c in claims)

    def test_design_and_construction_wording(self):
        claims = _extract(
            "THIS PROJECT CONSISTS OF DESIGN AND CONSTRUCTION OF THE RUNWAY 4 ENGINEERED MATERIAL "
            "ARRESTING SYSTEM (EMAS)."
        )
        assert len(claims) == 1

    def test_reconstruct_wording(self):
        claims = _extract("PURPOSE: RECONSTRUCT ENGINEERED MATERIAL ARRESTING SYSTEM SAFETY AREA.")
        assert len(claims) == 1

    def test_replacement_wording(self):
        claims = _extract("THIS PROJECT REPLACES AN ENGINEERED MATERIAL ARRESTING SYSTEM THAT HAS REACHED "
                           "THE END OF ITS USEFUL LIFE.")
        assert len(claims) == 1


class TestRunwayExtraction:
    def test_runway_single(self):
        claims = _extract("CONSTRUCTS AN ENGINEERED MATERIAL ARRESTING SYSTEM AT RUNWAY 22.")
        assert "RUNWAY 22" in claims[0].subject

    def test_runway_pair(self):
        claims = _extract("RUNWAY 1/19 ENGINEERED MATERIAL ARRESTING SYSTEM PROJECT.")
        assert "RUNWAY 1/19" in claims[0].subject

    def test_runway_letter_suffix(self):
        claims = _extract("RUNWAY 12R ENGINEERED MATERIAL ARRESTING SYSTEM REPLACEMENT.")
        assert "RUNWAY 12R" in claims[0].subject

    def test_runway_pair_with_letter_suffixes(self):
        text = "RECONSTRUCT EXISTING RUNWAY 12R/30L ENGINEERED MATERIAL ARRESTING SYSTEM SAFETY AREA."
        claims = _extract(text)
        assert "RUNWAY 12R/30L" in claims[0].subject

    def test_absence_of_runway_text_does_not_manufacture_one(self):
        claims = _extract("THIS PROJECT CONSTRUCTS AN ENGINEERED MATERIAL ARRESTING SYSTEM.")
        assert "runway unspecified" in claims[0].subject


class TestGrantPhaseClaim:
    def test_second_phase_extracted(self):
        claims = _extract(
            "CONSTRUCTS AN ENGINEERED MATERIAL ARRESTING SYSTEM. THIS GRANT FUNDS THE SECOND PHASE."
        )
        assert len(claims) == 2
        assert "second phase" in claims[1].statement

    def test_final_phase_extracted(self):
        claims = _extract(
            "CONSTRUCTS AN ENGINEERED MATERIAL ARRESTING SYSTEM. THIS GRANT FUNDS THE FINAL PHASE."
        )
        assert "final phase" in claims[1].statement

    def test_no_phase_wording_produces_no_fabricated_phase_claim(self):
        claims = _extract("CONSTRUCTS AN ENGINEERED MATERIAL ARRESTING SYSTEM AT RUNWAY 22.")
        assert len(claims) == 1
        assert claims[0].category == ClaimCategory.EXPLICIT_DOCUMENT_FACT


class TestTemporalSemantics:
    def test_temporal_is_historical_fact_anchored_to_published_date(self):
        claims = _extract("CONSTRUCTS AN ENGINEERED MATERIAL ARRESTING SYSTEM.", published_date=date(2019, 3, 1))
        assert claims[0].temporal.qualifier == TemporalQualifier.HISTORICAL_FACT
        assert claims[0].temporal.as_of_date == date(2019, 3, 1)

    def test_missing_published_date_does_not_crash_and_stays_unanchored(self):
        claims = _extract("CONSTRUCTS AN ENGINEERED MATERIAL ARRESTING SYSTEM.", published_date=None)
        assert claims[0].temporal.qualifier == TemporalQualifier.HISTORICAL_FACT
        assert claims[0].temporal.as_of_date is None

    def test_old_grant_still_produces_identical_claim_shape(self):
        """A FY2018 grant is still an EMAS project fact - this module makes
        no 'still current' judgment (design doc S8, SLT1's own territory)."""
        old = _extract("CONSTRUCTS AN ENGINEERED MATERIAL ARRESTING SYSTEM.", published_date=date(2018, 1, 1))
        new = _extract("CONSTRUCTS AN ENGINEERED MATERIAL ARRESTING SYSTEM.", published_date=date(2026, 1, 1))
        assert old[0].category == new[0].category
        assert old[0].temporal.qualifier == new[0].temporal.qualifier


class TestFailClosed:
    def test_missing_raw_text(self):
        assert _extract(None) == ()
        assert _extract("") == ()
        assert _extract("   ") == ()

    def test_missing_artifact_identity(self):
        assert _extract("ENGINEERED MATERIAL ARRESTING SYSTEM.", artifact_identity=None) == ()
        assert _extract("ENGINEERED MATERIAL ARRESTING SYSTEM.", artifact_identity="") == ()

    def test_missing_source_locator(self):
        assert _extract("ENGINEERED MATERIAL ARRESTING SYSTEM.", source_locator=None) == ()

    def test_wrong_source_type(self):
        assert _extract("ENGINEERED MATERIAL ARRESTING SYSTEM.", source_type="iija_grant") == ()
        assert _extract("ENGINEERED MATERIAL ARRESTING SYSTEM.", source_type="faa_construction_report") == ()
        assert _extract("ENGINEERED MATERIAL ARRESTING SYSTEM.", source_type=None) == ()

    def test_missing_fragment_hash_does_not_crash(self):
        claims = _extract("ENGINEERED MATERIAL ARRESTING SYSTEM.", raw_fragment_hash=None)
        assert len(claims) == 1
        assert claims[0].provenance.fragment_hash == ""


class TestNegativeControls:
    def test_rsa_only_no_emas(self):
        """A real cross-source-family shape (iija_grant's own real text),
        run against usaspending_grant source_type anyway to isolate the
        text-matching rule itself from the source_type gate."""
        claims = _extract("Construct/Extend Safety Area")
        assert claims == ()

    def test_generic_pavement_negative(self):
        claims = _extract(
            "PURPOSE: RECONSTRUCT RUNWAY. THIS PROJECT RECONSTRUCTS 4,700 FEET OF RUNWAY 6/24 TO "
            "MAINTAIN THE STRUCTURAL INTEGRITY OF THE PAVEMENT."
        )
        assert claims == ()

    def test_generic_lighting_negative(self):
        claims = _extract(
            "THIS PROJECT REPLACES RUNWAY EDGE LIGHTING AND SIGNAGE TO MEET FEDERAL AVIATION "
            "ADMINISTRATION DESIGN STANDARDS."
        )
        assert claims == ()

    def test_generic_airport_improvement_negative(self):
        claims = _extract("PURPOSE: CONSTRUCT/EXTEND/IMPROVE SAFETY AREA. GENERAL AIRPORT IMPROVEMENTS.")
        assert claims == ()

    def test_runway_safety_area_alone_is_not_emas(self):
        """Hard invariant: RSA != EMAS."""
        claims = _extract("THIS PROJECT CONSTRUCTS A RUNWAY SAFETY AREA TO ENHANCE SAFETY.")
        assert claims == ()


class TestUnsafeFactFirewall:
    def test_no_financial_fact_is_ever_produced(self):
        claims = _extract(
            "CONSTRUCTS AN ENGINEERED MATERIAL ARRESTING SYSTEM. THIS GRANT FUNDS THE SECOND PHASE "
            "IN THE AMOUNT OF $1,590,000."
        )
        assert all(c.financial is None for c in claims)

    def test_no_relationship_fact_is_ever_produced(self):
        claims = _extract(
            "SOLE SOURCE PROCUREMENT WITH RUNWAY SAFE FOR THE ENGINEERED MATERIAL ARRESTING SYSTEM."
        )
        assert all(c.relationship is None for c in claims)

    def test_no_completion_or_award_language_in_statements(self):
        claims = _extract(SA81_TEXT)
        for claim in claims:
            lowered = claim.statement.lower()
            assert "completed" not in lowered
            assert "installed" not in lowered
            assert "awarded" not in lowered
            assert "supplier" not in lowered

    def test_no_current_opportunity_or_lifecycle_language(self):
        """This module makes no 'still current'/lifecycle-tier judgment -
        that is SLT1's own, separate, untouched responsibility."""
        claims = _extract(SA81_TEXT)
        for claim in claims:
            lowered = claim.statement.lower()
            assert "current opportunity" not in lowered
            assert "2026" not in lowered
            assert claim.temporal.qualifier == TemporalQualifier.HISTORICAL_FACT


class TestDeterminism:
    def test_same_input_produces_identical_tuple(self):
        first = _extract(SA81_TEXT)
        second = _extract(SA81_TEXT)
        assert first == second

    def test_duplicate_matches_are_suppressed(self):
        text = (
            "ENGINEERED MATERIAL ARRESTING SYSTEM. ENGINEERED MATERIAL ARRESTING SYSTEM AGAIN, "
            "STILL NO RUNWAY IDENTIFIER, STILL NO PHASE."
        )
        claims = _extract(text)
        assert len(claims) == len(set(claims))
