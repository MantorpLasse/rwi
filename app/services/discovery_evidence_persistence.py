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
NO Airport/Runway/RunwayEnd, NO Installation, NO PhysicalInstallationIdentity,
NO UnknownAirportCandidate. It receives an already-built CandidateFragment
and a small set of already-resolved candidate airports, and persists
exactly what the deterministic guard decided - nothing more.

UAC2B (docs/architecture/rwi-uac2b-sourceassertion-unknown-airport-integration-report.md,
Slice 2 of docs/architecture/rwi-governed-new-airport-discovery-design.md)
adds `persist_candidate_linked_source_assertion()`: the smallest safe
bridge letting a caller persist a SourceAssertion linked to an
ALREADY-EXISTING, ALREADY-CREATED UnknownAirportCandidate (created
separately, by app.services.unknown_airport_candidate_persistence -
never here). This function performs NO candidate lookup by raw name or
fingerprint, NO candidate creation, and NO automatic canonical match - it
accepts only an explicit, caller-supplied `unknown_airport_candidate_id`
and validates that it refers to a real, already-persisted row before
touching anything. `persist_discovery_fragment()` itself is entirely
unmodified by this addition - it still only ever sets `airport_id`,
never `unknown_airport_candidate_id`, matching its own pre-UAC2B
behavior exactly.

EB3 (docs/architecture/rwi-eb3-evidencebag-discovery-persistence-report.md,
Slice 3 of docs/architecture/rwi-full-evidencebag-persistence-design.md)
adds lossless EvidenceBag snapshot persistence to BOTH functions: every
newly-created SourceAssertion is now persisted together with exactly one
immutable `SourceAssertionEvidenceBag` (app/models/source_assertion_evidence_bag.py)
representing the EXACT `EvidenceBag` value that reached the identity
guard - never a reconstruction, never a second, independently-normalized
copy. `_attach_evidence_bag_snapshot()` is the ONLY code in this module
permitted to construct that row; it owns serialization/hashing/
schema_version entirely (app.services.evidence_bag_serialization) so no
caller can ever supply those independently, closing EB1's own
deliberately-deferred write-time consistency boundary. Both functions
now also require the EB2 persistence schema to already exist
(`_verify_evidence_bag_schema_ready()`) - modern discovery persistence
never silently falls back to a SourceAssertion-only write missing its
flight-recorder snapshot. A legacy SourceAssertion found via the existing
fragment-identity dedup (`_get_existing_assertion()`) is NEVER
retroactively given a snapshot it never had (see
`_reconcile_replay_snapshot()`'s own docstring for why the fragment-
identity match alone is not sufficient proof of full EvidenceBag
equivalence) - but if it already has one, a replay whose current
EvidenceBag content differs from what is already stored is a genuine
provenance conflict and always fails loud
(ConflictingEvidenceBagReplayError), never silently ignored. EB3 creates
no IdentityGuardEvaluation row and never touches
SourceAssertion.identity_guard_decision/identity_guard_reason beyond
their pre-existing assignment - it only ever adds the sibling snapshot.

