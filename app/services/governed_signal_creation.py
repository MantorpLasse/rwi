"""Human-approved governed Signal creation
(docs/architecture/human-approved-governed-signal-creation-slice9c-report.md,
Slice 9C of docs/architecture/reviewer-action-human-signal-promotion-slice9-design.md).

    SourceAssertion (identity_guard_decision=ATTACH_CONFIRMED,
        intelligence_review_decision=REVIEW_REQUIRED,
        promotion_policy_decision=HUMAN_REVIEW_REQUIRED)
        + latest ReviewerAction == APPROVE_SIGNAL (Slice 9B)
        -> create_signal_from_approved_review()
        -> one internal Signal (published=False)
        -> SourceAssertion.signal_id set
        -> STOP (no publication - a separate, future, explicitly-approved
           step; no automatic promotion - AUTO_ELIGIBLE is refused here)

THIS IS THE FIRST SLICE ALLOWED TO CREATE A SIGNAL through the governed
discovery/intelligence pipeline. It is a new, seventh, explicit Signal-write
path alongside the six pre-existing ones (app.services.signal_rules and five
scripts/*.py importers) - none of those six are modified by this slice.

CALLER SUPPLIES NO SIGNAL OBJECT: this module accepts only explicit, named,
human-selected keyword arguments (title, category, confidence, status, and a
handful of others) - never a raw Signal ORM instance, and never any
financial-value field (estimated_total_value_usd, estimated_emas_value_usd).
Neither is a parameter this function accepts, at all - there is no code path
by which any dollar amount, safe-looking or not, can reach a created Signal
through this service (see the Slice 9 design doc's own S6 field-mapping
matrix: both are classified UNSAFE_WITH_CURRENT_MODEL, since MSP's own real
evidence proves a single "amount" field cannot distinguish an advance-deposit
Purchase Order from a CIP ceiling from a confirmed contract value without
corrupting one of those meanings). `confirmed_vendor` is similarly not
accepted here - no evidence pathway currently governed by this pipeline
produces an award-confirming relationship claim, and inventing an
unvalidated way to set it would risk exactly the same corruption.

PUBLICATION IS NEVER A PARAMETER EITHER: every Signal this module creates is
hardcoded `published=False` - not a caller-suppliable default, so there is no
way for a caller to accidentally publish a newly governed Signal by omitting
an argument.

Never commits and never imports app.database.SessionLocal - mutates the
caller-supplied Session and flushes only so a constraint violation surfaces
immediately; the caller owns the transaction boundary entirely, matching
every other persistence service in this pipeline. Signal creation and the
SourceAssertion.signal_id link happen inside the same uncommitted
transaction - if anything fails, a caller rollback leaves neither a Signal
nor a link (see docs/architecture/reviewer-action-human-signal-promotion-slice9-design.md
S10's own reasoning for why this must be atomic, not two separate steps).

AUTO_ELIGIBLE IS REFUSED HERE, deliberately, matching Slice 9B's own
narrowing of the design doc's earlier broader suggestion: this is the
HUMAN-approved route; only `promotion_policy_decision == "HUMAN_REVIEW_REQUIRED"`
qualifies. A future, separate automation path may eventually reuse the
lower-level Signal-write shape this module establishes, but this slice does
not build it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Runway, Signal, SourceAssertion
from app.models.signal import DEFAULT_SCORE_BY_CONFIDENCE
from app.services.reviewer_action_persistence import get_latest_reviewer_action

__all__ = [
    "REQUIRED_IDENTITY_DECISION",
    "REQUIRED_INTELLIGENCE_DECISION",
    "REQUIRED_PROMOTION_DECISION",
    "GovernedSignalCreationResult",
    "create_signal_from_approved_review",
    "link_source_assertion_to_duplicate_signal",
]

REQUIRED_IDENTITY_DECISION = "ATTACH_CONFIRMED"
REQUIRED_INTELLIGENCE_DECISION = "REVIEW_REQUIRED"
REQUIRED_PROMOTION_DECISION = "HUMAN_REVIEW_REQUIRED"

# Status strings a mere human REVIEW approval cannot legitimately establish -
# "completed" in particular has a separate, load-bearing meaning elsewhere
# (scripts/graduate_signal_to_installation.py's own idempotency check reads
# status == "completed" to mean "already graduated to a real Installation").
# Letting APPROVE_SIGNAL alone set a terminal/confirmed-sounding status would
# misrepresent "a human thinks this is worth tracking" as "this happened."
_DISALLOWED_INITIAL_STATUSES = frozenset({"completed", "awarded", "executed", "contracted"})

# The compatibility signature used to decide whether a second call for a
# SourceAssertion whose signal_id is already set is a legitimate idempotent
# repeat (same request, safe to reuse the existing Signal) or drift (a
# different request against an already-linked row, refused rather than
# silently reinterpreted or overwritten).
_CompatibilitySignature = tuple


@dataclass(frozen=True)
class GovernedSignalCreationResult:
    """created=True only when this call itself inserted a new Signal row;
    False on an idempotent repeat that reused the existing one."""

    signal: Signal
    created: bool
    source_assertion_id: int


def _validate_human_selected_fields(
    *, title: str, category: str, confidence: str, status: Optional[str],
) -> None:
    if not title.strip():
        raise ValueError("title is required")
    if not category.strip():
        raise ValueError("category is required")
    if confidence not in DEFAULT_SCORE_BY_CONFIDENCE:
        raise ValueError(
            f"confidence must be one of {sorted(DEFAULT_SCORE_BY_CONFIDENCE)!r}, got {confidence!r}"
        )
    if status is not None:
        if not status.strip():
            raise ValueError("status, if given, must not be blank")
        if status.strip().lower() in _DISALLOWED_INITIAL_STATUSES:
            raise ValueError(
                f"status {status!r} is not a state human review approval alone can establish "
                f"(disallowed: {sorted(_DISALLOWED_INITIAL_STATUSES)!r})"
            )


def _check_governance_gates(session: Session, source_assertion: SourceAssertion) -> None:
    if source_assertion.identity_guard_decision != REQUIRED_IDENTITY_DECISION:
        raise ValueError(
            f"create_signal_from_approved_review requires identity_guard_decision == "
            f"{REQUIRED_IDENTITY_DECISION!r}, got {source_assertion.identity_guard_decision!r}"
        )
    if source_assertion.intelligence_review_decision != REQUIRED_INTELLIGENCE_DECISION:
        raise ValueError(
            f"create_signal_from_approved_review requires intelligence_review_decision == "
            f"{REQUIRED_INTELLIGENCE_DECISION!r}, got {source_assertion.intelligence_review_decision!r}"
        )
    if source_assertion.promotion_policy_decision != REQUIRED_PROMOTION_DECISION:
        raise ValueError(
            f"create_signal_from_approved_review requires promotion_policy_decision == "
            f"{REQUIRED_PROMOTION_DECISION!r}, got {source_assertion.promotion_policy_decision!r} "
            "(AUTO_ELIGIBLE and DO_NOT_PROMOTE are both refused through this human-approved route)"
        )
    if source_assertion.airport_id is None:
        raise ValueError("source_assertion.airport_id is required (should always be set once ATTACH_CONFIRMED)")

    latest = get_latest_reviewer_action(session, source_assertion.id)
    if latest is None:
        raise ValueError("no ReviewerAction has been recorded for this SourceAssertion")
    if latest.source_assertion_id != source_assertion.id:
        raise ValueError("latest ReviewerAction does not belong to this SourceAssertion")
    if latest.action != "APPROVE_SIGNAL":
        raise ValueError(
            f"latest ReviewerAction is {latest.action!r}, not 'APPROVE_SIGNAL' - "
            "a historical approval superseded by a later action is not sufficient"
        )


def create_signal_from_approved_review(
    session: Session,
    source_assertion: SourceAssertion,
    *,
    title: str,
    category: str,
    confidence: str,
    status: Optional[str] = None,
    runway_id: Optional[int] = None,
    likely_supplier: Optional[str] = None,
    supplier: Optional[str] = None,
    supplier_reason: Optional[str] = None,
    notes: Optional[str] = None,
    source_notes: Optional[str] = None,
    target_year: Optional[int] = None,
    planning_year: Optional[int] = None,
    procurement_year: Optional[int] = None,
    construction_start: Optional[date] = None,
    completion_date: Optional[date] = None,
    manual_year_estimate: Optional[int] = None,
    last_verified_at: Optional[date] = None,
) -> GovernedSignalCreationResult:
    """Validates every governance gate and human-selected field, then either
    creates exactly one new internal Signal (published=False) or, on an
    idempotent repeat, reuses the one already linked via
    source_assertion.signal_id. Never commits; calls session.flush() so any
    constraint violation surfaces immediately. Never mutates ReviewerAction.
    """
    _validate_human_selected_fields(title=title, category=category, confidence=confidence, status=status)
    _check_governance_gates(session, source_assertion)

    if runway_id is not None:
        runway = session.get(Runway, runway_id)
        if runway is None or runway.airport_id != source_assertion.airport_id:
            raise ValueError("runway_id, if given, must belong to the same airport as source_assertion")

    requested_signature: _CompatibilitySignature = (title, category, confidence)

    if source_assertion.signal_id is not None:
        existing = session.get(Signal, source_assertion.signal_id)
        if existing is None:
            raise ValueError(
                f"source_assertion.signal_id={source_assertion.signal_id!r} points to a Signal "
                "that no longer exists"
            )
        existing_signature: _CompatibilitySignature = (existing.title, existing.category, existing.confidence)
        if existing_signature != requested_signature:
            raise ValueError(
                "source_assertion.signal_id already points to an existing Signal with different "
                f"core fields (existing={existing_signature!r}, requested={requested_signature!r}) - "
                "refusing to silently overwrite or create a second Signal"
            )
        return GovernedSignalCreationResult(signal=existing, created=False, source_assertion_id=source_assertion.id)

    signal = Signal(
        airport_id=source_assertion.airport_id,
        runway_id=runway_id,
        source_id=source_assertion.source_id,
        title=title,
        category=category,
        confidence=confidence,
        status=status,
        probability_score=DEFAULT_SCORE_BY_CONFIDENCE[confidence],
        likely_supplier=likely_supplier,
        supplier=supplier,
        supplier_reason=supplier_reason,
        notes=notes,
        source_notes=source_notes,
        target_year=target_year,
        planning_year=planning_year,
        procurement_year=procurement_year,
        construction_start=construction_start,
        completion_date=completion_date,
        manual_year_estimate=manual_year_estimate,
        last_verified_at=last_verified_at,
        published=False,
    )
    session.add(signal)
    session.flush()  # obtain signal.id

    source_assertion.signal_id = signal.id
    session.flush()

    return GovernedSignalCreationResult(signal=signal, created=True, source_assertion_id=source_assertion.id)


def link_source_assertion_to_duplicate_signal(
    session: Session, source_assertion: SourceAssertion,
) -> GovernedSignalCreationResult:
    """Separate, explicit path for MARK_DUPLICATE: links
    source_assertion.signal_id to the existing Signal the latest
    ReviewerAction names via duplicate_of_signal_id - never creates a new
    Signal, never reinterprets MARK_DUPLICATE as approval. Fails closed
    unless the latest recorded action for this SourceAssertion is exactly
    MARK_DUPLICATE. Idempotent and drift-safe the same way
    create_signal_from_approved_review() is: a second call reuses the
    already-linked Signal if it matches, and refuses if it does not.
    """
    latest = get_latest_reviewer_action(session, source_assertion.id)
    if latest is None or latest.action != "MARK_DUPLICATE":
        raise ValueError(
            "link_source_assertion_to_duplicate_signal requires the latest ReviewerAction to be "
            f"'MARK_DUPLICATE', got {(latest.action if latest else None)!r}"
        )
    target = session.get(Signal, latest.duplicate_of_signal_id)
    if target is None:
        raise ValueError("the MARK_DUPLICATE action's duplicate_of_signal_id does not reference an existing Signal")

    if source_assertion.signal_id is not None:
        if source_assertion.signal_id != target.id:
            raise ValueError(
                f"source_assertion.signal_id={source_assertion.signal_id!r} already points elsewhere "
                f"than the MARK_DUPLICATE target ({target.id!r}) - refusing to overwrite"
            )
        return GovernedSignalCreationResult(signal=target, created=False, source_assertion_id=source_assertion.id)

    source_assertion.signal_id = target.id
    session.flush()
    return GovernedSignalCreationResult(signal=target, created=False, source_assertion_id=source_assertion.id)
