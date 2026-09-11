"""Tests for app/services/research_loop.py (RWI HQ "Discovery Research
Loop V1", Slice 2/3). Fake SearchProvider only - no network, no database.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

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


@pytest.fixture(autouse=True)
def _no_real_dns(monkeypatch):
    """RWI HQ "Bounded Official-Hub Follow-Up Discovery" mission's own
    explicit "NO live network dependency in unit tests" requirement: the
    hub-follow-up tests below exercise real validate_fetch_target() calls
    against "flylouisville.com" through the injected fake client - this
    guarantees that never becomes a real DNS lookup, regardless of this
    test-running machine's own internet access (matching
    tests/test_generic_web_provider.py's own established convention).
    Inert for every other test in this file, which never touches a real
    socket at all."""
    import socket

    def fake_getaddrinfo(host, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

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


# --- Official-domain document pass (RWI HQ "Official-Domain Document
# Discovery Pass" mission - SDF/flylouisville.com benchmark) -----------------


def test_official_domain_none_leaves_baseline_completely_unchanged():
    """Part J/Success-criteria #1: existing generic behavior preserved -
    omitting `official_domain` produces byte-for-behavior identical plan/
    report shape to before this mission."""
    clue = _sdf_clue()
    without = run_research_loop(clue, provider=None)
    explicit_none = run_research_loop(clue, provider=None, official_domain=None)
    assert without == explicit_none
    assert without.official_domain is None
    assert without.official_domain_queries == ()
    assert without.official_domain_query_outcomes == ()
    assert len(without.planned_queries) == TOTAL_PLANNED_QUERIES_FOR_ALL_FIVE


def test_official_domain_plan_only_mode_shows_the_four_queries_with_zero_network():
    clue = _sdf_clue()
    report = run_research_loop(clue, provider=None, official_domain="flylouisville.com")
    assert report.official_domain == "flylouisville.com"
    assert [q.rendered for q in report.official_domain_queries] == [
        "site:flylouisville.com EMAS",
        "site:flylouisville.com EMAS presentation",
        "site:flylouisville.com EMAS capital program",
        "site:flylouisville.com EMAS board",
    ]
    # planner-only mode - zero execution regardless of official_domain.
    assert report.official_domain_query_outcomes == ()
    assert report.query_outcomes == ()
    # baseline dimension plan is an unmodified prefix, exactly as with
    # use_literal_anchors - the official-domain pass never touches it.
    assert report.planned_queries == plan_research_search_queries(clue)


def test_official_domain_pass_adds_exactly_four_queries_to_the_budget():
    """Part 9: query-budget cap - the pass adds AT MOST +4 queries, flat,
    never multiplied by the 5 requested dimensions."""
    clue = _sdf_clue()
    baseline_count = len(run_research_loop(clue, provider=None).planned_queries)
    report = run_research_loop(clue, provider=_FakeProvider({}), official_domain="flylouisville.com")
    assert len(report.official_domain_queries) == 4
    assert len(report.official_domain_query_outcomes) == 4
    assert len(report.query_outcomes) == baseline_count  # dimension plan itself untouched


def test_official_domain_queries_actually_execute_through_the_provider():
    clue = _sdf_clue()
    plan = run_research_loop(clue, provider=None, official_domain="flylouisville.com").official_domain_queries
    hit_query = plan[0]
    canned = {
        hit_query.rendered: SearchOutcome(
            query=hit_query, status=SearchOutcomeStatus.OK,
            results=(_result(hit_query, "https://www.flylouisville.com/wp-content/uploads/x.pdf", title="SDF EMAS presentation"),),
        )
    }
    report = run_research_loop(clue, provider=_FakeProvider(canned), official_domain="flylouisville.com")
    executed = {qo.search_query.rendered for qo in report.official_domain_query_outcomes}
    assert hit_query.rendered in executed
    urls = {t.triaged.deduped.result.url for t in report.triaged_candidates}
    assert "https://www.flylouisville.com/wp-content/uploads/x.pdf" in urls


def test_official_domain_hit_is_deduplicated_with_a_dimension_query_hit():
    """A URL surfaced by BOTH a dimension query and an official-domain
    query is deduplicated to exactly one candidate, with both queries
    preserved in its found_by provenance - the existing cross-query dedup
    principle, now extended to include the official-domain pass."""
    clue = _sdf_clue()
    dimension_plan = plan_research_search_queries(clue)
    domain_plan = run_research_loop(clue, provider=None, official_domain="flylouisville.com").official_domain_queries
    shared_url = "https://www.flylouisville.com/wp-content/uploads/shared.pdf"

    canned = {dimension_plan[0].search_query.rendered: SearchOutcome(
        query=dimension_plan[0].search_query, status=SearchOutcomeStatus.OK,
        results=(_result(dimension_plan[0].search_query, shared_url, title="SDF EMAS document"),),
    )}
    canned[domain_plan[0].rendered] = SearchOutcome(
        query=domain_plan[0], status=SearchOutcomeStatus.OK,
        results=(_result(domain_plan[0], shared_url, title="SDF EMAS document"),),
    )
    report = run_research_loop(clue, provider=_FakeProvider(canned), official_domain="flylouisville.com")

    matching = [t for t in report.triaged_candidates if t.triaged.deduped.result.url == shared_url]
    assert len(matching) == 1
    assert len(matching[0].triaged.deduped.found_by) == 2


def test_official_domain_triage_bonus_reflected_in_report():
    clue = _sdf_clue()
    domain_plan = run_research_loop(clue, provider=None, official_domain="flylouisville.com").official_domain_queries
    canned = {
        domain_plan[0].rendered: SearchOutcome(
            query=domain_plan[0], status=SearchOutcomeStatus.OK,
            results=(
                _result(
                    domain_plan[0],
                    "https://www.flylouisville.com/wp-content/uploads/x.pdf",
                    title="East Runway Engineered Materials Arresting System (EMAS)",
                ),
            ),
        )
    }
    report = run_research_loop(clue, provider=_FakeProvider(canned), official_domain="flylouisville.com")
    candidate = next(t for t in report.triaged_candidates if "flylouisville.com" in t.triaged.deduped.result.url)
    assert "Known official domain" in candidate.triaged.reasons
    assert candidate.triaged.band == PriorityBand.HIGH


def test_official_domain_query_never_attributed_a_dimension():
    """A candidate found ONLY via an official-domain query (no dimension
    query also found it) correctly reports an empty dimensions tuple -
    these queries answer no ResearchDimension question."""
    clue = _sdf_clue()
    domain_plan = run_research_loop(clue, provider=None, official_domain="flylouisville.com").official_domain_queries
    canned = {
        domain_plan[0].rendered: SearchOutcome(
            query=domain_plan[0], status=SearchOutcomeStatus.OK,
            results=(_result(domain_plan[0], "https://www.flylouisville.com/only-domain-hit.pdf"),),
        )
    }
    report = run_research_loop(clue, provider=_FakeProvider(canned), official_domain="flylouisville.com")
    candidate = next(t for t in report.triaged_candidates if "only-domain-hit" in t.triaged.deduped.result.url)
    assert candidate.dimensions == ()


# --- Bounded official-hub follow-up (RWI HQ "Bounded Official-Hub
# Follow-Up Discovery" mission) - wired through run_research_loop() -----------


class _FakeHubStreamResponse:
    def __init__(self, status_code=200, *, headers=None, content=b"", url="https://flylouisville.com/hub"):
        self.status_code = status_code
        self.headers = httpx.Headers(headers or {})
        self._content = content
        self.url = httpx.URL(url)

    @property
    def is_redirect(self):
        return False

    def iter_bytes(self):
        yield self._content

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeHubGetResponse:
    status_code = 404
    text = ""


class _FakeHubFetchClient:
    """Supports both .stream() (the hub-page fetch) and .get() (the
    robots.txt check) - independent copy of
    tests/test_official_hub_followup.py's own _FakeHubClient, matching
    this repo's own established per-test-file convention."""

    def __init__(self, html_by_url):
        self._html_by_url = html_by_url
        self.stream_calls = []

    def stream(self, method, url, **kwargs):
        self.stream_calls.append(url)
        html = self._html_by_url.get(url, "<html><body></body></html>")
        return _FakeHubStreamResponse(headers={"content-type": "text/html"}, content=html.encode("utf-8"), url=url)

    def get(self, url, **kwargs):
        return _FakeHubGetResponse()

    def close(self):
        pass


