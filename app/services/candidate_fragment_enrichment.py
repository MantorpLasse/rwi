"""A tiny, pure, deterministic, OPTIONAL post-extraction enrichment step
(docs/product/cross-airport-evidence-wiring-report.md).

Attaches alternate-airport runway-topology evidence to an already-built
CandidateFragment, for the narrow purpose of letting the Evidence
Attachment Guard reach a genuine REJECT_CROSS_AIRPORT (rather than merely
INSUFFICIENT_IDENTITY) when a fragment's own extracted runway tokens are
independently confirmed - by the CALLER, using already-known canonical
topology - to belong to one specific, real, different airport.

Sits strictly BETWEEN extraction (app/acquisition/*_extractor.py,
CandidateFragment) and the guard (app.services.evidence_attachment_guard)
- called, if at all, by orchestration, never by either of those two
modules themselves. Neither CandidateFragment nor the guard is modified
by this module's existence; both keep their existing, unmodified
semantics exactly.

Boundary discipline:
  - NO database access, NO HTTP, NO search-query context read - this
    module imports nothing from app.database, app.models, or httpx.
  - NEVER infers alternate-airport topology "because" of provider/
    source-family identity - the caller must supply the OTHER airport's
    real canonical runway topology explicitly, as plain frozensets. This
    module has no concept of "MSP," "SFO," or any specific airport/
    provider at all.
  - Only ever surfaces the INTERSECTION between what the fragment's own
    extraction ACTUALLY found (fragment.runway_ends/runway_pairs) and the
    caller-supplied other-airport topology - never invents evidence
    beyond what extraction already produced, never widens a caller's
    input into something the fragment's own text doesn't support. This
    is what makes "the fragment came from an MSP-focused provider" alone
    insufficient to produce alternate-airport evidence: if the fragment's
    own extracted runway tokens don't actually overlap with the supplied
    topology, nothing is added.
  - Reuses app.services.runway_identity's existing normalize_end/
    normalize_pair for the intersection comparison - never reimplements
    runway-designation normalization.
  - Does not resolve, guess, or query which airport a fragment "belongs
    to" - the caller has already independently established that
    (typically via the guard's own positive-evidence categories, e.g. an
    issuer match plus a topology match), this function only carries that
    already-established fact's TOPOLOGY forward into the shape the guard
    already understands (EvidenceBag.alternate_airport_runway_ends/
    _pairs, unchanged, docs/architecture/ai-discovery-evidence-
    attachment-guard.md S5 rule 3).
"""
from __future__ import annotations

from dataclasses import replace

from app.services.discovery_candidate_fragment import CandidateFragment
from app.services.runway_identity import AmbiguousRunwayDesignationError, normalize_end, normalize_pair

__all__ = ["enrich_with_alternate_airport_topology"]


def _normalized_end_set(tokens: frozenset[str]) -> frozenset[str]:
    normalized: set[str] = set()
    for token in tokens:
        try:
            normalized.add(normalize_end(token))
        except AmbiguousRunwayDesignationError:
            continue
    return frozenset(normalized)


def _normalized_pair_set(tokens: frozenset[str]) -> frozenset[str]:
    normalized: set[str] = set()
    for token in tokens:
        try:
            normalized.add(normalize_pair(token))
        except AmbiguousRunwayDesignationError:
            continue
    return frozenset(normalized)


def enrich_with_alternate_airport_topology(
    fragment: CandidateFragment,
    *,
    known_other_airport_runway_ends: frozenset[str] = frozenset(),
    known_other_airport_runway_pairs: frozenset[str] = frozenset(),
) -> CandidateFragment:
    """Returns a NEW CandidateFragment (the input is frozen and never
    mutated) whose alternate_airport_runway_ends/_pairs contain exactly
    the subset of the fragment's OWN extracted runway_ends/runway_pairs
    that also, independently, normalize-match
    known_other_airport_runway_ends/_pairs - a real, specific airport's
    already-known canonical topology, supplied entirely by the caller.

    known_other_airport_runway_ends/_pairs is typically
    `some_candidate_airport.canonical_runway_ends`/
    `.canonical_runway_pairs` from an app.services.evidence_attachment_guard.CandidateAirport
    the caller has ALREADY built for the airport this fragment is
    independently believed to belong to - this function does not build,
    look up, or require that object itself; plain frozensets are enough.

    If the fragment's own extraction found no runway tokens at all, or
    none of them match the supplied topology, the returned fragment's
    alternate_airport_* fields are unchanged from the input (empty by
    default) - this function never invents evidence beyond what
    extraction already produced. Any alternate-airport evidence already
    present on the input fragment (e.g. from a prior enrichment call) is
    preserved and unioned with, never discarded.
    """
    fragment_ends_norm = _normalized_end_set(fragment.runway_ends)
    fragment_pairs_norm = _normalized_pair_set(fragment.runway_pairs)
    other_ends_norm = _normalized_end_set(known_other_airport_runway_ends)
    other_pairs_norm = _normalized_pair_set(known_other_airport_runway_pairs)

    matched_ends = fragment_ends_norm & other_ends_norm
    matched_pairs = fragment_pairs_norm & other_pairs_norm

    if not matched_ends and not matched_pairs:
        return fragment

    return replace(
        fragment,
        alternate_airport_runway_ends=fragment.alternate_airport_runway_ends | matched_ends,
        alternate_airport_runway_pairs=fragment.alternate_airport_runway_pairs | matched_pairs,
    )
