"""The first governed persistence bridge from AI/web-discovery candidates
to real RWI evidence (docs/architecture/ai-discovery-governed-evidence-persistence-report.md).

Connects the already-committed pure discovery components to the existing
Source/SourceAssertion models:

    CandidateFragment (app.services.discovery_candidate_fragment)
        -> EvidenceBag (candidate_fragment_to_evidence_bag)
        -> AttachmentDecision (app.services.evidence_attachment_guard)
        -> Source / SourceAssertion (this module)

This module performs NO web search, NO fetch, NO parsing, NO AI
extraction, NO database-wide airport resolution, and creates NO Signal,
NO Airport/Runway/RunwayEnd, NO Installation, NO PhysicalInstallationIdentity.
It receives an already-built CandidateFragment and a small set of already-
resolved candidate airports, and persists exactly what the deterministic
guard decided - nothing more.

The service never commits and never imports app.database.SessionLocal -
it mutates the caller-supplied Session and flushes only enough to obtain
row ids; the caller owns the transaction boundary entirely (per the
design's own "no hidden commits" requirement).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Source, SourceAssertion
from app.services.discovery_candidate_fragment import CandidateFragment, candidate_fragment_to_evidence_bag
from app.services.evidence_attachment_guard import (
    AttachmentOutcome,
    CandidateAirport,
    evaluate_attachment_for_candidates,
)

__all__ = [
    "DiscoverySourceMetadata",
    "DiscoveryPersistenceResult",
    "persist_discovery_fragment",
]

# Discovery-created SourceAssertion rows use this assertion_type uniformly
# (one of the six already-governed ASSERTION_TYPES values,
# app/models/source_assertion.py) - matches the exact bucket
# scripts/import_usaspending_grants.py already uses for its own
# identity-unresolved rows. Not made configurable in this slice: no
# worked case needs a different value, and a richer discovery-specific
# taxonomy is future work, not invented speculatively here.
_ASSERTION_TYPE = "project_construction"
_EVIDENCE_QUALITY = "unverified_candidate"  # every discovery row is unreviewed by construction
_REVIEW_STATE = "unreviewed"

# Priority used only to pick the ONE decision this module persists when
# more than one candidate airport was evaluated (task S2 step 5: "create
# exactly one governed SourceAssertion representing the fragment"). Lower
# number wins. A real ATTACH_CONFIRMED/ATTACH_PROVISIONAL always outranks
# every REJECT_CROSS_AIRPORT/INSUFFICIENT_IDENTITY found for OTHER
# candidates evaluated in the same call - this is exactly the SFO/MSP
# contract (task S12): SFO rejects, MSP confirms, the persisted evidence
# reflects MSP. evaluate_attachment_for_candidates() itself already
# guarantees at most one candidate can carry ATTACH_CONFIRMED or
# ATTACH_PROVISIONAL in its returned decisions (ambiguous cases are
# already converted to REVIEW_REQUIRED for every qualifying candidate
# before this module ever sees them) - so "the" winner at either of those
# two priority levels is always unique, never arbitrarily chosen among
# several.
_OUTCOME_PRIORITY = {
    AttachmentOutcome.ATTACH_CONFIRMED: 0,
    AttachmentOutcome.ATTACH_PROVISIONAL: 1,
    AttachmentOutcome.REVIEW_REQUIRED: 2,
    AttachmentOutcome.REJECT_CROSS_AIRPORT: 3,
    AttachmentOutcome.INSUFFICIENT_IDENTITY: 4,
}


def _join_or_none(values) -> str | None:
    values = [v for v in values if v]
    if not values:
        return None
    return ", ".join(sorted(values))


@dataclass(frozen=True)
class DiscoverySourceMetadata:
    """What is needed to create/reuse a Source row for one discovered
    document (task S6/S7). `document_identity` is the ONLY field used to
    build Source.external_id (namespaced "discovery:{document_identity}",
    matching the existing "usaspending:..."/"faa_nasr:..." convention) -
    it MUST be derived from acquired-resource identity (an
    AcquisitionSource.key + Snapshot.sha256 composite, a canonical URL, or
    an equivalent stable document identifier), NEVER from a search query,
    seed airport, or crawler name alone (task S7's own explicit
    prohibition). This module never validates document_identity against
    AcquisitionSource/Snapshot itself - it is an opaque, caller-supplied
    string, exactly like CandidateFragment.artifact_identity."""

    document_identity: str
    title: str
    source_type: str = "web_discovery"
    publisher: str | None = None
    url: str | None = None
    published_date: date | None = None
    reliability_level: str = "unverified"


@dataclass(frozen=True)
class DiscoveryPersistenceResult:
    """Deterministic, ORM-free summary of what persist_discovery_fragment()
    did (task S19) - never exposes ORM instances directly."""

    source_id: int
    source_created: bool
    source_assertion_id: int
    source_assertion_created: bool
    outcome: AttachmentOutcome
    reason: str
    attached_airport_id: int | None
    evaluated_candidate_ids: tuple[object, ...] = field(default_factory=tuple)


def _get_or_create_source(session: Session, metadata: DiscoverySourceMetadata) -> tuple[Source, bool]:
    external_id = f"discovery:{metadata.document_identity}"
    existing = session.scalar(select(Source).where(Source.external_id == external_id))
    if existing is not None:
        return existing, False
    source = Source(
        title=metadata.title,
        source_type=metadata.source_type,
        publisher=metadata.publisher,
        url=metadata.url,
        published_date=metadata.published_date,
        reliability_level=metadata.reliability_level,
        external_id=external_id,
    )
    session.add(source)
    session.flush()
    return source, True


def _get_existing_assertion(session: Session, source_id: int, fragment: CandidateFragment) -> SourceAssertion | None:
    """Reuses SourceAssertion's own existing fragment-identity fields and
    the exact tuple its DB-level UniqueConstraint already enforces
    (source_id, artifact_identity, source_locator, raw_fragment_hash) -
    task S8's idempotency requirement: the same fragment rediscovered
    through a different search/channel must not create a second row."""
    return session.scalar(
        select(SourceAssertion).where(
            SourceAssertion.source_id == source_id,
            SourceAssertion.artifact_identity == fragment.artifact_identity,
            SourceAssertion.source_locator == fragment.source_locator,
            SourceAssertion.raw_fragment_hash == fragment.fragment_hash,
        )
    )


def _select_primary(
    decisions: "dict[object, object]",
) -> "tuple[object | None, AttachmentOutcome, str]":
    """Picks the ONE (candidate_id_or_None, outcome, reason) this module
    persists, per the priority in _OUTCOME_PRIORITY above."""
    best_priority = min(_OUTCOME_PRIORITY[d.outcome] for d in decisions.values())
    winners = {cid: d for cid, d in decisions.items() if _OUTCOME_PRIORITY[d.outcome] == best_priority}
    outcome = next(iter(winners.values())).outcome

    if outcome in (AttachmentOutcome.ATTACH_CONFIRMED, AttachmentOutcome.ATTACH_PROVISIONAL):
        # Exactly one, by construction of evaluate_attachment_for_candidates()
        # (see _OUTCOME_PRIORITY's own docstring above).
        ((candidate_id, decision),) = winners.items()
        return candidate_id, outcome, decision.reason

    # REVIEW_REQUIRED / REJECT_CROSS_AIRPORT / INSUFFICIENT_IDENTITY:
    # never attach an airport_id; combine every winning candidate's own
    # reason into one persisted, human-readable string rather than
    # picking one arbitrarily or creating multiple rows (task S11/S15 -
    # a real multi-candidate table is explicitly deferred).
    ordered = sorted(winners.items(), key=lambda kv: str(kv[0]))
    reason = "; ".join(f"[candidate {cid}] {decision.reason}" for cid, decision in ordered)
    return None, outcome, reason


def persist_discovery_fragment(
    session: Session,
    source_metadata: DiscoverySourceMetadata,
    fragment: CandidateFragment,
    candidate_airports: Sequence[CandidateAirport],
) -> DiscoveryPersistenceResult:
    """Governed bridge from one already-extracted CandidateFragment to
    persisted RWI evidence. Never commits; the caller owns the
    transaction (task S18). Never creates a Signal or any canonical fact
    row (task S16/S17) - only ever touches Source and SourceAssertion.

    1. CandidateFragment -> EvidenceBag (candidate_fragment_to_evidence_bag,
       unmodified, imported not reimplemented).
    2. Runs the deterministic guard against every supplied candidate
       (evaluate_attachment_for_candidates) - never picks a candidate
       itself, never invents a contradiction, never reads search/discovery
       context (structural guarantees already proven for both steps by
       their own test suites).
    3. Selects exactly one outcome to persist, per _OUTCOME_PRIORITY.
    4. Gets-or-creates the Source (task S6/S7 - keyed only by
       source_metadata.document_identity, never by search context).
    5. Gets-or-creates the one SourceAssertion for this fragment (task S8 -
       keyed by the fragment's own identity fields; an existing row is
       reused UNCHANGED, never overwritten by a later, possibly-different
       guard result - SourceAssertion rows are treated as append-only
       evidence here, consistent with InstallationAssertionLink's own
       stronger, DB-enforced immutability elsewhere in this repository).
    """
    evidence = candidate_fragment_to_evidence_bag(fragment)

    if candidate_airports:
        decisions = evaluate_attachment_for_candidates(evidence, list(candidate_airports))
    else:
        decisions = {}

    if decisions:
        primary_airport_id, outcome, reason = _select_primary(decisions)
    else:
        primary_airport_id, outcome, reason = (
            None,
            AttachmentOutcome.INSUFFICIENT_IDENTITY,
            "No candidate airports were supplied for evaluation.",
        )

    source, source_created = _get_or_create_source(session, source_metadata)

    existing_assertion = _get_existing_assertion(session, source.id, fragment)
    if existing_assertion is not None:
        return DiscoveryPersistenceResult(
            source_id=source.id,
            source_created=source_created,
            source_assertion_id=existing_assertion.id,
            source_assertion_created=False,
            outcome=AttachmentOutcome(existing_assertion.identity_guard_decision) if existing_assertion.identity_guard_decision else outcome,
            reason=existing_assertion.identity_guard_reason or reason,
            attached_airport_id=existing_assertion.airport_id,
            evaluated_candidate_ids=tuple(decisions.keys()),
        )

    assertion = SourceAssertion(
        source_id=source.id,
        airport_id=primary_airport_id,
        assertion_type=_ASSERTION_TYPE,
        raw_airport_identifier=_join_or_none(fragment.airport_identifiers),
        raw_airport_name=_join_or_none(fragment.airport_names),
        raw_runway_value=_join_or_none(fragment.runway_pairs),
        raw_runway_end_value=_join_or_none(fragment.runway_ends),
        raw_relevant_text=fragment.raw_text,
        source_locator=fragment.source_locator,
        raw_fragment_hash=fragment.fragment_hash,
        artifact_identity=fragment.artifact_identity,
        parser_identifier=fragment.parser_identifier,
        extracted_at=fragment.extracted_at,
        evidence_quality=_EVIDENCE_QUALITY,
        review_state=_REVIEW_STATE,
        identity_guard_decision=outcome.value,
        identity_guard_reason=reason,
    )
    session.add(assertion)
    session.flush()

    return DiscoveryPersistenceResult(
        source_id=source.id,
        source_created=source_created,
        source_assertion_id=assertion.id,
        source_assertion_created=True,
        outcome=outcome,
        reason=reason,
        attached_airport_id=primary_airport_id,
        evaluated_candidate_ids=tuple(decisions.keys()),
    )
