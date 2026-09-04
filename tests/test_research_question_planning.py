"""Tests for app/services/research_question_planning.py (RWI HQ "Discovery
Research Question Planner V1", Slice 1).

Every test is pure/offline - no database, no network, no LLM. Uses only
synthetic evidence text and explicit AirportSearchContext values, never a
real production record.
"""
from __future__ import annotations

import pytest

from app.discovery.query import SearchQuery
from app.services.discovery_temporal_followup import AirportSearchContext, AirportSearchContextError
from app.services.research_question_planning import (
    PlannedResearchQuery,
    ResearchClue,
    ResearchClueError,
    ResearchDimension,
    ResearchQuestion,
    plan_research_questions,
    plan_research_search_queries,
)

ALL_FIVE_DIMENSIONS = (
    ResearchDimension.RUNWAY_END,
    ResearchDimension.INSTALLATION_TYPE,
    ResearchDimension.PROJECT_PHASE,
    ResearchDimension.TIMING,
    ResearchDimension.SUPPLIER,
)


# --- SDF acceptance test (Part 9) -------------------------------------------


SDF_EVIDENCE_TEXT = (
    "Reconstruct Taxiway,Construct Engineered Material Arresting System Safety Area,"
    "Conduct Noise Compatibility Plan Study,Noise Mitigation Measures for Residences "
    "within 65-69 DNL"
)


def test_sdf_acceptance_generates_a_question_for_every_requested_dimension():
    context = AirportSearchContext(
        name="Louisville Muhammad Ali International Airport", iata_code="SDF", icao_code="KSDF",
    )
    clue = ResearchClue(
        evidence_text=SDF_EVIDENCE_TEXT, airport_context=context, unresolved_dimensions=ALL_FIVE_DIMENSIONS,
    )

    questions = plan_research_questions(clue)

    assert len(questions) == 5
    assert {q.dimension for q in questions} == set(ALL_FIVE_DIMENSIONS)
    for q in questions:
        assert isinstance(q, ResearchQuestion)
        assert isinstance(q.search_query, SearchQuery)
        assert q.search_query.identity_field == "name"
        assert q.search_query.identity_value == "Louisville Muhammad Ali International Airport"
        assert '"Louisville Muhammad Ali International Airport"' in q.search_query.rendered
        assert "EMAS" in q.search_query.rendered
        assert q.question  # non-empty, human-readable
        assert q.reason    # non-empty, deterministic


def test_sdf_acceptance_never_asserts_answers_only_asks_questions():
    """The planner must NOT assert/infer any specific answer - runway
    17L/35R, "north end"/"East Runway", new installation, replacement,
    a supplier name, an exact schedule, or a project value - anywhere in
    its output. Only the neutral question/reason text and a bounded
    verbatim excerpt of the evidence itself may appear."""
    context = AirportSearchContext(name="Louisville Muhammad Ali International Airport", iata_code="SDF", icao_code="KSDF")
    clue = ResearchClue(evidence_text=SDF_EVIDENCE_TEXT, airport_context=context, unresolved_dimensions=ALL_FIVE_DIMENSIONS)

    questions = plan_research_questions(clue)

    # "replacement"/"new installation" are legitimate QUESTION vocabulary
    # (the planner asks which one applies) - never forbidden on their own;
    # what's forbidden is a DECLARATIVE assertion that one of them is true.
    forbidden_answer_strings = (
        "17L", "35R", "17/35", "north end", "east runway",
        "is a replacement", "is a new installation",
        "runway safe", "$", "million", "17L/35R",
    )
    for q in questions:
        haystack = f"{q.question} {q.reason} {q.search_query.rendered}".lower()
        for forbidden in forbidden_answer_strings:
            assert forbidden.lower() not in haystack, f"planner output must never assert {forbidden!r}: {haystack!r}"


def test_sdf_reason_contains_bounded_verbatim_evidence_excerpt():
    context = AirportSearchContext(name="Louisville Muhammad Ali International Airport", iata_code="SDF", icao_code="KSDF")
    clue = ResearchClue(
        evidence_text=SDF_EVIDENCE_TEXT, airport_context=context,
        unresolved_dimensions=(ResearchDimension.RUNWAY_END,),
    )
    questions = plan_research_questions(clue)
    assert len(questions) == 1
    assert "Engineered Material Arresting System" in questions[0].reason
    assert "runway/end" in questions[0].reason


