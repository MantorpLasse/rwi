"""Discovery Search Foundation (RWI Mission #9D).

This package implements the smallest upstream Discovery capability:

    AirportIdentity -> deterministic Search Plan -> provider-neutral
    Search Execution -> deduplicated Search Results

It deliberately STOPS before SourceCandidate triage, fetching, extraction,
governance promotion or persistence (see docs/architecture, RWI Mission
#9C "Discovery Engine Architecture" for the full design and the rationale
for stopping here).

Hard invariants, enforced by convention and checked by
tests/test_discovery_architectural_safety.py:

- Nothing in this package imports or calls a database Session, a governed
  Signal/Installation/Airport creation function, SourceAssertion
  persistence, or any review-mutation service. This package is upstream
  and read-only by construction.
- Nothing here fabricates search results. A SearchProvider that cannot
  legitimately answer must report SearchOutcomeStatus.PROVIDER_FAILURE,
  never a silently-empty successful result.
"""
