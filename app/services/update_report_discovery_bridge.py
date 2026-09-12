"""RWI HQ "Update & Report V1.1 - Watch -> Discovery Bridge" mission.

    WatchItem (app.services.update_report_watchset)
        -> build_watch_discovery_subject()      [Part 2]
        -> plan_watch_discovery_queries()        [Part 3]
        -> run_watch_discovery()                 [Part 7]
        -> DiscoveryRunResult (non-persisted)
        -> STOP (feeding a DiscoveryRunResult's own `change_candidate` back
           into app.services.update_report_engine.generate_update_report()
           is the CALLER's job - scripts/run_update_report.py - not this
           module's; see Part 9's own "the bridge's job ends at
           SourceAssertion ID ready for Update & Report")

SELECTED INTEGRATION PATH (Part 1 recon) - every name below is an EXISTING,
UNMODIFIED function/type, reused directly from its own module, never
reimplemented:

    app.discovery.identity.AirportIdentity.from_airport()
    app.discovery.query.build_search_plan()
    app.discovery.query.plan_official_domain_document_queries()
    app.services.official_domain_discovery.select_preferred_official_hostname()
    app.services.discovery_candidate_fragment.CandidateFragment
    app.services.discovery_temporal_followup.{AirportSearchContext,
        detect_temporal_triggers, plan_follow_up_queries}
    app.discovery.search.SearchProvider / SearchOutcome / SearchOutcomeStatus
    app.discovery.dedup.deduplicate_results()
    app.discovery.triage.{triage_results, PriorityBand}
    app.services.update_report_change_detection.classify_candidate()

NOT reused: app.services.research_loop.run_research_loop() itself. That
orchestrator requires a ResearchClue (non-empty `evidence_text` that must
trace to real preserved evidence, plus an explicit, caller-decided
`unresolved_dimensions` tuple) - a stronger input shape than a bare
WatchItem can honestly supply in every case (an UNPUBLISHED_SIGNAL watch
item is a Signal, not an unresolved piece of evidence text). This bridge
instead composes the SAME lower-level primitives run_research_loop is
itself built from (query planning, search, dedup, triage) directly, for
the narrower "what should we look for, for this watch item" question -
never re-implementing search, dedup, or triage logic.

HUMAN GATES PRESERVED, UNCHANGED (Part 6/13): this module NEVER calls
app.services.generic_web_fetch.fetch_discovered_url(), never imports
scripts.fetch_research_candidate or scripts.review_fragment_selection, and
never calls any of the three governed evidence-persistence services
(app.services.discovery_evidence_persistence /
stage_only_evidence_persistence / known_airport_evidence_persistence). A
live SearchResult can only ever produce HUMAN_FETCH_REQUIRED guidance (a
literal, copy-pasteable `fetch_research_candidate.py` command) here - never
a Snapshot, a persisted CandidateFragment, or a SourceAssertion. The
existing operator workflow (fetch -> review_fragment_selection --keep ->
one of the three persistence services) is completely unchanged.

SEARCHRESULT IS NOT EVIDENCE (Part 5): nothing in this module ever
constructs a SourceAssertion, a Signal, or a field change from a
SearchResult/TriagedResult. The only path from a WatchItem to a
ChangeCandidateResult here is through an ALREADY-EXISTING, already-governed
`SourceAssertion` (`WatchItem.source_assertion_id`) - the exact "no
generic text-to-Signal-field extraction" limitation Part 10 requires stays
intact, because `classify_candidate()` itself (unmodified, see
app.services.update_report_change_detection) is the only thing that ever
turns a SourceAssertion into a classification.

BOUNDED BY DESIGN (Part 4): `MAX_LIVE_QUERIES_PER_WATCH_ITEM` caps how many
queries are ever actually sent to a live SearchProvider per watch item -
independent of how many are shown in a plan-only (`provider=None`) run,
so an operator can always see the FULL relevant query set without that
visibility ever entailing more live network calls. Official-domain hub
follow-up (app.services.official_hub_followup) is deliberately NOT called
here - it is an internal detail of run_research_loop(follow_up_official_hubs=True),
which this bridge does not invoke; wiring it in is out of scope for V1.1
(documented caveat, not a silent gap).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Union

from sqlalchemy.orm import Session

from app.discovery.dedup import deduplicate_results, normalize_url
from app.discovery.identity import AirportIdentity
from app.discovery.query import SearchQuery, build_search_plan, plan_official_domain_document_queries
from app.discovery.search import SearchOutcome, SearchOutcomeStatus, SearchProvider, SearchResult
from app.discovery.triage import PriorityBand, TriagedResult, triage_results
from app.models.airport import Airport
from app.models.source import Source
from app.models.source_assertion import SourceAssertion
from app.services.discovery_candidate_fragment import CandidateFragment, CandidateFragmentError
from app.services.discovery_temporal_followup import (
    AirportSearchContext,
    detect_temporal_triggers,
    plan_follow_up_queries,
)
from app.services.official_domain_discovery import select_preferred_official_hostname
from app.services.update_report_change_detection import (
    CandidateEvidenceInput,
    ChangeCandidateResult,
    UpdateReportChangeDetectionError,
    classify_candidate,
)
from app.services.update_report_watchset import WATCH_REASON_NEEDS_MORE_EVIDENCE, WatchItem

__all__ = [
    "MAX_LIVE_QUERIES_PER_WATCH_ITEM",
    "WatchDiscoveryStatus",
    "WatchDiscoverySubject",
    "WatchDiscoverySkipped",
    "WatchDiscoveryQueryPlan",
    "KnownSourceMatch",
    "DiscoveryRunResult",
    "build_watch_discovery_subject",
    "plan_watch_discovery_queries",
    "run_watch_discovery",
    "run_discovery_for_watch_set",
]

# Deliberately small (Part 4: "V1.1 defaults should be conservative"),
# matching the existing "≤3 queries/airport" discipline already established
# by app.services.official_domain_discovery's own bounded query budget -
# a little more generous here (5) because a watch item's query set is
# already narrowed to at most three families (official-domain, temporal
# follow-up, standard), never all of them blindly.
MAX_LIVE_QUERIES_PER_WATCH_ITEM = 5

# Bounds how many HUMAN_FETCH_REQUIRED fetch instructions are ever surfaced
# per watch item (Part 4/6). Real-data finding (SDF live benchmark, Part
# 14): a `site:<official domain>` query legitimately returns many pages on
# that domain, and app.discovery.triage's own EXISTING, unmodified scoring
# already promotes ANY same-domain result to at least MEDIUM band (its
# "official domain matched" compatibility axis) - correct behavior for
# triage, but without a presentation cap here it would print dozens of
# "official domain, so worth a look" fetch commands per watch item, which
# is exactly the "make the loop look automatic through noise" failure Part
# 6 forbids, even though nothing is actually fetched automatically.
# triage_results() itself already returns results in deterministic
# (band, -points, rank, url) order (its own docstring) - this cap only
# ever slices that existing order, never re-ranks or re-scores anything.
MAX_ACTIONABLE_CANDIDATES_PER_WATCH_ITEM = 5

# The one concept term ever used for temporal follow-up detection here.
# NOT a new semantic invention: "EMAS" is already the sole hardcoded
# concept in app.discovery.query.plan_official_domain_document_queries()
# (every one of its 4 fixed queries is "EMAS"/"EMAS presentation"/"EMAS
# capital program"/"EMAS board") and is this whole system's own primary
# domain concept end-to-end - reusing it here is the same reuse-not-invent
# discipline, not a new decision.
_TEMPORAL_FOLLOWUP_CONCEPT_TERM = "EMAS"


class WatchDiscoveryStatus(str, Enum):
    """Part 7's own suggested vocabulary, used verbatim."""

    NO_QUERY = "NO_QUERY"
    # Not in the mission's own "suggested" list, added deliberately (Part 7
    # calls its list "suggested," not exhaustive; Part 11 requires the
    # operator understand WHY an item remains on watch): a plan-only run
    # (`provider=None`) that DID produce a non-empty query plan is a
    # materially different, honest outcome from NO_QUERY ("no queries
    # could even be planned," e.g. missing airport identity) - collapsing
    # the two would silently misreport "N queries planned" watch items as
    # if nothing could be searched for at all.
    PLANNED_ONLY = "PLANNED_ONLY"
    NO_RESULTS = "NO_RESULTS"
    CANDIDATES_FOUND = "CANDIDATES_FOUND"
    HUMAN_FETCH_REQUIRED = "HUMAN_FETCH_REQUIRED"
    # Reachable ONLY when the watch item already carries an existing,
    # governed SourceAssertion (WatchItem.source_assertion_id) - this
    # bridge never creates a NEW SourceAssertion itself (Part 6/13's human
    # gates are never bypassed). The name is the mission's own suggestion;
    # in V1.1 it always means "already-staged evidence was found and
    # routed to Update & Report," never "this bridge just staged
    # something new."
    STAGED_EVIDENCE_CREATED = "STAGED_EVIDENCE_CREATED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class WatchDiscoverySubject:
    """Read-only context resolved from a WatchItem - never fabricated
    (Part 2). `official_domain`/evidence fields are None when not
    available; nothing here is guessed."""

    watch_item: WatchItem
    airport_identity: AirportIdentity
    official_domain: "Optional[str]"
    existing_source_assertion_raw_text: "Optional[str]"
    existing_source_assertion_artifact_identity: "Optional[str]"
    existing_source_assertion_locator: "Optional[str]"