The service never commits and never imports app.database.SessionLocal -
it mutates the caller-supplied Session and flushes only enough to obtain
row ids; the caller owns the transaction boundary entirely (per the
design's own "no hidden commits" requirement).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Source, SourceAssertion
from app.models.source_assertion_evidence_bag import SourceAssertionEvidenceBag
from app.models.unknown_airport_candidate import UnknownAirportCandidate
from app.services.discovery_candidate_fragment import CandidateFragment, candidate_fragment_to_evidence_bag
from app.services.evidence_attachment_guard import (
    AttachmentOutcome,
    CandidateAirport,
    EvidenceBag,
    evaluate_attachment_for_candidates,
)
from app.services.evidence_bag_serialization import (
    EVIDENCE_BAG_SCHEMA_VERSION,
    hash_serialized_evidence_bag,
    serialize_evidence_bag,
)

__all__ = [
    "DiscoverySourceMetadata",
    "DiscoveryPersistenceResult",
    "EvidenceBagSchemaRequiredError",
    "ConflictingEvidenceBagReplayError",
    "persist_discovery_fragment",
    "persist_candidate_linked_source_assertion",
]

# EB2's own atomic persistence unit (scripts/migrate_evidence_bag_persistence_eb2.py
# TABLES) - checked as a pair even though this module only ever writes to
# the first, so a partially-applied/broken manual schema state is
# rejected exactly like a wholly-missing one, never distinguished.
_EVIDENCE_BAG_SCHEMA_TABLES = ("source_assertion_evidence_bags", "identity_guard_evaluations")


class EvidenceBagSchemaRequiredError(RuntimeError):
    """Raised by both persistence functions, before any SourceAssertion is
    created or reconciled, when the EB2 EvidenceBag-persistence schema is
    not present on the target database. Modern discovery persistence
    never silently falls back to a SourceAssertion-only write missing its
    lossless EvidenceBag snapshot - run
    scripts/migrate_evidence_bag_persistence_eb2.py upgrade() first."""


class ConflictingEvidenceBagReplayError(ValueError):
    """Raised when a replay of an already-persisted fragment identity
    (source_id, artifact_identity, source_locator, raw_fragment_hash -
    the exact tuple `_get_existing_assertion()` matches on) produces an
    EvidenceBag whose canonical serialization differs from the snapshot
    already permanently attached to that SourceAssertion. The original
    snapshot is never overwritten and never silently treated as
    equivalent to drifted evidence - this always fails loud."""

# UAC2B (docs/architecture/rwi-uac2b-sourceassertion-unknown-airport-integration-report.md):
# the identity_guard_decision persist_candidate_linked_source_assertion()
# always records. Deliberately reuses the existing, unmodified
# AttachmentOutcome vocabulary rather than inventing a sixth outcome -
# INSUFFICIENT_IDENTITY already means exactly "no positive or
# contradicting identity evidence found" against whatever known Airport
# candidates were checked (zero or more), which is precisely the caller's
# own prerequisite for calling this function at all (design doc §6's
# governing flow: "EVERY supplied known candidate returns
# INSUFFICIENT_IDENTITY... -> persist as candidate-linked evidence").
# The candidate LINK itself is an orthogonal fact, recorded via the
# separate unknown_airport_candidate_id column - it is never folded into
# a new guard outcome.
_CANDIDATE_LINKED_IDENTITY_GUARD_DECISION = AttachmentOutcome.INSUFFICIENT_IDENTITY.value

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
    (or, as of UAC2B, persist_candidate_linked_source_assertion()) did
    (task S19) - never exposes ORM instances directly.

    `attached_unknown_airport_candidate_id` is additive (UAC2B): always
    None for a persist_discovery_fragment() result (that function never
    sets unknown_airport_candidate_id); populated only by
    persist_candidate_linked_source_assertion(). Mutually exclusive with
    `attached_airport_id` being non-None, mirroring the DB-level
    CheckConstraint on SourceAssertion itself."""

    source_id: int
    source_created: bool
    source_assertion_id: int
    source_assertion_created: bool
    outcome: AttachmentOutcome
    reason: str
    attached_airport_id: int | None
    evaluated_candidate_ids: tuple[object, ...] = field(default_factory=tuple)
    attached_unknown_airport_candidate_id: int | None = None
    # EB3 (docs/architecture/rwi-eb3-evidencebag-discovery-persistence-report.md):
    # the SourceAssertionEvidenceBag row id snapshotting the exact
    # EvidenceBag used for this fragment. Populated whenever a snapshot
    # was newly created OR already existed for a reused/existing
    # SourceAssertion; None only for a legacy existing assertion that
    # predates EB3 and has no snapshot (see _reconcile_replay_snapshot()'s
    # own docstring for why that gap is never backfilled).
    attached_evidence_bag_snapshot_id: int | None = None


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


def _verify_evidence_bag_schema_ready(session: Session) -> None:
    """Lightweight, session-connection-based table-existence check - never
    a duplicate of scripts/migrate_evidence_bag_persistence_eb2.py's own
    deep structural comparison (that script's inspect() opens its own
    file-path-based raw sqlite3 connection, independent of any live ORM
    Session/transaction, so it cannot be reused directly here). Queries
    `sqlite_master` through the Session's OWN connection via
    `session.execute()` - deliberately NOT `sqlalchemy.inspect(session.get_bind())`,
    which opens a second, independent `Connection` against the bound
    Engine; for an in-memory `sqlite:///:memory:` database (SingletonThreadPool,
    one shared physical connection per thread) that second connection's own
    transaction bookkeeping collides with the Session's own already-open
    transaction and silently corrupts it (empirically proven: reproduced
    an autoincrement-id/identity-map corruption this way during EB3's own
    adversarial self-check, matching the exact class of `:memory:`-engine
    connection-sharing hazard EB1's own review already found once before
    for a different reason). Going through `session.execute()` instead
    reuses the Session's existing connection/transaction directly, so no
    second connection is ever opened. A wrongly-shaped-but-present table
    is left to fail naturally at INSERT time with a real SQLAlchemy error;
    this check only distinguishes "missing entirely" (requires a real
    migration) from "present", matching this function's own narrow job of
    producing a clear, typed error instead of a raw OperationalError."""
    placeholders = ", ".join(f":table_{i}" for i in range(len(_EVIDENCE_BAG_SCHEMA_TABLES)))
    params = {f"table_{i}": name for i, name in enumerate(_EVIDENCE_BAG_SCHEMA_TABLES)}
    found = set(
        session.execute(
            text(f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({placeholders})"),
            params,
        ).scalars()
    )
    missing = [name for name in _EVIDENCE_BAG_SCHEMA_TABLES if name not in found]
    if missing:
        raise EvidenceBagSchemaRequiredError(
            "modern discovery persistence requires the EB2 EvidenceBag-persistence schema "
            f"(missing table(s): {missing}) - run scripts/migrate_evidence_bag_persistence_eb2.py "
            "upgrade() against this database before persisting discovery evidence."
        )


def _attach_evidence_bag_snapshot(
    session: Session, *, source_assertion_id: int, evidence_bag: EvidenceBag,
) -> SourceAssertionEvidenceBag:
    """The ONLY code path in this module permitted to construct a
    SourceAssertionEvidenceBag row (EB3 mission's own write-time
    consistency guarantee). Owns serialization, hashing, and
    schema_version entirely via the committed EB1 serializer - no caller
    may supply any of the three independently. Flush only, never commits,
    matching every other write in this module."""
    serialized = serialize_evidence_bag(evidence_bag)
    snapshot = SourceAssertionEvidenceBag(
        source_assertion_id=source_assertion_id,
        evidence_bag_json=serialized,
        evidence_bag_hash=hash_serialized_evidence_bag(serialized),
        schema_version=EVIDENCE_BAG_SCHEMA_VERSION,
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def _reconcile_replay_snapshot(
    session: Session, *, existing_assertion: SourceAssertion, evidence_bag: EvidenceBag,
) -> "int | None":
    """Called only when fragment-identity dedup (`_get_existing_assertion()`)
    found an ALREADY-EXISTING SourceAssertion. Never creates or backfills
    a snapshot for a legacy row that predates EB3: the fragment-identity
    match this dedup uses is keyed on `raw_fragment_hash`, which is only a
    hash of `raw_text` (see CandidateFragment.fragment_hash) - it does NOT
    cover the fully-structured EvidenceBag (identifiers/names/runway
    tokens/issuers/locations/contradictions/alternate-airport topology),
    so a byte-identical fragment-identity match is not, by itself,
    sufficient proof that the CURRENT call's derived EvidenceBag equals
    whatever originally produced that legacy row's identity_guard_decision
    (e.g. extraction logic could have changed between then and now). A
    missing snapshot on a legacy row therefore remains a valid, permanent
    state - exactly the "many SourceAssertions, at most one snapshot each"
    completeness signal app/models/source_assertion_evidence_bag.py's own
    module docstring already establishes - never silently and
    unpredictably backfilled depending on whether a later replay happens
    to occur.

    If a snapshot DOES already exist for this assertion, this function
    DOES verify it: the current call's own EvidenceBag is serialized and
    compared against the exact stored payload. Identical -> safe no-op
    (already correctly captured). Different -> a genuine provenance
    conflict (this exact fragment identity produced different evidence on
    replay) - raises ConflictingEvidenceBagReplayError rather than
    silently coexisting with drifted evidence; the original snapshot is
    never overwritten.

    Returns the existing snapshot's id, or None if this assertion has
    none.
    """
    existing_snapshot = session.scalar(
        select(SourceAssertionEvidenceBag).where(
            SourceAssertionEvidenceBag.source_assertion_id == existing_assertion.id
        )
    )
    if existing_snapshot is None:
        return None
    current_serialized = serialize_evidence_bag(evidence_bag)
    if current_serialized != existing_snapshot.evidence_bag_json:
        raise ConflictingEvidenceBagReplayError(
            f"SourceAssertion {existing_assertion.id} already has a permanent EvidenceBag snapshot, "
            "but this replay of the identical fragment identity "
            f"(artifact_identity={existing_assertion.artifact_identity!r}, "
            f"source_locator={existing_assertion.source_locator!r}, "
            f"raw_fragment_hash={existing_assertion.raw_fragment_hash!r}) produced a different "
            "EvidenceBag. The original snapshot is never overwritten - investigate why extraction "
            "produced different evidence for the same fragment identity."
        )
    return existing_snapshot.id


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

    _verify_evidence_bag_schema_ready(session)

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
        snapshot_id = _reconcile_replay_snapshot(session, existing_assertion=existing_assertion, evidence_bag=evidence)
        return DiscoveryPersistenceResult(
            source_id=source.id,
            source_created=source_created,
            source_assertion_id=existing_assertion.id,
            source_assertion_created=False,
            outcome=AttachmentOutcome(existing_assertion.identity_guard_decision) if existing_assertion.identity_guard_decision else outcome,
            reason=existing_assertion.identity_guard_reason or reason,
            attached_airport_id=existing_assertion.airport_id,
            evaluated_candidate_ids=tuple(decisions.keys()),
            attached_evidence_bag_snapshot_id=snapshot_id,
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

    snapshot = _attach_evidence_bag_snapshot(session, source_assertion_id=assertion.id, evidence_bag=evidence)

    return DiscoveryPersistenceResult(
        source_id=source.id,
        source_created=source_created,
        source_assertion_id=assertion.id,
        source_assertion_created=True,
        outcome=outcome,
        reason=reason,
        attached_airport_id=primary_airport_id,
        evaluated_candidate_ids=tuple(decisions.keys()),
        attached_evidence_bag_snapshot_id=snapshot.id,
    )


def persist_candidate_linked_source_assertion(
    session: Session,
    source_metadata: DiscoverySourceMetadata,
    fragment: CandidateFragment,
    *,
    unknown_airport_candidate_id: int,
) -> DiscoveryPersistenceResult:
    """Governed bridge from one already-extracted CandidateFragment to
    persisted RWI evidence LINKED TO AN ALREADY-EXISTING
    UnknownAirportCandidate (UAC2B). Never commits; the caller owns the
    transaction, matching persist_discovery_fragment()'s own convention.
    Never creates a Signal, an Airport, or an UnknownAirportCandidate -
    only ever touches Source and SourceAssertion.

    The caller is responsible for having already determined (elsewhere -
    typically via evaluate_attachment_for_candidates() returning no
    ATTACH_CONFIRMED/ATTACH_PROVISIONAL/REVIEW_REQUIRED result for any
    known Airport candidate) that this evidence has no known-airport
    match, and for having already found-or-created the target
    UnknownAirportCandidate via
    app.services.unknown_airport_candidate_persistence.find_or_create_unknown_airport_candidate().
    This function performs NO candidate lookup by raw name or
    fingerprint, and NO candidate creation of any kind - it validates
    only that `unknown_airport_candidate_id` refers to a real,
    already-persisted row (raising ValueError otherwise) and links to it
    exactly as given.

    1. Gets-or-creates the Source (identical to persist_discovery_fragment()).
    2. Gets-or-creates the one SourceAssertion for this fragment (identical
       fragment-identity dedup as persist_discovery_fragment() - the same
       fragment rediscovered again returns the existing row UNCHANGED,
       never re-linked or overwritten). If that existing row was already
       linked to a KNOWN Airport (by an earlier persist_discovery_fragment()
       call for the identical fragment identity), it is returned exactly
       as-is - this function never rewrites an already-resolved
       SourceAssertion's identity, preserving the DB-level mutual-
       exclusivity invariant by construction, never merely by convention.
    3. A new row sets airport_id=NULL, unknown_airport_candidate_id=the
       given id, and identity_guard_decision=INSUFFICIENT_IDENTITY (see
       module-level comment on _CANDIDATE_LINKED_IDENTITY_GUARD_DECISION
       for why this reuses the existing guard vocabulary rather than
       inventing a new outcome).
    """
    if session.get(UnknownAirportCandidate, unknown_airport_candidate_id) is None:
        raise ValueError(
            f"unknown_airport_candidate_id={unknown_airport_candidate_id!r} does not reference an "
            "existing UnknownAirportCandidate"
        )

    # EB3: this function never itself ran the identity guard (its caller
    # already did, elsewhere, to decide to route here at all - see
    # app.services.unknown_airport_discovery_integration's own module
    # docstring) - but candidate_fragment_to_evidence_bag() is a PURE,
    # deterministic function of `fragment` alone, so recomputing it here
    # from the SAME fragment object the caller used is guaranteed to
    # produce a value-equal EvidenceBag to whatever the caller already
    # derived, satisfying "the exact evidence used by the guard" via
    # equivalent immutable value semantics rather than literal object
    # identity (mission's own explicitly-allowed alternative).
    evidence = candidate_fragment_to_evidence_bag(fragment)

    _verify_evidence_bag_schema_ready(session)

    source, source_created = _get_or_create_source(session, source_metadata)

    existing_assertion = _get_existing_assertion(session, source.id, fragment)
    if existing_assertion is not None:
        existing_outcome = (
            AttachmentOutcome(existing_assertion.identity_guard_decision)
            if existing_assertion.identity_guard_decision
            else AttachmentOutcome(_CANDIDATE_LINKED_IDENTITY_GUARD_DECISION)
        )
        snapshot_id = _reconcile_replay_snapshot(session, existing_assertion=existing_assertion, evidence_bag=evidence)
        return DiscoveryPersistenceResult(
            source_id=source.id,
            source_created=source_created,
            source_assertion_id=existing_assertion.id,
            source_assertion_created=False,
            outcome=existing_outcome,
            reason=existing_assertion.identity_guard_reason or "",
            attached_airport_id=existing_assertion.airport_id,
            attached_unknown_airport_candidate_id=existing_assertion.unknown_airport_candidate_id,
            attached_evidence_bag_snapshot_id=snapshot_id,
        )

    reason = (
        f"No known Airport candidate matched; evidence linked to UnknownAirportCandidate "
        f"{unknown_airport_candidate_id} pending human resolution."
    )
    assertion = SourceAssertion(
        source_id=source.id,
        airport_id=None,
        unknown_airport_candidate_id=unknown_airport_candidate_id,
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
        identity_guard_decision=_CANDIDATE_LINKED_IDENTITY_GUARD_DECISION,
        identity_guard_reason=reason,
    )
    session.add(assertion)
    session.flush()

    snapshot = _attach_evidence_bag_snapshot(session, source_assertion_id=assertion.id, evidence_bag=evidence)

    return DiscoveryPersistenceResult(
        source_id=source.id,
        source_created=source_created,
        source_assertion_id=assertion.id,
        source_assertion_created=True,
        outcome=AttachmentOutcome(_CANDIDATE_LINKED_IDENTITY_GUARD_DECISION),
        reason=reason,
        attached_airport_id=None,
        attached_unknown_airport_candidate_id=unknown_airport_candidate_id,
        attached_evidence_bag_snapshot_id=snapshot.id,
    )
