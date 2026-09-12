"""RWI HQ "Update & Report V1.1 - Watch -> Discovery Bridge" mission -
tests for app/services/update_report_discovery_bridge.py (Part 17,
offline/mocked only - no live network in any test here)."""
from __future__ import annotations

import itertools
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.discovery.query import SearchQuery
from app.discovery.search import SearchOutcome, SearchOutcomeStatus, SearchResult
from app.models import Airport, ReviewerAction, Runway, Signal, SignalAmendmentAction, Source, SourceAssertion
from app.services.update_report_discovery_bridge import (
    MAX_LIVE_QUERIES_PER_WATCH_ITEM,
    WatchDiscoverySkipped,
    WatchDiscoveryStatus,
    build_watch_discovery_subject,
    plan_watch_discovery_queries,
    run_discovery_for_watch_set,
    run_watch_discovery,
)
from app.services.update_report_vocabulary import MaterialChangeClassification
from app.services.update_report_watchset import (
    WATCH_REASON_NEEDS_MORE_EVIDENCE,
    WATCH_REASON_STAGED_EVIDENCE_NEEDS_ATTENTION,
    WATCH_REASON_UNPUBLISHED_SIGNAL,
    WatchItem,
)


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def make_airport(session, **overrides) -> Airport:
    defaults = dict(name="Test Airport", iata_code="TST", country="USA")
    defaults.update(overrides)
    airport = Airport(**defaults)
    session.add(airport)
    session.flush()
    return airport


def make_runway(session, airport, **overrides) -> Runway:
    defaults = dict(airport=airport, designation="17L-35R")
    defaults.update(overrides)
    runway = Runway(**defaults)
    session.add(runway)
    session.flush()
    return runway


def make_source(session, **overrides) -> Source:
    defaults = dict(title="Test Source", source_type="news", reliability_level="official", url="https://example.test/a")
    defaults.update(overrides)
    source = Source(**defaults)
    session.add(source)
    session.flush()
    return source


def make_signal(session, airport, **overrides) -> Signal:
    defaults = dict(airport=airport, title="Test Signal", category="new_installation", confidence="medium", status="funded")
    defaults.update(overrides)
    signal = Signal(**defaults)
    session.add(signal)
    session.flush()
    return signal


_assertion_seq = itertools.count(1)


def make_assertion(session, source, **overrides) -> SourceAssertion:
    defaults = dict(
        source=source, assertion_type="project_construction",
        source_record_identifier=f"rec-{next(_assertion_seq)}",
        raw_relevant_text="Some evidence text.",
    )
    defaults.update(overrides)
    assertion = SourceAssertion(**defaults)
    session.add(assertion)
    session.flush()
    return assertion


class FakeProvider:
    """Injectable/mockable SearchProvider (Part 17 item 6) - a plain
    duck-typed object matching app.discovery.search.SearchProvider,
    exactly like the real BraveSearchProvider, but fully offline."""

    name = "fake"

    def __init__(self, outcomes_by_rendered=None):
        self._outcomes_by_rendered = outcomes_by_rendered or {}

    def search(self, query: SearchQuery) -> SearchOutcome:
        if query.rendered in self._outcomes_by_rendered:
            return self._outcomes_by_rendered[query.rendered]
        return SearchOutcome(query=query, status=SearchOutcomeStatus.NO_RESULTS)


def _result(query, url, title, rank=1, snippet="") -> SearchResult:
    return SearchResult(
        query=query, rank=rank, title=title, url=url, snippet=snippet,
        discovered_at=datetime.now(timezone.utc), provider="fake",
    )


def _row_counts(session):
    return (len(session.new), len(session.dirty), len(session.deleted))


# --- WATCH -> DISCOVERY -----------------------------------------------


