"""EB4 — post-resolution identity-guard re-evaluation
(docs/architecture/rwi-eb4-resolved-evidence-reevaluation-report.md,
Slice 4 of docs/architecture/rwi-full-evidencebag-persistence-design.md).

Answers exactly one question, for one already-canonically-resolved
SourceAssertion: "given the EXACT original EvidenceBag the identity guard
saw at discovery time, and the CURRENT canonical Airport topology, what
does the existing, unmodified identity guard
(app.services.evidence_attachment_guard) conclude today?"

UAC5's own adversarial review found a real STOP: a historical
SourceAssertion.identity_guard_decision could never safely be replayed,
because the full EvidenceBag it was computed from was never persisted -
only a handful of lossy, comma-joined raw_* strings survived. EB1-EB3
closed that prerequisite for every modern-discovery SourceAssertion (each
now carries exactly one immutable SourceAssertionEvidenceBag snapshot of
the EXACT EvidenceBag the guard actually saw). EB4 is the first module
that actually performs a re-evaluation - and, symmetrically, the first to
prove UAC5's own worked failure case (a lost contradicting_issuers fact
silently flipping REJECT_CROSS_AIRPORT into a false ATTACH_CONFIRMED) is
now structurally impossible for anything EB4 touches, because the exact
original contradiction evidence is what gets deserialized and replayed,
never a lossy reconstruction.

CURRENT TOPOLOGY, NOT HISTORICAL TOPOLOGY (read this before touching
anything downstream of this module). This module replays the ORIGINAL
EvidenceBag against the airport's CURRENT canonical Runway/RunwayEnd
topology at the moment reevaluate_resolved_candidate_evidence() is
called - it does NOT, and structurally cannot, reconstruct what that
Airport's topology looked like at the original discovery time (this
repository keeps no such time-travel record for Runway/RunwayEnd rows).
A re-evaluation is therefore a TIME-INDEXED INTERPRETATION ("is this old
evidence compatible with the airport as it is understood right now"),
never a re-creation of the original guard's environment. This is exactly
why IdentityGuardEvaluation is append-only rather than a single mutable
"current verdict" column: running this twice against a changed topology
can and should produce two different, both-correct-for-their-moment
rows, and `created_at` on each is what makes that history legible.

WHY THIS MODULE NEVER TRIES TO RE-DERIVE CANDIDATE PROVENANCE.
app.services.unknown_airport_candidate_resolution's own two execution
functions (UAC4) CLEAR SourceAssertion.unknown_airport_candidate_id the
moment they set airport_id - by the time this module runs, the
SourceAssertion's own foreign key back to whichever UnknownAirportCandidate
(if any) originally produced its canonical attribution is already gone,
by UAC4's own design. This module never tries to reconstruct that lost
link (there is nothing honest to reconstruct it from) - it takes the
CURRENT SourceAssertion.airport_id at face value as the canonical
re-evaluation target. That is sufficient for the guard question this
module answers ("is the original evidence compatible with THIS Airport");
it is deliberately NOT sufficient for "which human decision produced this
attribution," which is a separate auditability question already answered
by UAC4's own MatchExistingAirportResult/CreateNewAirportResult and the
UnknownAirportCandidateReview history, not by this module. Callers who
happen to know the triggering review (e.g. immediately after a UAC4
call) may pass it via the optional `triggering_review_id` keyword purely
as an audit annotation; this module never queries for it, never guesses
it, and treats its absence as an entirely ordinary, expected case.

SINGLE-CANDIDATE, NEVER GLOBAL RE-SELECTION. Human resolution (UAC4)
already chose canonical Airport X; this module asks only whether the
original evidence is compatible with X, never re-running discovery's own
candidate-selection question of "which airport, if any, should this
attach to." It therefore calls
app.services.evidence_attachment_guard.evaluate_attachment() (the
single-candidate primitive), never
evaluate_attachment_for_candidates() (which exists specifically to
detect cross-candidate ambiguity across a evaluated SET). A direct,
structural consequence, stated plainly rather than left implicit:
evaluate_attachment() cannot itself return AttachmentOutcome.REVIEW_REQUIRED
(that outcome is, by that function's own docstring, only decidable by
comparing more than one candidate) - so a re-evaluation produced by this
module can never be REVIEW_REQUIRED. This is not a limitation EB4
introduces; it is the same, already-reviewed guard is answering a
different, narrower question than global candidate selection does.

This module performs NO web search, NO fetch, NO network access, NO
Airport/Runway/RunwayEnd/Installation/Signal creation, NO promotion
action, NO candidate resolution, and NO mutation of
SourceAssertion.identity_guard_decision/identity_guard_reason or of any
SourceAssertionEvidenceBag snapshot - it only ever reads them and appends
exactly one new IdentityGuardEvaluation row. Never commits and never
imports app.database.SessionLocal - mutates the caller-supplied Session
and flushes only enough to obtain the new row's id; the caller owns the
transaction boundary entirely, matching every other persistence service
in this pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Airport, Runway, SourceAssertion
from app.models.identity_guard_evaluation import IdentityGuardEvaluation
from app.models.source_assertion_evidence_bag import SourceAssertionEvidenceBag
from app.models.unknown_airport_candidate import UnknownAirportCandidateReview
from app.services.evidence_attachment_guard import (
    AttachmentOutcome,
    candidate_airport_from_airport_like,
    evaluate_attachment,
)
from app.services.evidence_bag_serialization import (
    EVIDENCE_BAG_SCHEMA_VERSION,
    EvidenceBagSerializationError,
    deserialize_evidence_bag,
    hash_serialized_evidence_bag,
)

__all__ = [
    "SourceAssertionNotFoundError",
    "UnresolvedSourceAssertionError",
    "MissingEvidenceBagSnapshotError",
    "TamperedEvidenceBagSnapshotError",
    "IdentityGuardReevaluationResult",
    "reevaluate_resolved_candidate_evidence",
]


class SourceAssertionNotFoundError(ValueError):
    """Raised when `source_assertion_id` does not reference an existing
    SourceAssertion row."""


class UnresolvedSourceAssertionError(RuntimeError):
    """Raised when the SourceAssertion's own `airport_id` is NULL - either
    it was never resolved at all (identity_guard_decision left it
    unattached), or it is still linked to a not-yet-resolved
    UnknownAirportCandidate. SourceAssertion's own DB-level mutual-
    exclusivity CHECK constraint (app/models/source_assertion.py) already
    guarantees `airport_id IS NOT NULL` implies
    `unknown_airport_candidate_id IS NULL`, so this single check covers
    both "fully unresolved" and "still candidate-linked" - there is no
    canonical Airport to re-evaluate against in either case, and this
    module never guesses one."""

    def __init__(self, source_assertion_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        super().__init__(
            f"SourceAssertion {source_assertion_id} has no canonical airport_id - it is either "
            "still linked to an unresolved UnknownAirportCandidate or was never attached to any "
            "Airport. Re-evaluation requires a human-resolved canonical Airport target; this "
            "module never infers or guesses one."
        )


class MissingEvidenceBagSnapshotError(RuntimeError):
    """Raised when the SourceAssertion has no SourceAssertionEvidenceBag
    snapshot - expected and permanent for legacy rows that predate EB3
    (or for rows created by an EvidenceBag-free legacy importer, which
    never had one to begin with). This module never reconstructs a
    EvidenceBag from SourceAssertion's own lossy raw_* text columns, never
    reads app.models.acquisition.Snapshot's own payload, and never
    fabricates a partial EvidenceBag from whatever is available - a
    missing snapshot is a hard, permanent, honestly-reported blocker, not
    a degraded-but-attempted evaluation."""

    def __init__(self, source_assertion_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        super().__init__(
            f"SourceAssertion {source_assertion_id} has no SourceAssertionEvidenceBag snapshot - "
            "this is expected for legacy rows that predate EB3 (or rows from an EvidenceBag-free "
            "legacy importer) and is a permanent, not a transient, blocker. Re-evaluation requires "
            "the exact original EvidenceBag; this module never reconstructs one from lossy "
            "SourceAssertion text fields."
        )


class TamperedEvidenceBagSnapshotError(RuntimeError):
    """Raised when the persisted snapshot fails ANY independent write-time
    consistency check EB4 re-verifies from scratch - EB3 guarantees these
    invariants at write time, but EB4 treats persisted data as untrusted
    and never assumes EB3 (or any future write path) upheld them: stored
    `schema_version` column must equal the serializer's own supported
    `EVIDENCE_BAG_SCHEMA_VERSION`; `hash_serialized_evidence_bag(stored
    payload)` must equal the stored `evidence_bag_hash` column exactly;
    and the stored payload must deserialize cleanly under the committed
    EB1 serializer's own strict rules (valid JSON, supported embedded
    schema_version, every required field present with the right type,
    per app.services.evidence_bag_serialization.deserialize_evidence_bag()).
    Refusal always happens BEFORE the identity guard is ever invoked and
    BEFORE any IdentityGuardEvaluation row is added - a tampered or
    corrupted snapshot never reaches a guard call, and never produces an
    evaluation row of any kind."""

    def __init__(self, source_assertion_id: int, *, reason: str) -> None:
        self.source_assertion_id = source_assertion_id
        super().__init__(
            f"SourceAssertionEvidenceBag snapshot for SourceAssertion {source_assertion_id} failed "
            f"independent consistency verification: {reason}. Refusing before any guard evaluation."
        )


@dataclass(frozen=True)
class IdentityGuardReevaluationResult:
    """Deterministic, ORM-free summary of what
    reevaluate_resolved_candidate_evidence() did - never exposes ORM
    instances directly, matching this pipeline's established convention
    (DiscoveryPersistenceResult, MatchExistingAirportResult, ...)."""

    source_assertion_id: int
    evidence_bag_snapshot_id: int
    evaluated_against_airport_id: int
    outcome: AttachmentOutcome
    reason: str
    identity_guard_evaluation_id: int
    triggering_review_id: "int | None"


def reevaluate_resolved_candidate_evidence(
    session: Session,
    *,
    source_assertion_id: int,
    triggering_review_id: "int | None" = None,
) -> IdentityGuardReevaluationResult:
    """Re-runs the existing, unmodified identity guard for one
    already-canonically-resolved SourceAssertion, against its own exact
    original EvidenceBag snapshot and its CURRENT canonical Airport
    topology (see module docstring for the current-vs-historical-topology
    distinction), and persists exactly one new, append-only
    IdentityGuardEvaluation row. Never commits; the caller owns the
    transaction. Never mutates SourceAssertion.identity_guard_decision/
    identity_guard_reason, never mutates the snapshot, never creates an
    Airport/Runway/RunwayEnd/Signal, never promotes or publishes anything.

    `triggering_review_id`, if supplied, is validated (must reference a
    real UnknownAirportCandidateReview) and recorded purely as an audit
    annotation - this function never looks one up itself (see module
    docstring for why SourceAssertion no longer carries that provenance
    after UAC4 execution) and treats its absence as entirely ordinary.

    Fails closed, before any guard evaluation and before any
    IdentityGuardEvaluation row is added, for every precondition below -
    checked in this order:

    1. SourceAssertionNotFoundError - source_assertion_id does not exist.
    2. UnresolvedSourceAssertionError - airport_id is NULL (unresolved or
       still candidate-linked).
    3. ValueError - airport_id references a non-existent Airport (only
       reachable via a malformed/foreign-key-disabled database - a real
       SourceAssertion.airport_id FK makes this structurally impossible
       otherwise).
    4. MissingEvidenceBagSnapshotError - no snapshot exists (legacy row).
    5. TamperedEvidenceBagSnapshotError - the snapshot fails independent
       schema-version/hash/payload verification.
    6. ValueError - a supplied `triggering_review_id` does not reference a
       real UnknownAirportCandidateReview.

    Deliberately calls evaluate_attachment() (the single-candidate guard
    primitive) with exactly the resolved Airport as the sole candidate -
    never evaluate_attachment_for_candidates(), and never re-runs any
    candidate-selection question. A direct, structural consequence: this
    function can never produce AttachmentOutcome.REVIEW_REQUIRED (see
    module docstring) - every other real outcome (ATTACH_CONFIRMED,
    ATTACH_PROVISIONAL, REJECT_CROSS_AIRPORT, INSUFFICIENT_IDENTITY) is
    persisted verbatim, unchanged, uncollapsed.
    """
    # The ENTIRE precondition-checking + read-only evaluation phase below
    # is wrapped in no_autoflush - session.get()/session.scalar() each
    # autoflush pending state by default just as much as session.execute()
    # does, so wrapping only the later candidate-construction/guard-call
    # portion (an earlier version of this function's own mistake, found
    # and closed during this same implementation) would still let the
    # VERY FIRST precondition read silently flush whatever unrelated,
    # possibly-incomplete state the caller still had pending. Nothing in
    # this entire block writes anything; only the final
    # session.add(evaluation)/session.flush() below is allowed to. Matches
    # this repository's own no_autoflush precedent (e.g.
    # app.services.fleet_health_check) for read-heavy computation phases.
    with session.no_autoflush:
        assertion = session.get(SourceAssertion, source_assertion_id)
        if assertion is None:
            raise SourceAssertionNotFoundError(source_assertion_id)

        if assertion.airport_id is None:
            raise UnresolvedSourceAssertionError(source_assertion_id)

        # selectinload both levels up front (the exact convention already
        # established by app/static_export/build.py for this identical
        # Airport -> Runway -> RunwayEnd shape) - candidate_airport_from_airport_like()'s
        # own docstring explicitly puts eager-loading on the caller to
        # avoid a query per runway; without this, an airport with N
        # runways would otherwise cost N separate runway_ends SELECTs
        # (empirically confirmed during this review) purely from
        # iterating airport.runways[*].runway_ends with the default
        # lazy="select" relationship loading.
        airport = session.scalar(
            select(Airport)
            .where(Airport.id == assertion.airport_id)
            .options(selectinload(Airport.runways).selectinload(Runway.runway_ends))
        )
        if airport is None:
            raise ValueError(
                f"SourceAssertion {source_assertion_id}'s airport_id={assertion.airport_id!r} does not "
                "reference an existing Airport"
            )

        # .scalars().one_or_none() rather than .scalar(): a well-formed
        # database structurally cannot have more than one snapshot per
        # source_assertion_id (EB1's own unique=True FK), but .scalar()
        # would silently return an arbitrary one of several rows if that
        # constraint were ever bypassed (a malformed/corrupted database) -
        # exactly the "take the first snapshot it finds" failure mode this
        # service must never exhibit. one_or_none() instead raises
        # MultipleResultsFound in that case, failing loud rather than
        # silently picking one. This is a defense-in-depth service-level
        # guarantee, distinct from and in addition to EB1's own DB-level
        # uniqueness constraint.
        snapshot = session.scalars(
            select(SourceAssertionEvidenceBag).where(
                SourceAssertionEvidenceBag.source_assertion_id == source_assertion_id
            )
        ).one_or_none()
        if snapshot is None:
            raise MissingEvidenceBagSnapshotError(source_assertion_id)

        if snapshot.schema_version != EVIDENCE_BAG_SCHEMA_VERSION:
            raise TamperedEvidenceBagSnapshotError(
                source_assertion_id,
                reason=(
                    f"stored schema_version={snapshot.schema_version!r} does not match the serializer's "
                    f"own supported version {EVIDENCE_BAG_SCHEMA_VERSION!r}"
                ),
            )

        recomputed_hash = hash_serialized_evidence_bag(snapshot.evidence_bag_json)
        if recomputed_hash != snapshot.evidence_bag_hash:
            raise TamperedEvidenceBagSnapshotError(
                source_assertion_id,
                reason=(
                    f"stored evidence_bag_hash={snapshot.evidence_bag_hash!r} does not match the hash of "
                    f"the stored payload ({recomputed_hash!r}) - the payload and/or hash column has been "
                    "altered since it was written"
                ),
            )

        try:
            evidence = deserialize_evidence_bag(snapshot.evidence_bag_json)
        except EvidenceBagSerializationError as exc:
            raise TamperedEvidenceBagSnapshotError(
                source_assertion_id, reason=f"stored payload failed strict deserialization: {exc}",
            ) from exc

        if triggering_review_id is not None:
            review = session.get(UnknownAirportCandidateReview, triggering_review_id)
            if review is None:
                raise ValueError(
                    f"triggering_review_id={triggering_review_id!r} does not reference an existing "
                    "UnknownAirportCandidateReview"
                )
            # Adversarial-review finding: existence alone is not enough - a
            # review belonging to a completely unrelated
            # UnknownAirportCandidate could otherwise be passed and
            # recorded as if it caused THIS assertion's resolution,
            # fabricating a false causal audit link. UnknownAirportCandidate.resolved_airport_id
            # is set by BOTH UAC4 execution paths (resolve_candidate_to_existing_airport()
            # and create_airport_from_approved_candidate() alike) - reusing
            # it here (rather than UnknownAirportCandidateReview.matched_airport_id,
            # which is NULL for CREATE_NEW_AIRPORT reviews) lets this check
            # cover both resolution paths uniformly, with no new schema.
            # A REJECT_CANDIDATE/DEFER review's candidate never has
            # resolved_airport_id set at all, so it is correctly rejected
            # here too - such a review could never have caused any
            # canonical attribution.
            if review.candidate.resolved_airport_id != airport.id:
                raise ValueError(
                    f"triggering_review_id={triggering_review_id!r} belongs to UnknownAirportCandidate "
                    f"{review.candidate_id!r}, whose own resolved_airport_id="
                    f"{review.candidate.resolved_airport_id!r} does not match SourceAssertion "
                    f"{source_assertion_id}'s own airport_id={airport.id!r} - refusing to record a "
                    "false causal link between an unrelated resolution and this evaluation."
                )

        # Local import (not top-level): app.services.airport_alias itself
        # imports SourceAssertionNotFoundError FROM this module, so a
        # top-level import here would be circular. Deferred to call time,
        # by which point both modules are fully initialized - a
        # well-established idiom for exactly this shape, not a design
        # smell. get_admitted_airport_aliases() is a pure read (no
        # mutation, no flush) that derives the CURRENTLY admitted alias
        # set for this Airport from the append-only AirportAlias history
        # (docs/architecture, "RWI - Governed Canonical Airport Aliases -
        # Cross-Script Identity Design" mission) - the ONLY change this
        # mission makes to this function. evaluate_attachment() itself,
        # and every other line in this function, is completely unchanged.
        from app.services.airport_alias import get_admitted_airport_aliases

        candidate = candidate_airport_from_airport_like(
            airport, aliases=get_admitted_airport_aliases(session, airport.id),
        )
        decision = evaluate_attachment(candidate, evidence)

    evaluation = IdentityGuardEvaluation(
        source_assertion_id=source_assertion_id,
        evidence_bag_snapshot_id=snapshot.id,
        evaluated_against_airport_id=airport.id,
        triggering_review_id=triggering_review_id,
        outcome=decision.outcome.value,
        reason=decision.reason,
    )
    session.add(evaluation)
    # Scoped, not a blanket session.flush(): flushing only this one new
    # object (SQLAlchemy still resolves its own dependencies) means this
    # service's own write can never drag in unrelated, possibly-incomplete
    # state the caller still had pending elsewhere in the same Session -
    # the strongest, most literal reading of "no hidden autoflush/flush of
    # unrelated pending caller state" this module's own read-only
    # precondition phase above already establishes via no_autoflush.
    session.flush(objects=[evaluation])

    return IdentityGuardReevaluationResult(
        source_assertion_id=source_assertion_id,
        evidence_bag_snapshot_id=snapshot.id,
        evaluated_against_airport_id=airport.id,
        outcome=decision.outcome,
        reason=decision.reason,
        identity_guard_evaluation_id=evaluation.id,
        triggering_review_id=triggering_review_id,
    )
