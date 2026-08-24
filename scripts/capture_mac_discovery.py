"""Gated capture runner for the MAC Granicus discovery pipeline
(docs/product/msp-gated-discovery-capture-runner-dry-run.md).

Orchestrates the already-committed, unmodified pipeline:

    live MAC archive (app.acquisition.mac_granicus)
        -> AcquisitionService / Snapshot (app.services.acquisition, app.models.acquisition)
        -> MAC extractor (app.acquisition.mac_granicus_extractor)
        -> CandidateFragment (app.services.discovery_candidate_fragment)
        -> alternate-airport topology enrichment (app.services.candidate_fragment_enrichment)
        -> EvidenceBag (app.services.discovery_candidate_fragment.candidate_fragment_to_evidence_bag)
        -> Evidence Attachment Guard (app.services.evidence_attachment_guard)
        -> UAC3 discovery-identity orchestration
           (app.services.unknown_airport_discovery_integration.resolve_or_persist_discovery_identity)
           -> exactly one of: known-canonical attachment / ambiguous-known
              identity / governed UnknownAirportCandidate formation /
              unresolved identity (app.services.discovery_evidence_persistence,
              app.services.unknown_airport_candidate_persistence - both
              composed, not reimplemented, by the orchestrator itself)

UAC7 (docs/architecture/rwi-uac7-capture-mac-uac3-wiring-report.md) wired
the apply phase to the UAC3 orchestrator - before UAC7 this runner called
app.services.discovery_evidence_persistence.persist_discovery_fragment()
directly, which has no code path to
app.services.unknown_airport_candidate_persistence.find_or_create_unknown_airport_candidate()
and therefore could never route a fragment naming an airport RWI does not
already know into the governed UnknownAirportCandidate pipeline. This
module still implements NO new pipeline logic of its own beyond that
routing swap - it only wires the above components together, plus a small
amount of genuinely new orchestration (candidate-airport selection,
planning, fingerprinting, CLI safety gates).

SAFETY MODEL (default-closed):
  - No live network unless --allow-live-network.
  - No database write unless BOTH --apply AND --allow-database-write.
  - A real apply additionally requires the discovery migration columns to
    already exist on the target database (checked via the already-
    committed scripts.migrate_discovery_governed_evidence_slice1.inspect(),
    reused, not reimplemented) and a matching --expected-fingerprint,
    computed fresh at apply time - a stale/mismatched fingerprint refuses
    to write.
  - EVERY database operation - schema inspection, the persistence
    session, post-write verification, and the backup source - is bound to
    the SAME explicitly-resolved --database path. This module never
    imports app.database.SessionLocal or app.database.engine (the
    process-global default database) anywhere.

DRY-RUN GUARANTEE: governed-evidence planning
(plan_governed_persistence()) performs ONLY SELECT queries against
Source/SourceAssertion - it never calls session.add()/flush()/commit(),
and therefore works correctly even when the target database does not yet
have the discovery migration's columns (it never references them). Live
document acquisition in dry-run mode calls the provider's own
.retrieve() directly (pure HTTP, no session) rather than
AcquisitionService.acquire() (which always commits) - see
_fetch_document_dry_run() below.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import httpx
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.acquisition.faa import AcquisitionPayload
from app.acquisition.mac_granicus import (
    MACGranicusAcquisitionProvider,
    MACGranicusAgendaItemCandidate,
    MACGranicusMeetingListing,
    discover_agenda_items,
    discover_recent_meetings,
)
from app.acquisition.mac_granicus_extractor import extract_candidate_fragment
from app.models import Airport, AcquisitionRun, AcquisitionSource, PublishingSource, Runway, RunwayEnd, Snapshot, Source, SourceAssertion
from app.services.acquisition import AcquisitionService
from app.services.candidate_fragment_enrichment import enrich_with_alternate_airport_topology
from app.services.discovery_candidate_fragment import CandidateFragment, candidate_fragment_to_evidence_bag
from app.services.discovery_evidence_persistence import DiscoverySourceMetadata
# Reused directly, never duplicated - see module docstring.
from app.services.discovery_evidence_persistence import _select_primary
from app.services.evidence_attachment_guard import (
    AttachmentOutcome,
    CandidateAirport,
    candidate_airport_from_airport_like,
    evaluate_attachment_for_candidates,
)
from app.services.unknown_airport_discovery_integration import (
    DiscoveryIdentityResolutionResult,
    resolve_or_persist_discovery_identity,
)
# Reused directly, never duplicated - see module docstring and
# plan_governed_persistence()'s own docstring for why the PREVIEW path
# needs this exact, pure, read-only formability rule (never a
# runner-specific reimplementation of it).
from app.services.unknown_airport_discovery_integration import _extract_unknown_airport_candidate_seed
# Identity-precedence Option 3 mirror (docs/architecture/rwi-uac3-
# identity-precedence-review.md) - reused directly, same convention as
# the seed-formability import immediately above.
from app.services.unknown_airport_discovery_integration import _any_candidate_has_explicit_identity_match
from app.services.runway_identity import AmbiguousRunwayDesignationError, normalize_end, normalize_pair
from scripts.migrate_discovery_governed_evidence_slice1 import BACKUP_DIRECTORY, backup_database, inspect as inspect_discovery_schema

DEFAULT_DATABASE = Path("data/runway_safe.db")
DEFAULT_VIEW_ID = 4  # metroairports.granicus.com committee view observed to include PD&E meetings
DEFAULT_MAX_RECENT_MEETINGS = 3
PUBLISHER_NAME = "Metropolitan Airports Commission"

# Minimal, explicit, human-reviewed issuer->airport reference
# (docs/architecture/ai-discovery-evidence-attachment-guard.md S11/S12
# slice 4 concept, seeded narrowly for this pilot only). NOT derived from
# provider/source-family identity - used only to supply the guard's own
# `known_issuers` input when building a CandidateAirport; the guard still
# requires the fragment's OWN extracted issuer text to match this before
# any positive evidence is ever recorded (app/services/evidence_attachment_guard.py,
# unmodified). Every existing MSP pilot test already builds CandidateAirport
# this same explicit way.
KNOWN_ISSUER_REFERENCE: dict[str, frozenset[str]] = {
    "MSP": frozenset({PUBLISHER_NAME}),
}

# Explicit, small, human-reviewed supplemental candidate set for THIS
# pilot's own cross-airport safety demonstration
# (docs/product/msp-authoritative-discovery-provider-pilot.md,
# docs/product/cross-airport-evidence-wiring-report.md) - NOT derived
# from any evidence in a fragment, NOT provider identity. Included so the
# guard's cross-airport rejection behavior continues to be exercised even
# for a fragment whose real extracted topology never overlaps with SFO's
# own (which is exactly the case this pilot proves). Deliberately tiny;
# never silently grown - any future addition here is a reviewable code
# change, not an automatic inference.
#
# PILOT-SCOPED, NOT A GENERIC SELECTION MECHANISM: this list exists only
# to keep exercising ONE specific, already-proven safety case for this
# one MSP-focused runner. It must NOT be generalized into "the way future
# providers pick supplemental candidates," "a growing list of airports
# worth double-checking," or any other reusable pattern - a future
# provider covering a different authority/region needs its own explicit,
# separately-reviewed decision about whether it needs a supplemental list
# at all, not an entry appended here.
PILOT_SAFETY_CASE_SUPPLEMENTAL_CODES: tuple[str, ...] = ("SFO",)


class CaptureRunnerError(ValueError):
    """Raised for any refuse-to-proceed safety-gate failure."""


# ---------------------------------------------------------------------------
# Explicit database binding - never app.database.SessionLocal/engine.
# ---------------------------------------------------------------------------


def build_engine(database: Path):
    """Builds a fresh SQLAlchemy engine bound to EXACTLY the resolved
    `database` path - the only database-binding function in this module.
    Every other function that needs a session receives one built from
    this engine, explicitly, as a parameter - never a process-global."""
    resolved = database.resolve()
    engine = create_engine(f"sqlite:///{resolved}", connect_args={"check_same_thread": False}, future=True)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    return engine


# ---------------------------------------------------------------------------
# Candidate-airport selection (task S8) - topology-driven, bounded, plus a
# small explicit pilot supplemental list. Selection never decides
# attachment - every candidate still passes through the unmodified guard.
# ---------------------------------------------------------------------------


def _normalized_ends(tokens: frozenset[str]) -> set[str]:
    result: set[str] = set()
    for token in tokens:
        try:
            result.add(normalize_end(token))
        except AmbiguousRunwayDesignationError:
            continue
    return result


def _normalized_pairs(tokens: frozenset[str]) -> set[str]:
    result: set[str] = set()
    for token in tokens:
        try:
            result.add(normalize_pair(token))
        except AmbiguousRunwayDesignationError:
            continue
    return result


def _topology_matching_airport_ids(session: Session, fragment: CandidateFragment) -> set[int]:
    """A single, targeted, indexed-WHERE query against the fragment's OWN
    extracted runway tokens - not a blind scan of every airport's full
    topology in application code."""
    ends = _normalized_ends(fragment.runway_ends)
    pairs = _normalized_pairs(fragment.runway_pairs)
    ids: set[int] = set()
    if ends:
        ids |= set(
            session.scalars(
                select(Runway.airport_id).join(RunwayEnd, RunwayEnd.runway_id == Runway.id)
                .where(RunwayEnd.designation.in_(ends))
            ).all()
        )
    if pairs:
        ids |= set(
            session.scalars(select(Runway.airport_id).where(Runway.designation.in_(pairs))).all()
        )
    return ids


def select_candidate_airports(session: Session, fragment: CandidateFragment) -> list[CandidateAirport]:
    """Returns the narrow candidate set to evaluate this fragment against:
    (1) airports whose real canonical topology genuinely overlaps with
    the fragment's own extracted runway tokens (deterministic, evidence-
    driven, bounded query), UNION (2) PILOT_SAFETY_CASE_SUPPLEMENTAL_CODES
    (explicit, tiny, documented - see module docstring). Building a
    CandidateAirport for a code never makes it "the" answer - only the
    unmodified evaluate_attachment_for_candidates() call downstream does."""
    airport_ids = _topology_matching_airport_ids(session, fragment)
    airports: list[Airport] = list(session.scalars(select(Airport).where(Airport.id.in_(airport_ids)))) if airport_ids else []
    present_codes = {a.faa_code or a.iata_code or a.icao_code for a in airports}

    for code in PILOT_SAFETY_CASE_SUPPLEMENTAL_CODES:
        if code in present_codes:
            continue
        supplemental = session.scalar(
            select(Airport).where(
                (Airport.faa_code == code) | (Airport.iata_code == code) | (Airport.icao_code == code)
            )
        )
        if supplemental is not None:
            airports.append(supplemental)

    candidates: list[CandidateAirport] = []
    for airport in airports:
        code = airport.faa_code or airport.iata_code or airport.icao_code
        known_issuers = KNOWN_ISSUER_REFERENCE.get(code, frozenset())
        candidates.append(candidate_airport_from_airport_like(airport, known_issuers=known_issuers))
    return candidates


# ---------------------------------------------------------------------------
# Guard evaluation with two-pass alternate-airport enrichment (task S7).
# ---------------------------------------------------------------------------


def evaluate_with_enrichment(
    session: Session, fragment: CandidateFragment, candidates: Sequence[CandidateAirport],
) -> tuple[CandidateFragment, "dict[object, object]"]:
    """Pass 1: evaluate the raw, un-enriched fragment. If exactly one
    candidate independently reaches ATTACH_CONFIRMED (the fragment's own
    guard-confirmed "home" airport - never assumed from provider
    identity), enrich the fragment with THAT airport's real canonical
    topology (read from the target DB, read-only) via the committed
    app.services.candidate_fragment_enrichment helper, then re-evaluate.
    If pass 1 does not produce exactly one confirmed candidate, nothing is
    enriched and pass 1's own result is final - enrichment never invents
    a "home" airport the guard itself didn't already confirm."""
    if not candidates:
        return fragment, {}

    bag = candidate_fragment_to_evidence_bag(fragment)
    first_pass = evaluate_attachment_for_candidates(bag, list(candidates))
    confirmed_ids = [cid for cid, decision in first_pass.items() if decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED]
    if len(confirmed_ids) != 1:
        return fragment, first_pass

    home_airport = session.get(Airport, confirmed_ids[0])
    if home_airport is None:
        return fragment, first_pass

    home_ends = {end.designation for runway in home_airport.runways for end in runway.runway_ends}
    home_pairs = {runway.designation for runway in home_airport.runways}
    enriched_fragment = enrich_with_alternate_airport_topology(
        fragment,
        known_other_airport_runway_ends=frozenset(home_ends),
        known_other_airport_runway_pairs=frozenset(home_pairs),
    )
    enriched_bag = candidate_fragment_to_evidence_bag(enriched_fragment)
    second_pass = evaluate_attachment_for_candidates(enriched_bag, list(candidates))
    return enriched_fragment, second_pass