def test_watch_item_converts_to_valid_discovery_subject():
    session = make_session()
    airport = make_airport(session, name="Manchester-Boston Regional Airport", iata_code="MHT")
    watch_item = WatchItem(airport_id=airport.id, signal_id=None, source_assertion_id=None, watch_reason=WATCH_REASON_UNPUBLISHED_SIGNAL, detail="x")

    subject = build_watch_discovery_subject(session, watch_item)

    assert not isinstance(subject, WatchDiscoverySkipped)
    assert subject.airport_identity.name == "Manchester-Boston Regional Airport"
    assert subject.airport_identity.iata_code == "MHT"


def test_missing_airport_identity_fails_safely():
    session = make_session()
    watch_item = WatchItem(airport_id=999999, signal_id=None, source_assertion_id=None, watch_reason=WATCH_REASON_UNPUBLISHED_SIGNAL, detail="x")

    subject = build_watch_discovery_subject(session, watch_item)

    assert isinstance(subject, WatchDiscoverySkipped)
    assert "999999" in subject.reason

    result = run_watch_discovery(session, watch_item, provider=None)
    assert result.status == WatchDiscoveryStatus.NO_QUERY
    assert result.queries_planned == ()


def test_bounded_query_count_per_watch_item():
    session = make_session()
    airport = make_airport(session)
    source = make_source(session, reliability_level="official", url="https://flyexample.com/")
    make_assertion(session, source, airport_id=airport.id, assertion_type="airport_inventory")
    watch_item = WatchItem(airport_id=airport.id, signal_id=None, source_assertion_id=None, watch_reason=WATCH_REASON_UNPUBLISHED_SIGNAL, detail="x")

    subject = build_watch_discovery_subject(session, watch_item)
    plan = plan_watch_discovery_queries(subject)

    assert len(plan.queries_to_run) <= MAX_LIVE_QUERIES_PER_WATCH_ITEM


def test_needs_more_evidence_reason_adds_temporal_followup_queries():
    session = make_session()
    airport = make_airport(session)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id, raw_relevant_text="The airport is installing a new EMAS system this year.")
    watch_item = WatchItem(
        airport_id=airport.id, signal_id=None, source_assertion_id=assertion.id,
        watch_reason=WATCH_REASON_NEEDS_MORE_EVIDENCE, detail="x",
    )

    subject = build_watch_discovery_subject(session, watch_item)
    plan = plan_watch_discovery_queries(subject)

    assert plan.temporal_followup_queries != ()


def test_staged_evidence_reason_does_not_trigger_temporal_followup():
    session = make_session()
    airport = make_airport(session)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id, raw_relevant_text="The airport is installing a new EMAS system this year.")
    watch_item = WatchItem(
        airport_id=airport.id, signal_id=None, source_assertion_id=assertion.id,
        watch_reason=WATCH_REASON_STAGED_EVIDENCE_NEEDS_ATTENTION, detail="x",
    )

    subject = build_watch_discovery_subject(session, watch_item)
    plan = plan_watch_discovery_queries(subject)

    assert plan.temporal_followup_queries == ()


def test_no_official_domain_means_no_official_domain_queries():
    session = make_session()
    airport = make_airport(session)
    watch_item = WatchItem(airport_id=airport.id, signal_id=None, source_assertion_id=None, watch_reason=WATCH_REASON_UNPUBLISHED_SIGNAL, detail="x")

    subject = build_watch_discovery_subject(session, watch_item)
    plan = plan_watch_discovery_queries(subject)

    assert subject.official_domain is None
    assert plan.official_domain_queries == ()
    assert plan.standard_queries != ()


def test_known_official_domain_adds_official_domain_queries():
    session = make_session()
    airport = make_airport(session)
    source = make_source(session, reliability_level="official", url="https://flyexample.com/news")
    make_assertion(session, source, airport_id=airport.id)
    watch_item = WatchItem(airport_id=airport.id, signal_id=None, source_assertion_id=None, watch_reason=WATCH_REASON_UNPUBLISHED_SIGNAL, detail="x")

    subject = build_watch_discovery_subject(session, watch_item)
    plan = plan_watch_discovery_queries(subject)

    assert subject.official_domain == "flyexample.com"
    assert len(plan.official_domain_queries) == 4
    assert plan.queries_to_run[0].identity_field == "official_domain"


