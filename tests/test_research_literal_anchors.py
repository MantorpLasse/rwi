"""RWI HQ "Discovery Research Loop V1 - Slice 5E" - offline tests for
app.services.research_literal_anchors. Fake/in-memory inputs only - no
production DB, no network, no live search anywhere in this file."""

from __future__ import annotations

from app.services.discovery_temporal_followup import AirportSearchContext
from app.services.research_literal_anchors import (
    MAX_ANCHOR_DERIVED_QUERIES_PER_DIMENSION,
    MAX_ANCHORS_PER_DIMENSION,
    MAX_ANCHORS_TOTAL,
    AnchorKind,
    extract_literal_anchors,
    plan_research_search_queries_with_anchors,
)
from app.services.research_question_planning import (
    ResearchClue,
    ResearchDimension,
    plan_research_search_queries,
)

_CTX = AirportSearchContext(name="Louisville Muhammad Ali International Airport", iata_code="SDF", icao_code="KSDF")

# The exact, real, preserved SourceAssertion 258 text (Airport World candidate).
SA258_TEXT = (
    "On the airfield, reconstruction is expected on Taxiways B and D, phase 1 of the East "
    "Runway’s Engineered Materials Arresting System (EMAS) will be installed and electrical "
    "work will continue including the completion of the SDF MicroGrid."
)

# The exact, real, existing SourceAssertion 257 fixture text (FAA AIP bundled
# grant language) - matches SDF_TEXT in tests/test_research_airport_clue.py.
SA257_TEXT = (
    "Reconstruct Taxiway,Construct Engineered Material Arresting System Safety Area,"
    "Conduct Noise Compatibility Plan Study,Noise Mitigation Measures for Residences "
    "within 65-69 DNL"
)

_ALL_DIMENSIONS = tuple(ResearchDimension)


# --- extract_literal_anchors() -----------------------------------------------


def test_deterministic_extraction():
    first = extract_literal_anchors(SA258_TEXT, airport_context=_CTX)
    second = extract_literal_anchors(SA258_TEXT, airport_context=_CTX)
    assert first == second


def test_anchor_text_is_exact_substring_with_original_casing():
    anchors = extract_literal_anchors(SA258_TEXT, airport_context=_CTX)
    for anchor in anchors:
        assert anchor.text in SA258_TEXT


def test_sa258_extracts_east_runway_and_phase_1():
    anchors = extract_literal_anchors(SA258_TEXT, airport_context=_CTX)
    by_kind = {a.kind: a for a in anchors}
    assert len(anchors) == 2
    assert by_kind[AnchorKind.DIRECTIONAL_RUNWAY_NAME].text == "East Runway"
    assert by_kind[AnchorKind.DIRECTIONAL_RUNWAY_NAME].dimension_hint == ResearchDimension.RUNWAY_END
    assert by_kind[AnchorKind.PHASE_LITERAL].text == "phase 1"
    assert by_kind[AnchorKind.PHASE_LITERAL].dimension_hint == ResearchDimension.PROJECT_PHASE
    assert AnchorKind.NUMBERED_RUNWAY_DESIGNATION not in by_kind


def test_sa257_extracts_zero_anchors():
    anchors = extract_literal_anchors(SA257_TEXT, airport_context=_CTX)
    assert anchors == ()


def test_directional_runway_never_invents_numeric_runway():
    text = "Work will occur on the East Runway this year."
    anchors = extract_literal_anchors(text, airport_context=_CTX)
    kinds = {a.kind for a in anchors}
    assert AnchorKind.DIRECTIONAL_RUNWAY_NAME in kinds
    assert AnchorKind.NUMBERED_RUNWAY_DESIGNATION not in kinds
    assert not any("17" in a.text or "35" in a.text for a in anchors)


def test_numeric_runway_preserves_only_literal_designation():
    text = "The project affects Runway 17R and does not touch its reciprocal end."
    anchors = extract_literal_anchors(text, airport_context=_CTX)
    numeric = [a for a in anchors if a.kind == AnchorKind.NUMBERED_RUNWAY_DESIGNATION]
    assert len(numeric) == 1
    assert numeric[0].text == "Runway 17R"
    assert "35L" not in numeric[0].text  # never invents a reciprocal end not literally present