# ---------------------------------------------------------------------------
# Planned governed-evidence snapshot (task S10) - read-only, schema-tolerant.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedGovernedEvidence:
    document_identity: str
    fragment_identity: tuple[str, str, str]
    guard_outcome: str
    guard_reason: str
    attached_airport_id: "int | None"
    attached_airport_code: "str | None"
    source_external_id: str
    source_would_be_created: bool
    source_id_if_existing: "int | None"
    source_assertion_would_be_created: bool
    source_assertion_id_if_existing: "int | None"
    # UAC7: whether apply would route this fragment into UAC3's governed
    # UnknownAirportCandidate branch instead of the known-airport/
    # ambiguous/unresolved path - see plan_governed_persistence()'s own
    # docstring. Defaulted so existing direct-construction callers/tests
    # that predate UAC7 remain valid.
    would_form_unknown_airport_candidate: bool = False


def plan_governed_persistence(
    session: Session, document_identity: str, fragment: CandidateFragment, decisions: "dict[object, object]",
) -> PlannedGovernedEvidence:
    """Read-only: performs ONLY SELECT queries against Source/
    SourceAssertion using their pre-existing columns (external_id,
    artifact_identity, source_locator, raw_fragment_hash) - never
    references identity_guard_decision/identity_guard_reason, so this
    function works correctly whether or not the discovery migration has
    been applied to `session`'s target database. Never calls
    session.add()/flush()/commit() - zero ORM mutations, provably (see
    tests/test_capture_mac_discovery.py).

    UAC7: also previews whether apply would route this fragment into
    UAC3's UnknownAirportCandidate branch - reusing (never duplicating)
    resolve_or_persist_discovery_identity()'s own routing rule: no known
    candidate accepted the evidence (REJECT_CROSS_AIRPORT or
    INSUFFICIENT_IDENTITY - `_select_primary`'s priority ordering already
    ranks CONFIRMED/PROVISIONAL/REVIEW_REQUIRED strictly ahead of both, so
    reaching either one here means the same "no known match" bucket the
    orchestrator itself checks) AND the fragment's own extracted identity
    is independently formable (_extract_unknown_airport_candidate_seed() -
    a pure function of `fragment` alone, no I/O, imported not
    reimplemented). This is included in the fingerprint below precisely
    so a state change between preview and apply that would flip this
    routing decision is detected as a plan mismatch, never applied
    silently under a stale preview (task S16/S17).

    IDENTITY-PRECEDENCE OPTION 3 mirror (docs/architecture/rwi-uac3-
    identity-precedence-review.md S14/S16): the orchestrator's own known-
    or-ambiguous bucket is ALSO bypassed here when the fragment carries a
    formable name claim that no supplied candidate's positive evidence
    corroborates (_any_candidate_has_explicit_identity_match(), reused -
    not reimplemented). When this override applies, `candidate_id` is
    reset to None - apply will not attach to that airport, so the preview
    must not claim it would, and the resulting `attached_airport_id`/
    `attached_airport_code` fields (both part of the fingerprint below)
    must not silently disagree with what resolve_or_persist_discovery_identity()
    will actually do. `outcome`/`reason` (guard_outcome/guard_reason in the
    returned plan) are deliberately left as the raw, unmodified guard
    result - exactly like DiscoveryIdentityResolutionResult.attachment_outcome
    does for the same case - so a human reading the preview can see both
    the underlying guard verdict and the fact that the override changed
    the routing."""
    if decisions:
        candidate_id, outcome, reason = _select_primary(decisions)
    else:
        candidate_id, outcome, reason = None, AttachmentOutcome.INSUFFICIENT_IDENTITY, "No candidate airports were supplied for evaluation."

    seed_formable = _extract_unknown_airport_candidate_seed(fragment) is not None
    known_or_ambiguous = outcome in (
        AttachmentOutcome.ATTACH_CONFIRMED, AttachmentOutcome.ATTACH_PROVISIONAL, AttachmentOutcome.REVIEW_REQUIRED,
    )
    if (
        known_or_ambiguous
        and decisions
        and seed_formable
        and not _any_candidate_has_explicit_identity_match(decisions)
    ):
        known_or_ambiguous = False
        candidate_id = None

    would_form_unknown_airport_candidate = not known_or_ambiguous and seed_formable

    attached_code = None
    if candidate_id is not None:
        airport = session.get(Airport, candidate_id)
        if airport is not None:
            attached_code = airport.faa_code or airport.iata_code or airport.icao_code

    external_id = f"discovery:{document_identity}"
    existing_source = session.scalar(select(Source).where(Source.external_id == external_id))
    existing_assertion = None
    if existing_source is not None:
        existing_assertion = session.scalar(
            select(SourceAssertion).where(
                SourceAssertion.source_id == existing_source.id,
                SourceAssertion.artifact_identity == fragment.artifact_identity,
                SourceAssertion.source_locator == fragment.source_locator,
                SourceAssertion.raw_fragment_hash == fragment.fragment_hash,
            )
        )

    return PlannedGovernedEvidence(
        document_identity=document_identity,
        fragment_identity=fragment.identity,
        guard_outcome=outcome.value,
        guard_reason=reason,
        attached_airport_id=candidate_id,
        attached_airport_code=attached_code,
        source_external_id=external_id,
        source_would_be_created=existing_source is None,
        source_id_if_existing=existing_source.id if existing_source else None,
        source_assertion_would_be_created=existing_assertion is None,
        source_assertion_id_if_existing=existing_assertion.id if existing_assertion else None,
        would_form_unknown_airport_candidate=would_form_unknown_airport_candidate,
    )