# --- MHT safety test (Part 10) -----------------------------------------------


def test_mht_runway_end_question_does_not_resolve_the_conflict():
    """MHT has real, already-flagged conflicting evidence (NASR RWY_END_ID
    '06' vs. Signal 2's own reciprocal-naming implication of '24'). The
    planner must produce a RUNWAY_END question without ever choosing or
    even mentioning either runway number - it has no access to that
    conflict at all (it never reads Signal/SourceAssertion state itself;
    the caller decides what's unresolved)."""
    context = AirportSearchContext(name="Manchester-Boston Regional Airport", iata_code="MHT", icao_code="KMHT")
    clue = ResearchClue(
        evidence_text="Reconstruct Engineered Material Arresting System Safety Area",
        airport_context=context,
        unresolved_dimensions=(ResearchDimension.RUNWAY_END,),
    )

    questions = plan_research_questions(clue)

    assert len(questions) == 1
    q = questions[0]
    assert q.dimension == ResearchDimension.RUNWAY_END
    haystack = f"{q.question} {q.reason} {q.search_query.rendered}"
    assert "06" not in haystack
    assert "24" not in haystack
    assert "conflict" not in haystack.lower()  # the planner doesn't even know one exists


# --- BGM safety test (Part 11) -----------------------------------------------


def test_bgm_produces_questions_independent_of_existing_signal_state():
    """BGM has six real existing Signals. The planner must produce
    ordinary research questions from a fresh clue with no awareness of,
    dependency on, or reference to any of them - it never imports
    reconciliation/disposition/FH-D4 logic (see the architectural-safety
    test) and never receives Signal state as input at all in this
    module's own contract."""
    context = AirportSearchContext(name="Greater Binghamton Airport", iata_code="BGM", icao_code="KBGM")
    clue = ResearchClue(
        evidence_text="Reconstruct Engineered Material Arresting System Safety Area",
        airport_context=context,
        unresolved_dimensions=(ResearchDimension.INSTALLATION_TYPE, ResearchDimension.SUPPLIER),
    )

    questions = plan_research_questions(clue)

    assert len(questions) == 2
    assert {q.dimension for q in questions} == {ResearchDimension.INSTALLATION_TYPE, ResearchDimension.SUPPLIER}
    for q in questions:
        assert "signal" not in q.question.lower()
        assert "signal" not in q.reason.lower()


# --- Determinism tests (Part 12) ---------------------------------------------


def test_same_clue_produces_the_exact_same_ordered_output():
    context = AirportSearchContext(name="Test Airport", iata_code="TST", icao_code="KTST")
    clue = ResearchClue(evidence_text="Some evidence text.", airport_context=context, unresolved_dimensions=ALL_FIVE_DIMENSIONS)

    first = plan_research_questions(clue)
    second = plan_research_questions(clue)

    assert first == second


def test_question_order_is_stable_regardless_of_caller_supply_order():
    context = AirportSearchContext(name="Test Airport")
    clue_a = ResearchClue(
        evidence_text="x", airport_context=context,
        unresolved_dimensions=(ResearchDimension.SUPPLIER, ResearchDimension.RUNWAY_END, ResearchDimension.TIMING),
    )
    clue_b = ResearchClue(
        evidence_text="x", airport_context=context,
        unresolved_dimensions=(ResearchDimension.TIMING, ResearchDimension.RUNWAY_END, ResearchDimension.SUPPLIER),
    )

    order_a = [q.dimension for q in plan_research_questions(clue_a)]
    order_b = [q.dimension for q in plan_research_questions(clue_b)]

    assert order_a == order_b
    # canonical enum-declaration order
    assert order_a == [ResearchDimension.RUNWAY_END, ResearchDimension.TIMING, ResearchDimension.SUPPLIER]


def test_no_duplicate_rendered_queries():
    context = AirportSearchContext(name="Test Airport")
    clue = ResearchClue(evidence_text="x", airport_context=context, unresolved_dimensions=ALL_FIVE_DIMENSIONS)
    questions = plan_research_questions(clue)
    rendered = [q.search_query.rendered for q in questions]
    assert len(rendered) == len(set(rendered))