@dataclass(frozen=True)
class WatchDiscoverySkipped:
    """Returned instead of a WatchDiscoverySubject when identity is
    insufficient to safely plan discovery (Part 2) - an explainable
    skip, never a fabricated identity."""

    watch_item: WatchItem
    reason: str


@dataclass(frozen=True)
class WatchDiscoveryQueryPlan:
    """The smallest relevant query set for one watch item (Part 3),
    broken out by family so a report/operator can see WHY each query
    exists, plus one deterministic, bounded `queries_to_run` for live
    execution (Part 4)."""

    subject: WatchDiscoverySubject
    standard_queries: "tuple[SearchQuery, ...]"
    official_domain_queries: "tuple[SearchQuery, ...]"
    temporal_followup_queries: "tuple[SearchQuery, ...]"

    @property
    def all_planned_queries(self) -> "tuple[SearchQuery, ...]":
        return self.official_domain_queries + self.temporal_followup_queries + self.standard_queries

    @property
    def queries_to_run(self) -> "tuple[SearchQuery, ...]":
        """Official-domain queries first (Part 3: UNPUBLISHED_SIGNAL/
        NEEDS_MORE_EVIDENCE prefer "stronger/current official evidence"),
        then temporal follow-up, then the standard set - deduplicated by
        rendered text and capped at MAX_LIVE_QUERIES_PER_WATCH_ITEM. This
        bounds only LIVE execution, never what `all_planned_queries` shows
        an operator in a plan-only run."""
        seen: "set[str]" = set()
        ordered: "list[SearchQuery]" = []
        for query in self.all_planned_queries:
            if query.rendered in seen:
                continue
            seen.add(query.rendered)
            ordered.append(query)
        return tuple(ordered[:MAX_LIVE_QUERIES_PER_WATCH_ITEM])