def test_numeric_runway_preserves_full_pair_when_literally_present():
    text = "Installation will occur on Runway 17R/35L."
    anchors = extract_literal_anchors(text, airport_context=_CTX)
    numeric = [a for a in anchors if a.kind == AnchorKind.NUMBERED_RUNWAY_DESIGNATION]
    assert numeric[0].text == "Runway 17R/35L"


def test_phase_preserves_literal_number_and_original_casing():
    text = "Design work for phase 2 has already begun."
    anchors = extract_literal_anchors(text, airport_context=_CTX)
    phase = [a for a in anchors if a.kind == AnchorKind.PHASE_LITERAL]
    assert len(phase) == 1
    assert phase[0].text == "phase 2"  # original lowercase 'p' preserved verbatim


def test_appearance_order_is_deterministic_not_by_kind_or_importance():
    # "Phase 1" appears BEFORE "East Runway" in the literal text - order must follow that.
    anchors = extract_literal_anchors(SA258_TEXT, airport_context=_CTX)
    assert [a.kind for a in anchors] == [AnchorKind.PHASE_LITERAL, AnchorKind.DIRECTIONAL_RUNWAY_NAME]


def test_max_one_anchor_per_dimension():
    text = "Phase 1 work precedes Phase 2 work on the East Runway and also the West Runway."
    anchors = extract_literal_anchors(text, airport_context=_CTX)
    by_dimension = {}
    for a in anchors:
        by_dimension.setdefault(a.dimension_hint, []).append(a)
    for dimension, group in by_dimension.items():
        assert len(group) <= MAX_ANCHORS_PER_DIMENSION
    # first-appearance wins: "Phase 1" (not "Phase 2"), "East Runway" (not "West Runway")
    texts = {a.text for a in anchors}
    assert "Phase 1" in texts
    assert "Phase 2" not in texts
    assert "East Runway" in texts
    assert "West Runway" not in texts


def test_bounded_under_pathological_input():
    text = (
        "Phase 1 and Phase 2 and Phase 3 work will occur on the East Runway, the West Runway, "
        "the North Runway, the South Runway, Runway 17R, Runway 18L, and Runway 19/1."
    )
    anchors = extract_literal_anchors(text, airport_context=_CTX)
    assert len(anchors) <= MAX_ANCHORS_TOTAL


def test_noisy_prose_extracts_zero_anchors():
    text = (
        "The airport authority discussed budget priorities, terminal concessions, parking "
        "structures, and customer service improvements during the quarterly board meeting."
    )
    assert extract_literal_anchors(text, airport_context=_CTX) == ()


def test_empty_evidence_text_returns_empty_tuple():
    assert extract_literal_anchors("", airport_context=_CTX) == ()


def test_airport_name_and_codes_excluded_when_matched():
    ctx = AirportSearchContext(name="East Runway", iata_code="EAR", icao_code="KEAR")
    text = "The East Runway project has no further detail."
    anchors = extract_literal_anchors(text, airport_context=ctx)
    assert not any(a.text.casefold() == "east runway" for a in anchors)


def test_no_airport_context_supplied_still_extracts():
    anchors = extract_literal_anchors(SA258_TEXT, airport_context=None)
    assert len(anchors) == 2


# --- plan_research_search_queries_with_anchors() ----------------------------


def test_baseline_planner_output_unchanged_by_this_module():
    clue = ResearchClue(evidence_text=SA258_TEXT, airport_context=_CTX, unresolved_dimensions=_ALL_DIMENSIONS)
    before = plan_research_search_queries(clue)
    plan_research_search_queries_with_anchors(clue)  # exercised - must not mutate anything
    after = plan_research_search_queries(clue)
    assert before == after
    assert len(before) == 7


def test_sa258_anchor_aware_plan_is_strict_superset_of_baseline():
    clue = ResearchClue(evidence_text=SA258_TEXT, airport_context=_CTX, unresolved_dimensions=_ALL_DIMENSIONS)
    baseline = plan_research_search_queries(clue)
    full = plan_research_search_queries_with_anchors(clue)
    assert full[: len(baseline)] == baseline
    assert len(full) == 9
    extra = full[len(baseline):]
    rendered = {p.search_query.rendered for p in extra}
    assert rendered == {
        f'"{_CTX.name}" EMAS "East Runway"',
        f'"{_CTX.name}" EMAS "phase 1"',
    }


