"""Tests for app/services/research_loop.py (RWI HQ "Discovery Research
Loop V1", Slice 2). Fake SearchProvider only - no network, no database.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.discovery.query import SearchQuery
from app.discovery.search import SearchOutcome, SearchOutcomeStatus, SearchResult
from app.discovery.triage import PriorityBand
from app.services.discovery_temporal_followup import AirportSearchContext
from app.services.research_loop import ResearchLoopReport, run_research_loop
from app.services.research_question_planning import ResearchClue, ResearchDimension

_NOW = datetime(2026, 9, 4, tzinfo=timezone.utc)

ALL_FIVE_DIMENSIONS = (
    ResearchDimension.RUNWAY_END,
    ResearchDimension.INSTALLATION_TYPE,
    ResearchDimension.PROJECT_PHASE,
    ResearchDimension.TIMING,
    ResearchDimension.SUPPLIER,
)

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
    assert report.question_outcomes == ()
    assert report.triaged_candidates == ()


# --- SDF offline acceptance test (Part 12) -----------------------------------


def test_sdf_offline_acceptance_executes_exactly_five_questions_unchanged_queries():
    clue = _sdf_clue()
    from app.services.research_question_planning import plan_research_questions
    expected_questions = plan_research_questions(clue)

    canned = {
        expected_questions[0].search_query.rendered: SearchOutcome(
            query=expected_questions[0].search_query, status=SearchOutcomeStatus.OK,
            results=(_result(expected_questions[0].search_query, "https://faa.gov/sdf-runway-doc", title="SDF Runway 11/29 EMAS document"),),
        ),
        expected_questions[1].search_query.rendered: SearchOutcome(
            query=expected_questions[1].search_query, status=SearchOutcomeStatus.NO_RESULTS,
        ),
    }
    provider = _FakeProvider(canned)

    report = run_research_loop(clue, provider=provider)

    assert len(report.question_outcomes) == 5
    for qo in report.question_outcomes:
        # each executed SearchQuery is passed unchanged to the provider
        assert qo.outcome.query == qo.question.search_query
    assert {qo.question.dimension for qo in report.question_outcomes} == set(ALL_FIVE_DIMENSIONS)

    statuses = {qo.question.dimension: qo.outcome.status for qo in report.question_outcomes}
    assert statuses[ResearchDimension.RUNWAY_END] == SearchOutcomeStatus.OK
    assert statuses[ResearchDimension.INSTALLATION_TYPE] == SearchOutcomeStatus.NO_RESULTS
    # every other question defaulted to NO_RESULTS via the fake provider's canned fallback
    assert statuses[ResearchDimension.PROJECT_PHASE] == SearchOutcomeStatus.NO_RESULTS
    assert statuses[ResearchDimension.TIMING] == SearchOutcomeStatus.NO_RESULTS
    assert statuses[ResearchDimension.SUPPLIER] == SearchOutcomeStatus.NO_RESULTS


def test_sdf_offline_dedup_across_questions_and_triage_runs_once():
    """A URL surfaced by TWO different dimensions' queries must appear
    exactly once in triaged_candidates, with BOTH dimensions recovered."""
    context = AirportSearchContext(name="Louisville Muhammad Ali International Airport", iata_code="SDF", icao_code="KSDF")
    clue = ResearchClue(
        evidence_text=SDF_EVIDENCE_TEXT, airport_context=context,
        unresolved_dimensions=(ResearchDimension.RUNWAY_END, ResearchDimension.PROJECT_PHASE),
    )
    from app.services.research_question_planning import plan_research_questions
    questions = plan_research_questions(clue)
    shared_url = "https://faa.gov/sdf-emas-project"

    canned = {
        q.search_query.rendered: SearchOutcome(
            query=q.search_query, status=SearchOutcomeStatus.OK,
            results=(_result(q.search_query, shared_url, title="SDF EMAS Runway Project - FAA"),),
        )
        for q in questions
    }
    provider = _FakeProvider(canned)

    report = run_research_loop(clue, provider=provider)

    assert len(report.triaged_candidates) == 1  # deduplicated to one URL
    candidate = report.triaged_candidates[0]
    assert candidate.triaged.deduped.result.url == shared_url
    assert set(candidate.dimensions) == {ResearchDimension.RUNWAY_END, ResearchDimension.PROJECT_PHASE}
    assert len(candidate.triaged.deduped.found_by) == 2


def test_sdf_offline_no_answer_is_ever_inferred():
    """The loop's own output (questions/reasons/rendered queries - never
    the fake provider's own synthetic titles, which are test fixtures
    representing what a REAL provider might someday return, not planner
    output) must never assert runway 17L/35R, new installation, supplier,
    schedule, or project value."""
    clue = _sdf_clue()
    report = run_research_loop(clue, provider=None)  # plan-only: no results to accidentally leak an answer from
    haystack = " ".join(f"{q.question} {q.reason} {q.search_query.rendered}" for q in report.questions).lower()
    for forbidden in ("17l", "35r", "17/35", "north end", "east runway", "is a replacement", "is a new installation", "$", "million"):
        assert forbidden not in haystack


# --- Query-bias observability (Part 13) --------------------------------------


def test_report_preserves_exact_rendered_query_text():
    clue = _sdf_clue(dimensions=(ResearchDimension.INSTALLATION_TYPE, ResearchDimension.PROJECT_PHASE))
    report = run_research_loop(clue, provider=None)
    rendered = {q.dimension: q.search_query.rendered for q in report.questions}
    assert rendered[ResearchDimension.INSTALLATION_TYPE] == '"Louisville Muhammad Ali International Airport" EMAS replacement'
    assert rendered[ResearchDimension.PROJECT_PHASE] == '"Louisville Muhammad Ali International Airport" EMAS construction'


# --- MHT safety test (Part 14) -----------------------------------------------


def test_mht_runway_end_results_reported_never_resolved():
    context = AirportSearchContext(name="Manchester-Boston Regional Airport", iata_code="MHT", icao_code="KMHT")
    clue = ResearchClue(
        evidence_text="Reconstruct Engineered Material Arresting System Safety Area",
        airport_context=context, unresolved_dimensions=(ResearchDimension.RUNWAY_END,),
    )
    from app.services.research_question_planning import plan_research_questions
    question = plan_research_questions(clue)[0]

    provider = _FakeProvider({
        question.search_query.rendered: SearchOutcome(
            query=question.search_query, status=SearchOutcomeStatus.OK,
            results=(
                _result(question.search_query, "https://example.com/mht-06", title="MHT Runway 06 EMAS project"),
                _result(question.search_query, "https://example.com/mht-24", title="MHT Runway 24 EMAS reconstruction"),
            ),
        )
    })

    report = run_research_loop(clue, provider=provider)

    # both results are reported, verbatim, as candidates - never merged,
    # never resolved, never chosen between.
    urls = {c.triaged.deduped.result.url for c in report.triaged_candidates}
    assert urls == {"https://example.com/mht-06", "https://example.com/mht-24"}
    # the loop's OWN output (question/reason/query) never picks a side
    haystack = f"{question.question} {question.reason} {question.search_query.rendered}"
    assert "06 is correct" not in haystack
    assert "24 is correct" not in haystack


# --- BGM safety test (Part 15) -----------------------------------------------


def test_bgm_ranks_results_without_touching_existing_signal_state():
    context = AirportSearchContext(name="Greater Binghamton Airport", iata_code="BGM", icao_code="KBGM")
    clue = ResearchClue(
        evidence_text="Reconstruct Engineered Material Arresting System Safety Area",
        airport_context=context,
        unresolved_dimensions=(ResearchDimension.SUPPLIER, ResearchDimension.INSTALLATION_TYPE),
    )
    from app.services.research_question_planning import plan_research_questions
    questions = plan_research_questions(clue)

    provider = _FakeProvider({
        questions[0].search_query.rendered: SearchOutcome(
            query=questions[0].search_query, status=SearchOutcomeStatus.OK,
            results=(
                _result(questions[0].search_query, "https://example.com/bgm-a", title="BGM EMAS contractor announcement"),
                _result(questions[0].search_query, "https://example.com/bgm-b", title="BGM airport authority board minutes EMAS"),
            ),
        )
    })

    report = run_research_loop(clue, provider=provider)

    # results are ranked/deduplicated only - no Signal-shaped field
    # (signal_id, matched Signal, etc.) exists anywhere on the report.
    urls = {c.triaged.deduped.result.url for c in report.triaged_candidates}
    assert urls == {"https://example.com/bgm-a", "https://example.com/bgm-b"}
    for c in report.triaged_candidates:
        assert not hasattr(c, "signal_id")
        assert not hasattr(c.triaged, "signal_id")


# --- No-result acceptance test (Part 16) -------------------------------------


def test_no_result_run_completes_cleanly_with_zero_triaged_candidates():
    clue = _sdf_clue()
    provider = _FakeProvider({})  # every query falls through to NO_RESULTS

    report = run_research_loop(clue, provider=provider)

    assert len(report.question_outcomes) == 5
    assert all(qo.outcome.status == SearchOutcomeStatus.NO_RESULTS for qo in report.question_outcomes)
    assert report.triaged_candidates == ()


# --- Determinism / dedup / triage reuse --------------------------------------


def test_same_inputs_produce_the_same_report_shape():
    clue = _sdf_clue()
    from app.services.research_question_planning import plan_research_questions
    q0 = plan_research_questions(clue)[0]
    canned = {q0.search_query.rendered: SearchOutcome(
        query=q0.search_query, status=SearchOutcomeStatus.OK,
        results=(_result(q0.search_query, "https://example.com/x"),),
    )}

    report_a = run_research_loop(clue, provider=_FakeProvider(canned))
    report_b = run_research_loop(clue, provider=_FakeProvider(canned))

    assert report_a.questions == report_b.questions
    assert len(report_a.triaged_candidates) == len(report_b.triaged_candidates) == 1
    assert report_a.triaged_candidates[0].triaged.deduped.result.url == report_b.triaged_candidates[0].triaged.deduped.result.url


def test_provider_failure_does_not_abort_the_run():
    clue = _sdf_clue()
    from app.services.research_question_planning import plan_research_questions
    questions = plan_research_questions(clue)
    canned = {
        questions[0].search_query.rendered: SearchOutcome(
            query=questions[0].search_query, status=SearchOutcomeStatus.PROVIDER_FAILURE, error="simulated failure",
        ),
        questions[1].search_query.rendered: SearchOutcome(
            query=questions[1].search_query, status=SearchOutcomeStatus.OK,
            results=(_result(questions[1].search_query, "https://example.com/still-works"),),
        ),
    }
    report = run_research_loop(clue, provider=_FakeProvider(canned))

    assert len(report.question_outcomes) == 5  # all 5 questions still executed
    failed = [qo for qo in report.question_outcomes if qo.outcome.status == SearchOutcomeStatus.PROVIDER_FAILURE]
    assert len(failed) == 1
    assert failed[0].outcome.error == "simulated failure"
    assert len(report.triaged_candidates) == 1  # the OTHER question's real result still survives


def test_triage_band_semantics_are_reused_unmodified():
    """A strong-concept-in-title + identity match must still be HIGH,
    exactly matching app.discovery.triage's own existing rules - proves
    this module never redefines triage."""
    context = AirportSearchContext(name="Test Airport")
    clue = ResearchClue(
        evidence_text="x", airport_context=context, unresolved_dimensions=(ResearchDimension.RUNWAY_END,),
    )
    from app.services.research_question_planning import plan_research_questions
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