@dataclass(frozen=True)
class KnownSourceMatch:
    """A live search candidate whose URL already matches a Source this
    airport already has (Part 8) - reportable as known/corroborating,
    never as new intelligence."""

    triaged: TriagedResult
    matched_source_id: int
    matched_source_url: str


@dataclass(frozen=True)
class DiscoveryRunResult:
    """Non-persisted per-watch-item discovery outcome (Part 7)."""

    watch_item: WatchItem
    queries_planned: "tuple[SearchQuery, ...]"
    queries_executed: "tuple[SearchQuery, ...]"
    search_outcomes: "tuple[SearchOutcome, ...]"
    triaged_candidates: "tuple[TriagedResult, ...]"
    known_source_matches: "tuple[KnownSourceMatch, ...]"
    change_candidate: "Optional[ChangeCandidateResult]"
    status: WatchDiscoveryStatus
    notes: "tuple[str, ...]"
    fetch_instructions: "tuple[str, ...]" = ()


def build_watch_discovery_subject(
    session: Session, watch_item: WatchItem,
) -> "Union[WatchDiscoverySubject, WatchDiscoverySkipped]":
    """Read-only. Resolves the Airport row a WatchItem already points at
    (WatchItem.airport_id is always a real Airport id by construction -
    app.services.update_report_watchset.plan_watch_set()) plus, where the
    watch item already has one, its existing SourceAssertion's own raw
    evidence fields - never fabricated, never re-derived from anywhere
    else. Fails closed to WatchDiscoverySkipped when the Airport row is
    missing or has no usable name (a data-integrity edge case, not the
    common path)."""
    airport = session.get(Airport, watch_item.airport_id)
    if airport is None or not airport.name or not airport.name.strip():
        return WatchDiscoverySkipped(
            watch_item=watch_item,
            reason=f"airport {watch_item.airport_id} has no usable identity - cannot safely plan discovery",
        )

    identity = AirportIdentity.from_airport(airport)
    official_domain = select_preferred_official_hostname(session, watch_item.airport_id)

    raw_text = artifact_identity = source_locator = None
    if watch_item.source_assertion_id is not None:
        assertion = session.get(SourceAssertion, watch_item.source_assertion_id)
        if assertion is not None:
            raw_text = assertion.raw_relevant_text
            artifact_identity = assertion.artifact_identity
            source_locator = assertion.source_locator

    return WatchDiscoverySubject(
        watch_item=watch_item,
        airport_identity=identity,
        official_domain=official_domain,
        existing_source_assertion_raw_text=raw_text,
        existing_source_assertion_artifact_identity=artifact_identity,
        existing_source_assertion_locator=source_locator,
    )


