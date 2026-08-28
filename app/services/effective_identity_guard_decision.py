"""EB5 — downstream consumption of governed identity re-evaluation
(docs/architecture/rwi-eb5-downstream-identity-consumption-report.md,
Slice 5 of docs/architecture/rwi-full-evidencebag-persistence-design.md).

Answers exactly one narrow, read-only question for one SourceAssertion:
"for CURRENT downstream eligibility purposes (intelligence review,
promotion, etc.), what identity decision should a caller actually trust
right now?"

There are two distinct facts this module deliberately never collapses:

    A. HISTORICAL GUARD DECISION - SourceAssertion.identity_guard_decision.
       "What did the identity guard conclude when this assertion was
       originally discovered?" A permanent, once-only historical fact -
       this module never reads, writes, or depends on
       SourceAssertion.identity_guard_decision/identity_guard_reason ever
       changing, and never mutates either column (it performs no writes
       of any kind).

    B. LATEST GOVERNED RE-EVALUATION - IdentityGuardEvaluation.outcome,
       selected deterministically as the single most recent row
       (created_at DESC, id DESC) for this SourceAssertion
       (app.services.resolved_candidate_evidence_reevaluation, EB4,
       unmodified - this module never calls it, never triggers a new
       evaluation, and never creates one itself).

PRECEDENCE RULE (the one thing this whole module exists to implement):
if a currently-trustworthy latest evaluation exists, it is authoritative
for CURRENT downstream eligibility - not merely as an additional positive
signal, but as a full REPLACEMENT of the historical decision for this
purpose. A later negative evaluation (REJECT_CROSS_AIRPORT,
INSUFFICIENT_IDENTITY, ATTACH_PROVISIONAL, or even a malformed/
unrecognized value) makes a row NOT currently eligible even if the
original historical decision was ATTACH_CONFIRMED - EB4's own re-
evaluation is explicitly a replay against CURRENT canonical Airport
topology, so an original positive result that a later, more current replay
contradicts is stale for TODAY's purposes; this module never searches
history for "any positive result ever" (that would silently resurrect a
result the governed pipeline has since superseded). If no evaluation
exists at all, this module falls back to the original historical
decision unchanged - existing (pre-EB4) behavior for every row that has
never been re-evaluated is completely unaffected.

"Currently trustworthy" is a real, checked condition, not merely "the
most recent row found by a query" - see CANDIDATE-LINKED FIREWALL and
EVALUATION-AIRPORT CONSISTENCY below.

CANDIDATE-LINKED FIREWALL: an evaluation is consulted at all ONLY when
`SourceAssertion.airport_id IS NOT NULL` - a still-candidate-linked or
fully-unresolved row (both share `airport_id IS NULL`, per
SourceAssertion's own DB-level mutual-exclusivity CheckConstraint) has no
CURRENT canonical Airport identity for a later re-evaluation to be
meaningful against, so this module never even queries
IdentityGuardEvaluation for such a row - it always falls back to the
historical decision, exactly as if EB4 had never been built. This holds
regardless of whether a malformed/synthetic IdentityGuardEvaluation
happens to exist for such a row (only reachable via direct DB bypass -
EB4 itself refuses to create one for an unresolved assertion).

EVALUATION-AIRPORT CONSISTENCY: even once a canonical `airport_id` exists,
the latest evaluation is trusted ONLY if
`evaluation.evaluated_against_airport_id == source_assertion.airport_id`
exactly. A mismatch (only reachable via direct DB corruption/bypass - EB4
itself always writes the assertion's own current airport_id) is never
silently used; `basis=INCONSISTENT_REEVALUATION` is returned and this
module falls back to the historical decision, the same safe fallback as
"no evaluation exists," never a hard failure and never a false positive.

This module performs NO writes of any kind - no SourceAssertion mutation,
no IdentityGuardEvaluation creation, no Signal/Airport/Runway creation, no
candidate resolution, no re-evaluation trigger, no network access, no
commit. It reads only SourceAssertion's own two columns
(airport_id, identity_guard_decision) and IdentityGuardEvaluation rows for
that one assertion.

TIME SEMANTICS: this module trusts the latest EXISTING governed evaluation
- it never inspects current Runway/RunwayEnd topology itself, and never
triggers a new evaluation to refresh a stale one. If canonical topology
changes after the latest evaluation was recorded, the effective decision
here does not reflect that change until a NEW evaluation is separately,
explicitly run (a human/operator decision, not something this read path
performs automatically) and this module is called again.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import SourceAssertion
from app.models.identity_guard_evaluation import IdentityGuardEvaluation
from app.services.evidence_attachment_guard import AttachmentOutcome
from app.services.resolved_candidate_evidence_reevaluation import SourceAssertionNotFoundError
from app.services.source_assertion_legacy_identity_attestation import (
    get_latest_legacy_identity_attestation,
    is_attestation_current,
)

__all__ = [
    "EffectiveIdentityGuardDecisionBasis",
    "EffectiveIdentityGuardDecision",
    "resolve_effective_identity_guard_decision",
]

_EVALUATIONS_TABLE = "identity_guard_evaluations"
_LEGACY_ATTESTATIONS_TABLE = "source_assertion_legacy_identity_attestations"


def _identity_guard_evaluations_table_exists(session: Session) -> bool:
    """Adversarial-review finding: EB5 is a READ path consumed by
    intelligence-review/promotion-policy persistence, both of which MUST
    keep working against the CURRENT real database - which, as of this
    writing, has never run the EB2 migration at all (no
    identity_guard_evaluations table exists). Querying that table
    unconditionally would make every single
    persist_intelligence_review()/persist_promotion_policy() call raise a
    raw OperationalError for EVERY SourceAssertion, including rows with
    no relationship to EB1-EB5 whatsoever - a severe backward-
    compatibility regression, empirically reproduced during this review
    before being fixed.

    Existence-only, not a duplicate of
    scripts/migrate_evidence_bag_persistence_eb2.py's own deep structural
    inspector (column/constraint comparison) - this only distinguishes
    "the table has never been created" (a legitimate, permanent,
    pre-EB2-migration deployment state that must fall back cleanly) from
    "the table exists" (in which case the real query below runs
    normally, and any genuine structural malformation is left to fail
    loud there, exactly as EB3's own equivalent design deliberately does
    - never silently reinterpreted as "zero evaluations").

    Queries `sqlite_master` through the Session's OWN connection via
    `session.execute()` - deliberately NOT `sqlalchemy.inspect(session.get_bind())`
    or `engine.connect()`, which open a second, independent Connection
    against the bound Engine and, for an in-memory `sqlite:///:memory:`
    database (SingletonThreadPool), can silently corrupt the Session's
    own open transaction - the exact class of hazard EB3's own
    adversarial review already found and fixed once for this identical
    reason."""
    return (
        session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name = :table"),
            {"table": _EVALUATIONS_TABLE},
        ).first()
        is not None
    )


def _legacy_attestations_table_exists(session: Session) -> bool:
    """Identical reasoning and technique to
    `_identity_guard_evaluations_table_exists()` above, for the new
    source_assertion_legacy_identity_attestations table (this same
    mission's own migration) - a database that has not yet run that
    migration must fall back cleanly to ORIGINAL_DECISION, never raise."""
    return (
        session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name = :table"),
            {"table": _LEGACY_ATTESTATIONS_TABLE},
        ).first()
        is not None
    )


class EffectiveIdentityGuardDecisionBasis(str, Enum):
    """Why `effective_decision` was selected - explicit provenance rather
    than an overloaded boolean."""

    # No usable latest evaluation exists (none at all, no canonical
    # airport_id yet, or the one found was inconsistent) - effective
    # decision is the original historical identity_guard_decision,
    # unchanged from pre-EB4 behavior.
    ORIGINAL_DECISION = "ORIGINAL_DECISION"
    # A currently-trustworthy latest evaluation exists and is
    # authoritative - effective decision is that evaluation's own outcome.
    LATEST_REEVALUATION = "LATEST_REEVALUATION"
    # A latest evaluation exists but evaluated_against_airport_id does not
    # match the assertion's own CURRENT airport_id - never trusted; falls
    # back to the original historical decision, distinctly flagged for
    # audit rather than silently treated the same as "no evaluation".
    INCONSISTENT_REEVALUATION = "INCONSISTENT_REEVALUATION"
    # docs/architecture/rwi-legacy-attached-sourceassertion-identity-
    # governance-design.md: a currently-trustworthy (non-stale)
    # CONFIRM_EXISTING_ATTACHMENT SourceAssertionLegacyIdentityAttestation
    # exists for a row that never ran the modern identity guard at all
    # (identity_guard_decision IS NULL) - authoritative for CURRENT
    # downstream eligibility, but explicitly, permanently distinguished in
    # this basis from a real machine ORIGINAL_DECISION or LATEST_REEVALUATION;
    # never conflated with either.
    LEGACY_HUMAN_ATTESTATION = "LEGACY_HUMAN_ATTESTATION"


def _map_raw_outcome(raw: "str | None") -> AttachmentOutcome:
    """Fail-closed mapping from a persisted, free-text outcome column to a
    real AttachmentOutcome - identical defensive convention already
    established by app.services.intelligence_review_persistence's own
    `_identity_decision_from_assertion()`: None, an unrecognized string,
    or anything other than a real AttachmentOutcome member value all
    resolve to INSUFFICIENT_IDENTITY, the conservative "identity unknown/
    unconfirmed" member. Reused here (not reimplemented ad-hoc) so both
    modules apply exactly the same defensive rule."""
    if not raw:
        return AttachmentOutcome.INSUFFICIENT_IDENTITY
    try:
        return AttachmentOutcome(raw)
    except ValueError:
        return AttachmentOutcome.INSUFFICIENT_IDENTITY


def _fallback_decision(
    session: Session, assertion: SourceAssertion, source_assertion_id: int, original_decision: AttachmentOutcome,
) -> "EffectiveIdentityGuardDecision":
    """The one place both "no EB4 evaluations table yet" and "table exists
    but no evaluation row for this assertion" now converge (previously each
    directly returned ORIGINAL_DECISION inline - identical behavior,
    refactored so both call sites can also consult a legacy attestation).

    docs/architecture/rwi-legacy-attached-sourceassertion-identity-
    governance-design.md S6/S9: only ever consults a legacy attestation
    when `assertion.identity_guard_decision IS NULL` - a row WITH a real
    historical decision (however it turned out, e.g. REJECT_CROSS_AIRPORT)
    already has genuine machine-governed information that a legacy
    attestation must never override; `check_legacy_attestation_eligibility()`
    already refuses to let one be recorded for such a row in the first
    place, and this is the matching defensive read-side enforcement of the
    identical rule. Falls back to plain ORIGINAL_DECISION, completely
    unchanged from pre-this-mission behavior, whenever: the assertion
    already has a real identity_guard_decision, the legacy-attestations
    table has not been migrated yet, no attestation exists, the latest one
    is REJECT/DEFER (never manufactures a positive result), or the latest
    CONFIRM is stale (a live snapshot no longer matches what was reviewed -
    see is_attestation_current())."""
    if assertion.identity_guard_decision is not None or not _legacy_attestations_table_exists(session):
        return EffectiveIdentityGuardDecision(
            source_assertion_id=source_assertion_id,
            original_decision=original_decision,
            latest_evaluation_id=None,
            latest_evaluation_outcome=None,
            effective_decision=original_decision,
            basis=EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION,
        )

    latest_attestation = get_latest_legacy_identity_attestation(session, source_assertion_id)
    if (
        latest_attestation is not None
        and latest_attestation.action == "CONFIRM_EXISTING_ATTACHMENT"
        and is_attestation_current(session, latest_attestation)
    ):
        return EffectiveIdentityGuardDecision(
            source_assertion_id=source_assertion_id,
            original_decision=original_decision,
            latest_evaluation_id=None,
            latest_evaluation_outcome=None,
            effective_decision=AttachmentOutcome.ATTACH_CONFIRMED,
            basis=EffectiveIdentityGuardDecisionBasis.LEGACY_HUMAN_ATTESTATION,
        )

    return EffectiveIdentityGuardDecision(
        source_assertion_id=source_assertion_id,
        original_decision=original_decision,
        latest_evaluation_id=None,
        latest_evaluation_outcome=None,
        effective_decision=original_decision,
        basis=EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION,
    )


@dataclass(frozen=True)
class EffectiveIdentityGuardDecision:
    """Deterministic, ORM-free summary of the effective downstream
    identity state for one SourceAssertion - never exposes ORM instances
    directly, matching this pipeline's established convention."""

    source_assertion_id: int
    original_decision: AttachmentOutcome
    latest_evaluation_id: "int | None"
    latest_evaluation_outcome: "AttachmentOutcome | None"
    effective_decision: AttachmentOutcome
    basis: EffectiveIdentityGuardDecisionBasis

    @property
    def is_identity_confirmed(self) -> bool:
        return self.effective_decision == AttachmentOutcome.ATTACH_CONFIRMED