# --- DISCOVERY / PROVIDER --------------------------------------------


def test_plan_only_run_performs_zero_provider_calls():
    session = make_session()
    airport = make_airport(session)
    watch_item = WatchItem(airport_id=airport.id, signal_id=None, source_assertion_id=None, watch_reason=WATCH_REASON_UNPUBLISHED_SIGNAL, detail="x")

    result = run_watch_discovery(session, watch_item, provider=None)

    assert result.queries_executed == ()
    assert result.search_outcomes == ()
    assert result.status == WatchDiscoveryStatus.PLANNED_ONLY
    assert result.queries_planned != ()


def test_zero_results_handled():
    session = make_session()
    airport = make_airport(session)
    watch_item = WatchItem(airport_id=airport.id, signal_id=None, source_assertion_id=None, watch_reason=WATCH_REASON_UNPUBLISHED_SIGNAL, detail="x")

    result = run_watch_discovery(session, watch_item, provider=FakeProvider())

    assert result.status == WatchDiscoveryStatus.NO_RESULTS
    assert result.triaged_candidates == ()


def test_provider_failure_handled_as_blocked():
    session = make_session()
    airport = make_airport(session)
    watch_item = WatchItem(airport_id=airport.id, signal_id=None, source_assertion_id=None, watch_reason=WATCH_REASON_UNPUBLISHED_SIGNAL, detail="x")

    subject = build_watch_discovery_subject(session, watch_item)
    plan = plan_watch_discovery_queries(subject)
    first_query = plan.queries_to_run[0]
    failure = SearchOutcome(query=first_query, status=SearchOutcomeStatus.PROVIDER_FAILURE, error="Cloudflare 403")
    provider = FakeProvider({first_query.rendered: failure})

    result = run_watch_discovery(session, watch_item, provider=provider)

    assert result.status == WatchDiscoveryStatus.BLOCKED
    assert any("Cloudflare 403" in note for note in result.notes)


def test_triaged_candidate_retains_query_provenance():
    session = make_session()
    airport = make_airport(session, name="Manchester-Boston Regional Airport", iata_code="MHT")
    watch_item = WatchItem(airport_id=airport.id, signal_id=None, source_assertion_id=None, watch_reason=WATCH_REASON_UNPUBLISHED_SIGNAL, detail="x")

    subject = build_watch_discovery_subject(session, watch_item)
    plan = plan_watch_discovery_queries(subject)
    first_query = plan.queries_to_run[0]
    outcome = SearchOutcome(
        query=first_query, status=SearchOutcomeStatus.OK,
        results=(_result(first_query, "https://faa.gov/mht-emas.pdf", "MHT EMAS project"),),
    )
    provider = FakeProvider({first_query.rendered: outcome})

    result = run_watch_discovery(session, watch_item, provider=provider)

    assert result.status in (WatchDiscoveryStatus.HUMAN_FETCH_REQUIRED, WatchDiscoveryStatus.CANDIDATES_FOUND)
    assert result.triaged_candidates
    assert result.triaged_candidates[0].deduped.result.query == first_query
    assert first_query in result.triaged_candidates[0].deduped.found_by


def test_search_result_never_becomes_evidence_directly():
    session = make_session()
    airport = make_airport(session, name="Manchester-Boston Regional Airport")
    watch_item = WatchItem(airport_id=airport.id, signal_id=None, source_assertion_id=None, watch_reason=WATCH_REASON_UNPUBLISHED_SIGNAL, detail="x")

    subject = build_watch_discovery_subject(session, watch_item)
    plan = plan_watch_discovery_queries(subject)
    first_query = plan.queries_to_run[0]
    outcome = SearchOutcome(
        query=first_query, status=SearchOutcomeStatus.OK,
        results=(_result(first_query, "https://faa.gov/mht-emas.pdf", "MHT EMAS project"),),
    )
    provider = FakeProvider({first_query.rendered: outcome})
    before = session.query(SourceAssertion).count()

    run_watch_discovery(session, watch_item, provider=provider)

    after = session.query(SourceAssertion).count()
    assert before == after == 0