def compute_plan_fingerprint(planned: Sequence[PlannedGovernedEvidence]) -> str:
    """Deterministic over the UPSTREAM-CONTENT-DERIVED fields only
    (document/fragment identity, guard outcome, attached airport code,
    Source external id, UAC7's would_form_unknown_airport_candidate
    routing flag) - deliberately excludes target-DB-state fields
    (would_be_created/existing ids) so the same real upstream content
    fingerprints identically regardless of which database it is planned
    against, and identically across repeated runs while upstream content
    is unchanged (task S10). would_form_unknown_airport_candidate is
    itself content-derived (guard_outcome, already in this tuple, plus a
    pure function of the fragment alone - see plan_governed_persistence()),
    not DB-state-derived, so including it does not reintroduce the
    DB-state dependency this fingerprint deliberately avoids; it is
    included explicitly, as its own field, rather than left as an
    implication of guard_outcome alone, so a future change to either
    signal's derivation cannot silently stop being covered by the
    fingerprint (task S16 - the apply-time UAC3 routing decision must
    never be able to drift from what was previewed without detection)."""
    rows = sorted(
        (
            p.document_identity, p.fragment_identity[0], p.fragment_identity[1], p.fragment_identity[2],
            p.guard_outcome, p.attached_airport_code or "", p.source_external_id,
            p.would_form_unknown_airport_candidate,
        )
        for p in planned
    )
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Discovery + extraction orchestration - pure HTTP, no DB, any mode.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiscoveredCandidate:
    item: MACGranicusAgendaItemCandidate
    fragment: "CandidateFragment | None"
    vendors: tuple[str, ...] = ()
    fetch_report: "dict | None" = None