def resolve_effective_identity_guard_decision(
    session: Session, *, source_assertion_id: int,
) -> EffectiveIdentityGuardDecision:
    """Read-only. Raises SourceAssertionNotFoundError (reused verbatim
    from EB4, never a second near-duplicate exception type) if
    `source_assertion_id` does not exist - every other malformed or
    inconsistent state below returns an explicit, never-falsely-positive
    result rather than raising, matching this pipeline's own convention
    for read-only derivation services (e.g.
    get_latest_unknown_airport_candidate_review() returning None rather
    than raising for "no review yet").
    """
    # Adversarial-review finding: this entire function is read-only (it
    # never writes anything), but session.get()/session.execute()/
    # session.scalars() all autoflush pending state by default just like
    # any other query - a caller with OTHER unrelated pending work in the
    # same Session (e.g. a batch pipeline that already added a new,
    # not-yet-flushed row before calling
    # persist_intelligence_review()/persist_promotion_policy(), both of
    # which call this resolver internally) could have that unrelated
    # state silently and prematurely flushed merely by asking for an
    # effective identity decision - empirically reproduced during this
    # review, the exact class of hazard EB4's own adversarial review
    # already found and fixed once for this identical reason. The entire
    # body below is wrapped in no_autoflush accordingly.
    with session.no_autoflush:
        assertion = session.get(SourceAssertion, source_assertion_id)
        if assertion is None:
            raise SourceAssertionNotFoundError(source_assertion_id)

        original_decision = _map_raw_outcome(assertion.identity_guard_decision)

        # Candidate-linked firewall: no canonical airport_id means no
        # CURRENT canonical identity for any evaluation to be meaningful
        # against - never even query IdentityGuardEvaluation for such a row.
        if assertion.airport_id is None:
            return EffectiveIdentityGuardDecision(
                source_assertion_id=source_assertion_id,
                original_decision=original_decision,
                latest_evaluation_id=None,
                latest_evaluation_outcome=None,
                effective_decision=original_decision,
                basis=EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION,
            )

        # Pre-EB2-migration deployment compatibility: the table has never
        # been created at all (the current real database's own state) -
        # this is a legitimate, permanent "zero evaluations, structurally"
        # case, not an error. If the table DOES exist, fall through to
        # the real query below and let any genuine structural
        # malformation fail loud there instead of being silently
        # reinterpreted here.
        if not _identity_guard_evaluations_table_exists(session):
            return _fallback_decision(session, assertion, source_assertion_id, original_decision)

        latest = session.scalars(
            select(IdentityGuardEvaluation)
            .where(IdentityGuardEvaluation.source_assertion_id == source_assertion_id)
            .order_by(IdentityGuardEvaluation.created_at.desc(), IdentityGuardEvaluation.id.desc())
            .limit(1)
        ).first()

        if latest is None:
            return _fallback_decision(session, assertion, source_assertion_id, original_decision)

        latest_outcome = _map_raw_outcome(latest.outcome)

        # Evaluation-airport consistency: only reachable via direct DB
        # corruption/bypass in a real deployment (EB4 always writes the
        # assertion's own current airport_id) - never trusted if mismatched.
        if latest.evaluated_against_airport_id != assertion.airport_id:
            return EffectiveIdentityGuardDecision(
                source_assertion_id=source_assertion_id,
                original_decision=original_decision,
                latest_evaluation_id=latest.id,
                latest_evaluation_outcome=latest_outcome,
                effective_decision=original_decision,
                basis=EffectiveIdentityGuardDecisionBasis.INCONSISTENT_REEVALUATION,
            )

        return EffectiveIdentityGuardDecision(
            source_assertion_id=source_assertion_id,
            original_decision=original_decision,
            latest_evaluation_id=latest.id,
            latest_evaluation_outcome=latest_outcome,
            effective_decision=latest_outcome,
            basis=EffectiveIdentityGuardDecisionBasis.LATEST_REEVALUATION,
        )