_HUB_HTML = '<html><body><a href="/wp-content/uploads/2026/02/board-minutes-feb-2026.pdf">Feb 2026 minutes</a></body></html>'


def test_follow_up_default_false_leaves_behavior_unchanged():
    clue = _sdf_clue()
    without = run_research_loop(clue, provider=_FakeProvider({}), official_domain="flylouisville.com")
    explicit_false = run_research_loop(clue, provider=_FakeProvider({}), official_domain="flylouisville.com", follow_up_official_hubs=False)
    assert without == explicit_false
    assert without.hub_follow_up_outcomes == ()


def test_follow_up_enabled_without_official_domain_is_documented_noop():
    clue = _sdf_clue()
    report = run_research_loop(clue, provider=_FakeProvider({}), follow_up_official_hubs=True)
    assert report.official_domain is None
    assert report.hub_follow_up_outcomes == ()


def test_follow_up_enabled_fetches_hub_and_merges_extracted_candidate():
    clue = _sdf_clue()
    domain_plan = run_research_loop(clue, provider=None, official_domain="flylouisville.com").official_domain_queries
    hub_url = "https://flylouisville.com/corporate/lraa-board-meeting-minutes/"
    canned = {
        domain_plan[0].rendered: SearchOutcome(
            query=domain_plan[0], status=SearchOutcomeStatus.OK,
            results=(_result(domain_plan[0], hub_url, title="LRAA Board Meeting Minutes"),),
        )
    }
    hub_client = _FakeHubFetchClient({hub_url: _HUB_HTML})

    report = run_research_loop(
        clue, provider=_FakeProvider(canned), official_domain="flylouisville.com",
        follow_up_official_hubs=True, hub_fetch_client=hub_client,
    )

    assert len(report.hub_follow_up_outcomes) == 1
    assert report.hub_follow_up_outcomes[0].hub_url == hub_url
    assert report.hub_follow_up_outcomes[0].fetched is True
    extracted_urls = {t.triaged.deduped.result.url for t in report.triaged_candidates}
    assert "https://flylouisville.com/wp-content/uploads/2026/02/board-minutes-feb-2026.pdf" in extracted_urls
    extracted = next(t for t in report.triaged_candidates if t.triaged.deduped.result.url.endswith("board-minutes-feb-2026.pdf"))
    assert extracted.triaged.deduped.result.provider == "official_hub_followup"