def plan_watch_discovery_queries(subject: WatchDiscoverySubject) -> WatchDiscoveryQueryPlan:
    """Pure - no session, no network (Part 3). Chooses the smallest
    relevant query set per watch reason:

    - every watch item: the standard EMAS/RESA/... query set
      (build_search_plan) - the same set scripts/discover_airport_sources.py
      already uses for one airport.
    - a known official domain: + the 4 fixed site: document queries
      (plan_official_domain_document_queries) - "look for stronger/current
      official evidence" (Part 3, UNPUBLISHED_SIGNAL/NEEDS_MORE_EVIDENCE).
    - NEEDS_MORE_EVIDENCE with existing evidence text: + temporal
      follow-up queries (detect_temporal_triggers/plan_follow_up_queries)
      against that evidence's own raw text - "look for evidence that
      resolves the known gap" (Part 3). Never attempted for any other
      watch reason - a not-yet-reviewed staged item has no established
      "gap" to resolve yet, only unread evidence.
    """
    watch_item = subject.watch_item
    standard_queries = tuple(build_search_plan(subject.airport_identity))

    official_domain_queries: "tuple[SearchQuery, ...]" = ()
    if subject.official_domain:
        official_domain_queries = plan_official_domain_document_queries(subject.official_domain)

    temporal_followup_queries: "tuple[SearchQuery, ...]" = ()
    if watch_item.watch_reason == WATCH_REASON_NEEDS_MORE_EVIDENCE and subject.existing_source_assertion_raw_text:
        try:
            fragment = CandidateFragment(
                artifact_identity=subject.existing_source_assertion_artifact_identity
                or f"source_assertion:{watch_item.source_assertion_id}",
                source_locator=subject.existing_source_assertion_locator
                or f"source_assertion:{watch_item.source_assertion_id}",
                raw_text=subject.existing_source_assertion_raw_text,
            )
        except CandidateFragmentError:
            fragment = None
        if fragment is not None:
            context = AirportSearchContext(
                name=subject.airport_identity.name,
                iata_code=subject.airport_identity.iata_code,
                icao_code=subject.airport_identity.icao_code,
            )
            triggers = detect_temporal_triggers(
                fragment, airport_context=context, concept_term=_TEMPORAL_FOLLOWUP_CONCEPT_TERM,
            )
            for trigger in triggers:
                temporal_followup_queries += plan_follow_up_queries(trigger)

    return WatchDiscoveryQueryPlan(
        subject=subject,
        standard_queries=standard_queries,
        official_domain_queries=official_domain_queries,
        temporal_followup_queries=temporal_followup_queries,
    )


def _existing_change_candidate(session: Session, watch_item: WatchItem) -> "Optional[ChangeCandidateResult]":
    """Part 9: if the watch item already has a usable, existing
    SourceAssertion, route it into the existing, UNMODIFIED
    classify_candidate() - the bridge's job ends at "SourceAssertion ID
    ready for Update & Report," never a re-implementation of
    reconciliation/classification."""
    if watch_item.source_assertion_id is None:
        return None
    try:
        return classify_candidate(session, CandidateEvidenceInput(source_assertion_id=watch_item.source_assertion_id))
    except UpdateReportChangeDetectionError:
        return None