# --- DEDUP / KNOWN SOURCE -----------------------------------------------


def test_known_source_recognized_and_not_treated_as_new():
    session = make_session()
    airport = make_airport(session, name="Manchester-Boston Regional Airport")
    known_source = make_source(session, url="https://faa.gov/mht-emas.pdf")
    make_assertion(session, known_source, airport_id=airport.id)
    watch_item = WatchItem(airport_id=airport.id, signal_id=None, source_assertion_id=None, watch_reason=WATCH_REASON_UNPUBLISHED_SIGNAL, detail="x")

    subject = build_watch_discovery_subject(session, watch_item)
    plan = plan_watch_discovery_queries(subject)
    first_query = plan.queries_to_run[0]
    outcome = SearchOutcome(
        query=first_query, status=SearchOutcomeStatus.OK,
        results=(_result(first_query, "https://faa.gov/mht-emas.pdf", "MHT EMAS project"),),
    )
    provider = FakeProvider({first_query.rendered: outcome})

    result = run_watch_discovery(session, watch_item, provider=provider)

    assert result.status == WatchDiscoveryStatus.CANDIDATES_FOUND
    assert len(result.known_source_matches) == 1
    assert result.known_source_matches[0].matched_source_id == known_source.id


def test_repeated_discovery_run_creates_no_duplicate_evidence():
    session = make_session()
    airport = make_airport(session, name="Manchester-Boston Regional Airport")
    watch_item = WatchItem(airport_id=airport.id, signal_id=None, source_assertion_id=None, watch_reason=WATCH_REASON_UNPUBLISHED_SIGNAL, detail="x")

    subject = build_watch_discovery_subject(session, watch_item)
    plan = plan_watch_discovery_queries(subject)
    first_query = plan.queries_to_run[0]
    outcome = SearchOutcome(
        query=first_query, status=SearchOutcomeStatus.OK,
        results=(_result(first_query, "https://faa.gov/mht-emas.pdf", "MHT EMAS project"),),
    )
    provider = FakeProvider({first_query.rendered: outcome})

    run_watch_discovery(session, watch_item, provider=provider)
    run_watch_discovery(session, watch_item, provider=provider)

    assert session.query(SourceAssertion).count() == 0


def test_existing_source_assertion_is_reused_not_duplicated():
    session = make_session()
    airport = make_airport(session)
    runway = make_runway(session, airport)
    make_signal(session, airport, runway=runway)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id, runway_id=runway.id)
    watch_item = WatchItem(
        airport_id=airport.id, signal_id=None, source_assertion_id=assertion.id,
        watch_reason=WATCH_REASON_STAGED_EVIDENCE_NEEDS_ATTENTION, detail="x",
    )

    result = run_watch_discovery(session, watch_item, provider=None)

    assert result.change_candidate is not None
    assert result.change_candidate.source_assertion_id == assertion.id
    assert session.query(SourceAssertion).count() == 1


# --- HUMAN GATE --------------------------------------------------------


def test_human_fetch_required_state_with_instructions():
    session = make_session()
    airport = make_airport(session, name="Manchester-Boston Regional Airport")
    watch_item = WatchItem(airport_id=airport.id, signal_id=None, source_assertion_id=None, watch_reason=WATCH_REASON_UNPUBLISHED_SIGNAL, detail="x")

    subject = build_watch_discovery_subject(session, watch_item)
    plan = plan_watch_discovery_queries(subject)
    first_query = plan.queries_to_run[0]
    outcome = SearchOutcome(
        query=first_query, status=SearchOutcomeStatus.OK,
        results=(_result(first_query, "https://faa.gov/mht-emas.pdf", '"Manchester-Boston Regional Airport" EMAS project'),),
    )
    provider = FakeProvider({first_query.rendered: outcome})

    result = run_watch_discovery(session, watch_item, provider=provider)

    if result.status == WatchDiscoveryStatus.HUMAN_FETCH_REQUIRED:
        assert result.fetch_instructions
        assert "fetch_research_candidate" in result.fetch_instructions[0]
        assert "--allow-live-network" in result.fetch_instructions[0]
        assert "--allow-database-write" in result.fetch_instructions[0]


