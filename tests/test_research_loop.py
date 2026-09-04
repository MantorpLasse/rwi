"""Tests for app/services/research_loop.py (RWI HQ "Discovery Research
Loop V1", Slice 2/3). Fake SearchProvider only - no network, no database.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.discovery.query import SearchQuery
from app.discovery.search import SearchOutcome, SearchOutcomeStatus, SearchResult
from app.discovery.triage import PriorityBand
from app.services.discovery_temporal_followup import AirportSearchContext
from app.services.research_loop import (
    DimensionSearchStatus,
    ResearchLoopReport,
    compute_dimension_search_status,
    run_research_loop,
)
from app.services.research_question_planning import (
    ResearchClue,
    ResearchDimension,
    plan_research_questions,
    plan_research_search_queries,
)

_NOW = datetime(2026, 9, 4, tzinfo=timezone.utc)

ALL_FIVE_DIMENSIONS = (
    ResearchDimension.RUNWAY_END,
    ResearchDimension.INSTALLATION_TYPE,
    ResearchDimension.PROJECT_PHASE,
    ResearchDimension.TIMING,
    ResearchDimension.SUPPLIER,
)

# RUNWAY_END=1 + INSTALLATION_TYPE=2 + PROJECT_PHASE=2 + TIMING=1 + SUPPLIER=1
TOTAL_PLANNED_QUERIES_FOR_ALL_FIVE = 7

SDF_EVIDENCE_TEXT = (
    "Reconstruct Taxiway,Construct Engineered Material Arresting System Safety Area,"
    "Conduct Noise Compatibility Plan Study,Noise Mitigation Measures for Residences "
    "within 65-69 DNL"
)


class _FakeProvider:
    """Mirrors tests/test_review_temporal_followup.py's own _FakeProvider
    convention exactly - canned outcomes keyed by exact rendered query
    text, NO_RESULTS for anything not explicitly canned."""

    name = "fake"

    def __init__(self, canned: "dict[str, SearchOutcome]"):
        self._canned = canned

    def search(self, query: SearchQuery) -> SearchOutcome:
        return self._canned.get(query.rendered, SearchOutcome(query=query, status=SearchOutcomeStatus.NO_RESULTS))


def _result(query: SearchQuery, url: str, *, title: str = "A result", snippet: str = "") -> SearchResult:
    return SearchResult(query=query, rank=1, title=title, url=url, snippet=snippet, discovered_at=_NOW, provider="fake")


def _sdf_clue(dimensions=ALL_FIVE_DIMENSIONS) -> ResearchClue:
    context = AirportSearchContext(
        name="Louisville Muhammad Ali International Airport", iata_code="SDF", icao_code="KSDF",
    )
    return ResearchClue(evidence_text=SDF_EVIDENCE_TEXT, airport_context=context, unresolved_dimensions=dimensions)


# --- Plan-only mode (no provider) -------------------------------------------


def test_no_provider_returns_plan_only_zero_network():
    clue = _sdf_clue()
    report = run_research_loop(clue, provider=None)
    assert isinstance(report, ResearchLoopReport)
    assert len(report.questions) == 5
    assert len(report.planned_queries) == TOTAL_PLANNED_QUERIES_FOR_ALL_FIVE
    assert report.query_outcomes == ()
    assert report.triaged_candidates == ()


# --- SDF offline acceptance test (Part 10) -----------------------------------


def test_sdf_offline_acceptance_executes_every_planned_query_unchanged():
    clue = _sdf_clue()
    planned = plan_research_search_queries(clue)
    assert len(planned) == TOTAL_PLANNED_QUERIES_FOR_ALL_FIVE

    canned = {
        planned[0].search_query.rendered: SearchOutcome(
            query=planned[0].search_query, status=SearchOutcomeStatus.OK,
            results=(_result(planned[0].search_query, "https://faa.gov/sdf-runway-doc", title="SDF Runway 11/29 EMAS document"),),
        ),
    }
    provider = _FakeProvider(canned)

    report = run_research_loop(clue, provider=provider)

    assert len(report.query_outcomes) == TOTAL_PLANNED_QUERIES_FOR_ALL_FIVE
    for qo in report.query_outcomes:
        # each executed SearchQuery is passed unchanged to the provider
        assert qo.outcome.query == qo.planned_query.search_query
    assert {qo.planned_query.dimension for qo in report.query_outcomes} == set(ALL_FIVE_DIMENSIONS)


def test_sdf_offline_dedup_across_planned_queries_and_triage_runs_once():
    """A URL surfaced by planned queries across TWO different dimensions
    must appear exactly once in triaged_candidates, with BOTH dimensions
    recovered - and, within PROJECT_PHASE's own two concept queries, both
    contribute to the same found_by list."""
    context = AirportSearchContext(name="Louisville Muhammad Ali International Airport", iata_code="SDF", icao_code="KSDF")
    clue = ResearchClue(
        evidence_text=SDF_EVIDENCE_TEXT, airport_context=context,
        unresolved_dimensions=(ResearchDimension.RUNWAY_END, ResearchDimension.PROJECT_PHASE),
    )
    planned = plan_research_search_queries(clue)
    assert len(planned) == 3  # RUNWAY_END(1) + PROJECT_PHASE(2)
    shared_url = "https://faa.gov/sdf-emas-project"

    canned = {
        p.search_query.rendered: SearchOutcome(
            query=p.search_query, status=SearchOutcomeStatus.OK,
            results=(_result(p.search_query, shared_url, title="SDF EMAS Runway Project - FAA"),),
        )
        for p in planned
    }
    provider = _FakeProvider(canned)

    report = run_research_loop(clue, provider=provider)

    assert len(report.triaged_candidates) == 1  # deduplicated to one URL
    candidate = report.triaged_candidates[0]
    assert candidate.triaged.deduped.result.url == shared_url
    assert set(candidate.dimensions) == {ResearchDimension.RUNWAY_END, ResearchDimension.PROJECT_PHASE}
    assert len(candidate.triaged.deduped.found_by) == 3


def test_sdf_offline_no_answer_is_ever_inferred():
    """The loop's own output (questions/reasons/rendered queries - never
    the fake provider's own synthetic titles, which are test fixtures
    representing what a REAL provider might someday return, not planner
    output) must never assert runway 17L/35R, new installation, supplier,
    schedule, or project value."""
    clue = _sdf_clue()
    report = run_research_loop(clue, provider=None)  # plan-only: no results to accidentally leak an answer from
    haystack = " ".join(
        f"{q.question} {q.reason} {q.search_query.rendered}" for q in report.questions
    ).lower()
    haystack += " " + " ".join(
        f"{p.question} {p.reason} {p.search_query.rendered}" for p in report.planned_queries
    ).lower()
    for forbidden in ("17l", "35r", "17/35", "north end", "east runway", "is a replacement", "is a new installation", "$", "million"):
        assert forbidden not in haystack


# --- Query-plan hardening / bias observability (Part 4) ---------------------


def test_report_preserves_the_widened_installation_type_and_project_phase_plan():
    clue = _sdf_clue(dimensions=(ResearchDimension.INSTALLATION_TYPE, ResearchDimension.PROJECT_PHASE))
    report = run_research_loop(clue, provider=None)
    assert len(report.planned_queries) == 4  # 2 + 2

    rendered_by_dimension: dict[ResearchDimension, list[str]] = {}
    for p in report.planned_queries:
        rendered_by_dimension.setdefault(p.dimension, []).append(p.search_query.rendered)

    installation_rendered = rendered_by_dimension[ResearchDimension.INSTALLATION_TYPE]
    project_phase_rendered = rendered_by_dimension[ResearchDimension.PROJECT_PHASE]

    # never solely "EMAS replacement" / "EMAS construction" any more
    assert installation_rendered != ['"Louisville Muhammad Ali International Airport" EMAS replacement']
    assert project_phase_rendered != ['"Louisville Muhammad Ali International Airport" EMAS construction']
    assert len(installation_rendered) == 2
    assert len(project_phase_rendered) == 2
    assert len(set(installation_rendered)) == 2  # both distinct
    assert len(set(project_phase_rendered)) == 2


# --- MHT safety test (Part 13) -----------------------------------------------


def test_mht_runway_end_results_reported_never_resolved():
    context = AirportSearchContext(name="Manchester-Boston Regional Airport", iata_code="MHT", icao_code="KMHT")
    clue = ResearchClue(
        evidence_text="Reconstruct Engineered Material Arresting System Safety Area",
        airport_context=context, unresolved_dimensions=(ResearchDimension.RUNWAY_END,),
    )
    planned = plan_research_search_queries(clue)
    query = planned[0].search_query

    provider = _FakeProvider({
        query.rendered: SearchOutcome(
            query=query, status=SearchOutcomeStatus.OK,
            results=(
                _result(query, "https://example.com/mht-06", title="MHT Runway 06 EMAS project"),
                _result(query, "https://example.com/mht-24", title="MHT Runway 24 EMAS reconstruction"),
            ),
        )
    })

    report = run_research_loop(clue, provider=provider)

    # both results are reported, verbatim, as candidates - never merged,
    # never resolved, never chosen between.
    urls = {c.triaged.deduped.result.url for c in report.triaged_candidates}
    assert urls == {"https://example.com/mht-06", "https://example.com/mht-24"}

    # dimension status may legitimately become CANDIDATES_FOUND ...
    status = compute_dimension_search_status(ResearchDimension.RUNWAY_END, report)
    assert status == DimensionSearchStatus.CANDIDATES_FOUND
    # ... but that NEVER means the research question was answered, and the
    # loop's own output never picks a side.
    haystack = f"{planned[0].question} {planned[0].reason} {query.rendered}"
    assert "06 is correct" not in haystack
    assert "24 is correct" not in haystack
    assert "the runway is 06" not in haystack.lower()
    assert "the runway is 24" not in haystack.lower()


# --- BGM safety test (Part 15/16 precursor) ----------------------------------


def test_bgm_ranks_results_without_touching_existing_signal_state():
    context = AirportSearchContext(name="Greater Binghamton Airport", iata_code="BGM", icao_code="KBGM")
    clue = ResearchClue(
        evidence_text="Reconstruct Engineered Material Arresting System Safety Area",
        airport_context=context,
        unresolved_dimensions=(ResearchDimension.SUPPLIER, ResearchDimension.INSTALLATION_TYPE),
    )
    planned = plan_research_search_queries(clue)
    supplier_query = next(p.search_query for p in planned if p.dimension == ResearchDimension.SUPPLIER)

    provider = _FakeProvider({
        supplier_query.rendered: SearchOutcome(
            query=supplier_query, status=SearchOutcomeStatus.OK,
            results=(
                _result(supplier_query, "https://example.com/bgm-a", title="BGM EMAS contractor announcement"),
                _result(supplier_query, "https://example.com/bgm-b", title="BGM airport authority board minutes EMAS"),
            ),
        )
    })

    report = run_research_loop(clue, provider=provider)

    urls = {c.triaged.deduped.result.url for c in report.triaged_candidates}
    assert urls == {"https://example.com/bgm-a", "https://example.com/bgm-b"}
    for c in report.triaged_candidates:
        assert not hasattr(c, "signal_id")
        assert not hasattr(c.triaged, "signal_id")


# --- No-result safety test (Part 14) -----------------------------------------


def test_no_result_run_completes_cleanly_with_zero_triaged_candidates():
    clue = _sdf_clue()
    provider = _FakeProvider({})  # every query falls through to NO_RESULTS

    report = run_research_loop(clue, provider=provider)

    assert len(report.query_outcomes) == TOTAL_PLANNED_QUERIES_FOR_ALL_FIVE
    assert all(qo.outcome.status == SearchOutcomeStatus.NO_RESULTS for qo in report.query_outcomes)
    assert report.triaged_candidates == ()
    for dimension in ALL_FIVE_DIMENSIONS:
        assert compute_dimension_search_status(dimension, report) == DimensionSearchStatus.NO_CANDIDATES_FOUND


# --- Provider failure test (Part 15) -----------------------------------------


def test_provider_failure_does_not_abort_the_run():
    clue = _sdf_clue()
    planned = plan_research_search_queries(clue)
    canned = {
        planned[0].search_query.rendered: SearchOutcome(
            query=planned[0].search_query, status=SearchOutcomeStatus.PROVIDER_FAILURE, error="simulated failure",
        ),
        planned[1].search_query.rendered: SearchOutcome(
            query=planned[1].search_query, status=SearchOutcomeStatus.OK,
            results=(_result(planned[1].search_query, "https://example.com/still-works"),),
        ),
    }
    report = run_research_loop(clue, provider=_FakeProvider(canned))

    assert len(report.query_outcomes) == TOTAL_PLANNED_QUERIES_FOR_ALL_FIVE  # every planned query still executed
    failed = [qo for qo in report.query_outcomes if qo.outcome.status == SearchOutcomeStatus.PROVIDER_FAILURE]
    assert len(failed) == 1
    assert failed[0].outcome.error == "simulated failure"
    assert len(report.triaged_candidates) == 1  # the OTHER query's real result still survives


def test_search_failed_status_only_when_every_query_for_a_dimension_fails():
    """A dimension with TWO planned queries where only ONE fails must NOT
    be reported SEARCH_FAILED - it found candidates via the other query,
    so it is CANDIDATES_FOUND (the failure is still visible in the
    per-query breakdown, never hidden)."""
    context = AirportSearchContext(name="Test Airport")
    clue = ResearchClue(
        evidence_text="x", airport_context=context,
        unresolved_dimensions=(ResearchDimension.INSTALLATION_TYPE,),
    )
    planned = plan_research_search_queries(clue)
    assert len(planned) == 2
    canned = {
        planned[0].search_query.rendered: SearchOutcome(
            query=planned[0].search_query, status=SearchOutcomeStatus.PROVIDER_FAILURE, error="boom",
        ),
        planned[1].search_query.rendered: SearchOutcome(
            query=planned[1].search_query, status=SearchOutcomeStatus.OK,
            results=(_result(planned[1].search_query, "https://example.com/still-works", title="Test Airport EMAS replacement"),),
        ),
    }
    report = run_research_loop(clue, provider=_FakeProvider(canned))
    status = compute_dimension_search_status(ResearchDimension.INSTALLATION_TYPE, report)
    assert status == DimensionSearchStatus.CANDIDATES_FOUND


def test_search_failed_status_when_every_query_for_a_dimension_fails():
    context = AirportSearchContext(name="Test Airport")
    clue = ResearchClue(
        evidence_text="x", airport_context=context,
        unresolved_dimensions=(ResearchDimension.INSTALLATION_TYPE,),
    )
    planned = plan_research_search_queries(clue)
    canned = {
        p.search_query.rendered: SearchOutcome(query=p.search_query, status=SearchOutcomeStatus.PROVIDER_FAILURE, error="boom")
        for p in planned
    }
    report = run_research_loop(clue, provider=_FakeProvider(canned))
    status = compute_dimension_search_status(ResearchDimension.INSTALLATION_TYPE, report)
    assert status == DimensionSearchStatus.SEARCH_FAILED


# --- Determinism / dedup / triage reuse --------------------------------------


def test_same_inputs_produce_the_same_report_shape():
    clue = _sdf_clue()
    q0 = plan_research_questions(clue)[0]
    canned = {q0.search_query.rendered: SearchOutcome(
        query=q0.search_query, status=SearchOutcomeStatus.OK,
        results=(_result(q0.search_query, "https://example.com/x"),),
    )}

    report_a = run_research_loop(clue, provider=_FakeProvider(canned))
    report_b = run_research_loop(clue, provider=_FakeProvider(canned))

    assert report_a.questions == report_b.questions
    assert report_a.planned_queries == report_b.planned_queries
    assert len(report_a.triaged_candidates) == len(report_b.triaged_candidates) == 1
    assert report_a.triaged_candidates[0].triaged.deduped.result.url == report_b.triaged_candidates[0].triaged.deduped.result.url


def test_triage_band_semantics_are_reused_unmodified():
    """A strong-concept-in-title + identity match must still be HIGH,
    exactly matching app.discovery.triage's own existing rules - proves
    this module never redefines triage (mission's own explicit
    "do not change triage semantics" instruction)."""
    context = AirportSearchContext(name="Test Airport")
    clue = ResearchClue(
        evidence_text="x", airport_context=context, unresolved_dimensions=(ResearchDimension.RUNWAY_END,),
    )
    q = plan_research_questions(clue)[0]
    provider = _FakeProvider({
        q.search_query.rendered: SearchOutcome(
            query=q.search_query, status=SearchOutcomeStatus.OK,
            results=(_result(q.search_query, "https://example.com/y", title="Test Airport EMAS project"),),
        )
    })
    report = run_research_loop(clue, provider=provider)
    assert len(report.triaged_candidates) == 1
    assert report.triaged_candidates[0].triaged.band == PriorityBand.HIGH


# --- Honest search status vs. research resolution (Slice 3 core fix) -------


def test_candidates_found_never_implies_resolved():
    """A dimension with a HIGH-band candidate is CANDIDATES_FOUND - and
    the DimensionSearchStatus vocabulary itself contains no RESOLVED/
    CONFIRMED/VERIFIED/ANSWERED/ESTABLISHED member, so there is no way to
    even ACCIDENTALLY report this dimension as answered."""
    context = AirportSearchContext(name="Test Airport")
    clue = ResearchClue(evidence_text="x", airport_context=context, unresolved_dimensions=(ResearchDimension.SUPPLIER,))
    q = plan_research_questions(clue)[0]
    provider = _FakeProvider({
        q.search_query.rendered: SearchOutcome(
            query=q.search_query, status=SearchOutcomeStatus.OK,
            results=(_result(q.search_query, "https://example.com/z", title="Test Airport EMAS supplier"),),
        )
    })
    report = run_research_loop(clue, provider=provider)
    status = compute_dimension_search_status(ResearchDimension.SUPPLIER, report)
    assert status == DimensionSearchStatus.CANDIDATES_FOUND
    banned = {"RESOLVED", "CONFIRMED", "VERIFIED", "ANSWERED", "ESTABLISHED"}
    assert not (banned & {m.value for m in DimensionSearchStatus})


def test_no_candidates_found_is_not_negative_evidence():
    """A dimension with zero surviving candidates must never be reported
    or interpretable as 'no supplier exists' - only that search found
    nothing this round."""
    context = AirportSearchContext(name="Test Airport")
    clue = ResearchClue(evidence_text="x", airport_context=context, unresolved_dimensions=(ResearchDimension.SUPPLIER,))
    report = run_research_loop(clue, provider=_FakeProvider({}))
    status = compute_dimension_search_status(ResearchDimension.SUPPLIER, report)
    assert status == DimensionSearchStatus.NO_CANDIDATES_FOUND
    assert status != "no supplier"
    assert status.value != "NO_SUPPLIER"


# --- Literal-anchor opt-in (RWI HQ "Discovery Research Loop V1 - Slice 5F") --

# The exact, real, preserved SourceAssertion 258 text (Airport World candidate).
SA258_EVIDENCE_TEXT = (
    "On the airfield, reconstruction is expected on Taxiways B and D, phase 1 of the East "
    "Runway’s Engineered Materials Arresting System (EMAS) will be installed and electrical "
    "work will continue including the completion of the SDF MicroGrid."
)


def _sdf_clue_with_text(evidence_text: str, dimensions=ALL_FIVE_DIMENSIONS) -> ResearchClue:
    context = AirportSearchContext(
        name="Louisville Muhammad Ali International Airport", iata_code="SDF", icao_code="KSDF",
    )
    return ResearchClue(evidence_text=evidence_text, airport_context=context, unresolved_dimensions=dimensions)


def test_default_behavior_is_unchanged_seven_query_baseline():
    """use_literal_anchors defaults to False - existing behavior, byte-for-
    behavior, for every existing caller that never passes it."""
    clue = _sdf_clue_with_text(SA258_EVIDENCE_TEXT)
    report = run_research_loop(clue, provider=None)
    assert len(report.planned_queries) == TOTAL_PLANNED_QUERIES_FOR_ALL_FIVE
    assert report.planned_queries == plan_research_search_queries(clue)


def test_use_literal_anchors_false_explicit_matches_default():
    clue = _sdf_clue_with_text(SA258_EVIDENCE_TEXT)
    default_report = run_research_loop(clue, provider=None)
    explicit_false_report = run_research_loop(clue, provider=None, use_literal_anchors=False)
    assert default_report.planned_queries == explicit_false_report.planned_queries


def test_opt_in_uses_anchor_aware_planner_sa258_gives_nine_queries():
    clue = _sdf_clue_with_text(SA258_EVIDENCE_TEXT)
    report = run_research_loop(clue, provider=None, use_literal_anchors=True)
    assert len(report.planned_queries) == 9
    rendered = {p.search_query.rendered for p in report.planned_queries}
    name = "Louisville Muhammad Ali International Airport"
    assert f'"{name}" EMAS "East Runway"' in rendered
    assert f'"{name}" EMAS "phase 1"' in rendered


def test_opt_in_sa257_style_evidence_stays_at_seven_queries():
    clue = _sdf_clue_with_text(SDF_EVIDENCE_TEXT)  # the existing FAA-AIP-style fixture
    report = run_research_loop(clue, provider=None, use_literal_anchors=True)
    assert len(report.planned_queries) == TOTAL_PLANNED_QUERIES_FOR_ALL_FIVE
    assert report.planned_queries == plan_research_search_queries(clue)


def test_opt_in_baseline_prefix_unchanged():
    clue = _sdf_clue_with_text(SA258_EVIDENCE_TEXT)
    baseline = plan_research_search_queries(clue)
    anchor_aware_report = run_research_loop(clue, provider=None, use_literal_anchors=True)
    assert anchor_aware_report.planned_queries[: len(baseline)] == baseline


def test_dimension_search_status_vocabulary_unaffected_by_opt_in():
    clue = _sdf_clue_with_text(SA258_EVIDENCE_TEXT)
    report = run_research_loop(clue, provider=_FakeProvider({}), use_literal_anchors=True)
    for dimension in ALL_FIVE_DIMENSIONS:
        status = compute_dimension_search_status(dimension, report)
        assert status in set(DimensionSearchStatus)
    assert {m.value for m in DimensionSearchStatus} == {"CANDIDATES_FOUND", "NO_CANDIDATES_FOUND", "SEARCH_FAILED"}


def test_opt_in_with_live_provider_executes_the_extra_anchor_queries():
    """Proves the two extra anchor queries are ACTUALLY executed through
    the injected provider when opted in - not merely planned."""
    clue = _sdf_clue_with_text(SA258_EVIDENCE_TEXT)
    plan = run_research_loop(clue, provider=None, use_literal_anchors=True).planned_queries
    anchor_queries = [p for p in plan if "East Runway" in p.search_query.rendered or '"phase 1"' in p.search_query.rendered]
    assert len(anchor_queries) == 2

    canned = {
        p.search_query.rendered: SearchOutcome(
            query=p.search_query, status=SearchOutcomeStatus.OK,
            results=(_result(p.search_query, f"https://example.com/{i}", title="Anchor hit"),),
        )
        for i, p in enumerate(anchor_queries)
    }
    report = run_research_loop(clue, provider=_FakeProvider(canned), use_literal_anchors=True)
    assert len(report.query_outcomes) == 9
    executed_rendered = {qo.outcome.query.rendered for qo in report.query_outcomes}
    for aq in anchor_queries:
        assert aq.search_query.rendered in executed_rendered