# ---------------------------------------------------------------------------
# Acquisition-layer document fetch: dry-run (no DB) vs apply (real Snapshot).
# ---------------------------------------------------------------------------


def snapshot_change_status_dry_run(session: Session, item: MACGranicusAgendaItemCandidate, payload: AcquisitionPayload) -> str:
    """Read-only: reports NEW_DOCUMENT/UNCHANGED_DOCUMENT/CHANGED_DOCUMENT
    without ever creating an AcquisitionSource/Snapshot row - never calls
    AcquisitionService (which always commits, see module docstring)."""
    digest = hashlib.sha256(payload.content).hexdigest()
    source = session.scalar(select(AcquisitionSource).where(AcquisitionSource.key == item.acquisition_source_key))
    if source is None:
        return "NEW_DOCUMENT"
    existing = session.scalar(
        select(Snapshot).where(
            Snapshot.acquisition_source_id == source.id,
            Snapshot.sha256 == digest,
            Snapshot.byte_size == len(payload.content),
        )
    )
    return "UNCHANGED_DOCUMENT" if existing is not None else "CHANGED_DOCUMENT"


def discover_relevant_fragments(
    session: Session, client: httpx.Client, *,
    view_id: int, max_recent_meetings: int, historical_meeting_clip_ids: Sequence[int] = (),
) -> tuple[list[MACGranicusMeetingListing], list[DiscoveredCandidate]]:
    """Scans a bounded set of real meetings (the N most recent, per the
    archive's own listing, plus any explicitly-requested historical
    clip_ids - archive addressing, never a search query or a hardcoded
    document URL) and extracts a CandidateFragment for every agenda item
    the extractor judges relevant. Non-relevant items are still reported
    (item, fragment=None) so the caller can show what was correctly
    ignored. Every document fetch uses the provider's own .retrieve()
    directly (pure HTTP) - this function never opens an AcquisitionService
    session and therefore never commits, regardless of caller mode; `session`
    is used only for the read-only NEW/UNCHANGED/CHANGED status check."""
    meetings = discover_recent_meetings(client, view_id=view_id, max_meetings=max_recent_meetings)
    seen_clips = {m.clip_id for m in meetings}
    for clip_id in historical_meeting_clip_ids:
        if clip_id in seen_clips:
            continue
        meetings.append(MACGranicusMeetingListing(
            view_id=view_id, clip_id=clip_id, committee_name="(explicitly requested)", meeting_date_raw="(unknown)",
        ))
        seen_clips.add(clip_id)

    discovered: list[DiscoveredCandidate] = []
    for meeting in meetings:
        items = discover_agenda_items(client, meeting)
        for item in items:
            if not item.is_relevant:
                discovered.append(DiscoveredCandidate(item=item, fragment=None))
                continue
            provider = MACGranicusAcquisitionProvider(item.document_url, client=client)
            payload = provider.retrieve()
            fetch_report = {
                "url": item.document_url,
                "http_status": payload.http_status,
                "content_type": payload.content_type,
                "byte_size": len(payload.content),
                "change_status": snapshot_change_status_dry_run(session, item, payload),
            }
            result = extract_candidate_fragment(
                payload.content, payload.content_type,
                artifact_identity=item.acquisition_source_key, source_locator=f"item-{item.item_number}",
                document_title=item.item_title, url=item.document_url,
            )
            if result is None:
                discovered.append(DiscoveredCandidate(item=item, fragment=None, fetch_report=fetch_report))
                continue
            fragment, vendors = result
            discovered.append(DiscoveredCandidate(item=item, fragment=fragment, vendors=vendors, fetch_report=fetch_report))
    return meetings, discovered