def test_only_requested_dimensions_generate_questions():
    context = AirportSearchContext(name="Test Airport")
    clue = ResearchClue(
        evidence_text="x", airport_context=context,
        unresolved_dimensions=(ResearchDimension.SUPPLIER,),
    )
    questions = plan_research_questions(clue)
    assert [q.dimension for q in questions] == [ResearchDimension.SUPPLIER]


def test_duplicate_requested_dimensions_produce_exactly_one_question_each():
    context = AirportSearchContext(name="Test Airport")
    clue = ResearchClue(
        evidence_text="x", airport_context=context,
        unresolved_dimensions=(ResearchDimension.SUPPLIER, ResearchDimension.SUPPLIER),
    )
    questions = plan_research_questions(clue)
    assert len(questions) == 1


def test_empty_unresolved_dimensions_returns_empty_result_cleanly():
    context = AirportSearchContext(name="Test Airport")
    clue = ResearchClue(evidence_text="x", airport_context=context, unresolved_dimensions=())
    questions = plan_research_questions(clue)
    assert questions == ()


def test_empty_evidence_text_fails_closed():
    context = AirportSearchContext(name="Test Airport")
    with pytest.raises(ResearchClueError):
        ResearchClue(evidence_text="", airport_context=context, unresolved_dimensions=(ResearchDimension.SUPPLIER,))
    with pytest.raises(ResearchClueError):
        ResearchClue(evidence_text="   ", airport_context=context, unresolved_dimensions=())


def test_malformed_dimension_in_unresolved_dimensions_fails_closed():
    context = AirportSearchContext(name="Test Airport")
    with pytest.raises(ResearchClueError):
        ResearchClue(evidence_text="x", airport_context=context, unresolved_dimensions=("SUPPLIER",))  # plain str, not enum


def test_empty_airport_search_context_name_fails_closed_via_existing_type():
    """The planner relies entirely on AirportSearchContext's own existing
    fail-closed behavior (Missions #17B) - never reimplements it."""
    with pytest.raises(AirportSearchContextError):
        AirportSearchContext(name="")


# --- Query text uses only the name field (no SDF-specific hardcoding) -------


def test_query_generation_works_for_any_airport_name_single_word():
    context = AirportSearchContext(name="Heathrow")
    clue = ResearchClue(evidence_text="x", airport_context=context, unresolved_dimensions=(ResearchDimension.TIMING,))
    q = plan_research_questions(clue)[0]
    assert q.search_query.rendered == "Heathrow EMAS schedule"


def test_query_generation_quotes_multi_word_airport_names():
    context = AirportSearchContext(name="London City Airport")
    clue = ResearchClue(evidence_text="x", airport_context=context, unresolved_dimensions=(ResearchDimension.SUPPLIER,))
    q = plan_research_questions(clue)[0]
    assert q.search_query.rendered == '"London City Airport" EMAS supplier'


# --- Slice 3: plan_research_search_queries() query-quality hardening -------


def test_installation_type_search_plan_is_no_longer_solely_replacement():
    """Part 10/4: the live SDF dry run found a single 'replacement' term
    biases recall away from new-installation evidence - the widened plan
    must not consist solely of that one term any more."""
    context = AirportSearchContext(name="Test Airport")
    clue = ResearchClue(evidence_text="x", airport_context=context, unresolved_dimensions=(ResearchDimension.INSTALLATION_TYPE,))
    planned = plan_research_search_queries(clue)
    rendered = [p.search_query.rendered for p in planned]
    assert rendered != ["Test Airport EMAS replacement"]
    assert len(rendered) >= 2
    assert len(set(rendered)) == len(rendered)  # no duplicate rendered queries


def test_project_phase_search_plan_is_no_longer_solely_construction():
    context = AirportSearchContext(name="Test Airport")
    clue = ResearchClue(evidence_text="x", airport_context=context, unresolved_dimensions=(ResearchDimension.PROJECT_PHASE,))
    planned = plan_research_search_queries(clue)
    rendered = [p.search_query.rendered for p in planned]
    assert rendered != ["Test Airport EMAS construction"]
    assert len(rendered) >= 2
    assert len(set(rendered)) == len(rendered)


