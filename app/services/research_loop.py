"""Discovery Research Loop V1, Slice 2/3 - bounded, ONE-ROUND search
execution over a deterministic query plan, with honest search-discovery
status reporting.

    ResearchClue
        -> plan_research_search_queries() (Slice 3, reused unmodified from
           app.services.research_question_planning - the FULL query plan,
           possibly more than one SearchQuery per dimension), OR, ONLY when
           the caller explicitly passes use_literal_anchors=True (Slice 5F,
           default False - see run_research_loop's own docstring),
           plan_research_search_queries_with_anchors() (Slice 5E, reused
           unmodified from app.services.research_literal_anchors - the
           same baseline plan plus bounded, literal, evidence-derived
           query variants)
        -> execute each PlannedResearchQuery.search_query through an
           injected SearchProvider (app.discovery.search - unmodified)
        -> collect every SearchOutcome (one per planned query, honestly,
           never swallowed - see EXECUTION CONTRACT below)
        -> deduplicate_results() ONCE across ALL planned queries' results
           (app.discovery.dedup - unmodified)
        -> triage_results() ONCE over the aggregate deduplicated
           population (app.discovery.triage - unmodified, unchanged HIGH
           semantics - see CORE SEMANTIC RULE)
        -> compute_dimension_search_status() per requested dimension
        -> ResearchLoopReport (runtime-only, presentation-oriented)
        -> STOP

ONE ROUND ONLY (unchanged since Slice 2): no round 2, no proper-noun
extraction from result titles, no query refinement, no recursive
planning, no LLM-generated follow-up, no automatic re-assessment of
unresolved dimensions.

CORE SEMANTIC RULE (RWI HQ "Discovery Research Loop V1 - Slice 3", the
central fix this slice makes): Search may discover CANDIDATES. Search may
NOT resolve a research dimension. A ResearchDimension remains unresolved
until later preserved/extracted evidence is evaluated through the
evidence/governance path - full stop, unconditionally, regardless of how
many candidates were found or how they triaged. Therefore:

    Search candidate found != question answered
    MEDIUM != resolved
    HIGH != resolved
    NO_RESULTS != negative evidence

Slice 2's own CLI computed `unresolved_dimensions` by checking whether any
HIGH/MEDIUM candidate existed for a dimension - a real defect this slice
removes entirely: that computation conflated SEARCH DISCOVERY (did we find
something) with RESEARCH RESOLUTION (has the question been answered),
which are permanently different questions in this pipeline. `DimensionSearchStatus`
(CANDIDATES_FOUND / NO_CANDIDATES_FOUND / SEARCH_FAILED) answers ONLY the
first, and its own docstring/values deliberately avoid every word that
could be misread as the second (no RESOLVED/CONFIRMED/VERIFIED/ANSWERED/
ESTABLISHED member or synonym anywhere in this module). The report/CLI
must always print BOTH the search status AND a constant, unconditional
"remains unresolved pending preserved evidence review" statement for
every requested dimension - see scripts/research_airport_clue.py.

TRIAGE SEMANTICS UNCHANGED (mission's own explicit instruction): this
slice does not touch app.discovery.triage in any way. The real live SDF
dry run found 0 HIGH / 56 MEDIUM / 8 LOW, including one genuinely relevant
EMAS/"East Runway" mention capped at MEDIUM because the strong concept
term appeared in the result's snippet, not its title - triage's own
documented, deliberate HIGH-band rule. This is a search-recall/triage-
precision question, explicitly deferred to a later mission; this slice
fixes query quality and status reporting only, never triage's own rules.

QUERY-PLAN CARDINALITY (see app.services.research_question_planning's own
module docstring, "QUERY-PLAN CARDINALITY"): INSTALLATION_TYPE and
PROJECT_PHASE now each produce two PlannedResearchQuery entries (Slice 3's
own query-quality hardening); every other dimension still produces
exactly one. This module executes the FULL plan_research_search_queries()
plan, never merely one query per dimension - see EXECUTION CONTRACT.

REUSES VERBATIM, MODIFIES NOTHING: app.services.research_question_planning
(Slice 1/3), app.discovery.query.SearchQuery, app.discovery.search.SearchProvider/
SearchOutcome/SearchOutcomeStatus, app.discovery.dedup.deduplicate_results(),
app.discovery.triage.triage_results()/TriagedResult/PriorityBand/
DomainCategory, app.discovery.identity.AirportIdentity. No parallel
SearchResult/SearchOutcome/Triage type is invented anywhere in this module.

CROSS-QUERY DEDUP (unchanged principle from Slice 2, now across the wider
plan): all_results from every PLANNED QUERY (not merely one per dimension)
are collected into one flat list, in plan order, then
deduplicate_results() is called EXACTLY ONCE.

DIMENSION-LINEAGE PRESERVATION WITHOUT REDESIGN (unchanged from Slice 2):
TriagedCandidate (report-layer only) pairs each existing, unmodified
TriagedResult with the tuple of ResearchDimension values recovered by
matching DedupedResult.found_by's own SearchQuery.rendered strings back
against this run's own FULL query plan (plan_research_search_queries()'s
own "no duplicate rendered queries across the whole plan" invariant makes
this recovery exact even with multiple queries per dimension).

EXECUTION CONTRACT (unchanged from Slice 2): a provider that cannot
legitimately answer must return SearchOutcomeStatus.PROVIDER_FAILURE; this
module never catches an exception from provider.search(). One planned
query's outcome is fully independent of every other's - a single
PROVIDER_FAILURE never aborts the run, and (new in Slice 3) never by
itself marks a whole dimension SEARCH_FAILED if another of that
dimension's own queries still succeeds - see compute_dimension_search_status().
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.discovery.dedup import deduplicate_results
from app.discovery.identity import AirportIdentity
from app.discovery.search import SearchOutcome, SearchOutcomeStatus, SearchProvider
from app.discovery.triage import TriagedResult, triage_results
from app.services.research_literal_anchors import plan_research_search_queries_with_anchors
from app.services.research_question_planning import (
    PlannedResearchQuery,
    ResearchClue,
    ResearchDimension,
    ResearchQuestion,
    plan_research_questions,
    plan_research_search_queries,
)

__all__ = [
    "DimensionSearchStatus",
    "QueryOutcome",
    "TriagedCandidate",
    "ResearchLoopReport",
    "run_research_loop",
    "compute_dimension_search_status",
]


class DimensionSearchStatus(str, Enum):
    """SEARCH DISCOVERY STATUS only - never evidence-resolution status
    (module docstring "CORE SEMANTIC RULE"). Deliberately avoids
    RESOLVED/CONFIRMED/VERIFIED/ANSWERED/ESTABLISHED or any synonym: a
    CANDIDATES_FOUND dimension is exactly as unresolved, from this
    pipeline's own governance point of view, as a NO_CANDIDATES_FOUND one -
    the only difference is whether a human has something to look at next."""

    CANDIDATES_FOUND = "CANDIDATES_FOUND"
    NO_CANDIDATES_FOUND = "NO_CANDIDATES_FOUND"
    SEARCH_FAILED = "SEARCH_FAILED"


@dataclass(frozen=True)
class QueryOutcome:
    """One executed PlannedResearchQuery plus the raw SearchOutcome it
    produced - preserves exactly "which query was asked, which SearchQuery
    rendered, which status resulted," never hidden or collapsed. A
    dimension with two PlannedResearchQuery entries produces two
    QueryOutcome entries, independently."""

    planned_query: PlannedResearchQuery
    outcome: SearchOutcome


@dataclass(frozen=True)
class TriagedCandidate:
    """One existing, unmodified TriagedResult, annotated (report layer
    only - see module docstring "DIMENSION-LINEAGE PRESERVATION") with
    every ResearchDimension whose planned query (re-)discovered this URL,
    in first-discovery order. `triaged` itself is never mutated or
    subclassed - TriagedResult's own PriorityBand/reasons/domain_category
    semantics are reused exactly as app.discovery.triage defines them."""

    triaged: TriagedResult
    dimensions: "tuple[ResearchDimension, ...]"


@dataclass(frozen=True)
class ResearchLoopReport:
    """Runtime-only, presentation-oriented result of one bounded research
    round. Never persisted, never an ORM object, carries no acceptance
    status/confidence/probability/fact-state field of any kind (see
    tests/test_research_loop_architectural_safety.py). `clue`/`questions`/
    `planned_queries` are always populated; `query_outcomes`/
    `triaged_candidates` are empty when no provider was supplied (a valid,
    network-free "show me the plan" mode, matching
    scripts/discover_airport_sources.py's own existing convention).
    `questions` remains one human-readable ResearchQuestion per dimension
    (Slice 1, unchanged); `planned_queries` is the FULL executed query
    plan (Slice 3), which may contain more than one entry for the same
    dimension."""

    clue: ResearchClue
    questions: "tuple[ResearchQuestion, ...]"
    planned_queries: "tuple[PlannedResearchQuery, ...]"
    query_outcomes: "tuple[QueryOutcome, ...]"
    triaged_candidates: "tuple[TriagedCandidate, ...]"


def _dimensions_for(
    triaged: TriagedResult, rendered_to_query: "dict[str, PlannedResearchQuery]",
) -> "tuple[ResearchDimension, ...]":
    seen: list[ResearchDimension] = []
    for search_query in triaged.deduped.found_by:
        planned = rendered_to_query.get(search_query.rendered)
        if planned is not None and planned.dimension not in seen:
            seen.append(planned.dimension)
    return tuple(seen)


def compute_dimension_search_status(
    dimension: ResearchDimension, report: ResearchLoopReport,
) -> DimensionSearchStatus:
    """Pure, deterministic. Derived ONLY from search execution - never
    from PriorityBand (module docstring "CORE SEMANTIC RULE": do not infer
    meaning from HIGH/MEDIUM/LOW, which affects attention ordering only).

    CANDIDATES_FOUND: at least one triaged (deduplicated) candidate is
    associated with `dimension`, at ANY band including LOW - a search
    candidate, however weakly it triaged, is still a candidate, and this
    status says nothing at all about how strong it is.

    SEARCH_FAILED: `dimension` was requested, has at least one
    QueryOutcome, and EVERY one of its queries returned
    SearchOutcomeStatus.PROVIDER_FAILURE - i.e. this dimension could not be
    searched at all. If even one of a dimension's queries succeeded
    (OK or NO_RESULTS), a partial failure elsewhere for the SAME dimension
    never demotes it to SEARCH_FAILED - see the per-query breakdown in
    `query_outcomes` for that detail instead.

    NO_CANDIDATES_FOUND: every query for `dimension` executed without
    PROVIDER_FAILURE, but no candidate survived dedup/triage. This is a
    valid, honest, non-error result - never a negative fact.
    """
    relevant_outcomes = [qo for qo in report.query_outcomes if qo.planned_query.dimension == dimension]
    if any(dimension in c.dimensions for c in report.triaged_candidates):
        return DimensionSearchStatus.CANDIDATES_FOUND
    if relevant_outcomes and all(
        qo.outcome.status == SearchOutcomeStatus.PROVIDER_FAILURE for qo in relevant_outcomes
    ):
        return DimensionSearchStatus.SEARCH_FAILED
    return DimensionSearchStatus.NO_CANDIDATES_FOUND


def run_research_loop(
    clue: ResearchClue, *, provider: "SearchProvider | None" = None, use_literal_anchors: bool = False,
) -> ResearchLoopReport:
    """Pure orchestration (aside from the one injected provider.search()
    call per planned query) - no database, no file, no persistence of any
    kind. `provider=None` returns the query plan only, with zero network
    access, mirroring discover_airport_sources.py's own
    `run(identity, provider=None)` convention exactly - useful for
    reviewing exactly what would be searched before spending a live
    network budget.

    `use_literal_anchors` (RWI HQ "Discovery Research Loop V1 - Slice 5F",
    default False - existing callers/behavior are completely unaffected
    unless this is explicitly passed True): when False (the default),
    `planned_queries` is exactly `plan_research_search_queries(clue)`,
    byte-for-behavior identical to every prior slice. When True,
    `planned_queries` is `plan_research_search_queries_with_anchors(clue)`
    instead (app.services.research_literal_anchors, Slice 5E) - the
    existing baseline plan plus bounded, literal, evidence-derived query
    variants, always as an unmodified prefix. This flag changes ONLY
    which queries get executed; it introduces no new DimensionSearchStatus
    member, no new ResearchQuestion/ResearchClue semantic, and no
    resolution of any kind - CANDIDATES_FOUND still means only "search
    returned candidates," exactly as before.
    """
    questions = plan_research_questions(clue)
    planned_queries = (
        plan_research_search_queries_with_anchors(clue) if use_literal_anchors
        else plan_research_search_queries(clue)
    )

    if provider is None:
        return ResearchLoopReport(
            clue=clue, questions=questions, planned_queries=planned_queries,
            query_outcomes=(), triaged_candidates=(),
        )

    query_outcomes: list[QueryOutcome] = []
    all_results = []
    for planned in planned_queries:
        outcome = provider.search(planned.search_query)
        query_outcomes.append(QueryOutcome(planned_query=planned, outcome=outcome))
        all_results.extend(outcome.results)

    deduped = deduplicate_results(all_results)
    identity = AirportIdentity(
        name=clue.airport_context.name,
        iata_code=clue.airport_context.iata_code,
        icao_code=clue.airport_context.icao_code,
    )
    triaged = triage_results(deduped, identity=identity)

    rendered_to_query = {p.search_query.rendered: p for p in planned_queries}
    triaged_candidates = tuple(
        TriagedCandidate(triaged=t, dimensions=_dimensions_for(t, rendered_to_query))
        for t in triaged
    )

    return ResearchLoopReport(
        clue=clue,
        questions=questions,
        planned_queries=planned_queries,
        query_outcomes=tuple(query_outcomes),
        triaged_candidates=triaged_candidates,
    )
