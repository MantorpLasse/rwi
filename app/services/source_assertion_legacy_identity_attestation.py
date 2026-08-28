"""Governed persistence for a human's identity decision about a LEGACY,
already-airport-attached SourceAssertion that predates the modern identity-
guard pipeline entirely (docs/architecture/rwi-legacy-attached-
sourceassertion-identity-governance-design.md, the locked design this
module implements; see app.models.source_assertion_legacy_identity_attestation
for the persisted row shape).

    SourceAssertion (airport_id already set, identity_guard_decision IS NULL,
        no SourceAssertionEvidenceBag, signal_id IS NULL)
        -> record_legacy_identity_attestation()
        -> one SourceAssertionLegacyIdentityAttestation row, append-only
        -> STOP (no Signal, no Airport mutation, no EvidenceBag - a later,
           separately-authorized step reads this via EB5, see
           app.services.effective_identity_guard_decision)

THIS MODULE NEVER MUTATES SourceAssertion.identity_guard_decision/
identity_guard_reason (the permanent historical fact), NEVER creates a
SourceAssertionEvidenceBag (a real one could only ever come from EB1-EB3's
own discovery-time pipeline - fabricating one here to satisfy KAR's own
composite-FK precondition would misrepresent history), and NEVER creates or
mutates an Airport, UnknownAirportCandidate, Signal, or Installation.

REVIEW-TIME SNAPSHOT (design doc S5): `_build_review_snapshot_payload()`/
`serialize_review_snapshot()`/`hash_review_snapshot()` follow the exact same
deterministic convention already established twice in this pipeline -
app.services.evidence_bag_serialization.serialize_evidence_bag() (EB1) and
app.services.existing_signal_reconciliation_review.compute_reconciliation_fingerprint()
(R4A) - `json.dumps(..., sort_keys=True, ensure_ascii=False)` then a plain
SHA-256 hex digest of that exact string, never repr()/str()/a second
independently-normalized representation. The payload captures only fields
that can meaningfully drift (SourceAssertion's own raw/identity fields, the
target Airport's own canonical identifiers) - `created_at` on the persisted
row itself, not the hashed payload, is what records "when reviewed" (EB1's
own identical convention: schema_version/audit fields stay off the hashed
identity-relevant payload).

STALENESS (design doc S5/S7, R4C's own "recompute fresh, compare against
stored value" pattern reused as a pattern, not duplicated as code): a
CONFIRM_EXISTING_ATTACHMENT attestation is trustworthy for
app.services.effective_identity_guard_decision (EB5) ONLY while a freshly
recomputed snapshot hash still matches the one stored at write time - never
by re-checking individual fields piecemeal, and never by trusting a caller-
supplied "still valid" flag. `is_attestation_current()` is the single shared
function both this module (for future re-validation) and EB5 call, so the
two can never independently drift on what "stale" means.

REVERSAL SAFETY (design doc S6, this mission's own explicit Commander
requirement): a second attestation whose own `action` CONTRADICTS the
immediately-latest existing attestation's `action` for the same assertion
(CONFIRM_EXISTING_ATTACHMENT <-> REJECT_EXISTING_ATTACHMENT, in either
direction) is refused UNLESS the caller explicitly supplies
`supersedes_attestation_id` equal to that exact latest row's id -
`ConflictingAttestationRequiresSupersessionError` otherwise. DEFER_IDENTITY_REVIEW
never conflicts with anything (a neutral "still unresolved" state, freely
repeatable, matching KAR1's own DEFER precedent). This makes a reversal
IMPOSSIBLE to record by accident - the caller must name the specific
decision being reversed - while still recording it as a plain, append-only,
fully-visible new row (never editing or hiding the first).

Never commits and never imports app.database.SessionLocal - mutates the
caller-supplied Session and flushes only so a constraint violation surfaces
immediately; the caller owns the transaction boundary entirely, matching
every other persistence service in this pipeline.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Airport, SourceAssertion
from app.models.source_assertion_evidence_bag import SourceAssertionEvidenceBag
from app.models.source_assertion_legacy_identity_attestation import (
    SOURCE_ASSERTION_LEGACY_IDENTITY_ATTESTATION_ACTIONS,
    SourceAssertionLegacyIdentityAttestation,
)
from app.services.resolved_candidate_evidence_reevaluation import SourceAssertionNotFoundError

__all__ = [
    "SourceAssertionNotFoundError",
    "NotLegacyAttachedError",
    "ModernIdentityGuardAlreadyRanError",
    "ModernEvidenceBagExistsError",
    "SignalAlreadyLinkedError",
    "MissingReviewableEvidenceError",
    "TargetAirportNotFoundError",
    "TargetAirportMismatchError",
    "ConflictingAttestationRequiresSupersessionError",
    "LegacyIdentityAttestationResult",
    "build_review_snapshot_payload",
    "serialize_review_snapshot",
    "hash_review_snapshot",
    "is_attestation_current",
    "check_legacy_attestation_eligibility",
    "record_legacy_identity_attestation",
    "get_latest_legacy_identity_attestation",
]

# Contradictory pairs - the only actions a reversal-safety check ever
# applies to. DEFER_IDENTITY_REVIEW is deliberately absent: it never
# conflicts with anything (design doc S6).
_CONTRADICTORY_ACTIONS = frozenset({"CONFIRM_EXISTING_ATTACHMENT", "REJECT_EXISTING_ATTACHMENT"})

_RAW_EVIDENCE_FIELDS = (
    "raw_relevant_text", "raw_airport_identifier", "raw_airport_name",
    "raw_runway_value", "raw_runway_end_value", "raw_product_type",
)


class NotLegacyAttachedError(ValueError):
    """Raised when `source_assertion.airport_id` is NULL - this mechanism
    exists only to review an ALREADY-attached legacy assertion; an
    unresolved one belongs to KAR/UAC, never here."""

    def __init__(self, source_assertion_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        super().__init__(
            f"SourceAssertion {source_assertion_id} has no airport_id - this mechanism only reviews "
            "already-attached legacy assertions; an unresolved one belongs to KAR/UAC instead."
        )


class ModernIdentityGuardAlreadyRanError(ValueError):
    """Raised when `source_assertion.identity_guard_decision` is already
    set - this row already has a real, modern machine decision (however it
    turned out); it belongs to KAR/EB4, never this legacy-only mechanism."""

    def __init__(self, source_assertion_id: int, *, decision: str) -> None:
        self.source_assertion_id = source_assertion_id
        self.decision = decision
        super().__init__(
            f"SourceAssertion {source_assertion_id} already has identity_guard_decision={decision!r} - "
            "it has already run through the modern identity guard; use KAR/EB4, not this legacy mechanism."
        )


class ModernEvidenceBagExistsError(ValueError):
    """Raised when a real SourceAssertionEvidenceBag snapshot already
    exists for this assertion - it belongs to the EB1-EB3/KAR/EB4 pipeline,
    which this module never duplicates or bypasses."""

    def __init__(self, source_assertion_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        super().__init__(
            f"SourceAssertion {source_assertion_id} already has a real SourceAssertionEvidenceBag "
            "snapshot - use KAR/EB4, not this legacy mechanism."
        )


class SignalAlreadyLinkedError(ValueError):
    """Raised when `source_assertion.signal_id` is already set - a row that
    already produced a Signal despite ungoverned identity is a data-
    integrity anomaly outside this mechanism's scope, never silently
    accepted."""

    def __init__(self, source_assertion_id: int, *, signal_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        self.signal_id = signal_id
        super().__init__(
            f"SourceAssertion {source_assertion_id} already has signal_id={signal_id!r} set - "
            "this legacy identity mechanism never reviews evidence that has already produced a Signal."
        )


class MissingReviewableEvidenceError(ValueError):
    """Raised when the assertion carries no preserved raw evidence text at
    all in any of its raw_* fields - a human cannot honestly review
    something with nothing to read, and this module never approves on the
    strength of airport_id alone."""

    def __init__(self, source_assertion_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        super().__init__(
            f"SourceAssertion {source_assertion_id} has no preserved raw evidence text in any raw_* "
            "field - nothing for a human to review; refusing to proceed."
        )


class TargetAirportNotFoundError(ValueError):
    """Raised when `source_assertion.airport_id` does not reference an
    existing Airport - only reachable via a malformed/foreign-key-disabled
    database, matching KAR1's own identical precedent."""

    def __init__(self, airport_id: int) -> None:
        self.airport_id = airport_id
        super().__init__(f"airport_id={airport_id!r} does not reference an existing Airport")


