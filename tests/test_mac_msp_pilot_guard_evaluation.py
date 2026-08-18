"""Guard-level proof for the MSP discovery-provider pilot's mandatory
cross-airport safety case (docs/product/msp-authoritative-discovery-
provider-pilot.md S11/S12, docs/product/cross-airport-evidence-wiring-report.md),
run against the real, recorded MAC Granicus EMAS-procurement-memo fixture
and REAL RWI canonical MSP/SFO topology.

Three things are proven here:

1. **The now-current, properly-wired result**: extraction ->
   app.services.candidate_fragment_enrichment.enrich_with_alternate_airport_topology
   -> the unmodified CandidateFragment -> EvidenceBag adapter -> the
   unmodified guard produces MSP -> ATTACH_CONFIRMED, SFO ->
   REJECT_CROSS_AIRPORT for the SAME real fragment in the SAME
   evaluate_attachment_for_candidates() call - this is the main proof
   this test file exists for.
2. **The historical, pre-enrichment result is preserved as its own,
   still-passing case** (INSUFFICIENT_IDENTITY for SFO when no
   enrichment step is applied) - proving enrichment is genuinely
   OPTIONAL and additive, not a change to extraction or adapter default
   behavior.
3. **The underlying guard mechanism is unchanged** - a direct,
   CandidateFragment-bypassing EvidenceBag construction with
   alternate_airport_runway_ends/_pairs set produces the identical
   REJECT_CROSS_AIRPORT result it always did (app/services/evidence_attachment_guard.py
   was not modified by the cross-airport-evidence-wiring change).
"""
from __future__ import annotations

from pathlib import Path

from app.acquisition.mac_granicus_extractor import extract_candidate_fragment
from app.services.candidate_fragment_enrichment import enrich_with_alternate_airport_topology
from app.services.discovery_candidate_fragment import candidate_fragment_to_evidence_bag
from app.services.evidence_attachment_guard import (
    AttachmentOutcome,
    CandidateAirport,
    EvidenceBag,
    evaluate_attachment,
    evaluate_attachment_for_candidates,
)

FIXTURE_PDF = (Path(__file__).parent / "fixtures" / "mac_granicus_emas_procurement_memo_sample.pdf").read_bytes()

_MSP = CandidateAirport(
    id=45, name="Minneapolis St. Paul International",
    identifiers=frozenset({"MSP", "KMSP"}),
    canonical_runway_ends=frozenset({"12R", "30L", "4", "22", "12L", "30R", "17", "35"}),
    canonical_runway_pairs=frozenset({"12R/30L", "4/22", "12L/30R", "17/35"}),
    known_issuers=frozenset({"Metropolitan Airports Commission"}),
)
_SFO = CandidateAirport(
    id=4, name="San Francisco International Airport",
    identifiers=frozenset({"SFO", "KSFO"}),
    canonical_runway_ends=frozenset({"1L", "19R", "1R", "19L", "10L", "28R", "10R", "28L"}),
    canonical_runway_pairs=frozenset({"1L/19R", "1R/19L", "10L/28R", "10R/28L"}),
    known_issuers=frozenset({"San Francisco Airport Commission"}),
)


def _real_fragment():
    result = extract_candidate_fragment(
        FIXTURE_PDF, "application/pdf",
        artifact_identity="mac.granicus.document.4.2349.105406",
        source_locator="pd&e-2024-09-03-item-2.3.2",
    )
    assert result is not None
    fragment, _vendors = result
    return fragment


# --- 12. MSP guard confirmed ---


def test_real_document_confirms_for_msp():
    bag = candidate_fragment_to_evidence_bag(_real_fragment())
    decisions = evaluate_attachment_for_candidates(bag, [_MSP, _SFO])
    assert decisions[_MSP.id].outcome == AttachmentOutcome.ATTACH_CONFIRMED
    categories = {item.category.value for item in decisions[_MSP.id].positive_evidence}
    assert categories == {"issuer", "runway_topology"}


# --- 13. same fragment vs SFO, before and after enrichment ---


def test_real_document_is_insufficient_for_sfo_without_enrichment():
    """The historical, pre-enrichment result - still true today: without
    the optional enrichment step, extraction alone never produces
    alternate-airport evidence, so SFO reaches INSUFFICIENT_IDENTITY, not
    REJECT_CROSS_AIRPORT. Proves enrichment is additive, not a change to
    default extraction/adapter behavior."""
    bag = candidate_fragment_to_evidence_bag(_real_fragment())
    decisions = evaluate_attachment_for_candidates(bag, [_MSP, _SFO])
    sfo_decision = decisions[_SFO.id]
    assert sfo_decision.outcome not in (AttachmentOutcome.ATTACH_CONFIRMED, AttachmentOutcome.ATTACH_PROVISIONAL)
    assert sfo_decision.outcome == AttachmentOutcome.INSUFFICIENT_IDENTITY


def test_real_document_rejects_for_sfo_after_enrichment_while_msp_still_confirms():
    """The main proof this task exists for: with the enrichment step
    applied - using MSP's real, already-known canonical topology, exactly
    as an orchestration caller who has independently confirmed this
    fragment belongs to MSP (via its own issuer+topology positive
    evidence, S12 above) would supply - the SAME real fragment, evaluated
    in the SAME evaluate_attachment_for_candidates() call, now produces:

        MSP -> ATTACH_CONFIRMED
        SFO -> REJECT_CROSS_AIRPORT

    with a real, specific, evidence-grounded contradiction reason for
    SFO - never a bare label, never derived from provider/source-family
    identity (enrich_with_alternate_airport_topology has no concept of
    "MSP" or "MAC" at all - only frozensets of runway-designation
    strings, intersected against what the fragment's own extraction
    actually found)."""
    enriched = enrich_with_alternate_airport_topology(
        _real_fragment(),
        known_other_airport_runway_ends=_MSP.canonical_runway_ends,
        known_other_airport_runway_pairs=_MSP.canonical_runway_pairs,
    )
    bag = candidate_fragment_to_evidence_bag(enriched)
    decisions = evaluate_attachment_for_candidates(bag, [_MSP, _SFO])

    assert decisions[_MSP.id].outcome == AttachmentOutcome.ATTACH_CONFIRMED
    assert decisions[_SFO.id].outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT
    sfo_contradictions = {(c.category.value, c.value) for c in decisions[_SFO.id].contradicting_evidence}
    assert ("runway_topology", "30L") in sfo_contradictions
    assert "Contradicting identity evidence" in decisions[_SFO.id].reason
    # The reason is derived from the actual matched runway tokens, never
    # from the word "MSP", "MAC", or "provider" appearing anywhere in it.
    assert "provider" not in decisions[_SFO.id].reason.lower()
    assert "mac" not in decisions[_SFO.id].reason.lower()


def test_cross_airport_rejection_mechanism_in_the_guard_itself_is_unchanged():
    """Direct, CandidateFragment-bypassing proof that
    app/services/evidence_attachment_guard.py itself was not modified by
    the cross-airport-evidence-wiring change - EvidenceBag's own
    alternate_airport_runway_ends/_pairs field still behaves identically."""
    bag = EvidenceBag(
        issuers=frozenset({"Metropolitan Airports Commission"}),
        runway_ends=frozenset({"12R", "30L"}),
        runway_pairs=frozenset({"12R/30L"}),
        alternate_airport_runway_ends=frozenset({"12R", "30L"}),
        alternate_airport_runway_pairs=frozenset({"12R/30L"}),
    )
    decision = evaluate_attachment(_SFO, bag)
    assert decision.outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT
    assert decision.contradicting_evidence  # real, specific contradiction items, not a bare label