def test_installation_type_plan_searches_neutrally_across_new_and_replacement():
    context = AirportSearchContext(name="Test Airport")
    clue = ResearchClue(evidence_text="x", airport_context=context, unresolved_dimensions=(ResearchDimension.INSTALLATION_TYPE,))
    rendered = " ".join(p.search_query.rendered.lower() for p in plan_research_search_queries(clue))
    assert "new" in rendered
    assert "replacement" in rendered or "reconstruction" in rendered


def test_project_phase_plan_emphasizes_emas_lifecycle_language():
    context = AirportSearchContext(name="Test Airport")
    clue = ResearchClue(evidence_text="x", airport_context=context, unresolved_dimensions=(ResearchDimension.PROJECT_PHASE,))
    rendered = " ".join(p.search_query.rendered.lower() for p in plan_research_search_queries(clue))
    assert any(term in rendered for term in ("design", "bid"))
    assert any(term in rendered for term in ("installation", "completion"))


def test_single_query_dimensions_unaffected_by_widening():
    context = AirportSearchContext(name="Test Airport")
    clue = ResearchClue(
        evidence_text="x", airport_context=context,
        unresolved_dimensions=(ResearchDimension.RUNWAY_END, ResearchDimension.TIMING, ResearchDimension.SUPPLIER),
    )
    planned = plan_research_search_queries(clue)
    by_dimension: dict = {}
    for p in planned:
        by_dimension.setdefault(p.dimension, []).append(p)
    assert len(by_dimension[ResearchDimension.RUNWAY_END]) == 1
    assert len(by_dimension[ResearchDimension.TIMING]) == 1
    assert len(by_dimension[ResearchDimension.SUPPLIER]) == 1


def test_plan_research_search_queries_matches_first_entry_of_research_question():
    """ResearchQuestion.search_query and plan_research_search_queries()'s
    own first entry for that dimension must never independently disagree -
    both are derived from the exact same _QUERY_CONCEPTS table."""
    context = AirportSearchContext(name="Test Airport")
    clue = ResearchClue(
        evidence_text="x", airport_context=context,
        unresolved_dimensions=(ResearchDimension.INSTALLATION_TYPE, ResearchDimension.RUNWAY_END),
    )
    questions = {q.dimension: q for q in plan_research_questions(clue)}
    planned = plan_research_search_queries(clue)
    for dimension, question in questions.items():
        first_planned = next(p for p in planned if p.dimension == dimension)
        assert first_planned.search_query.rendered == question.search_query.rendered


def test_plan_research_search_queries_is_deterministic_and_ordered():
    context = AirportSearchContext(name="Test Airport")
    clue = ResearchClue(evidence_text="x", airport_context=context, unresolved_dimensions=(
        ResearchDimension.SUPPLIER, ResearchDimension.INSTALLATION_TYPE, ResearchDimension.RUNWAY_END,
    ))
    first = plan_research_search_queries(clue)
    second = plan_research_search_queries(clue)
    assert first == second

    # canonical dimension order (RUNWAY_END, INSTALLATION_TYPE, SUPPLIER per
    # enum declaration order), never the caller's own supply order, and
    # INSTALLATION_TYPE's own two entries stay adjacent/in-order.
    assert [p.dimension for p in first] == [
        ResearchDimension.RUNWAY_END,
        ResearchDimension.INSTALLATION_TYPE, ResearchDimension.INSTALLATION_TYPE,
        ResearchDimension.SUPPLIER,
    ]


def test_plan_research_search_queries_empty_dimensions_returns_empty():
    context = AirportSearchContext(name="Test Airport")
    clue = ResearchClue(evidence_text="x", airport_context=context, unresolved_dimensions=())
    assert plan_research_search_queries(clue) == ()


def test_planned_research_query_is_the_documented_shape():
    context = AirportSearchContext(name="Test Airport")
    clue = ResearchClue(evidence_text="x", airport_context=context, unresolved_dimensions=(ResearchDimension.SUPPLIER,))
    planned = plan_research_search_queries(clue)
    assert len(planned) == 1
    p = planned[0]
    assert isinstance(p, PlannedResearchQuery)
    assert isinstance(p.search_query, SearchQuery)
    assert p.dimension == ResearchDimension.SUPPLIER
    assert p.question and p.reason
