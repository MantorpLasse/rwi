"""Discovery Research Loop V1, Slice 2/3 CLI (RWI HQ "Discovery Research
Loop V1 - Slice 2/3").

SLICE 3: reports each requested ResearchDimension's SEARCH status
(CANDIDATES_FOUND / NO_CANDIDATES_FOUND / SEARCH_FAILED - search discovery
only) alongside a constant, unconditional "remains unresolved pending
preserved evidence review" statement - never a computed "resolved"/
"unresolved" verdict derived from search-result quality (Slice 2's own
`unresolved_dimensions: []` output was exactly this defect: it conflated
"found a MEDIUM candidate" with "answered the question," which are
permanently different things in this pipeline - see
app.services.research_loop's own module docstring "CORE SEMANTIC RULE").

    persisted SourceAssertion (read-only)
        -> explicit, human-chosen ResearchDimension list
        -> explicit, human-chosen search-name/IATA/ICAO context
        -> app.services.research_loop.run_research_loop() (ONE bounded
           search round - see that module's own docstring)
        -> Commander-facing human-readable / JSON report
        -> STOP

Mirrors scripts/review_temporal_followup.py's own established structure
closely: read-only DB access is limited to the one, explicitly requested
SourceAssertion row, the session is opened and closed before any Search
call is made, and this script never Fetches, never persists, and never
inspects or mutates governed intelligence (Airport/Installation/Signal -
none of those models are imported here beyond the one read-only
SourceAssertion lookup).

--search-name (and --search-iata/--search-icao) are REQUIRED, not merely
optional (a deliberate deviation, matching review_temporal_followup.py's
own --identity-name=required=True precedent exactly, not a design choice
invented for this script): the recon that authorized this slice found
Airport 40 (SDF) stored under its stale pre-2019 name "Standiford" while
its current public identity is "Louisville Muhammad Ali International
Airport" - reading Airport.name automatically would silently reproduce
that exact staleness risk. The caller supplies the current, correct
research name explicitly every time; this script never reads Airport.name
at all.

DIMENSIONS ARE NEVER INFERRED: --dimension must be supplied explicitly,
one or more times, by a human. This script does not inspect Signal or
SourceAssertion field state to guess what is unresolved (Slice 1's own
explicit design constraint, unchanged here).

Invocation (matches this repository's existing script convention):

    python -m scripts.research_airport_clue \\
        --database data/runway_safe.db --source-assertion-id 257 \\
        --dimension runway_end --dimension installation_type \\
        --dimension project_phase --dimension timing --dimension supplier \\
        --search-name "Louisville Muhammad Ali International Airport" \\
        --search-iata SDF --search-icao KSDF \\
        --allow-live-network

RWI HQ "Discovery Research Loop V1 - Slice 5F": pass --use-literal-anchors
to additionally search bounded, literal, evidence-derived terms found in
the preserved text itself (app.services.research_literal_anchors, Slice
5E) - opt-in, default OFF. Omitting the flag is byte-for-behavior
identical to every prior slice.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.discovery.brave_search_provider import BraveSearchProvider
from app.discovery.search import SearchOutcomeStatus, SearchProvider
from app.models import SourceAssertion
from app.services.discovery_temporal_followup import AirportSearchContext, AirportSearchContextError
from app.services.research_literal_anchors import extract_literal_anchors
from app.services.research_loop import ResearchLoopReport, compute_dimension_search_status, run_research_loop
from app.services.research_question_planning import ResearchClue, ResearchClueError, ResearchDimension

# Same registration convention as scripts/discover_airport_sources.py and
# scripts/review_temporal_followup.py - one entry, one adapter, no domain
# semantics. BraveSearchProvider() itself fails closed with
# PROVIDER_FAILURE per query if no API key is configured.
PROVIDER_REGISTRY: dict[str, SearchProvider] = {"brave": BraveSearchProvider()}

_DISCLAIMER = (
    "RESEARCH LOOP - discovery candidates only, not evidence, not a fact, not a "
    "verified source, not an accepted claim. SearchResult != evidence. "
    "TriagedResult != fact. HIGH priority != truth. No result != negative evidence. "
    "Only a Fetched, preserved, extracted, human-KEPT, IdentityGuard-evaluated, "
    "human-PERSISTed excerpt can ever become durable evidence."
)

_FETCH_HINT = (
    "\nSTOP\nSearch results are discovery candidates only.\n"
    "To preserve evidence, explicitly Fetch a selected URL using the existing human\n"
    "Fetch workflow:\n"
    "  python -m scripts.fetch_discovered_url <url> --database <path>\n"
    "This script never does that automatically."
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True, help="SQLite database path (e.g. data/runway_safe.db).")
    parser.add_argument(
        "--source-assertion-id", required=True, type=int,
        help="Persisted SourceAssertion.id to read preserved evidence text from (read-only).",
    )
    parser.add_argument(
        "--dimension", action="append", dest="dimensions", default=[],
        choices=sorted(d.value.lower() for d in ResearchDimension),
        help="A ResearchDimension to ask about. Repeat for more than one. Never inferred - "
        "a human must name each one explicitly.",
    )
    parser.add_argument(
        "--search-name", required=True,
        help="Explicit, current research search name - SEARCH CONTEXT, never read from "
        "Airport.name (which may be stale - see this script's own module docstring).",
    )
    parser.add_argument("--search-iata", default=None, help="Optional IATA code enrichment.")
    parser.add_argument("--search-icao", default=None, help="Optional ICAO code enrichment.")
    parser.add_argument(
        "--allow-live-network", action="store_true",
        help="Execute the generated questions against a real SearchProvider (brave). "
        "Omit to print the question plan only, with zero network access.",
    )
    parser.add_argument(
        "--use-literal-anchors", action="store_true",
        help="RWI HQ 'Discovery Research Loop V1 - Slice 5F'. Opt-in, default OFF. When "
        "given, bounded, literal, evidence-derived query variants (app.services."
        "research_literal_anchors, Slice 5E) are added to the plan alongside the "
        "existing baseline queries. Without this flag, behavior is byte-for-behavior "
        "identical to every prior slice.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    return parser


def load_evidence_text(session: Session, source_assertion_id: int) -> str:
    """Read-only. Mirrors scripts/review_temporal_followup.py's own
    load_fragment_for_review() precedent: raises ValueError (never
    fabricates a weaker input) if the row does not exist or has no
    preserved evidence text."""
    assertion = session.get(SourceAssertion, source_assertion_id)
    if assertion is None:
        raise ValueError(f"No SourceAssertion with id={source_assertion_id!r} exists.")
    if not assertion.raw_relevant_text:
        raise ValueError(f"SourceAssertion id={source_assertion_id!r} has no raw_relevant_text - nothing to research.")
    return assertion.raw_relevant_text


_MAX_CANDIDATES_SHOWN_PER_DIMENSION_HUMAN = 5

_RESEARCH_STATUS_LINE = (
    "Research status: STILL UNRESOLVED - search candidates are not evidence "
    "and do not resolve this research question."
)


def _queries_for(report: ResearchLoopReport, dimension: ResearchDimension):
    return [p for p in report.planned_queries if p.dimension is dimension]


def _candidates_for(report: ResearchLoopReport, dimension: ResearchDimension):
    return [c for c in report.triaged_candidates if dimension in c.dimensions]


def _print_human(report: ResearchLoopReport, *, network_used: bool, use_literal_anchors: bool) -> None:
    print(_DISCLAIMER)

    context = report.clue.airport_context
    print("\nRESEARCH CLUE")
    print(f"Airport: {context.name}" + (f" (IATA={context.iata_code}, ICAO={context.icao_code})" if context.iata_code or context.icao_code else ""))
    excerpt = report.clue.evidence_text
    print(f"Evidence excerpt: {excerpt[:200]}{'...' if len(excerpt) > 200 else ''}")
    print(f"Dimensions: {', '.join(q.dimension.value for q in report.questions)}")
    print(f"Literal anchors: {'ENABLED' if use_literal_anchors else 'disabled'}")
    if use_literal_anchors:
        anchors = extract_literal_anchors(report.clue.evidence_text, airport_context=context)
        if anchors:
            print("Extracted literal anchors (explainability only - not a resolved value or answer):")
            for a in anchors:
                print(f"  - {a.text!r} [{a.kind.value}] -> {a.dimension_hint.value}")
        else:
            print("Extracted literal anchors: none found - plan is identical to the baseline plan.")
    print("\nSearch candidates are not evidence and do not resolve the research question.")

    for q in report.questions:
        dimension = q.dimension
        queries = _queries_for(report, dimension)
        print(f"\n{dimension.value}")
        print(f"Question: {q.question}")
        print(f"Reason: {q.reason}")
        print("Queries:")
        for planned in queries:
            print(f"  {planned.search_query.rendered}")

        if not network_used:
            continue

        status = compute_dimension_search_status(dimension, report)
        print(f"Search status: {status.value}")
        print(_RESEARCH_STATUS_LINE)
        candidates = _candidates_for(report, dimension)
        if candidates:
            shown = candidates[:_MAX_CANDIDATES_SHOWN_PER_DIMENSION_HUMAN]
            print(f"Top candidates (showing {len(shown)} of {len(candidates)}):")
            for c in shown:
                r = c.triaged.deduped.result
                print(f"  [{c.triaged.band.value}] {r.title}")
                print(f"    {r.url}")
                print(f"    domain: {c.triaged.domain_category.value}; why: {'; '.join(c.triaged.reasons)}")

    if not network_used:
        print(
            "\nNo --allow-live-network given: NO SEARCH PROVIDER EXECUTED. "
            "No network access was performed - the plan above was never searched."
        )
        print(_FETCH_HINT)
        return

    outcomes = [qo.outcome for qo in report.query_outcomes]
    failures = [o for o in outcomes if o.status == SearchOutcomeStatus.PROVIDER_FAILURE]
    no_results = [o for o in outcomes if o.status == SearchOutcomeStatus.NO_RESULTS]
    ok = [o for o in outcomes if o.status == SearchOutcomeStatus.OK]
    unique_urls = len(report.triaged_candidates)

    print("\nSEARCH SUMMARY")
    print(f"{len(outcomes)} queries")
    print(f"{len(ok)} success")
    print(f"{len(no_results)} no results")
    print(f"{len(failures)} errors")
    print(f"{unique_urls} unique URLs")
    if failures:
        print("\nProvider failures:")
        for o in failures:
            print(f"  [{o.query.rendered}] {o.error}")

    print(_FETCH_HINT)


def _print_json(report: ResearchLoopReport, *, network_used: bool, use_literal_anchors: bool) -> None:
    context = report.clue.airport_context
    anchors = extract_literal_anchors(report.clue.evidence_text, airport_context=context) if use_literal_anchors else ()
    payload = {
        "_disclaimer": _DISCLAIMER,
        "_research_status_note": _RESEARCH_STATUS_LINE,
        "clue": {
            "airport_name": context.name, "iata_code": context.iata_code, "icao_code": context.icao_code,
            "evidence_excerpt": report.clue.evidence_text[:200],
            "dimensions": [q.dimension.value for q in report.questions],
        },
        "literal_anchors_enabled": use_literal_anchors,
        "literal_anchors": [
            {"text": a.text, "kind": a.kind.value, "dimension_hint": a.dimension_hint.value} for a in anchors
        ],
        "questions": [
            {
                "dimension": q.dimension.value, "question": q.question,
                "reason": q.reason,
                "queries": [p.search_query.rendered for p in _queries_for(report, q.dimension)],
                # search_status is search-discovery status ONLY - never
                # evidence-resolution status. research_status is always
                # the same, constant, unconditional string (module
                # docstring "CORE SEMANTIC RULE") - it is never computed
                # from search-result quality.
                "search_status": compute_dimension_search_status(q.dimension, report).value if network_used else None,
                "research_status": "STILL_UNRESOLVED" if network_used else None,
            }
            for q in report.questions
        ],
        "network_used": network_used,
        "query_outcomes": [
            {
                "dimension": qo.planned_query.dimension.value, "query": qo.outcome.query.rendered,
                "status": qo.outcome.status.value, "error": qo.outcome.error,
                "result_count": len(qo.outcome.results),
            }
            for qo in report.query_outcomes
        ],
        "triaged_candidates": [
            {
                "priority_band": c.triaged.band.value,
                "domain_category": c.triaged.domain_category.value,
                "reasons": list(c.triaged.reasons),
                "title": c.triaged.deduped.result.title,
                "url": c.triaged.deduped.result.url,
                "snippet": c.triaged.deduped.result.snippet,
                "found_by": [q.rendered for q in c.triaged.deduped.found_by],
                "dimensions": [d.value for d in c.dimensions],
            }
            for c in report.triaged_candidates
        ],
    }
    print(json.dumps(payload, indent=2))


def main(argv: "Sequence[str] | None" = None) -> int:
    args = _parser().parse_args(argv)

    if not args.dimensions:
        print("Refused: at least one --dimension is required.", file=sys.stderr)
        return 2

    try:
        context = AirportSearchContext(name=args.search_name, iata_code=args.search_iata, icao_code=args.search_icao)
    except AirportSearchContextError as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2

    engine = create_engine(f"sqlite:///{args.database}")
    with Session(engine) as session:
        try:
            evidence_text = load_evidence_text(session, args.source_assertion_id)
        except ValueError as exc:
            print(f"Refused: {exc}", file=sys.stderr)
            return 2
    # Session closed here - no database access happens below this point.

    dimensions = tuple(ResearchDimension(d.upper()) for d in args.dimensions)
    try:
        clue = ResearchClue(evidence_text=evidence_text, airport_context=context, unresolved_dimensions=dimensions)
    except ResearchClueError as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2

    provider: "SearchProvider | None" = PROVIDER_REGISTRY["brave"] if args.allow_live_network else None
    report = run_research_loop(clue, provider=provider, use_literal_anchors=args.use_literal_anchors)

    if args.json:
        _print_json(report, network_used=args.allow_live_network, use_literal_anchors=args.use_literal_anchors)
    else:
        _print_human(report, network_used=args.allow_live_network, use_literal_anchors=args.use_literal_anchors)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