def _known_source_ids_and_urls(session: Session, airport_id: int) -> "dict[str, int]":
    """Read-only lookup of this airport's already-known Source URLs
    (normalized), for Part 8 dedup/known-source control. Bounded to this
    one airport - never a full-table scan."""
    rows = (
        session.query(Source.id, Source.url)
        .join(SourceAssertion, SourceAssertion.source_id == Source.id)
        .filter(SourceAssertion.airport_id == airport_id, Source.url.isnot(None))
        .all()
    )
    known: "dict[str, int]" = {}
    for source_id, url in rows:
        if url:
            known[normalize_url(url)] = source_id
    return known


def _split_known_sources(
    session: Session, airport_id: int, triaged: "list[TriagedResult]",
) -> "tuple[tuple[KnownSourceMatch, ...], tuple[TriagedResult, ...]]":
    known_urls = _known_source_ids_and_urls(session, airport_id)
    known_matches: "list[KnownSourceMatch]" = []
    new_candidates: "list[TriagedResult]" = []
    for candidate in triaged:
        key = normalize_url(candidate.deduped.result.url)
        source_id = known_urls.get(key)
        if source_id is not None:
            known_matches.append(
                KnownSourceMatch(
                    triaged=candidate, matched_source_id=source_id, matched_source_url=candidate.deduped.result.url,
                )
            )
        else:
            new_candidates.append(candidate)
    return tuple(known_matches), tuple(new_candidates)


def _fetch_instruction(url: str) -> str:
    return f"python -m scripts.fetch_research_candidate {url} --allow-live-network --allow-database-write"