def _resolve_or_create_acquisition_source(session: Session, item: MACGranicusAgendaItemCandidate) -> AcquisitionSource:
    publisher = session.scalar(select(PublishingSource).where(PublishingSource.name == PUBLISHER_NAME))
    if publisher is None:
        publisher = PublishingSource(
            name=PUBLISHER_NAME, source_type="government", homepage_url="https://metroairports.org", country_code="US",
            reliability_level="official",
        )
        session.add(publisher)
        session.flush()

    source = session.scalar(select(AcquisitionSource).where(AcquisitionSource.key == item.acquisition_source_key))
    if source is None:
        source = AcquisitionSource(
            publishing_source=publisher, key=item.acquisition_source_key, display_name=item.item_title,
            acquisition_type="http", canonical_url=item.document_url, expected_media_type=None, active=True,
        )
        session.add(source)
        session.flush()
    return source


def acquire_document_for_apply(session: Session, client: httpx.Client, item: MACGranicusAgendaItemCandidate) -> AcquisitionRun:
    """Real acquisition - commits Snapshot/AcquisitionRun/AcquisitionSource
    via the existing, unmodified AcquisitionService, bound to the caller's
    explicit session. Only ever called when --apply --allow-database-write
    are both set."""
    source = _resolve_or_create_acquisition_source(session, item)
    provider = MACGranicusAcquisitionProvider(item.document_url, client=client)
    return AcquisitionService(session, provider).acquire(source)


