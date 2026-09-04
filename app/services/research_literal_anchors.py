"""Deterministic Literal Research Anchors (RWI HQ "Discovery Research Loop
V1 - Slice 5E", following the Slice 5D design recon).

    ResearchClue.evidence_text
        -> extract_literal_anchors() (pure, regex-only, bounded)
        -> tuple[ResearchAnchor, ...]
        -> plan_research_search_queries_with_anchors() (this module)
        -> STOP

WHY THE ADDITIVE PLANNER FUNCTION LIVES HERE, NOT IN
app.services.research_question_planning: that module's own
architectural-safety test (test_imports_only_the_two_authorized_runtime_types
in tests/test_research_question_planning_architectural_safety.py) asserts,
by exact set equality, that it imports ONLY app.discovery.query and
app.services.discovery_temporal_followup - nothing else, ever. This
module's own ResearchAnchor.dimension_hint field must be a real
ResearchDimension (Slice 5E's own explicit instruction), so this module
necessarily imports FROM research_question_planning - a one-way,
downward dependency. Importing back the other way (research_question_planning
importing this module) would be a circular import AND would break that
frozen, exact-set test. Placing plan_research_search_queries_with_anchors()
here instead keeps research_question_planning.py, and its existing test
suite, completely untouched - a strictly SAFER outcome than modifying that
file, not a scope reduction: the function still calls
plan_research_search_queries()/plan_research_questions() verbatim,
unmodified, exactly as specified, and their own output remains the
required strict prefix of this function's own result.

CORE INVARIANT (Slice 5D Part 2, unchanged here): literal clue -> search
term. Literal clue != semantic conclusion. A ResearchAnchor is a SUBSTRING
of evidence_text, verbatim, with original casing - never a normalized,
inferred, or looked-up value. `dimension_hint` means only "which research
query family this literal text belongs in" - it is never a resolved
value, an answer, a confidence, a fact, or a governed conclusion. Nothing
in this module ever maps a directional runway name to a numbered runway
designation, infers a reciprocal runway end, infers a supplier, infers a
project phase's real-world meaning, or infers timing - it only recognizes
that specific literal text is PRESENT.

This module performs zero database access, zero network access, zero
LLM/NLP call, and zero randomization - same discipline every other
research-planning module in this pipeline already established (see
app.services.research_question_planning's own module docstring).

SCOPE, DELIBERATELY NARROW (Slice 5E's own explicit instruction): exactly
three V1 anchor kinds - DIRECTIONAL_RUNWAY_NAME, NUMBERED_RUNWAY_DESIGNATION,
PHASE_LITERAL. QUOTED_PHRASE (considered during the Slice 5D design recon)
is deliberately NOT implemented here: V1 stays limited to narrow,
demonstrated aviation/project literals until real evidence shows a need
for anything broader - matching app.selection.structured_extraction's own
"add structure only once a real, demonstrated need exists" discipline.

PATTERN PROVENANCE:
  - NUMBERED_RUNWAY_DESIGNATION reuses (adapted, not imported - matching
    this codebase's own "independent copy, same reasoning, zero
    cross-import" convention already used between Discovery Triage/
    Selection and between MAC's own two extractor modules)
    app.acquisition.usaspending_grant_claims._RUNWAY_PATTERN's exact
    shape: RUNWAY <digits><optional L/R/C>(optional /<digits><optional
    L/R/C>). Never invents a reciprocal end - the optional second half
    only appears in the anchor's text if it was literally present in
    evidence_text.
  - PHASE_LITERAL reuses the same module's _GRANT_PHASE_PATTERN
    discipline ("captures only the phase's own wording, verbatim, never a
    total-project budget or a phase number invented from context"),
    generalized to standalone "Phase <digits>" text (not requiring a
    "THIS GRANT FUNDS..." prefix, since evidence_text here is not
    USAspending-specific grant language).
  - DIRECTIONAL_RUNWAY_NAME is a new, narrow, closed-vocabulary pattern
    (exactly the four cardinal compass points named in the mission
    brief - North/South/East/West - followed by the literal word
    "Runway"). It is structurally incapable of producing a numbered
    designation: the two patterns never share a code path or a captured
    group.

BOUNDEDNESS (Slice 5E's own explicit constants, mirroring
app.selection.manual_range_selection.MAX_MANUAL_RANGE_CHARS's own
"explicit, conservative V1 bound" convention):
  - MAX_ANCHORS_PER_DIMENSION: only the first (by literal appearance
    offset in evidence_text - never a guessed "importance") anchor for a
    given dimension_hint survives; a second, later mention of the same
    dimension's literal (e.g. a second "Phase 2" appearing after an
    earlier "Phase 1") is dropped, not queried separately.
  - MAX_ANCHORS_TOTAL: a hard ceiling on the final anchor count,
    independent of how many distinct dimension_hint values V1 or any
    future version defines - future-proofing against an unbounded plan
    even if more anchor kinds/dimension_hints are added later.
  - Anchor text length bound (3-40 chars): a belt-and-suspenders guard;
    every pattern below is already a narrow, closed-vocabulary regex, so
    this rarely if ever actually rejects a real match.
  - An anchor whose text exactly matches (case-insensitively)
    airport_context.name/.iata_code/.icao_code, when airport_context is
    supplied, is dropped - it would be a redundant, useless search term
    (the airport identity is already embedded in every rendered query).

Never raises on ordinary text: an evidence_text with no supported literal
present simply produces an empty tuple - this is the expected, safe
fallback for generic/noisy evidence (Slice 5D Part 10), not an error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from app.discovery.query import SearchQuery
from app.services.discovery_temporal_followup import AirportSearchContext
from app.services.research_question_planning import (
    PlannedResearchQuery,
    ResearchClue,
    ResearchDimension,
    plan_research_questions,
    plan_research_search_queries,
)

__all__ = [
    "AnchorKind",
    "ResearchAnchor",
    "MAX_ANCHORS_TOTAL",
    "MAX_ANCHORS_PER_DIMENSION",
    "MAX_ANCHOR_DERIVED_QUERIES_PER_DIMENSION",
    "MIN_ANCHOR_TEXT_LEN",
    "MAX_ANCHOR_TEXT_LEN",
    "extract_literal_anchors",
    "plan_research_search_queries_with_anchors",
]


class AnchorKind(str, Enum):
    """Exactly the three V1 kinds (Slice 5E's own explicit instruction) -
    QUOTED_PHRASE is deliberately not a member here; see module docstring
    "SCOPE, DELIBERATELY NARROW"."""

    DIRECTIONAL_RUNWAY_NAME = "DIRECTIONAL_RUNWAY_NAME"
    NUMBERED_RUNWAY_DESIGNATION = "NUMBERED_RUNWAY_DESIGNATION"
    PHASE_LITERAL = "PHASE_LITERAL"


@dataclass(frozen=True)
class ResearchAnchor:
    """One literal token found in preserved evidence text. `text` is
    ALWAYS an exact substring of the `evidence_text` it was extracted
    from, original casing preserved verbatim - no normalized/inferred
    value field exists on this type, deliberately (module docstring's own
    "CORE INVARIANT"). `dimension_hint` is a purely structural routing
    label - see module docstring for the exact list of things it must
    never mean."""

    text: str
    kind: AnchorKind
    dimension_hint: ResearchDimension

    def __post_init__(self) -> None:
        if not self.text or not self.text.strip():
            raise ValueError("ResearchAnchor.text is required and cannot be empty")


# --- Boundedness constants (module docstring "BOUNDEDNESS") -----------------

MAX_ANCHORS_TOTAL = 5
MAX_ANCHORS_PER_DIMENSION = 1
MAX_ANCHOR_DERIVED_QUERIES_PER_DIMENSION = 1
MIN_ANCHOR_TEXT_LEN = 3
MAX_ANCHOR_TEXT_LEN = 40

# --- Patterns (module docstring "PATTERN PROVENANCE") -----------------------

# Exactly the four cardinal compass points named in the mission brief,
# followed by the literal word "Runway" - a closed, narrow vocabulary,
# never a general "<any capitalized word> Runway" match. Structurally
# distinct from _NUMBERED_RUNWAY_PATTERN below: these two patterns share
# no code path, no captured group, and can never produce each other's
# output.
_DIRECTIONAL_RUNWAY_PATTERN = re.compile(r"\b(?:North|South|East|West)\s+Runway\b", re.IGNORECASE)

# Adapted from app.acquisition.usaspending_grant_claims._RUNWAY_PATTERN
# (independent copy, not imported - see module docstring). RUNWAY
# <1-2 digits><optional L/R/C>, optionally followed by /<1-2
# digits><optional L/R/C>. The optional second half is captured only if
# literally present in the text - this pattern never synthesizes a
# reciprocal end that was not actually written.
_NUMBERED_RUNWAY_PATTERN = re.compile(r"\bRunway\s+\d{1,2}[LRC]?(?:/\d{1,2}[LRC]?)?\b", re.IGNORECASE)

# "Phase 1" / "phase 2" / etc. - captures only the phase's own literal
# wording (the word "Phase" plus its digits), never a total-project value
# or an invented phase number (module docstring's own PHASE_LITERAL
# provenance note).
_PHASE_PATTERN = re.compile(r"\bPhase\s+\d+\b", re.IGNORECASE)

_PATTERN_TABLE: "tuple[tuple[re.Pattern[str], AnchorKind, ResearchDimension], ...]" = (
    (_DIRECTIONAL_RUNWAY_PATTERN, AnchorKind.DIRECTIONAL_RUNWAY_NAME, ResearchDimension.RUNWAY_END),
    (_NUMBERED_RUNWAY_PATTERN, AnchorKind.NUMBERED_RUNWAY_DESIGNATION, ResearchDimension.RUNWAY_END),
    (_PHASE_PATTERN, AnchorKind.PHASE_LITERAL, ResearchDimension.PROJECT_PHASE),
)


class _RawMatch(tuple):
    """Internal only: (start_offset, text, kind, dimension_hint) - a plain
    tuple (not exported) used solely to sort/filter before constructing
    the real, public ResearchAnchor objects."""

    __slots__ = ()

    def __new__(cls, start: int, text: str, kind: AnchorKind, dimension_hint: ResearchDimension):
        return super().__new__(cls, (start, text, kind, dimension_hint))

    @property
    def start(self) -> int:
        return self[0]

    @property
    def text(self) -> str:
        return self[1]

    @property
    def kind(self) -> AnchorKind:
        return self[2]

    @property
    def dimension_hint(self) -> ResearchDimension:
        return self[3]


def _excluded_identity_terms(airport_context: "AirportSearchContext | None") -> frozenset[str]:
    if airport_context is None:
        return frozenset()
    terms = {airport_context.name}
    if airport_context.iata_code:
        terms.add(airport_context.iata_code)
    if airport_context.icao_code:
        terms.add(airport_context.icao_code)
    return frozenset(term.casefold() for term in terms if term)


def extract_literal_anchors(
    evidence_text: str, *, airport_context: "AirportSearchContext | None" = None,
) -> "tuple[ResearchAnchor, ...]":
    """Pure, deterministic, regex-only. The same `evidence_text` (and the
    same `airport_context`, if supplied) always produces the same ordered
    tuple of ResearchAnchor. Zero database, network, LLM, or randomization
    access anywhere in this function.

    Order of operations (all deterministic, no scoring/ranking anywhere):
      1. Run every pattern in _PATTERN_TABLE over the full text, collecting
         every match with its literal offset.
      2. Drop matches outside the [MIN_ANCHOR_TEXT_LEN, MAX_ANCHOR_TEXT_LEN]
         length bound.
      3. Drop matches that exactly equal (case-insensitively) the
         airport's own name/IATA/ICAO, when airport_context is supplied.
      4. Sort ALL surviving matches by first-appearance offset in
         evidence_text (never by kind, dimension, or any guessed
         importance).
      5. Enforce MAX_ANCHORS_PER_DIMENSION: keep only the first surviving
         match for each dimension_hint value.
      6. Enforce MAX_ANCHORS_TOTAL as a final hard cap.

    An evidence_text with no supported literal present returns an empty
    tuple - the safe, expected fallback for generic/noisy evidence, never
    an error.
    """
    if not evidence_text:
        return ()

    excluded = _excluded_identity_terms(airport_context)

    raw_matches: list[_RawMatch] = []
    for pattern, kind, dimension_hint in _PATTERN_TABLE:
        for match in pattern.finditer(evidence_text):
            text = match.group(0)
            if not (MIN_ANCHOR_TEXT_LEN <= len(text) <= MAX_ANCHOR_TEXT_LEN):
                continue
            if text.casefold() in excluded:
                continue
            raw_matches.append(_RawMatch(match.start(), text, kind, dimension_hint))

    raw_matches.sort(key=lambda m: m.start)

    seen_dimensions: set[ResearchDimension] = set()
    bounded: list[_RawMatch] = []
    for raw in raw_matches:
        if raw.dimension_hint in seen_dimensions:
            continue
        seen_dimensions.add(raw.dimension_hint)
        bounded.append(raw)
        if len(bounded) >= MAX_ANCHORS_TOTAL:
            break

    return tuple(ResearchAnchor(text=raw.text, kind=raw.kind, dimension_hint=raw.dimension_hint) for raw in bounded)


def _render_anchor_query(name: str, anchor_text: str) -> str:
    """Independent copy of app.services.research_question_planning's own
    _render_query() quoting convention for a multi-word airport name (not
    imported - that helper is private to its own module) - the anchor
    text itself is ALSO quoted, so the search engine is asked for the
    literal phrase (Slice 5E Part 6's own explicit instruction)."""
    rendered_name = f'"{name}"' if " " in name else name
    return f'{rendered_name} EMAS "{anchor_text}"'


def plan_research_search_queries_with_anchors(clue: ResearchClue) -> "tuple[PlannedResearchQuery, ...]":
    """Pure, deterministic, additive. Calls plan_research_search_queries()
    FIRST, verbatim and unmodified - its exact output is always a strict,
    unmodified prefix of this function's own return value (Slice 5E's own
    "baseline order must remain unchanged" requirement). Then extracts
    literal anchors from clue.evidence_text and appends at most
    MAX_ANCHOR_DERIVED_QUERIES_PER_DIMENSION additional PlannedResearchQuery
    per dimension, one per surviving anchor.

    An anchor is silently skipped (never raises) if:
      - its dimension_hint was not actually requested in
        clue.unresolved_dimensions (this function never searches for a
        dimension the caller did not ask about, exactly like the existing
        planner's own `requested = set(...)` discipline), or
      - its rendered query string would exactly duplicate one already in
        the plan (baseline or an earlier anchor), or
      - that dimension has already received
        MAX_ANCHOR_DERIVED_QUERIES_PER_DIMENSION anchor-derived queries.

    `question`/`reason` for each anchor-derived query are the SAME text as
    that dimension's own ResearchQuestion (via the existing, public,
    unmodified plan_research_questions()) - never separately invented
    text, matching PlannedResearchQuery's own existing contract exactly.

    Evidence with no supported literal anchor produces zero additional
    queries - the returned tuple is then identical to
    plan_research_search_queries(clue)'s own output (the safe fallback for
    generic/noisy evidence).
    """
    baseline = plan_research_search_queries(clue)
    questions_by_dimension = {q.dimension: q for q in plan_research_questions(clue)}
    anchors = extract_literal_anchors(clue.evidence_text, airport_context=clue.airport_context)

    name = clue.airport_context.name
    existing_rendered = {p.search_query.rendered for p in baseline}
    dimension_extra_count: dict[ResearchDimension, int] = {}

    extras: list[PlannedResearchQuery] = []
    for anchor in anchors:
        dimension = anchor.dimension_hint
        question = questions_by_dimension.get(dimension)
        if question is None:
            continue  # dimension not requested - never search beyond what was asked for
        if dimension_extra_count.get(dimension, 0) >= MAX_ANCHOR_DERIVED_QUERIES_PER_DIMENSION:
            continue

        rendered = _render_anchor_query(name, anchor.text)
        if rendered in existing_rendered:
            continue

        extras.append(
            PlannedResearchQuery(
                dimension=dimension,
                question=question.question,
                search_query=SearchQuery(
                    rendered=rendered,
                    template_id=f"research_anchor_{dimension.value.lower()}_{anchor.kind.value.lower()}",
                    identity_field="name",
                    identity_value=name,
                ),
                reason=question.reason,
            )
        )
        existing_rendered.add(rendered)
        dimension_extra_count[dimension] = dimension_extra_count.get(dimension, 0) + 1

    return baseline + tuple(extras)
