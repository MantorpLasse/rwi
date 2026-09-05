"""Lightweight human-approved Signal creation for known-Airport-staged
funding evidence (RWI HQ "Funding Human Review Gate - Slice B").

    SourceAssertion (known-Airport-staged, assertion_type="project_construction",
        eligible per app.services.known_airport_funding_lightweight_path_guard)
        + latest ReviewerAction == APPROVE_SIGNAL (via
          app.services.known_airport_funding_reviewer_action)
        -> create_signal_from_lightweight_funding_review()
        -> one internal Signal (published=False)
        -> SourceAssertion.signal_id set
        -> STOP (no publication, no FH-D4 activity - both remain separate,
           future, explicitly-authorized steps)

This module is the lightweight-path SIBLING to
app.services.governed_signal_creation.create_signal_from_approved_review() -
it never imports, calls, or modifies that function's own write path.
`app.services.governed_signal_creation` remains completely unmodified; the
heavy Discovery Engine governance path (EB5 identity gate,
intelligence_review_decision, promotion_policy_decision) is untouched and
this module never relaxes it for any row it governs.

WHY A SEPARATE FUNCTION, NOT A MODIFIED create_signal_from_approved_review():
that function's identity/intelligence/promotion-policy preconditions are
genuinely unsatisfiable, permanently, by design, for known-Airport-staged
funding evidence (see docs/architecture, RWI HQ "Funding Human Review &
Promotion Contract Recon" mission, Section 3) - those three fields are never
populated by app.services.known_airport_evidence_persistence, on purpose.
Weakening that function's gate to admit NULL governance state would also
admit every OTHER kind of ungoverned row it currently, correctly refuses.

REUSED DIRECTLY, UNMODIFIED (never reimplemented):
  - app.services.known_airport_funding_lightweight_path_guard.
    check_lightweight_funding_path_eligibility() - the SAME field-shape gate
    app.services.known_airport_funding_reviewer_action already applies, so
    the two modules can never disagree about eligibility.
  - app.services.reviewer_action_persistence.get_latest_reviewer_action() -
    the SAME "latest by (created_at, id) recency" definition of "current"
    used everywhere else in this pipeline.
  - app.services.existing_signal_reconciliation_candidates.
    build_reconciliation_subject() / find_reconciliation_candidates() and
    app.services.existing_signal_reconciliation.
    evaluate_existing_signal_reconciliation() - the R1/R2 reconciliation core,
    confirmed (recon mission Section 3) to depend on nothing but
    SourceAssertion's own structural columns (airport_id, runway_id,
    source_id, artifact_identity, installation_assertion_links) and an
    airport-scoped Signal query - no identity_guard_decision/
    intelligence_review_decision/promotion_policy_decision dependency
    anywhere, so it is exactly as valid for this lightweight path as for the
    heavy one. This module never reimplements any anchor rule, compatibility
    rule, or candidate-discovery SQL - R1/R2 remain the only truth source.
  - app.services.governed_signal_creation.ExistingSignalPossibleMatchError /
    GovernedSignalCreationResult - reused as-is rather than duplicated, so a
    caller handling either path's result/exception shape needs no
    path-specific branching.

WHAT THIS MODULE DELIBERATELY NARROWS RELATIVE TO THE HEAVY PATH (smallest
appropriate surface, not merely "everything create_signal_from_approved_review
accepts minus two fields"):
  - Only `APPROVE_SIGNAL` is a valid latest action here - unlike the heavy
    path, `CONFIRM_DISTINCT_SIGNAL` is NOT accepted (that R4B/R4C mechanism
    is a follow-on resolution to a blocked heavy-pipeline approval; this
    lightweight path has no analogous stale-confirmation-fingerprint
    concept and does not invent one).
  - No `runway_id` parameter - known-Airport funding evidence has no
    established runway-resolution mechanism of its own (out of scope for
    this slice).
  - No `likely_supplier`/`supplier`/`supplier_reason` parameters at all -
    "no supplier inference" (recon mission's own hard invariant) is
    guaranteed structurally, the same way the dollar-value exclusion is,
    by never exposing the parameter, not merely by never auto-filling it.
  - No `claims` parameter - no funding pipeline currently governed by this
    slice produces structured Claim objects; `build_reconciliation_subject`
    is always called with an empty claims tuple.
  - NO `estimated_total_value_usd` / `estimated_emas_value_usd` parameter,
    at all - identical exclusion and identical rationale to the heavy path
    (see that module's own docstring: a single "amount" field cannot
    distinguish an advance-deposit from a CIP ceiling from a confirmed
    contract value). No amount is ever read from `source_assertion.
    raw_relevant_text` either - this module never inspects that column.

CALLER SUPPLIES NO SIGNAL OBJECT; PUBLICATION IS NEVER A PARAMETER: identical
to the heavy path - every Signal this module creates is hardcoded
`published=False`.

Never commits and never imports app.database.SessionLocal - mutates the
caller-supplied Session and flushes only so a constraint violation surfaces
immediately; the caller owns the transaction boundary entirely. Signal
creation and the SourceAssertion.signal_id link happen inside the same
uncommitted transaction, exactly matching the heavy path's own atomicity
reasoning.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Signal, SourceAssertion
from app.models.signal import DEFAULT_SCORE_BY_CONFIDENCE
from app.services.existing_signal_reconciliation import (
    ExistingSignalReconciliationOutcome,
    evaluate_existing_signal_reconciliation,
)
from app.services.existing_signal_reconciliation_candidates import (
    build_reconciliation_subject,
    find_reconciliation_candidates,
)
from app.services.governed_signal_creation import (
    ExistingSignalPossibleMatchError,
    GovernedSignalCreationResult,
)
from app.services.known_airport_funding_lightweight_path_guard import (
    check_lightweight_funding_path_eligibility,
)
from app.services.reviewer_action_persistence import get_latest_reviewer_action

__all__ = [
    "create_signal_from_lightweight_funding_review",
]

# Mirrors app.services.governed_signal_creation._DISALLOWED_INITIAL_STATUSES
# exactly - deliberately reimplemented, not imported (that name is private
# to its own module), for the identical reason: a mere human review approval
# must never establish a terminal/confirmed-sounding status.
_DISALLOWED_INITIAL_STATUSES = frozenset({"completed", "awarded", "executed", "contracted"})

# Unlike the heavy path's VALID_LATEST_ACTIONS_FOR_CREATION, this lightweight
# path accepts only APPROVE_SIGNAL - see module docstring "WHAT THIS MODULE
# DELIBERATELY NARROWS".
_VALID_LATEST_ACTION_FOR_CREATION = "APPROVE_SIGNAL"

_CompatibilitySignature = tuple


def _validate_human_selected_fields(
    *, title: str, category: str, confidence: str, status: "Optional[str]",
) -> None:
    """Deliberately reimplemented (five short checks), not imported, from
    app.services.governed_signal_creation._validate_human_selected_fields -
    that name is private to its own module and this function must never
    modify or import from it. Byte-for-byte the same rules."""
    if not title.strip():
        raise ValueError("title is required")
    if not category.strip():
        raise ValueError("category is required")
    if confidence not in DEFAULT_SCORE_BY_CONFIDENCE:
        raise ValueError(f"confidence must be one of {sorted(DEFAULT_SCORE_BY_CONFIDENCE)!r}, got {confidence!r}")
    if status is not None:
        if not status.strip():
            raise ValueError("status, if given, must not be blank")
        if status.strip().lower() in _DISALLOWED_INITIAL_STATUSES:
            raise ValueError(
                f"status {status!r} is not a state human review approval alone can establish "
                f"(disallowed: {sorted(_DISALLOWED_INITIAL_STATUSES)!r})"
            )


def _resolve_reference_year(
    *,
    target_year: "Optional[int]",
    planning_year: "Optional[int]",
    procurement_year: "Optional[int]",
    construction_start: "Optional[date]",
    completion_date: "Optional[date]",
) -> "Optional[int]":
    """Deliberately reimplemented, not imported, from
    app.services.governed_signal_creation._resolve_reference_year - a
    five-line field-precedence convention (not reconciliation decision
    logic), reused byte-for-byte so both modules feed R1/R2 the identical
    priority order. See that module's own docstring for why this is
    reimplemented rather than duplicated as a cross-module private import."""
    for value in (target_year, planning_year, procurement_year):
        if value is not None:
            return value
    if construction_start is not None:
        return construction_start.year
    if completion_date is not None:
        return completion_date.year
    return None


def _check_lightweight_governance_gates(session: Session, source_assertion: SourceAssertion):
    """The lightweight-path counterpart to
    app.services.governed_signal_creation._check_governance_gates(). Returns
    the latest ReviewerAction. Raises ValueError (or
    NotLightweightFundingAssertionError, a ValueError subclass) for any
    unmet precondition."""
    # source_external_id resolved here, exactly like the ReviewerAction
    # sibling module - the guard function itself never touches the
    # relationship (RWI HQ "Lightweight Funding Eligibility Hardening").
    source = source_assertion.source
    check_lightweight_funding_path_eligibility(
        source_assertion, source_external_id=source.external_id if source is not None else None,
    )

    if source_assertion.airport_id is None:
        raise ValueError("source_assertion.airport_id is required for lightweight funding Signal creation")

    latest = get_latest_reviewer_action(session, source_assertion.id)
    if latest is None:
        raise ValueError("no ReviewerAction has been recorded for this SourceAssertion")
    if latest.source_assertion_id != source_assertion.id:
        raise ValueError("latest ReviewerAction does not belong to this SourceAssertion")
    if latest.action != _VALID_LATEST_ACTION_FOR_CREATION:
        raise ValueError(
            f"latest ReviewerAction is {latest.action!r}, not {_VALID_LATEST_ACTION_FOR_CREATION!r} - "
            "a historical approval superseded by a later action is not sufficient"
        )
    return latest


def create_signal_from_lightweight_funding_review(
    session: Session,
    source_assertion: SourceAssertion,
    *,
    title: str,
    category: str,
    confidence: str,
    status: "Optional[str]" = None,
    notes: "Optional[str]" = None,
    source_notes: "Optional[str]" = None,
    target_year: "Optional[int]" = None,
    planning_year: "Optional[int]" = None,
    procurement_year: "Optional[int]" = None,
    construction_start: "Optional[date]" = None,
    completion_date: "Optional[date]" = None,
    manual_year_estimate: "Optional[int]" = None,
    last_verified_at: "Optional[date]" = None,
) -> GovernedSignalCreationResult:
    """Validates the lightweight governance gate and every human-selected
    field, evaluates the existing-Signal reconciliation guard (R1/R2,
    reused unmodified), then either creates exactly one new internal Signal
    (published=False) or, on an idempotent repeat, reuses the one already
    linked via source_assertion.signal_id. Never commits; calls
    session.flush() so any constraint violation surfaces immediately. Never
    mutates ReviewerAction.

    Raises ExistingSignalPossibleMatchError (imported from
    app.services.governed_signal_creation, see its own docstring) before
    touching any Signal or source_assertion.signal_id if reconciliation
    finds a genuine identity anchor to an existing Signal.
    """
    _validate_human_selected_fields(title=title, category=category, confidence=confidence, status=status)
    _check_lightweight_governance_gates(session, source_assertion)

    reference_year = _resolve_reference_year(
        target_year=target_year, planning_year=planning_year, procurement_year=procurement_year,
        construction_start=construction_start, completion_date=completion_date,
    )
    reconciliation_subject = build_reconciliation_subject(
        source_assertion, (), category=category, reference_year=reference_year,
    )
    reconciliation_candidates = find_reconciliation_candidates(session, source_assertion)
    reconciliation_decision = evaluate_existing_signal_reconciliation(
        reconciliation_subject, reconciliation_candidates,
    )
    if reconciliation_decision.outcome == ExistingSignalReconciliationOutcome.POSSIBLE_EXISTING_SIGNAL_MATCH:
        # Fails closed before any Signal is constructed, added, or flushed,
        # and before source_assertion.signal_id is touched. Unreachable when
        # source_assertion.signal_id is already set - R1's own ALREADY_LINKED
        # short-circuit fires first in that case (identical reasoning to the
        # heavy path - see app.services.governed_signal_creation's own
        # "RECONCILIATION GATE" docstring section). No CONFIRM_DISTINCT_SIGNAL
        # escape hatch here - see module docstring "WHAT THIS MODULE
        # DELIBERATELY NARROWS".
        raise ExistingSignalPossibleMatchError(reconciliation_decision)

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
        return GovernedSignalCreationResult(
            signal=existing, created=False, source_assertion_id=source_assertion.id,
            reconciliation_decision=reconciliation_decision,
        )

    signal = Signal(
        airport_id=source_assertion.airport_id,
        runway_id=None,
        source_id=source_assertion.source_id,
        title=title,
        category=category,
        confidence=confidence,
        status=status,
        probability_score=DEFAULT_SCORE_BY_CONFIDENCE[confidence],
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

    return GovernedSignalCreationResult(
        signal=signal, created=True, source_assertion_id=source_assertion.id,
        reconciliation_decision=reconciliation_decision,
    )
