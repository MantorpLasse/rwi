"""Discovery Research Loop V1, Slice 2 - bounded, ONE-ROUND search
execution over a deterministic question plan.

    ResearchClue
        -> plan_research_questions() (Slice 1, unmodified)
        -> execute each ResearchQuestion.search_query through an injected
           SearchProvider (app.discovery.search - unmodified)
        -> collect every SearchOutcome (one per question, honestly, never
           swallowed - see EXECUTION CONTRACT below)
        -> deduplicate_results() ONCE across ALL questions' results
           (app.discovery.dedup - unmodified)
        -> triage_results() ONCE over the aggregate deduplicated
           population (app.discovery.triage - unmodified)
        -> ResearchLoopReport (runtime-only, presentation-oriented)
        -> STOP

ONE ROUND ONLY (this slice's own explicit, deliberate limitation): no
round 2, no proper-noun extraction from result titles, no query
refinement, no recursive planning, no LLM-generated follow-up, no
automatic re-assessment of unresolved dimensions. The point of Slice 2 is
to evaluate whether Slice 1's deterministic questions actually surface
useful real search results before any further autonomy is added - see the
mission's own "does the giraffe find anything useful?" framing.

CORE PRINCIPLE (restated so it cannot be missed by a future reader of this
file alone, mirroring app.discovery.triage's own equivalent section):
SearchResult != evidence. TriagedResult != fact. HIGH priority != truth.
No result != negative evidence. This module RANKS and REPORTS. It never
CONCLUDES, never FETCHES, never PERSISTS, and never touches governed
domain state (Signal/SourceAssertion/Airport/Installation/FH-D4) - none of
those types, or anything that writes them, is imported here. See
tests/test_research_loop_architectural_safety.py, which enforces this by
AST inspection, not merely convention.

REUSES VERBATIM, MODIFIES NOTHING: app.services.research_question_planning
(Slice 1), app.discovery.query.SearchQuery, app.discovery.search.SearchProvider/
SearchOutcome/SearchOutcomeStatus, app.discovery.dedup.deduplicate_results(),
app.discovery.triage.triage_results()/TriagedResult/PriorityBand/
DomainCategory, app.discovery.identity.AirportIdentity. No parallel
SearchResult/SearchOutcome/Triage type is invented anywhere in this module.

CROSS-QUESTION DEDUP (mission Part 5): all_results from every question are
collected into one flat list, in question order, then
deduplicate_results() is called EXACTLY ONCE - the same "call once, across
everything" discipline app.services.discovery_temporal_followup's own
review_temporal_followup.py CLI already established for cross-trigger
dedup, extended here to cross-QUESTION (a URL surfaced by more than one
dimension's query remains exactly one discovered URL,
DedupedResult.found_by preserving every SearchQuery that (re-)found it).

DIMENSION-LINEAGE PRESERVATION WITHOUT REDESIGN (mission Part 5's own
"if current types cannot preserve that association cleanly, report the
limitation rather than inventing persistence or complex lineage"): existing
types (SearchQuery, DedupedResult, TriagedResult) carry no back-reference
to a ResearchQuestion/ResearchDimension, and this module does not add one
to them - instead, TriagedCandidate (defined here, report-layer only) pairs
each existing, unmodified TriagedResult with the tuple of
ResearchDimension values recovered by matching DedupedResult.found_by's
own SearchQuery.rendered strings back against this run's own question
plan (rendered text is unique per question within one run - Slice 1's own
"no duplicate rendered queries" invariant makes this recovery exact, never
an approximation).

EXECUTION CONTRACT (mission Part 4): a provider that cannot legitimately
answer must return SearchOutcomeStatus.PROVIDER_FAILURE (SearchOutcome's
own existing __post_init__ already enforces this) - this module never
catches an exception from provider.search() and never converts a raised
exception into a swallowed/hidden failure; a provider violating its own
contract by raising is allowed to propagate, exactly like
scripts/discover_airport_sources.py's/scripts/review_temporal_followup.py's
own existing execution loops. One question's outcome is fully independent
of every other's - a single PROVIDER_FAILURE never aborts the run.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.discovery.dedup import deduplicate_results
from app.discovery.identity import AirportIdentity
from app.discovery.search import SearchOutcome, SearchProvider
from app.discovery.triage import TriagedResult, triage_results
from app.services.research_question_planning import (
    ResearchClue,
    ResearchDimension,
    ResearchQuestion,
    plan_research_questions,
)

__all__ = [
    "QuestionOutcome",
    "TriagedCandidate",
    "ResearchLoopReport",
    "run_research_loop",
]


@dataclass(frozen=True)
class QuestionOutcome:
    """One executed ResearchQuestion plus the raw SearchOutcome it
    produced - preserves exactly "which question was asked, which
    SearchQuery rendered, which status resulted" (mission Part 4), never
    hidden or collapsed."""

    question: ResearchQuestion
    outcome: SearchOutcome


@dataclass(frozen=True)
class TriagedCandidate:
    """One existing, unmodified TriagedResult, annotated (report layer
    only - see module docstring "DIMENSION-LINEAGE PRESERVATION") with
    every ResearchDimension whose question's query (re-)discovered this
    URL, in first-discovery order. `triaged` itself is never mutated or
    subclassed - TriagedResult's own PriorityBand/reasons/domain_category
    semantics are reused exactly as app.discovery.triage defines them."""

    triaged: TriagedResult
    dimensions: "tuple[ResearchDimension, ...]"


@dataclass(frozen=True)
class ResearchLoopReport:
    """Runtime-only, presentation-oriented result of one bounded research
    round. Never persisted, never an ORM object, carries no acceptance
    status/confidence/probability/fact-state field of any kind (see
    tests/test_research_loop_architectural_safety.py). `clue` and
    `questions` are always populated; `question_outcomes`/
    `triaged_candidates` are empty when no provider was supplied (a valid,
    network-free "show me the plan" mode, matching
    scripts/discover_airport_sources.py's own existing convention)."""

    clue: ResearchClue
    questions: "tuple[ResearchQuestion, ...]"
    question_outcomes: "tuple[QuestionOutcome, ...]"
    triaged_candidates: "tuple[TriagedCandidate, ...]"


def _dimensions_for(
    triaged: TriagedResult, rendered_to_question: "dict[str, ResearchQuestion]",
) -> "tuple[ResearchDimension, ...]":
    seen: list[ResearchDimension] = []
    for search_query in triaged.deduped.found_by:
        question = rendered_to_question.get(search_query.rendered)
        if question is not None and question.dimension not in seen:
            seen.append(question.dimension)
    return tuple(seen)


def run_research_loop(
    clue: ResearchClue, *, provider: "SearchProvider | None" = None,
) -> ResearchLoopReport:
    """Pure orchestration (aside from the one injected provider.search()
    call per question) - no database, no file, no persistence of any
    kind. `provider=None` returns the question plan only, with zero
    network access, mirroring discover_airport_sources.py's own
    `run(identity, provider=None)` convention exactly - useful for
    reviewing exactly what would be searched before spending a live
    network budget.
    """
    questions = plan_research_questions(clue)

    if provider is None:
        return ResearchLoopReport(
            clue=clue, questions=questions, question_outcomes=(), triaged_candidates=(),
        )

    question_outcomes: list[QuestionOutcome] = []
    all_results = []
    for question in questions:
        outcome = provider.search(question.search_query)
        question_outcomes.append(QuestionOutcome(question=question, outcome=outcome))
        all_results.extend(outcome.results)

    deduped = deduplicate_results(all_results)
    identity = AirportIdentity(
        name=clue.airport_context.name,
        iata_code=clue.airport_context.iata_code,
        icao_code=clue.airport_context.icao_code,
    )
    triaged = triage_results(deduped, identity=identity)

    rendered_to_question = {q.search_query.rendered: q for q in questions}
    triaged_candidates = tuple(
        TriagedCandidate(triaged=t, dimensions=_dimensions_for(t, rendered_to_question))
        for t in triaged
    )

    return ResearchLoopReport(
        clue=clue,
        questions=questions,
        question_outcomes=tuple(question_outcomes),
        triaged_candidates=triaged_candidates,
    )
