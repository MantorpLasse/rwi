"""Follow-Up Discovery Review V1 (RWI Mission #18C).

    persisted SourceAssertion (read-only)
        -> CandidateFragment-equivalent input
        -> detect_temporal_triggers() (frozen, Mission #17B - unmodified)
        -> plan_follow_up_queries() (frozen, Mission #17B - unmodified)
        -> existing SearchProvider (app.discovery.brave_search_provider)
        -> ALL SearchResults across the WHOLE query plan
        -> existing deduplicate_results() called ONCE, cross-query
           (app.discovery.dedup - unmodified; DedupedResult.found_by
           preserves every query that (re-)discovered a given URL)
        -> existing triage_results() (app.discovery.triage - unmodified)
        -> human-readable / JSON DISCOVERY-FETCH-REVIEW output

STOP THERE. This script never Fetches, never persists, never inspects or
mutates governed intelligence (Airport/Installation/Signal - none of
those models are imported here), and introduces NO new domain contract:
Mission #18B found SearchQuery/SearchResult/DedupedResult/TriagedResult/
DiscoveryTrigger already sufficient, and this script reuses every one of
them verbatim rather than duplicating any of their logic. A human who
decides a candidate is worth pursuing must separately, manually invoke
the existing scripts/fetch_discovered_url.py - this script never calls it
and never imports anything from app.acquisition or
app.services.generic_web_fetch/discovery_evidence_persistence.

Mirrors scripts/discover_airport_sources.py's own established structure
closely, feeding it the frozen temporal-follow-up query plan
(app.services.discovery_temporal_followup) instead of that script's own
airport-identity-only build_search_plan() plan.

DATABASE ACCESS BOUNDARY (Mission #18C Part F): the only database read
performed anywhere in this script is loading the one, explicitly
requested SourceAssertion row (artifact_identity/source_locator/
raw_relevant_text only) - never Installation, Signal, or any other
governed table, never used to rank/filter/select results. The DB session
is opened and closed before any Search call is made.

Invocation (matches this repository's existing script convention):

    python -m scripts.review_temporal_followup \\
        --database data/runway_safe.db --source-assertion-id 239 \\
        --identity-name "London City Airport" --identity-iata LCY --identity-icao EGLC \\
        --concept-term EMAS --provider brave
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.discovery.brave_search_provider import BraveSearchProvider
from app.discovery.dedup import DedupedResult, deduplicate_results
from app.discovery.identity import AirportIdentity
from app.discovery.query import SearchQuery
from app.discovery.search import SearchOutcome, SearchOutcomeStatus, SearchProvider
from app.discovery.triage import PriorityBand, TriagedResult, triage_results
from app.models import SourceAssertion
from app.services.discovery_candidate_fragment import CandidateFragment
from app.services.discovery_temporal_followup import (
    AirportSearchContext,
    AirportSearchContextError,
    DiscoveryTrigger,
    detect_temporal_triggers,
    plan_follow_up_queries,
)

# Same registration convention as scripts/discover_airport_sources.py - one
# entry, one adapter, no domain semantics. BraveSearchProvider() itself
# fails closed with PROVIDER_FAILURE per query if no API key is configured.
PROVIDER_REGISTRY: dict[str, SearchProvider] = {"brave": BraveSearchProvider()}

_DISCLAIMER = (
    "DISCOVERY / FETCH REVIEW - not evidence, not a fact, not a verified source, "
    "not an accepted claim. Titles/snippets below are discovery context only, "
    "explaining why a URL might be worth a human's attention - never evidence "
    "themselves. Only a Fetched, preserved, extracted, human-KEPT, IdentityGuard-"
    "evaluated, human-PERSISTed excerpt can ever become durable evidence."
)

_FETCH_HINT = (
    "\nTo pursue a candidate: a human may manually run\n"
    "  python -m scripts.fetch_discovered_url <url> --database <path>\n"
    "This script never does that automatically."
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True, help="SQLite database path (e.g. data/runway_safe.db).")
    parser.add_argument(
        "--source-assertion-id", required=True, type=int,
        help="Persisted SourceAssertion.id to read historical evidence from (read-only).",
    )
    parser.add_argument("--identity-name", required=True, help="Airport name - explicit SEARCH CONTEXT, never evidence.")
    parser.add_argument("--identity-iata", default=None)
    parser.add_argument("--identity-icao", default=None)
    parser.add_argument(
        "--concept-term", required=True,
        help='Explicit discovery concept term, e.g. "EMAS" - SEARCH CONTEXT, never inferred from text.',
    )
    parser.add_argument(
        "--provider", default=None,
        help="Registered SearchProvider name to execute against. Omit to only "
        "print the generated trigger/query plan with no network access.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    return parser


def load_fragment_for_review(session: Session, source_assertion_id: int) -> CandidateFragment:
    """Read-only. Builds a CandidateFragment-equivalent input from an
    already-persisted SourceAssertion's own preserved fields, never
    normalized/modified. Raises ValueError (never fabricates a weaker
    identity), matching this repository's established convention (e.g.
    app.services.snapshot_extraction.load_snapshot_for_extraction), if the
    row does not exist or lacks the minimum fields a CandidateFragment
    requires."""
    assertion = session.get(SourceAssertion, source_assertion_id)
    if assertion is None:
        raise ValueError(f"No SourceAssertion with id={source_assertion_id!r} exists.")
    if not assertion.artifact_identity or not assertion.source_locator or not assertion.raw_relevant_text:
        raise ValueError(
            f"SourceAssertion id={source_assertion_id!r} is missing artifact_identity/source_locator/"
            "raw_relevant_text - cannot build a review input from it."
        )
    return CandidateFragment(
        artifact_identity=assertion.artifact_identity,
        source_locator=assertion.source_locator,
        raw_text=assertion.raw_relevant_text,
    )


def run(
    triggers: "Sequence[DiscoveryTrigger]",
    *,
    provider: "SearchProvider | None",
) -> "tuple[list[SearchQuery], list[SearchOutcome], list[DedupedResult]]":
    """Core, provider-injectable logic (kept separate from argv/DB handling
    so tests can call this directly with a fake SearchProvider and
    synthetic triggers - no subprocess, no network, no database).

    Collects ALL SearchResults from ALL generated queries, across ALL
    supplied triggers, into one flat list, then calls
    deduplicate_results() exactly ONCE - cross-query, cross-trigger -
    preserving DedupedResult.found_by provenance (Mission #18C Part J).
    Never deduplicates per-query or per-trigger independently.
    """
    plan: list[SearchQuery] = []
    for trigger in triggers:
        plan.extend(plan_follow_up_queries(trigger))

    if provider is None:
        return plan, [], []

    outcomes: list[SearchOutcome] = []
    all_results = []
    for query in plan:
        outcome = provider.search(query)
        outcomes.append(outcome)
        all_results.extend(outcome.results)

    deduped = deduplicate_results(all_results)
    return plan, outcomes, deduped


def _print_human(
    triggers: "Sequence[DiscoveryTrigger]",
    plan: "list[SearchQuery]",
    outcomes: "list[SearchOutcome]",
    triaged: "list[TriagedResult]",
    provider_name: "str | None",
) -> None:
    print(_DISCLAIMER)

    for t in triggers:
        print(f"\nOriginating trigger: {t.trigger_kind.value}")
        print(f"  matched_text: {t.matched_text!r}")
        print(f"  source: artifact={t.artifact_identity!r} locator={t.source_locator!r}")
        print(f"  reason: {t.reason}")

    print(f"\nGenerated {len(plan)} deterministic follow-up queries:")
    for q in plan:
        print(f"  [{q.template_id}] {q.rendered}")

    if provider_name is None:
        print(
            "\nNo --provider given (or none configured): NO SEARCH PROVIDER "
            "CONFIGURED. No network access was performed."
        )
        return

    failures = [o for o in outcomes if o.status == SearchOutcomeStatus.PROVIDER_FAILURE]
    no_results = [o for o in outcomes if o.status == SearchOutcomeStatus.NO_RESULTS]
    print(f"\nProvider: {provider_name}")
    print(
        f"Queries executed: {len(outcomes)}  |  OK: {len(outcomes) - len(failures) - len(no_results)}  "
        f"|  NO_RESULTS: {len(no_results)}  |  PROVIDER_FAILURE: {len(failures)}"
    )
    if failures:
        print("\nProvider failures:")
        for o in failures:
            print(f"  [{o.query.rendered}] {o.error}")

    bands: dict[PriorityBand, list[TriagedResult]] = {PriorityBand.HIGH: [], PriorityBand.MEDIUM: [], PriorityBand.LOW: []}
    for t in triaged:
        bands[t.band].append(t)

    for band in (PriorityBand.HIGH, PriorityBand.MEDIUM):
        items = bands[band]
        print(f"\n{band.value} PRIORITY ({len(items)}) - discovery leads, not evidence")
        for tr in items:
            r = tr.deduped.result
            found_by = ", ".join(q.rendered for q in tr.deduped.found_by)
            print(f"\n  [{tr.domain_category.value}] {r.title}")
            print(f"  why: {'; '.join(tr.reasons)}")
            print(f"  {r.url}")
            print(f"  snippet (discovery context only, not evidence): {r.snippet}")
            print(f"  found by ({len(tr.deduped.found_by)} quer{'y' if len(tr.deduped.found_by) == 1 else 'ies'}): {found_by}")

    low = bands[PriorityBand.LOW]
    print(f"\nLOW PRIORITY ({len(low)}) - compact, not discarded")
    for tr in low:
        r = tr.deduped.result
        print(f"  - {r.title} | {r.url}")

    print(_FETCH_HINT)


def _print_json_triage_section(triaged: "list[TriagedResult]") -> dict:
    bands: dict[str, list[dict]] = {"HIGH": [], "MEDIUM": [], "LOW": []}
    for t in triaged:
        r = t.deduped.result
        bands[t.band.value].append(
            {
                "priority_band": t.band.value,
                "domain_category": t.domain_category.value,
                "reasons": list(t.reasons),
                "title": r.title,
                "url": r.url,
                "snippet": r.snippet,
                "provider": r.provider,
                "found_by": [q.rendered for q in t.deduped.found_by],
            }
        )
    return bands


def _print_json(
    triggers: "Sequence[DiscoveryTrigger]",
    plan: "list[SearchQuery]",
    outcomes: "list[SearchOutcome]",
    deduped: "list[DedupedResult]",
    provider_name: "str | None",
    triaged: "list[TriagedResult] | None" = None,
) -> None:
    payload = {
        "_disclaimer": _DISCLAIMER,
        "triggers": [
            {
                "trigger_kind": t.trigger_kind.value,
                "matched_text": t.matched_text,
                "artifact_identity": t.artifact_identity,
                "source_locator": t.source_locator,
                "concept_term": t.concept_term,
                "reason": t.reason,
            }
            for t in triggers
        ],
        "queries": [
            {"rendered": q.rendered, "template_id": q.template_id, "identity_field": q.identity_field, "identity_value": q.identity_value}
            for q in plan
        ],
        "provider": provider_name,
        "outcomes": [
            {"query": o.query.rendered, "status": o.status.value, "error": o.error, "result_count": len(o.results)}
            for o in outcomes
        ],
        "deduplicated_results": [
            {
                "title": item.result.title, "url": item.result.url, "snippet": item.result.snippet,
                "provider": item.result.provider, "found_by": [q.rendered for q in item.found_by],
            }
            for item in deduped
        ],
    }
    if triaged is not None:
        payload["triage"] = _print_json_triage_section(triaged)
    print(json.dumps(payload, indent=2))


def main(argv: "Sequence[str] | None" = None) -> int:
    args = _parser().parse_args(argv)

    try:
        context = AirportSearchContext(name=args.identity_name, iata_code=args.identity_iata, icao_code=args.identity_icao)
    except AirportSearchContextError as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2

    if not args.concept_term or not args.concept_term.strip():
        print("Refused: --concept-term is required and cannot be empty.", file=sys.stderr)
        return 2

    engine = create_engine(f"sqlite:///{args.database}")
    with Session(engine) as session:
        try:
            fragment = load_fragment_for_review(session, args.source_assertion_id)
        except ValueError as exc:
            print(f"Refused: {exc}", file=sys.stderr)
            return 2
    # Session closed here - no database access happens below this point.

    triggers = detect_temporal_triggers(fragment, airport_context=context, concept_term=args.concept_term)

    if not triggers:
        print(
            "No temporal follow-up trigger matched this evidence - nothing to search for. "
            "Exiting cleanly. (This is a correct, expected outcome for historical-only, "
            "negated, or option-considered text - see app.services.discovery_temporal_followup.)"
        )
        return 0

    provider: "SearchProvider | None" = None
    if args.provider:
        provider = PROVIDER_REGISTRY.get(args.provider)
        if provider is None:
            print(
                f"Unknown --provider {args.provider!r}. Registered providers: {sorted(PROVIDER_REGISTRY) or '(none)'}.",
                file=sys.stderr,
            )
            return 2

    plan, outcomes, deduped = run(triggers, provider=provider)
    provider_name = provider.name if provider is not None else None

    triaged: "list[TriagedResult] | None" = None
    if provider_name is not None:
        identity = AirportIdentity(name=context.name, iata_code=context.iata_code, icao_code=context.icao_code)
        triaged = triage_results(deduped, identity=identity)

    if args.json:
        _print_json(triggers, plan, outcomes, deduped, provider_name, triaged)
    else:
        _print_human(triggers, plan, outcomes, triaged or [], provider_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