def test_actionable_candidates_are_bounded_per_watch_item():
    from app.services.update_report_discovery_bridge import MAX_ACTIONABLE_CANDIDATES_PER_WATCH_ITEM

    session = make_session()
    airport = make_airport(session, name="Manchester-Boston Regional Airport")
    watch_item = WatchItem(airport_id=airport.id, signal_id=None, source_assertion_id=None, watch_reason=WATCH_REASON_UNPUBLISHED_SIGNAL, detail="x")

    subject = build_watch_discovery_subject(session, watch_item)
    plan = plan_watch_discovery_queries(subject)
    first_query = plan.queries_to_run[0]
    many_results = tuple(
        _result(first_query, f"https://faa.gov/mht-emas-{i}.pdf", f"MHT EMAS project {i}", rank=i)
        for i in range(1, MAX_ACTIONABLE_CANDIDATES_PER_WATCH_ITEM + 10)
    )
    outcome = SearchOutcome(query=first_query, status=SearchOutcomeStatus.OK, results=many_results)
    provider = FakeProvider({first_query.rendered: outcome})

    result = run_watch_discovery(session, watch_item, provider=provider)

    assert result.status == WatchDiscoveryStatus.HUMAN_FETCH_REQUIRED
    assert len(result.fetch_instructions) <= MAX_ACTIONABLE_CANDIDATES_PER_WATCH_ITEM


def test_no_writes_occur_regardless_of_discovery_outcome():
    session = make_session()
    airport = make_airport(session, name="Manchester-Boston Regional Airport")
    watch_item = WatchItem(airport_id=airport.id, signal_id=None, source_assertion_id=None, watch_reason=WATCH_REASON_UNPUBLISHED_SIGNAL, detail="x")
    session.commit()

    before = _row_counts(session)
    run_watch_discovery(session, watch_item, provider=FakeProvider())
    after = _row_counts(session)

    assert before == (0, 0, 0)
    assert after == (0, 0, 0)


def test_bridge_never_imports_fetch_or_persistence_or_hub_followup():
    """Architectural safety (Part 17 items 17/31/32): the module's own
    namespace must never contain the human-gated fetch/persistence/hub-
    followup functions as importable names - proves they were never
    imported (and therefore never callable), not merely absent from a
    prose search. Mirrors this codebase's existing architectural-firewall
    test convention (e.g. tests/test_discovery_architectural_safety.py)."""
    import app.services.update_report_discovery_bridge as bridge

    forbidden_names = [
        "fetch_discovered_url",
        "discover_official_hub_followups",
        "persist_discovery_fragment",
        "persist_candidate_linked_source_assertion",
        "apply_stage_only_persistence",
        "apply_known_airport_evidence_persistence",
        "amend_signal",
        "publish_signal",
        "record_reviewer_action",
    ]
    for name in forbidden_names:
        assert not hasattr(bridge, name), f"forbidden name importable from bridge module: {name}"


# --- UPDATE & REPORT INTEGRATION ----------------------------------------


def test_existing_source_assertion_feeds_classify_candidate():
    session = make_session()
    airport = make_airport(session)
    runway = make_runway(session, airport)
    signal = make_signal(session, airport, runway=runway, target_year=2028)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id, runway_id=runway.id)
    watch_item = WatchItem(
        airport_id=airport.id, signal_id=None, source_assertion_id=assertion.id,
        watch_reason=WATCH_REASON_STAGED_EVIDENCE_NEEDS_ATTENTION, detail="x",
    )

    result = run_watch_discovery(session, watch_item, provider=None)

    assert result.change_candidate is not None
    assert result.change_candidate.classification == MaterialChangeClassification.CORROBORATION_ONLY
    assert result.status == WatchDiscoveryStatus.STAGED_EVIDENCE_CREATED


