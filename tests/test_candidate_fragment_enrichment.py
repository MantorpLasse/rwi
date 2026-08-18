"""Tests for app/services/candidate_fragment_enrichment.py
(docs/product/cross-airport-evidence-wiring-report.md).

Covers the enrichment helper's own pure behavior plus the explicit
invariants this slice must prove:
  - search query cannot create contradiction evidence
  - provider/source-family identity cannot create contradiction evidence
  - alternate topology is not inferred inside CandidateFragment
  - alternate topology is not inferred inside the guard
  - contradiction-first behavior remains unchanged
  - raw evidence remains unchanged
"""
from __future__ import annotations

from app.services.candidate_fragment_enrichment import enrich_with_alternate_airport_topology
from app.services.discovery_candidate_fragment import (
    CandidateFragment,
    DiscoveryContext,
    candidate_fragment_to_evidence_bag,
)
from app.services.evidence_attachment_guard import (
    AttachmentOutcome,
    CandidateAirport,
    EvidenceBag,
    evaluate_attachment,
)


def _fragment(**overrides) -> CandidateFragment:
    kwargs = dict(
        artifact_identity="doc-1", source_locator="p1",
        raw_text="Metropolitan Airports Commission. Runway 30L EMAS. Sole source procurement with Runway Safe.",
        issuers=frozenset({"Metropolitan Airports Commission"}),
        runway_ends=frozenset({"30L"}),
        runway_pairs=frozenset({"12R/30L"}),
    )
    kwargs.update(overrides)
    return CandidateFragment(**kwargs)


# --- enrichment helper: basic behavior ---


def test_enrichment_adds_only_the_intersection_with_the_fragments_own_tokens():
    fragment = _fragment()
    enriched = enrich_with_alternate_airport_topology(
        fragment,
        known_other_airport_runway_ends=frozenset({"30L", "12R", "4", "22"}),  # MSP's full topology
        known_other_airport_runway_pairs=frozenset({"12R/30L", "4/22"}),
    )
    # Only "30L" and "12R/30L" are actually in the fragment's own extracted
    # tokens - "4"/"22"/"4/22" are part of MSP's topology but were never
    # extracted from this fragment's text, so they must NOT appear.
    assert enriched.alternate_airport_runway_ends == frozenset({"30L"})
    assert enriched.alternate_airport_runway_pairs == frozenset({"12R/30L"})


def test_enrichment_with_no_overlap_produces_no_alternate_evidence():
    """The fragment's own extracted tokens don't match the supplied
    topology at all - proves the function requires genuine overlap, not
    just being told 'this is airport X's topology' (no shortcut)."""
    fragment = _fragment(runway_ends=frozenset({"9"}), runway_pairs=frozenset())
    enriched = enrich_with_alternate_airport_topology(
        fragment,
        known_other_airport_runway_ends=frozenset({"30L", "12R"}),
        known_other_airport_runway_pairs=frozenset({"12R/30L"}),
    )
    assert enriched.alternate_airport_runway_ends == frozenset()
    assert enriched.alternate_airport_runway_pairs == frozenset()


def test_enrichment_with_no_fragment_runway_tokens_produces_no_alternate_evidence():
    fragment = _fragment(runway_ends=frozenset(), runway_pairs=frozenset())
    enriched = enrich_with_alternate_airport_topology(
        fragment,
        known_other_airport_runway_ends=frozenset({"30L"}),
        known_other_airport_runway_pairs=frozenset(),
    )
    assert enriched.alternate_airport_runway_ends == frozenset()
    assert enriched.alternate_airport_runway_pairs == frozenset()


def test_enrichment_normalizes_before_comparing():
    """"06" (leading zero) and "6" must be recognized as the same end -
    reuses app.services.runway_identity, never reimplements it."""
    fragment = _fragment(runway_ends=frozenset({"06"}), runway_pairs=frozenset())
    enriched = enrich_with_alternate_airport_topology(
        fragment, known_other_airport_runway_ends=frozenset({"6"}),
    )
    assert enriched.alternate_airport_runway_ends == frozenset({"6"})


def test_enrichment_is_additive_across_multiple_calls():
    fragment = _fragment(runway_ends=frozenset({"30L", "12R"}), runway_pairs=frozenset({"12R/30L"}))
    once = enrich_with_alternate_airport_topology(fragment, known_other_airport_runway_ends=frozenset({"30L"}))
    twice = enrich_with_alternate_airport_topology(once, known_other_airport_runway_ends=frozenset({"12R"}))
    assert twice.alternate_airport_runway_ends == frozenset({"30L", "12R"})


def test_enrichment_returns_a_new_fragment_and_does_not_mutate_the_input():
    fragment = _fragment()
    enriched = enrich_with_alternate_airport_topology(fragment, known_other_airport_runway_ends=frozenset({"30L"}))
    assert fragment.alternate_airport_runway_ends == frozenset()  # input untouched
    assert enriched is not fragment
    assert enriched.alternate_airport_runway_ends == frozenset({"30L"})


# --- required invariant: search query cannot create contradiction evidence ---


def test_enrichment_function_has_no_search_query_parameter():
    import inspect
    params = set(inspect.signature(enrich_with_alternate_airport_topology).parameters)
    assert not any("query" in p or "search" in p or "seed" in p for p in params)