def test_follow_up_dedupes_extracted_link_against_an_independently_found_hit():
    clue = _sdf_clue()
    domain_plan = run_research_loop(clue, provider=None, official_domain="flylouisville.com").official_domain_queries
    hub_url = "https://flylouisville.com/corporate/lraa-board-meeting-minutes/"
    pdf_url = "https://flylouisville.com/wp-content/uploads/2026/02/board-minutes-feb-2026.pdf"
    canned = {
        domain_plan[0].rendered: SearchOutcome(
            query=domain_plan[0], status=SearchOutcomeStatus.OK,
            results=(
                _result(domain_plan[0], hub_url, title="LRAA Board Meeting Minutes"),
                _result(domain_plan[0], pdf_url, title="Feb 2026 minutes PDF", snippet=""),
            ),
        )
    }
    hub_client = _FakeHubFetchClient({hub_url: _HUB_HTML})

    report = run_research_loop(
        clue, provider=_FakeProvider(canned), official_domain="flylouisville.com",
        follow_up_official_hubs=True, hub_fetch_client=hub_client,
    )

    matching = [t for t in report.triaged_candidates if t.triaged.deduped.result.url == pdf_url]
    assert len(matching) == 1  # deduplicated, not duplicated
    assert len(matching[0].triaged.deduped.found_by) == 2  # both the real query AND the follow-up link


def test_follow_up_never_fetches_more_than_max_hub_fetches():
    from app.services.official_hub_followup import MAX_HUB_FETCHES

    clue = _sdf_clue()
    domain_plan = run_research_loop(clue, provider=None, official_domain="flylouisville.com").official_domain_queries
    hub_urls = [f"https://flylouisville.com/corporate/board-meeting-{i}/" for i in range(5)]
    canned = {
        domain_plan[0].rendered: SearchOutcome(
            query=domain_plan[0], status=SearchOutcomeStatus.OK,
            results=tuple(_result(domain_plan[0], u, title=f"Board meeting {i}") for i, u in enumerate(hub_urls)),
        )
    }
    hub_client = _FakeHubFetchClient({u: "<html><body></body></html>" for u in hub_urls})

    report = run_research_loop(
        clue, provider=_FakeProvider(canned), official_domain="flylouisville.com",
        follow_up_official_hubs=True, hub_fetch_client=hub_client,
    )
    assert len(report.hub_follow_up_outcomes) == MAX_HUB_FETCHES == 2
    assert len(hub_client.stream_calls) == 2