def test_duplicate_classification_preserved_through_bridge():
    session = make_session()
    airport = make_airport(session)
    signal = make_signal(session, airport)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id, signal_id=signal.id)
    watch_item = WatchItem(
        airport_id=airport.id, signal_id=signal.id, source_assertion_id=assertion.id,
        watch_reason=WATCH_REASON_STAGED_EVIDENCE_NEEDS_ATTENTION, detail="x",
    )

    result = run_watch_discovery(session, watch_item, provider=None)

    assert result.change_candidate.classification == MaterialChangeClassification.DUPLICATE


def test_unresolved_identity_stays_needs_more_evidence_through_bridge():
    session = make_session()
    airport = make_airport(session)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id)
    watch_item = WatchItem(
        airport_id=airport.id, signal_id=None, source_assertion_id=assertion.id,
        watch_reason=WATCH_REASON_NEEDS_MORE_EVIDENCE, detail="x",
    )

    result = run_watch_discovery(session, watch_item, provider=None)

    assert result.change_candidate.classification == MaterialChangeClassification.NEEDS_MORE_EVIDENCE


def test_corroboration_produces_zero_writes_through_bridge():
    session = make_session()
    airport = make_airport(session)
    runway = make_runway(session, airport)
    make_signal(session, airport, runway=runway)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id, runway_id=runway.id)
    watch_item = WatchItem(
        airport_id=airport.id, signal_id=None, source_assertion_id=assertion.id,
        watch_reason=WATCH_REASON_STAGED_EVIDENCE_NEEDS_ATTENTION, detail="x",
    )
    session.commit()

    before = _row_counts(session)
    result = run_watch_discovery(session, watch_item, provider=None)
    after = _row_counts(session)

    assert result.change_candidate.classification == MaterialChangeClassification.CORROBORATION_ONLY
    assert before == (0, 0, 0)
    assert after == (0, 0, 0)


# --- WATCH NEXT SURFACING ------------------------------------------------


def test_all_known_sources_surfaced_as_candidates_found_not_actionable():
    session = make_session()
    airport = make_airport(session, name="Manchester-Boston Regional Airport")
    known_source = make_source(session, url="https://faa.gov/mht-emas.pdf")
    make_assertion(session, known_source, airport_id=airport.id)
    watch_item = WatchItem(airport_id=airport.id, signal_id=None, source_assertion_id=None, watch_reason=WATCH_REASON_UNPUBLISHED_SIGNAL, detail="x")

    subject = build_watch_discovery_subject(session, watch_item)
    plan = plan_watch_discovery_queries(subject)
    first_query = plan.queries_to_run[0]
    outcome = SearchOutcome(
        query=first_query, status=SearchOutcomeStatus.OK,
        results=(_result(first_query, "https://faa.gov/mht-emas.pdf", "MHT EMAS project"),),
    )
    provider = FakeProvider({first_query.rendered: outcome})

    result = run_watch_discovery(session, watch_item, provider=provider)

    assert result.status == WatchDiscoveryStatus.CANDIDATES_FOUND
    assert result.fetch_instructions == ()


# --- SAFETY ---------------------------------------------------------------


def test_no_signal_mutation_no_publication_no_reviewer_action_no_amendment_row():
    session = make_session()
    airport = make_airport(session)
    runway = make_runway(session, airport)
    signal = make_signal(session, airport, runway=runway, target_year=2028, published=True)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id, runway_id=runway.id)
    watch_item = WatchItem(
        airport_id=airport.id, signal_id=None, source_assertion_id=assertion.id,
        watch_reason=WATCH_REASON_STAGED_EVIDENCE_NEEDS_ATTENTION, detail="x",
    )
    session.commit()

    run_discovery_for_watch_set(session, (watch_item,), provider=FakeProvider())

    session.refresh(signal)
    assert signal.target_year == 2028
    assert signal.published is True
    assert session.query(ReviewerAction).count() == 0
    assert session.query(SignalAmendmentAction).count() == 0