def test_discovery_context_does_not_affect_enrichment_output():
    without_context = enrich_with_alternate_airport_topology(
        _fragment(), known_other_airport_runway_ends=frozenset({"30L"}),
    )
    with_context = enrich_with_alternate_airport_topology(
        _fragment(discovery_context=DiscoveryContext(search_query="SFO EMAS Runway Safe contract", seed_airport="SFO")),
        known_other_airport_runway_ends=frozenset({"30L"}),
    )
    assert without_context.alternate_airport_runway_ends == with_context.alternate_airport_runway_ends


# --- required invariant: provider/source-family identity cannot create contradiction evidence ---


def test_naming_the_other_airports_topology_alone_is_insufficient_without_real_overlap():
    """A caller cannot produce alternate-airport evidence just by
    asserting 'this fragment is from an MSP-focused provider, so treat it
    as MSP's' - only a genuine, computed overlap between the fragment's
    OWN extracted tokens and the supplied topology counts. Simulated here
    with a fragment that mentions a runway heading MSP's topology does
    NOT actually contain."""
    fragment = _fragment(runway_ends=frozenset({"27"}), runway_pairs=frozenset())  # not an MSP runway end
    msp_like_topology = frozenset({"12R", "30L", "4", "22", "12L", "30R", "17", "35"})
    enriched = enrich_with_alternate_airport_topology(fragment, known_other_airport_runway_ends=msp_like_topology)
    assert enriched.alternate_airport_runway_ends == frozenset()


# --- required invariant: alternate topology is not inferred inside CandidateFragment ---


def test_candidate_fragment_never_computes_alternate_topology_itself():
    """Constructing a CandidateFragment with real runway tokens present
    never auto-populates alternate_airport_runway_ends/_pairs - the field
    stays exactly what the caller explicitly passed (empty by default)."""
    fragment = _fragment(runway_ends=frozenset({"30L"}), runway_pairs=frozenset({"12R/30L"}))
    assert fragment.alternate_airport_runway_ends == frozenset()
    assert fragment.alternate_airport_runway_pairs == frozenset()


def test_adapter_passes_alternate_topology_through_verbatim_never_inventing_it():
    fragment = _fragment(
        runway_ends=frozenset({"30L"}),
        alternate_airport_runway_ends=frozenset({"30L", "12R"}),
        alternate_airport_runway_pairs=frozenset({"12R/30L"}),
    )
    bag = candidate_fragment_to_evidence_bag(fragment)
    assert bag.alternate_airport_runway_ends == frozenset({"30L", "12R"})
    assert bag.alternate_airport_runway_pairs == frozenset({"12R/30L"})

    # A fragment with runway tokens but NO explicit alternate topology
    # must map to an EMPTY EvidenceBag.alternate_airport_* - the adapter
    # never derives it from runway_ends/runway_pairs itself.
    plain_fragment = _fragment(runway_ends=frozenset({"30L"}), runway_pairs=frozenset({"12R/30L"}))
    plain_bag = candidate_fragment_to_evidence_bag(plain_fragment)
    assert plain_bag.alternate_airport_runway_ends == frozenset()
    assert plain_bag.alternate_airport_runway_pairs == frozenset()


# --- required invariant: alternate topology is not inferred inside the guard ---


def test_guard_never_infers_alternate_topology_from_absence_alone():
    """A runway token simply absent from the candidate's own topology,
    with alternate_airport_runway_ends/_pairs left empty and no other
    contradicting field set, must remain absence-only (no contradiction)
    - the guard itself never invents alternate-airport evidence."""
    candidate = CandidateAirport(
        id=4, name="San Francisco International Airport",
        canonical_runway_ends=frozenset({"1L", "19R", "1R", "19L"}),
    )
    bag = EvidenceBag(issuers=frozenset(), runway_ends=frozenset({"30L"}))  # no alternate fields set
    decision = evaluate_attachment(candidate, bag)
    assert decision.outcome == AttachmentOutcome.INSUFFICIENT_IDENTITY
    assert decision.contradicting_evidence == ()


# --- required invariant: contradiction-first behavior remains unchanged ---


def test_contradiction_still_vetoes_positive_evidence_with_alternate_topology_present():
    """Even when a candidate has genuine positive evidence (issuer match),
    a real alternate-airport-topology contradiction still wins - identical
    to the guard's pre-existing, unmodified contradiction-first rule."""
    candidate = CandidateAirport(
        id=4, name="San Francisco International Airport",
        canonical_runway_ends=frozenset({"1L", "19R", "1R", "19L"}),
        known_issuers=frozenset({"Metropolitan Airports Commission"}),  # deliberately shared issuer, for this test only
    )
    bag = EvidenceBag(
        issuers=frozenset({"Metropolitan Airports Commission"}),
        runway_ends=frozenset({"30L"}),
        alternate_airport_runway_ends=frozenset({"30L"}),
    )
    decision = evaluate_attachment(candidate, bag)
    assert decision.outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT
    assert len(decision.positive_evidence) >= 1  # the issuer match was found and recorded...
    # ...but did not change the outcome, per the guard's own contradiction-first algorithm.


# --- required invariant: raw evidence remains unchanged ---


def test_enrichment_never_changes_raw_text_or_fragment_identity():
    fragment = _fragment()
    enriched = enrich_with_alternate_airport_topology(fragment, known_other_airport_runway_ends=frozenset({"30L"}))
    assert enriched.raw_text == fragment.raw_text
    assert enriched.fragment_hash == fragment.fragment_hash
    assert enriched.identity == fragment.identity
    assert enriched.issuers == fragment.issuers
    assert enriched.runway_ends == fragment.runway_ends
    assert enriched.runway_pairs == fragment.runway_pairs
    assert enriched.artifact_identity == fragment.artifact_identity
    assert enriched.source_locator == fragment.source_locator