class TargetAirportMismatchError(ValueError):
    """Raised when a caller-supplied `matched_airport_id` (for
    CONFIRM_EXISTING_ATTACHMENT) does not equal the assertion's own current
    `airport_id` - this mechanism never attaches to, or proposes, a
    DIFFERENT Airport than the one the legacy importer already set; moving
    an assertion to another Airport is a separate, out-of-scope, governed
    action."""

    def __init__(self, source_assertion_id: int, *, current_airport_id: int, matched_airport_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        self.current_airport_id = current_airport_id
        self.matched_airport_id = matched_airport_id
        super().__init__(
            f"matched_airport_id={matched_airport_id!r} does not equal SourceAssertion "
            f"{source_assertion_id}'s own current airport_id={current_airport_id!r} - this mechanism can "
            "only confirm the EXISTING attachment, never propose a different Airport."
        )


class ConflictingAttestationRequiresSupersessionError(ValueError):
    """Raised when a new action would CONTRADICT the immediately-latest
    existing attestation's own action (CONFIRM <-> REJECT, either
    direction) and the caller did not explicitly supply
    `supersedes_attestation_id` equal to that exact latest row's id - see
    module docstring, REVERSAL SAFETY. A second human decision must never
    silently erase the governance meaning of the first."""

    def __init__(self, source_assertion_id: int, *, latest_attestation_id: int, latest_action: str, new_action: str) -> None:
        self.source_assertion_id = source_assertion_id
        self.latest_attestation_id = latest_attestation_id
        self.latest_action = latest_action
        self.new_action = new_action
        super().__init__(
            f"New action {new_action!r} contradicts the latest existing attestation "
            f"(id={latest_attestation_id!r}, action={latest_action!r}) for SourceAssertion "
            f"{source_assertion_id} - pass supersedes_attestation_id={latest_attestation_id!r} explicitly "
            "to record this as a deliberate reversal."
        )


@dataclass(frozen=True)
class LegacyIdentityAttestationResult:
    """Deterministic, ORM-free summary of what
    record_legacy_identity_attestation() did - never exposes ORM instances
    directly, matching this pipeline's own established convention."""

    attestation_id: int
    source_assertion_id: int
    action: str
    matched_airport_id: "int | None"
    reviewed_snapshot_hash: str
    is_reversal: bool
    superseded_attestation_id: "int | None"


def build_review_snapshot_payload(source_assertion: SourceAssertion, airport: Airport) -> dict:
    """Pure. The ONLY fields that can meaningfully drift - never a
    timestamp (that lives on the persisted row's own `created_at`, outside
    the hashed payload, exactly matching EB1's own schema_version-outside-
    the-hash convention)."""
    return {
        "source_assertion": {
            "id": source_assertion.id,
            "airport_id": source_assertion.airport_id,
            "source_id": source_assertion.source_id,
            "raw_relevant_text": source_assertion.raw_relevant_text,
            "raw_product_type": source_assertion.raw_product_type,
            "assertion_type": source_assertion.assertion_type,
            "evidence_quality": source_assertion.evidence_quality,
            "parser_identifier": source_assertion.parser_identifier,
        },
        "airport": {
            "id": airport.id,
            "name": airport.name,
            "iata_code": airport.iata_code,
            "icao_code": airport.icao_code,
            "faa_code": airport.faa_code,
        },
    }


def serialize_review_snapshot(payload: dict) -> str:
    """Deterministic - matches EB1's own serialize_evidence_bag() /
    R4A's own compute_reconciliation_fingerprint() convention exactly:
    json.dumps(..., sort_keys=True, ensure_ascii=False), never repr()/
    str()/an object-serialization module."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def hash_review_snapshot(serialized: str) -> str:
    """SHA-256 hex digest of the EXACT serialized string - never a second,
    independently-normalized representation, matching
    hash_serialized_evidence_bag()'s own identical convention."""
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def get_latest_legacy_identity_attestation(
    session: Session, source_assertion_id: int,
) -> "SourceAssertionLegacyIdentityAttestation | None":
    """Read-only. Recency alone determines "current" - never derived by
    walking supersedes_attestation_id, matching every other append-only
    table in this pipeline (mirrors get_latest_reviewer_action())."""
    return session.scalars(
        select(SourceAssertionLegacyIdentityAttestation)
        .where(SourceAssertionLegacyIdentityAttestation.source_assertion_id == source_assertion_id)
        .order_by(SourceAssertionLegacyIdentityAttestation.created_at.desc(), SourceAssertionLegacyIdentityAttestation.id.desc())
        .limit(1)
    ).first()


def is_attestation_current(
    session: Session, attestation: SourceAssertionLegacyIdentityAttestation,
) -> bool:
    """Read-only. True only if a FRESHLY recomputed snapshot of the
    attestation's own source_assertion/airport still hashes to exactly the
    value stored at write time, AND the assertion's own airport_id has not
    changed since. Never re-checks individual fields piecemeal - the hash
    comparison alone is authoritative, matching R4C's own "recompute fresh,
    compare against stored value" pattern. The single function both this
    module and EB5 call, so the two can never independently define
    "stale" differently."""
    assertion = session.get(SourceAssertion, attestation.source_assertion_id)
    if assertion is None or assertion.airport_id is None:
        return False
    if attestation.action == "CONFIRM_EXISTING_ATTACHMENT" and attestation.matched_airport_id != assertion.airport_id:
        return False
    airport = session.get(Airport, assertion.airport_id)
    if airport is None:
        return False
    fresh_payload = build_review_snapshot_payload(assertion, airport)
    fresh_hash = hash_review_snapshot(serialize_review_snapshot(fresh_payload))
    return fresh_hash == attestation.reviewed_snapshot_hash


def check_legacy_attestation_eligibility(session: Session, source_assertion: SourceAssertion) -> None:
    """The single source of truth for "is this assertion even eligible for
    this mechanism at all" - both record_legacy_identity_attestation() and
    any future read-only CLI inspect call must call this exact function, so
    they can never disagree about eligibility (mirrors
    scripts/migrate_source_assertion_identity_resolution_kar1.py's own
    "both upgrade() and inspect() call this one function" discipline).
    Raises the first violated precondition; never partially reports."""
    if source_assertion.airport_id is None:
        raise NotLegacyAttachedError(source_assertion.id)
    if source_assertion.identity_guard_decision is not None:
        raise ModernIdentityGuardAlreadyRanError(source_assertion.id, decision=source_assertion.identity_guard_decision)
    has_evidence_bag = session.scalar(
        select(SourceAssertionEvidenceBag.id).where(
            SourceAssertionEvidenceBag.source_assertion_id == source_assertion.id
        )
    )
    if has_evidence_bag is not None:
        raise ModernEvidenceBagExistsError(source_assertion.id)
    if source_assertion.signal_id is not None:
        raise SignalAlreadyLinkedError(source_assertion.id, signal_id=source_assertion.signal_id)
    airport = session.get(Airport, source_assertion.airport_id)
    if airport is None:
        raise TargetAirportNotFoundError(source_assertion.airport_id)
    has_evidence = any(
        (getattr(source_assertion, field) or "").strip() for field in _RAW_EVIDENCE_FIELDS
    )
    if not has_evidence:
        raise MissingReviewableEvidenceError(source_assertion.id)


def record_legacy_identity_attestation(
    session: Session,
    *,
    source_assertion_id: int,
    action: str,
    reason: str,
    reviewer: str,
    matched_airport_id: "int | None" = None,
    supersedes_attestation_id: "int | None" = None,
) -> LegacyIdentityAttestationResult:
    """Validates every precondition (§ check_legacy_attestation_eligibility
    plus the action-specific rules below), builds the review-time snapshot
    fresh (never caller-supplied - a caller-built snapshot could not be
    trusted to reflect the ACTUAL current row), and appends exactly one new
    SourceAssertionLegacyIdentityAttestation row. Never commits; calls
    session.flush() so any constraint violation surfaces immediately.

    Preconditions, checked in this exact order, all inside one
    `session.no_autoflush` block (mirrors every other governed-write
    service in this pipeline - an unrelated pending row in the same
    Session must never leak into a precondition failure):

    1. source_assertion exists -> SourceAssertionNotFoundError
    2-6. check_legacy_attestation_eligibility() (airport_id set, no modern
         identity_guard_decision, no EvidenceBag, no signal_id, target
         Airport exists, reviewable evidence exists)
    7. action is a real vocabulary member -> ValueError
    8. reason/reviewer non-empty after .strip() -> ValueError
    9. matched_airport_id required iff CONFIRM_EXISTING_ATTACHMENT -> ValueError
    10. (CONFIRM only) matched_airport_id == source_assertion.airport_id
        -> TargetAirportMismatchError
    11. reversal safety: if the new action contradicts the latest existing
        attestation's own action, supersedes_attestation_id must be
        supplied and must equal that latest row's id
        -> ConflictingAttestationRequiresSupersessionError
    """
    with session.no_autoflush:
        source_assertion = session.get(SourceAssertion, source_assertion_id)
        if source_assertion is None:
            raise SourceAssertionNotFoundError(source_assertion_id)

        check_legacy_attestation_eligibility(session, source_assertion)

        if action not in SOURCE_ASSERTION_LEGACY_IDENTITY_ATTESTATION_ACTIONS:
            raise ValueError(
                f"action must be one of {SOURCE_ASSERTION_LEGACY_IDENTITY_ATTESTATION_ACTIONS!r}, got {action!r}"
            )
        if not reason.strip():
            raise ValueError("reason is required")
        if not reviewer.strip():
            raise ValueError("reviewer is required")

        if action == "CONFIRM_EXISTING_ATTACHMENT":
            if matched_airport_id is None:
                raise ValueError("matched_airport_id is required for action=CONFIRM_EXISTING_ATTACHMENT")
            if matched_airport_id != source_assertion.airport_id:
                raise TargetAirportMismatchError(
                    source_assertion_id,
                    current_airport_id=source_assertion.airport_id,
                    matched_airport_id=matched_airport_id,
                )
        elif matched_airport_id is not None:
            raise ValueError(f"matched_airport_id must be omitted for action={action!r}")

        latest = get_latest_legacy_identity_attestation(session, source_assertion_id)
        is_reversal = (
            latest is not None
            and latest.action in _CONTRADICTORY_ACTIONS
            and action in _CONTRADICTORY_ACTIONS
            and latest.action != action
        )
        if is_reversal and supersedes_attestation_id != latest.id:
            raise ConflictingAttestationRequiresSupersessionError(
                source_assertion_id, latest_attestation_id=latest.id, latest_action=latest.action, new_action=action,
            )
        if supersedes_attestation_id is not None and latest is not None and supersedes_attestation_id != latest.id:
            raise ValueError(
                f"supersedes_attestation_id={supersedes_attestation_id!r} does not match the current latest "
                f"attestation id={latest.id!r} for SourceAssertion {source_assertion_id} - a newer attestation "
                "exists; re-inspect before recording another one."
            )

        airport = session.get(Airport, source_assertion.airport_id)
        payload = build_review_snapshot_payload(source_assertion, airport)
        serialized = serialize_review_snapshot(payload)
        snapshot_hash = hash_review_snapshot(serialized)

    attestation = SourceAssertionLegacyIdentityAttestation(
        source_assertion_id=source_assertion_id,
        action=action,
        reason=reason,
        reviewer=reviewer,
        matched_airport_id=matched_airport_id,
        reviewed_snapshot_json=serialized,
        reviewed_snapshot_hash=snapshot_hash,
        supersedes_attestation_id=supersedes_attestation_id,
    )
    session.add(attestation)
    session.flush()

    return LegacyIdentityAttestationResult(
        attestation_id=attestation.id,
        source_assertion_id=source_assertion_id,
        action=action,
        matched_airport_id=matched_airport_id,
        reviewed_snapshot_hash=snapshot_hash,
        is_reversal=is_reversal,
        superseded_attestation_id=supersedes_attestation_id,
    )