def run_watch_discovery(
    session: Session, watch_item: WatchItem, *, provider: "Optional[SearchProvider]" = None,
) -> DiscoveryRunResult:
    """Runs (or plans, if `provider` is None) discovery for exactly one
    watch item. `provider=None` performs ZERO network calls - the same
    "plan-only by default" convention app.services.research_loop.run_research_loop()
    already uses. Never writes to the database, never fetches a document,
    never creates a SourceAssertion (Part 5/6/13) - the only write-adjacent
    action possible here is classify_candidate()'s own read-only
    classification of an ALREADY-EXISTING SourceAssertion."""
    subject_or_skip = build_watch_discovery_subject(session, watch_item)
    change_candidate = _existing_change_candidate(session, watch_item)

    if isinstance(subject_or_skip, WatchDiscoverySkipped):
        return DiscoveryRunResult(
            watch_item=watch_item, queries_planned=(), queries_executed=(), search_outcomes=(),
            triaged_candidates=(), known_source_matches=(), change_candidate=change_candidate,
            status=WatchDiscoveryStatus.NO_QUERY, notes=(subject_or_skip.reason,),
        )
    subject = subject_or_skip

    plan = plan_watch_discovery_queries(subject)
    queries_to_run = plan.queries_to_run

    if provider is None:
        notes = []
        if change_candidate is not None:
            notes.append(
                f"existing SourceAssertion {watch_item.source_assertion_id} already available - "
                f"routed to Update & Report (classification={change_candidate.classification.value})"
            )
            status = WatchDiscoveryStatus.STAGED_EVIDENCE_CREATED
        elif queries_to_run:
            notes.append(
                f"{len(queries_to_run)} of {len(plan.all_planned_queries)} planned discovery quer"
                f"{'y' if len(queries_to_run) == 1 else 'ies'} would run live - not authorized this run (plan-only)"
            )
            status = WatchDiscoveryStatus.PLANNED_ONLY
        else:
            notes.append("no discovery queries could be planned for this watch item")
            status = WatchDiscoveryStatus.NO_QUERY
        return DiscoveryRunResult(
            watch_item=watch_item, queries_planned=plan.all_planned_queries, queries_executed=(), search_outcomes=(),
            triaged_candidates=(), known_source_matches=(), change_candidate=change_candidate,
            status=status, notes=tuple(notes),
        )

    if not queries_to_run:
        return DiscoveryRunResult(
            watch_item=watch_item, queries_planned=(), queries_executed=(), search_outcomes=(),
            triaged_candidates=(), known_source_matches=(), change_candidate=change_candidate,
            status=WatchDiscoveryStatus.STAGED_EVIDENCE_CREATED if change_candidate is not None else WatchDiscoveryStatus.NO_QUERY,
            notes=("no discovery queries could be planned for this watch item",),
        )

    outcomes = tuple(provider.search(query) for query in queries_to_run)
    failures = tuple(o for o in outcomes if o.status == SearchOutcomeStatus.PROVIDER_FAILURE)
    if failures:
        return DiscoveryRunResult(
            watch_item=watch_item, queries_planned=queries_to_run, queries_executed=queries_to_run,
            search_outcomes=outcomes, triaged_candidates=(), known_source_matches=(), change_candidate=change_candidate,
            status=WatchDiscoveryStatus.BLOCKED,
            notes=tuple(f"provider failure for query {o.query.rendered!r}: {o.error}" for o in failures),
        )

    all_results: "list[SearchResult]" = [result for outcome in outcomes for result in outcome.results]
    if not all_results:
        return DiscoveryRunResult(
            watch_item=watch_item, queries_planned=queries_to_run, queries_executed=queries_to_run,
            search_outcomes=outcomes, triaged_candidates=(), known_source_matches=(), change_candidate=change_candidate,
            status=WatchDiscoveryStatus.STAGED_EVIDENCE_CREATED if change_candidate is not None else WatchDiscoveryStatus.NO_RESULTS,
            notes=("live search executed, zero results across all planned queries",),
        )

    deduped = deduplicate_results(all_results)
    official_domains = frozenset({subject.official_domain}) if subject.official_domain else frozenset()
    triaged = triage_results(deduped, identity=subject.airport_identity, official_domains=official_domains)
    known_matches, new_candidates = _split_known_sources(session, watch_item.airport_id, triaged)

    if not new_candidates:
        return DiscoveryRunResult(
            watch_item=watch_item, queries_planned=queries_to_run, queries_executed=queries_to_run,
            search_outcomes=outcomes, triaged_candidates=tuple(triaged), known_source_matches=known_matches,
            change_candidate=change_candidate, status=WatchDiscoveryStatus.CANDIDATES_FOUND,
            notes=("every candidate result already matches a known, already-preserved Source for this airport - no new intelligence",),
        )

    actionable = tuple(c for c in new_candidates if c.band in (PriorityBand.HIGH, PriorityBand.MEDIUM))
    if actionable:
        shown = actionable[:MAX_ACTIONABLE_CANDIDATES_PER_WATCH_ITEM]
        note = f"{len(actionable)} new candidate(s) found requiring human review before fetch"
        if len(actionable) > len(shown):
            note += f" (showing top {len(shown)}, bounded by MAX_ACTIONABLE_CANDIDATES_PER_WATCH_ITEM)"
        return DiscoveryRunResult(
            watch_item=watch_item, queries_planned=queries_to_run, queries_executed=queries_to_run,
            search_outcomes=outcomes, triaged_candidates=tuple(triaged), known_source_matches=known_matches,
            change_candidate=change_candidate, status=WatchDiscoveryStatus.HUMAN_FETCH_REQUIRED,
            notes=(note,),
            fetch_instructions=tuple(_fetch_instruction(c.deduped.result.url) for c in shown),
        )

    return DiscoveryRunResult(
        watch_item=watch_item, queries_planned=queries_to_run, queries_executed=queries_to_run,
        search_outcomes=outcomes, triaged_candidates=tuple(triaged), known_source_matches=known_matches,
        change_candidate=change_candidate, status=WatchDiscoveryStatus.CANDIDATES_FOUND,
        notes=("candidates found, none scored HIGH/MEDIUM triage priority",),
    )


def run_discovery_for_watch_set(
    session: Session, watch_items: "tuple[WatchItem, ...]", *, provider: "Optional[SearchProvider]" = None,
) -> "tuple[DiscoveryRunResult, ...]":
    """Runs run_watch_discovery() once per watch item, in order. No
    aggregation, no cross-item logic - each watch item's discovery is
    fully independent (Part 4: bounded per-item, never a shared/escalating
    budget)."""
    return tuple(run_watch_discovery(session, item, provider=provider) for item in watch_items)
