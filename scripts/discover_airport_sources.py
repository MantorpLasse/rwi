"""Discovery Search Foundation CLI (RWI Mission #9D, Part J).

    Airport Identity -> deterministic Search Plan -> (optional) Search
    Execution -> deduplicated, provenance-preserving human-readable output.

Read-only. Never writes to the database under any flag. Never creates a
Signal, Installation, Airport, or SourceAssertion - see app/discovery/ for
the independently-tested architecture this CLI is a thin wrapper around.

Invocation (matches this repository's existing script convention, e.g.
scripts/add_london_city_emas.py - run as a module from the repo root, not
as a bare file, so `app`/`scripts` resolve as packages):

    python -m scripts.discover_airport_sources \\
        --name "London City Airport" --iata LCY --icao EGLC \\
        --country "United Kingdom"

SAFETY MODEL (matches scripts/capture_mac_discovery.py's precedent):
  - No live network call happens unless a real SearchProvider is passed
    in via --provider AND that provider name is registered in
    PROVIDER_REGISTRY below. As of Mission #9F, PROVIDER_REGISTRY has one
    entry, "brave" (app.discovery.brave_search_provider.BraveSearchProvider),
    which itself fails closed with PROVIDER_FAILURE per query - never a
    live call - if BRAVE_SEARCH_API_KEY is not configured in the
    environment/.env (see app/config.py; no credential default exists in
    source). Running this CLI with no --provider (the default) performs
    zero network access, fabricates no results, and only prints the
    deterministic query plan plus an explicit notice that no provider is
    configured.
  - This script never writes to the database under any flag.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from app.discovery.brave_search_provider import BraveSearchProvider
from app.discovery.dedup import DedupedResult, deduplicate_results
from app.discovery.identity import AirportIdentity
from app.discovery.query import SearchQuery, build_search_plan
from app.discovery.search import SearchOutcome, SearchOutcomeStatus, SearchProvider
from app.discovery.triage import PriorityBand, TriagedResult, triage_results

# Mission #9F: Brave is RWI's first real, legitimately-configured search
# provider (see the Mission #9E HQ report's provider recon and the Mission
# #9F HQ report). BraveSearchProvider() reads its API key from
# app.config.settings.brave_search_api_key (env/.env only, no default,
# never invented here) and fails closed with PROVIDER_FAILURE per query if
# it is absent - so registering it unconditionally is safe even when no
# key is configured. A future provider (e.g. the Serper fallback discussed
# in the #9E report) should be added the same way: one entry, one adapter,
# no domain semantics.
PROVIDER_REGISTRY: dict[str, SearchProvider] = {"brave": BraveSearchProvider()}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="Airport name (required)")
    parser.add_argument("--iata", default=None, help="IATA code (optional)")
    parser.add_argument("--icao", default=None, help="ICAO code (optional)")
    parser.add_argument("--city", default=None, help="City (optional)")
    parser.add_argument("--country", default=None, help="Country (optional)")
    parser.add_argument(
        "--provider",
        default=None,
        help="Registered SearchProvider name to execute against. Omit to only "
        "print the generated query plan with no network access.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON instead of human-readable text."
    )
    parser.add_argument(
        "--triage",
        action="store_true",
        help="Group deduplicated results into explainable HIGH/MEDIUM/LOW review "
        "priority bands (Mission #10B). Ranks and explains only - never implies "
        "any result is verified evidence. No effect without --provider.",
    )
    return parser


def run(
    identity: AirportIdentity,
    *,
    provider: SearchProvider | None,
) -> tuple[list[SearchQuery], list[SearchOutcome], list[DedupedResult]]:
    """Core, provider-injectable logic (kept separate from argv handling
    so tests can call this directly with a fake SearchProvider - no
    subprocess, no network, no database)."""
    plan = build_search_plan(identity)

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
    identity: AirportIdentity,
    plan: list[SearchQuery],
    outcomes: list[SearchOutcome],
    deduped: list[DedupedResult],
    provider_name: str | None,
) -> None:
    print(f"Airport identity: {identity.name}")
    details = [
        f"{label}={value}"
        for label, value in (
            ("iata", identity.iata_code),
            ("icao", identity.icao_code),
            ("city", identity.city),
            ("country", identity.country),
        )
        if value
    ]
    if details:
        print("  " + ", ".join(details))

    print(f"\nGenerated {len(plan)} deterministic queries:")
    for query in plan:
        print(f"  [{query.template_id}/{query.identity_field}] {query.rendered}")

    if provider_name is None:
        print(
            "\nNo --provider given (or none configured): NO SEARCH PROVIDER "
            "CONFIGURED. No network access was performed. See the Mission #9D "
            "HQ report, Part G: BLOCKED BY SEARCH PROVIDER."
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
        for outcome in failures:
            print(f"  [{outcome.query.rendered}] {outcome.error}")

    print(f"\nDeduplicated results: {len(deduped)}")
    for item in deduped:
        r = item.result
        found_by = ", ".join(q.rendered for q in item.found_by)
        print(f"\n  {r.title}\n  {r.url}\n  {r.snippet}\n  found by: {found_by}")


_DOMAIN_LABEL = {
    "REGULATOR": "[Regulator]",
    "VENDOR_CONTRACTOR": "[Vendor/contractor]",
}


def _print_human_triage(
    identity: AirportIdentity,
    plan: list[SearchQuery],
    outcomes: list[SearchOutcome],
    triaged: list[TriagedResult],
    provider_name: str,
) -> None:
    """Reviewer-facing HIGH/MEDIUM/LOW output (Mission #10B Part L/N).
    RANK + EXPLAIN only - band names never imply verification; see
    app.discovery.triage.PriorityBand's own docstring. No internal
    numeric score is ever printed here."""
    print(f"Airport identity: {identity.name}")
    print(f"\nProvider: {provider_name}  |  Queries executed: {len(outcomes)}")

    bands: dict[PriorityBand, list[TriagedResult]] = {PriorityBand.HIGH: [], PriorityBand.MEDIUM: [], PriorityBand.LOW: []}
    for t in triaged:
        bands[t.band].append(t)

    for band in (PriorityBand.HIGH, PriorityBand.MEDIUM):
        items = bands[band]
        print(f"\n{band.value} PRIORITY ({len(items)})")
        for t in items:
            r = t.deduped.result
            label = _DOMAIN_LABEL.get(t.domain_category.value, "")
            prefix = f"{label} " if label else ""
            found_by = ", ".join(q.rendered for q in t.deduped.found_by)
            print(f"\n  {prefix}{r.title}")
            print(f"  why: {'; '.join(t.reasons)}")
            print(f"  {r.url}")
            print(f"  found by: {found_by}")

    low = bands[PriorityBand.LOW]
    print(f"\nLOW PRIORITY ({len(low)}) - compact, not discarded")
    for t in low:
        r = t.deduped.result
        print(f"  - {r.title} | {r.url}")


def _print_json_triage_section(triaged: list[TriagedResult]) -> dict:
    """Machine-readable triage section (Mission #10B Part N). No internal
    numeric score field exists anywhere in this structure."""
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
    identity: AirportIdentity,
    plan: list[SearchQuery],
    outcomes: list[SearchOutcome],
    deduped: list[DedupedResult],
    provider_name: str | None,
    triaged: list[TriagedResult] | None = None,
) -> None:
    payload = {
        "identity": {
            "name": identity.name,
            "iata_code": identity.iata_code,
            "icao_code": identity.icao_code,
            "city": identity.city,
            "country": identity.country,
        },
        "queries": [
            {
                "rendered": q.rendered,
                "template_id": q.template_id,
                "identity_field": q.identity_field,
                "identity_value": q.identity_value,
            }
            for q in plan
        ],
        "provider": provider_name,
        "outcomes": [
            {
                "query": o.query.rendered,
                "status": o.status.value,
                "error": o.error,
                "result_count": len(o.results),
            }
            for o in outcomes
        ],
        "deduplicated_results": [
            {
                "title": item.result.title,
                "url": item.result.url,
                "snippet": item.result.snippet,
                "provider": item.result.provider,
                "found_by": [q.rendered for q in item.found_by],
            }
            for item in deduped
        ],
    }
    if triaged is not None:
        payload["triage"] = _print_json_triage_section(triaged)
    print(json.dumps(payload, indent=2))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    identity = AirportIdentity(
        name=args.name,
        iata_code=args.iata,
        icao_code=args.icao,
        city=args.city,
        country=args.country,
    )

    provider: SearchProvider | None = None
    if args.provider:
        provider = PROVIDER_REGISTRY.get(args.provider)
        if provider is None:
            print(
                f"Unknown --provider '{args.provider}'. Registered providers: "
                f"{sorted(PROVIDER_REGISTRY) or '(none)'}.",
                file=sys.stderr,
            )
            return 2

    plan, outcomes, deduped = run(identity, provider=provider)
    provider_name = provider.name if provider is not None else None

    triaged: list[TriagedResult] | None = None
    if args.triage and provider_name is not None:
        triaged = triage_results(deduped, identity=identity)

    if args.json:
        _print_json(identity, plan, outcomes, deduped, provider_name, triaged)
    elif triaged is not None:
        _print_human_triage(identity, plan, outcomes, triaged, provider_name)
    else:
        _print_human(identity, plan, outcomes, deduped, provider_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
