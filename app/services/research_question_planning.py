"""Discovery Research Question Planner V1, Slice 1
(RWI HQ "Discovery Research Loop V1" recon, Part 3/4).

    preserved evidence text + explicit airport search context
        + caller-supplied unresolved dimensions
        -> plan_research_questions()
        -> deterministic tuple[ResearchQuestion, ...]
        -> STOP (no search executed, no Fetch, no Snapshot, no persistence,
           no Fact/Signal/Installation/Airport mutation - all future,
           separately-authorized slices)

Research Planner = journalist/scout, never judge. This module may FORMULATE
questions about unresolved project dimensions (which runway/end, new
installation vs. replacement, project phase, timing, supplier) - it may
NEVER answer them. UNKNOWN must remain UNKNOWN: failure to find or infer
something is never converted into negative evidence anywhere in this
module ("supplier unresolved" is not "no supplier exists" - see the recon
mission's own explicit warning, Part 4).

THIS IS NOT A NEW DISCOVERY ENGINE, NOT AN AUTONOMOUS AGENT, AND NOT A
GOVERNANCE ENGINE. It performs zero database access, zero network access,
zero LLM call, and zero randomization - same discipline
app.services.discovery_temporal_followup already established and this
module deliberately mirrors throughout (see MODELED ON below).

DIMENSION SCOPE (recon mission Part 2/HQ's own narrowing instruction):
exactly five V1 dimensions - RUNWAY_END, INSTALLATION_TYPE, PROJECT_PHASE,
TIMING, SUPPLIER. PROCUREMENT was considered and explicitly excluded: its
own natural sub-states (design/bidding/procurement/award) are already
named as PROJECT_PHASE's own example phases in the mission brief - adding
it as a separate dimension would create exactly the "overlap/confusion"
HQ's own instruction warned against, not a genuinely distinct question.
Five strong dimensions, not nine weak ones (recon Part 4's own guidance).

MODELED ON app.services.discovery_temporal_followup.py (reused, never
modified): this module reuses that module's own `AirportSearchContext`
type verbatim (identical semantics apply - explicit, caller-supplied
SEARCH CONTEXT, never evidence identity, never an Airport ORM read; the
caller owns provenance, matching the recon mission's own explicit finding
that Airport.name can be stale - Airport 40/SDF is stored as "Standiford"
while current public identity is "Louisville Muhammad Ali International
Airport" - and searching a stale name would silently weaken recall). This
module is a pure, sibling reuse of that type - it does not import anything
else from discovery_temporal_followup.py, and discovery_temporal_followup.py
itself is not modified by this mission (its own frozen behavior, Missions
#17B/#18C, is untouched).

Reuses app.discovery.query.SearchQuery verbatim as the query output shape
(same discipline plan_follow_up_queries() already established: no
LLM-generated free-form strings, every rendered query fully reconstructable
from template_id/identity_field/identity_value). Query text uses only
airport_context.name (quoted if multi-word, exactly matching
plan_follow_up_queries()'s own deliberate V1 scope choice, which its own
docstring explicitly defers IATA/ICAO query enrichment to "a future
mission" - this module makes the identical choice for the identical
reason, consistency over premature enrichment) - IATA/ICAO remain part of
AirportSearchContext for provenance/future use but do not appear in
rendered query text in V1.

DOES NOT INSPECT EVIDENCE TEXT TO DETERMINE WHAT IS UNRESOLVED (recon
mission Part 3's own explicit design constraint, reinforced by HQ's Slice 1
instruction): the caller supplies `unresolved_dimensions` explicitly. This
module performs NO NLP, NO regex extraction of runway numbers/suppliers/
dates/phases, and NO fuzzy inference over `evidence_text` - the only use of
evidence_text is a bounded, deterministic excerpt embedded in each
question's own `reason` field, for human explainability, never for
decision-making. A later, separately-authorized slice may build an adapter
that derives unresolved_dimensions from Signal/SourceAssertion field state
(inspecting which fields are populated vs. None) - that adapter is
explicitly NOT built here.

QUERY-PLAN CARDINALITY (RWI HQ "Discovery Research Loop V1 - Slice 3"):
Slice 2's real live SDF dry run found INSTALLATION_TYPE's single
"replacement" concept term biased recall toward replacement language and
away from new-installation evidence, and PROJECT_PHASE's single
"construction" term pulled in substantial generic terminal/parking-
expansion noise unrelated to EMAS. The fix is a small, fixed, per-
dimension EXPANSION of concept terms (_QUERY_CONCEPTS, now a tuple per
dimension instead of one string), never an OR-string or fuzzy query and
never an LLM-generated one. `ResearchQuestion` itself is DELIBERATELY left
unchanged in shape - it remains exactly one human question per dimension,
its own `search_query` field always set to that dimension's FIRST concept
term (so it stays a single, valid, backward-compatible query, never a
second responsibility bolted onto the type). The FULL query plan (all
concept terms for all requested dimensions) is available separately via
`plan_research_search_queries()` -> `PlannedResearchQuery`, a small,
additive, runtime-only search-plan layer engineered specifically so a
caller needing "every query for every dimension" (app.services.research_loop,
Slice 2) has one, while a caller needing only "the human-readable
question text" (unchanged since Slice 1) is unaffected. Both functions
read from the exact same `_QUERY_CONCEPTS` table, so they can never
independently disagree about what a dimension's first/primary query is.

Never imports app.models, app.database, any persistence/governance
service, or any network/search-execution module - see
tests/test_research_question_planning_architectural_safety.py, which
enforces this by AST inspection, not merely convention.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.discovery.query import SearchQuery
from app.services.discovery_temporal_followup import AirportSearchContext

__all__ = [
    "ResearchDimension",
    "ResearchClueError",
    "ResearchClue",
    "ResearchQuestion",
    "PlannedResearchQuery",
    "plan_research_questions",
    "plan_research_search_queries",
]


class ResearchDimension(str, Enum):
    """The five V1 research dimensions - a deterministic, closed
    vocabulary (matches TemporalTriggerKind's own enum-not-free-text
    discipline). See module docstring "DIMENSION SCOPE" for why exactly
    these five, and why PROCUREMENT is not a sixth."""

    RUNWAY_END = "RUNWAY_END"
    INSTALLATION_TYPE = "INSTALLATION_TYPE"
    PROJECT_PHASE = "PROJECT_PHASE"
    TIMING = "TIMING"
    SUPPLIER = "SUPPLIER"


class ResearchClueError(ValueError):
    """Raised when a ResearchClue is constructed with an unusable
    evidence_text or a non-ResearchDimension entry in
    unresolved_dimensions. Fails closed rather than silently proceeding
    with a degraded clue - matches CandidateFragment's own
    EMPTY_RAW_TEXT precedent and EmasEvidenceObservation's own
    isinstance-checked __post_init__ convention."""


# Human-readable question text, per dimension - generic, reusable for any
# airport (recon mission Part 4/HQ's own "do not hardcode SDF-specific
# wording" instruction). Deterministic, template-based, never generated.
_QUESTION_TEXT: dict[ResearchDimension, str] = {
    ResearchDimension.RUNWAY_END: "Which runway or runway end does this project concern?",
    ResearchDimension.INSTALLATION_TYPE: (
        "Does the evidence describe a new installation, or a replacement/reconstruction/"
        "rehabilitation of an existing one?"
    ),
    ResearchDimension.PROJECT_PHASE: (
        "What project phase is this work in (planning, design, bidding, procurement, "
        "construction, or completion)?"
    ),
    ResearchDimension.TIMING: (
        "What is the known or planned schedule (start, completion, or delivery window) "
        "for this work?"
    ),
    ResearchDimension.SUPPLIER: (
        "Has any supplier, contractor, or vendor been explicitly identified for this work?"
    ),
}

# Human-readable label used inside each generated reason string.
_DIMENSION_LABEL: dict[ResearchDimension, str] = {
    ResearchDimension.RUNWAY_END: "runway/end",
    ResearchDimension.INSTALLATION_TYPE: "new installation vs. replacement",
    ResearchDimension.PROJECT_PHASE: "project phase",
    ResearchDimension.TIMING: "timing",
    ResearchDimension.SUPPLIER: "supplier",
}

# Search-query anchor term(s) per dimension, rendered alongside "EMAS"
# (RWI's own domain-wide concept term, already used throughout
# app.discovery.query's own _CONCEPT_PLAN - not SDF-specific). Three
# dimensions keep a single, already-proven term; INSTALLATION_TYPE and
# PROJECT_PHASE were widened to two terms each by Slice 3, per the real
# live SDF dry run's own findings (module docstring "QUERY-PLAN
# CARDINALITY"):
#   - INSTALLATION_TYPE: "replacement" alone biased recall toward
#     replacement language. "new installation" and "replacement
#     reconstruction" together search neutrally across both possibilities
#     (mission's own explicit "Preferred intent").
#   - PROJECT_PHASE: "construction" alone pulled in generic terminal/
#     parking-expansion noise unrelated to EMAS. "design bid" and
#     "installation completion" emphasize EMAS-specific project-lifecycle
#     language instead (mission's own explicit example wording, used
#     verbatim).
# The FIRST entry in each tuple is also the one _question_for() uses for
# ResearchQuestion.search_query (module docstring) - never a separately
# maintained value.
_QUERY_CONCEPTS: dict[ResearchDimension, "tuple[str, ...]"] = {
    ResearchDimension.RUNWAY_END: ("runway",),
    ResearchDimension.INSTALLATION_TYPE: ("new installation", "replacement reconstruction"),
    ResearchDimension.PROJECT_PHASE: ("design bid", "installation completion"),
    ResearchDimension.TIMING: ("schedule",),
    ResearchDimension.SUPPLIER: ("supplier",),
}

# Fixed canonical order (enum declaration order) - output order never
# depends on the order the caller happened to list unresolved_dimensions
# in, matching build_search_plan()'s own fixed-_CONCEPT_PLAN-order
# determinism discipline exactly.
_DIMENSION_ORDER: tuple[ResearchDimension, ...] = tuple(ResearchDimension)

_EXCERPT_MAX_LEN = 140


@dataclass(frozen=True)
class ResearchClue:
    """Everything plan_research_questions() needs, and nothing more.

    `evidence_text`: preserved evidence text (e.g. an already-persisted
    SourceAssertion.raw_relevant_text, or an in-hand pre-KEEP
    CandidateFragment.raw_text) - used ONLY to build a bounded, literal
    excerpt for each question's own `reason` field. Never parsed, never
    inspected for content beyond that excerpt.

    `airport_context`: explicit, caller-supplied AirportSearchContext
    (reused from app.services.discovery_temporal_followup - see module
    docstring). The caller owns provenance; this module never reads
    Airport.name or any other ORM field.

    `unresolved_dimensions`: explicit, caller-supplied tuple of
    ResearchDimension. This module does NOT inspect evidence_text or any
    other state to decide what is unresolved (module docstring). May be
    empty - produces an empty result cleanly, not an error.
    """

    evidence_text: str
    airport_context: AirportSearchContext
    unresolved_dimensions: "tuple[ResearchDimension, ...]" = ()

    def __post_init__(self) -> None:
        if not self.evidence_text or not self.evidence_text.strip():
            raise ResearchClueError(
                "ResearchClue.evidence_text is required and cannot be empty - a research "
                "question must always be traceable to real preserved evidence text."
            )
        for dimension in self.unresolved_dimensions:
            if not isinstance(dimension, ResearchDimension):
                raise ResearchClueError(
                    f"ResearchClue.unresolved_dimensions must contain only ResearchDimension "
                    f"members, got {dimension!r}"
                )


@dataclass(frozen=True)
class ResearchQuestion:
    """One deterministic research question - never an accepted fact, never
    a conclusion. `search_query` reuses app.discovery.query.SearchQuery
    verbatim, not a new query type - it is that dimension's FIRST concept
    term (_QUERY_CONCEPTS[dimension][0]); see plan_research_search_queries()
    for the full query plan when a dimension has more than one."""

    dimension: ResearchDimension
    question: str
    search_query: SearchQuery
    reason: str


@dataclass(frozen=True)
class PlannedResearchQuery:
    """One concrete SearchQuery serving one ResearchDimension - the
    search-plan layer (module docstring "QUERY-PLAN CARDINALITY"), kept
    deliberately separate from ResearchQuestion rather than overloading it
    with a second responsibility. `question`/`reason` are the SAME text as
    that dimension's own ResearchQuestion - every PlannedResearchQuery for
    one dimension explains the same underlying question, just via a
    different literal search angle (self-evident from `search_query.rendered`
    itself, e.g. "new installation" vs. "replacement reconstruction" -
    no separate per-angle narrative is invented)."""

    dimension: ResearchDimension
    question: str
    search_query: SearchQuery
    reason: str


def _bounded_excerpt(text: str, *, max_len: int = _EXCERPT_MAX_LEN) -> str:
    """Deterministic, whitespace-collapsed, length-bounded excerpt of
    evidence text - never the full blob (module docstring's own "do not
    copy huge evidence blobs" instruction)."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[:max_len].rstrip() + "..."


def _render_query(name: str, concept: str) -> str:
    """Matches app.services.discovery_temporal_followup.plan_follow_up_queries()'s
    own exact quoting convention for a multi-word airport name."""
    rendered_name = f'"{name}"' if " " in name else name
    return f"{rendered_name} EMAS {concept}"


def _reason_for(clue: ResearchClue, dimension: ResearchDimension) -> str:
    excerpt = _bounded_excerpt(clue.evidence_text)
    label = _DIMENSION_LABEL[dimension]
    return f'Evidence ("{excerpt}") does not establish {label} - it remains unresolved.'


def _search_query_for(name: str, dimension: ResearchDimension, concept: str) -> SearchQuery:
    return SearchQuery(
        rendered=_render_query(name, concept),
        template_id=f"research_question_{dimension.value.lower()}_{concept.replace(' ', '_')}",
        identity_field="name",
        identity_value=name,
    )


def _question_for(clue: ResearchClue, dimension: ResearchDimension) -> ResearchQuestion:
    name = clue.airport_context.name
    primary_concept = _QUERY_CONCEPTS[dimension][0]

    return ResearchQuestion(
        dimension=dimension,
        question=_QUESTION_TEXT[dimension],
        search_query=_search_query_for(name, dimension, primary_concept),
        reason=_reason_for(clue, dimension),
    )


def plan_research_questions(clue: ResearchClue) -> "tuple[ResearchQuestion, ...]":
    """Pure, deterministic: the same ResearchClue always produces the same
    ordered tuple of ResearchQuestion, one per requested dimension, in
    fixed canonical dimension order (never the caller's own supply order -
    see _DIMENSION_ORDER). Duplicate dimensions in
    clue.unresolved_dimensions produce exactly one question each (a set
    intersection against the fixed order, not a naive per-item loop).
    Zero database, network, LLM, or randomization access anywhere in this
    function or anything it calls.
    """
    requested = set(clue.unresolved_dimensions)
    return tuple(
        _question_for(clue, dimension)
        for dimension in _DIMENSION_ORDER
        if dimension in requested
    )


def plan_research_search_queries(clue: ResearchClue) -> "tuple[PlannedResearchQuery, ...]":
    """Pure, deterministic: the FULL query plan - every concept term for
    every requested dimension, in fixed canonical dimension order, and in
    fixed concept order within a dimension (matches _QUERY_CONCEPTS'
    own declaration order). A dimension with N concept terms produces N
    PlannedResearchQuery entries, each with a distinct `search_query`
    (never duplicate rendered text - see the module's own tests). This is
    the query plan app.services.research_loop actually executes; use
    plan_research_questions() instead when only the human-readable
    one-question-per-dimension text is needed.
    """
    requested = set(clue.unresolved_dimensions)
    name = clue.airport_context.name
    question_text = {d: _QUESTION_TEXT[d] for d in _DIMENSION_ORDER}
    reason_text = {d: _reason_for(clue, d) for d in _DIMENSION_ORDER if d in requested}

    planned: list[PlannedResearchQuery] = []
    for dimension in _DIMENSION_ORDER:
        if dimension not in requested:
            continue
        for concept in _QUERY_CONCEPTS[dimension]:
            planned.append(
                PlannedResearchQuery(
                    dimension=dimension,
                    question=question_text[dimension],
                    search_query=_search_query_for(name, dimension, concept),
                    reason=reason_text[dimension],
                )
            )
    return tuple(planned)