# ---------------------------------------------------------------------------
# Top-level orchestration.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaptureConfig:
    database: Path = DEFAULT_DATABASE
    allow_live_network: bool = False
    apply: bool = False
    allow_database_write: bool = False
    expected_fingerprint: "str | None" = None
    skip_backup: bool = False
    backup_directory: Path = BACKUP_DIRECTORY
    view_id: int = DEFAULT_VIEW_ID
    max_recent_meetings: int = DEFAULT_MAX_RECENT_MEETINGS
    historical_meeting_clip_ids: tuple[int, ...] = ()
    # Non-live-network mode operates only on caller-supplied fixture
    # documents (task S5: "operate only on fixtures/cached test input or
    # fail with a clear message").
    fixture_documents: tuple["FixtureDocument", ...] = ()


@dataclass(frozen=True)
class FixtureDocument:
    pdf_bytes: bytes
    artifact_identity: str
    source_locator: str
    item_title: "str | None" = None
    document_url: "str | None" = None


def run_capture(config: CaptureConfig, *, client: "httpx.Client | None" = None) -> dict:
    """The single entry point both the CLI and tests use. Every database
    operation is bound to config.database, explicitly, via build_engine()
    - never app.database.SessionLocal."""
    if not config.allow_live_network and not config.fixture_documents:
        raise CaptureRunnerError(
            "NO_LIVE_NETWORK_AND_NO_FIXTURE_PROVIDED: pass --allow-live-network or supply fixture_documents."
        )
    if config.apply and not config.allow_database_write:
        raise CaptureRunnerError("--apply requires --allow-database-write.")
    if config.allow_database_write and not config.apply:
        raise CaptureRunnerError("--allow-database-write requires --apply.")

    report: dict = {
        "database": str(config.database.resolve()),
        "allow_live_network": config.allow_live_network,
        "apply_requested": config.apply,
        "meetings_inspected": [],
        "agenda_items_inspected": 0,
        "agenda_items_relevant": 0,
        "agenda_items_ignored": 0,
        "documents_fetched": [],
        "candidate_fragments": [],
        "planned_governed_evidence": [],
        "plan_fingerprint": None,
        "schema_readiness": None,
        "applied": False,
        "apply_result": [],
        "blockers": [],
    }

    engine = build_engine(config.database)
    schema = inspect_discovery_schema(config.database)
    report["schema_readiness"] = {
        "identity_guard_decision_column_exists": schema["identity_guard_decision_column_exists"],
        "identity_guard_reason_column_exists": schema["identity_guard_reason_column_exists"],
        "source_assertions_count": schema["source_assertions_count"],
    }
    schema_ready = schema["identity_guard_decision_column_exists"] and schema["identity_guard_reason_column_exists"]

    with Session(engine) as session:
        discovered: list[DiscoveredCandidate] = []

        if config.allow_live_network:
            http_client = client or httpx.Client(follow_redirects=True)
            meetings, discovered = discover_relevant_fragments(
                session, http_client, view_id=config.view_id, max_recent_meetings=config.max_recent_meetings,
                historical_meeting_clip_ids=config.historical_meeting_clip_ids,
            )
            report["meetings_inspected"] = [
                {"committee": m.committee_name, "date": m.meeting_date_raw, "clip_id": m.clip_id} for m in meetings
            ]
            report["documents_fetched"] = [dc.fetch_report for dc in discovered if dc.fetch_report is not None]
        else:
            for fixture in config.fixture_documents:
                result = extract_candidate_fragment(
                    fixture.pdf_bytes, "application/pdf",
                    artifact_identity=fixture.artifact_identity, source_locator=fixture.source_locator,
                    document_title=fixture.item_title, url=fixture.document_url,
                )
                pseudo_item = MACGranicusAgendaItemCandidate(
                    view_id=0, clip_id=0, meta_id="fixture", item_number="fixture",
                    item_title=fixture.item_title or fixture.artifact_identity,
                    document_url=fixture.document_url or "",
                    committee_name="(fixture)", meeting_date_raw="(fixture)",
                )
                if result is None:
                    discovered.append(DiscoveredCandidate(item=pseudo_item, fragment=None))
                else:
                    fragment, vendors = result
                    discovered.append(DiscoveredCandidate(item=pseudo_item, fragment=fragment, vendors=vendors))

        report["agenda_items_inspected"] = len(discovered)
        report["agenda_items_relevant"] = sum(1 for d in discovered if d.fragment is not None)
        report["agenda_items_ignored"] = sum(1 for d in discovered if d.fragment is None)

        # --- PLANNING PASS: read-only, always runs, never gated. Builds
        # everything apply (below) needs, but performs no add()/flush() of
        # its own - plan_governed_persistence() only ever SELECTs. ---
        planned: list[PlannedGovernedEvidence] = []
        evaluated: list[tuple[DiscoveredCandidate, list[CandidateAirport], CandidateFragment, "dict[object, object]"]] = []

        for dc in discovered:
            if dc.fragment is None:
                continue
            fragment = dc.fragment
            document_identity = fragment.artifact_identity

            candidates = select_candidate_airports(session, fragment)
            enriched_fragment, decisions = evaluate_with_enrichment(session, fragment, candidates)
            evaluated.append((dc, candidates, enriched_fragment, decisions))

            report["candidate_fragments"].append({
                "item_title": dc.item.item_title,
                "document_url": dc.item.document_url,
                "artifact_identity": fragment.artifact_identity,
                "source_locator": fragment.source_locator,
                "runway_ends": sorted(fragment.runway_ends),
                "runway_pairs": sorted(fragment.runway_pairs),
                "issuers": sorted(fragment.issuers),
                "vendors": list(dc.vendors),
                "money_values": [str(m.numeric_value) for m in fragment.money_values],
                "candidate_airport_codes": sorted({c.name for c in candidates}),
                "decisions": {
                    str(cid): {"outcome": d.outcome.value, "reason": d.reason} for cid, d in decisions.items()
                },
            })

            planned.append(plan_governed_persistence(session, document_identity, enriched_fragment, decisions))

        report["planned_governed_evidence"] = [
            {
                "document_identity": p.document_identity,
                "fragment_identity": list(p.fragment_identity),
                "guard_outcome": p.guard_outcome,
                "guard_reason": p.guard_reason,
                "attached_airport_id": p.attached_airport_id,
                "attached_airport_code": p.attached_airport_code,
                "source_external_id": p.source_external_id,
                "source_would_be_created": p.source_would_be_created,
                "source_assertion_would_be_created": p.source_assertion_would_be_created,
                "would_form_unknown_airport_candidate": p.would_form_unknown_airport_candidate,
            }
            for p in planned
        ]
        fingerprint = compute_plan_fingerprint(planned)
        report["plan_fingerprint"] = fingerprint

        if not config.apply:
            session.rollback()
            return report

        # --- APPLY GATES: must ALL pass before any persistence attempt.
        # Nothing above this point ever called session.add()/flush() for
        # governed evidence, so refusing here leaves the session exactly
        # as clean as a pure dry-run. ---
        if not schema_ready:
            session.rollback()
            report["blockers"].append("DISCOVERY_SCHEMA_MIGRATION_REQUIRED")
            return report
        if config.expected_fingerprint is None or config.expected_fingerprint != fingerprint:
            session.rollback()
            report["blockers"].append(
                f"FINGERPRINT_MISMATCH: expected {config.expected_fingerprint!r}, computed {fingerprint!r}."
            )
            return report

        if not config.skip_backup:
            backup_path = backup_database(config.database, config.backup_directory)
            report["backup_path"] = str(backup_path)

        if config.allow_live_network:
            http_client = client or httpx.Client(follow_redirects=True)
            for dc, _candidates, _fragment, _decisions in evaluated:
                acquire_document_for_apply(session, http_client, dc.item)

        # UAC7: routed through the single UAC3 orchestration entry point
        # (app.services.unknown_airport_discovery_integration.resolve_or_persist_discovery_identity),
        # never persist_discovery_fragment() directly - this is the exact
        # seam that used to make this runner structurally unable to reach
        # the UnknownAirportCandidate branch (see module docstring). This
        # runner still implements none of that routing logic itself - it
        # only calls the one already-reviewed, already-tested orchestrator
        # function, exactly as every other caller of this pipeline does.
        apply_results: list[DiscoveryIdentityResolutionResult] = []
        for dc, candidates, enriched_fragment, _decisions in evaluated:
            meta = DiscoverySourceMetadata(
                document_identity=enriched_fragment.artifact_identity, title=dc.item.item_title,
                publisher=PUBLISHER_NAME, url=dc.item.document_url,
            )
            apply_results.append(resolve_or_persist_discovery_identity(session, meta, enriched_fragment, candidates))

        session.commit()
        report["applied"] = True
        report["apply_result"] = [
            {
                # DiscoveryIdentityOutcome (KNOWN_CANONICAL_ATTACHMENT /
                # AMBIGUOUS_KNOWN_IDENTITY / UNKNOWN_AIRPORT_CANDIDATE /
                # UNRESOLVED_IDENTITY) - the UAC3 routing decision itself,
                # never collapsed into a generic "persisted=True" (task S12).
                "routing_outcome": r.outcome.value,
                # The underlying AttachmentOutcome the routing decision was
                # made from (ATTACH_CONFIRMED / ATTACH_PROVISIONAL /
                # REVIEW_REQUIRED / REJECT_CROSS_AIRPORT / INSUFFICIENT_IDENTITY) -
                # always populated, for audit/debugging (matches
                # DiscoveryIdentityResolutionResult.attachment_outcome's own
                # docstring).
                "attachment_outcome": r.attachment_outcome.value,
                "source_id": r.source_id, "source_created": r.source_created,
                "source_assertion_id": r.source_assertion_id, "source_assertion_created": r.source_assertion_created,
                "attached_airport_id": r.attached_airport_id,
                "unknown_airport_candidate_id": r.unknown_airport_candidate_id,
                "unknown_airport_candidate_created": r.unknown_airport_candidate_created,
                "evidence_bag_snapshot_id": r.evidence_bag_snapshot_id,
            }
            for r in apply_results
        ]
        return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--allow-live-network", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-database-write", action="store_true")
    parser.add_argument("--expected-fingerprint", type=str, default=None)
    parser.add_argument("--skip-backup", action="store_true", help="isolated/temp DBs only")
    parser.add_argument("--backup-directory", type=Path, default=BACKUP_DIRECTORY)
    parser.add_argument("--view-id", type=int, default=DEFAULT_VIEW_ID)
    parser.add_argument("--max-recent-meetings", type=int, default=DEFAULT_MAX_RECENT_MEETINGS)
    parser.add_argument(
        "--historical-meeting-clip-id", type=int, action="append", default=[],
        help="Explicit archive clip_id(s) to additionally scan (archive addressing, never a search query).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = CaptureConfig(
        database=args.database,
        allow_live_network=args.allow_live_network,
        apply=args.apply,
        allow_database_write=args.allow_database_write,
        expected_fingerprint=args.expected_fingerprint,
        skip_backup=args.skip_backup,
        backup_directory=args.backup_directory,
        view_id=args.view_id,
        max_recent_meetings=args.max_recent_meetings,
        historical_meeting_clip_ids=tuple(args.historical_meeting_clip_id),
    )
    try:
        report = run_capture(config)
    except CaptureRunnerError as exc:
        print(f"Refused: {exc}")
        return 2
    print(json.dumps(report, indent=2, default=str))
    return 0 if not report.get("blockers") else 1


if __name__ == "__main__":
    raise SystemExit(main())