def test_sa257_anchor_aware_plan_equals_baseline_exactly():
    clue = ResearchClue(evidence_text=SA257_TEXT, airport_context=_CTX, unresolved_dimensions=_ALL_DIMENSIONS)
    baseline = plan_research_search_queries(clue)
    full = plan_research_search_queries_with_anchors(clue)
    assert full == baseline
    assert len(full) == 7


def test_no_sdf_specific_code_generic_airport_also_diverges():
    """The central Slice 5E acceptance criterion, restated generically: any
    airport whose evidence text contains a supported literal produces a
    richer plan than one whose evidence text does not - proven here with a
    DIFFERENT airport/evidence pair, never SDF."""
    ctx = AirportSearchContext(name="Example Regional Airport", iata_code="EXR", icao_code="KEXR")
    rich_clue = ResearchClue(
        evidence_text="Phase 3 construction will affect the North Runway.",
        airport_context=ctx, unresolved_dimensions=_ALL_DIMENSIONS,
    )
    plain_clue = ResearchClue(
        evidence_text="General capital improvements are planned across the terminal.",
        airport_context=ctx, unresolved_dimensions=_ALL_DIMENSIONS,
    )
    assert len(plan_research_search_queries_with_anchors(rich_clue)) == 9
    assert len(plan_research_search_queries_with_anchors(plain_clue)) == 7


def test_duplicate_rendered_queries_suppressed():
    ctx = AirportSearchContext(name="Example Airport")
    clue = ResearchClue(
        evidence_text="Phase 1 work is planned; Phase 1 remains the current stage.",
        airport_context=ctx, unresolved_dimensions=(ResearchDimension.PROJECT_PHASE,),
    )
    full = plan_research_search_queries_with_anchors(clue)
    rendered = [p.search_query.rendered for p in full]
    assert len(rendered) == len(set(rendered))


def test_anchor_never_added_for_unrequested_dimension():
    clue = ResearchClue(
        evidence_text=SA258_TEXT, airport_context=_CTX,
        unresolved_dimensions=(ResearchDimension.SUPPLIER,),  # RUNWAY_END/PROJECT_PHASE NOT requested
    )
    full = plan_research_search_queries_with_anchors(clue)
    dimensions = {p.dimension for p in full}
    assert ResearchDimension.RUNWAY_END not in dimensions
    assert ResearchDimension.PROJECT_PHASE not in dimensions


def test_generic_evidence_falls_back_to_current_planner_behavior():
    clue = ResearchClue(
        evidence_text="Nothing notable is described in this excerpt.",
        airport_context=_CTX, unresolved_dimensions=_ALL_DIMENSIONS,
    )
    assert plan_research_search_queries_with_anchors(clue) == plan_research_search_queries(clue)


def test_question_and_reason_text_match_existing_research_question():
    from app.services.research_question_planning import plan_research_questions

    clue = ResearchClue(evidence_text=SA258_TEXT, airport_context=_CTX, unresolved_dimensions=_ALL_DIMENSIONS)
    questions = {q.dimension: q for q in plan_research_questions(clue)}
    full = plan_research_search_queries_with_anchors(clue)
    baseline = plan_research_search_queries(clue)
    for extra in full[len(baseline):]:
        assert extra.question == questions[extra.dimension].question
        assert extra.reason == questions[extra.dimension].reason


def test_dimension_search_status_vocabulary_unaffected():
    """This module is not wired into app.services.research_loop in Slice
    5E (mission's own explicit "does NOT wire the new planner into the
    live research CLI" instruction) - proven here by confirming
    DimensionSearchStatus's own vocabulary is exactly the pre-existing
    three members, completely unchanged by this module's mere existence,
    and that run_research_loop (unmodified) still only ever executes the
    original plan_research_search_queries(), never this module's anchor-
    aware variant."""
    import inspect

    from app.services.research_loop import DimensionSearchStatus, run_research_loop

    assert {m.value for m in DimensionSearchStatus} == {"CANDIDATES_FOUND", "NO_CANDIDATES_FOUND", "SEARCH_FAILED"}
    source = inspect.getsource(run_research_loop)
    assert "plan_research_search_queries_with_anchors" not in source
    assert "research_literal_anchors" not in inspect.getsource(
        __import__("app.services.research_loop", fromlist=["research_loop"])
    )
