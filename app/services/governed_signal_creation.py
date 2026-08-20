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

RECONCILIATION GATE (R3 of docs/architecture/existing-signal-reconciliation-
guard-design.md's own S16 roadmap - see
docs/architecture/existing-signal-reconciliation-r3-governed-creation-report.md).
Immediately before any `Signal(...)` construction or
`source_assertion.signal_id` assignment, this function builds the
already-reviewed, already-committed R1/R2 reconciliation inputs
(`app.services.existing_signal_reconciliation_candidates.build_reconciliation_subject`/
`find_reconciliation_candidates`) and evaluates them through the unmodified
R1 pure core (`app.services.existing_signal_reconciliation.
evaluate_existing_signal_reconciliation`). This module never reimplements
any anchor rule, compatibility rule, latest-link-supersession logic,
candidate-discovery SQL, or disconfirming-evidence rule - R1/R2 remain the
only truth sources for reconciliation semantics; this function only ever
calls them and interprets their three-outcome result:

    ALREADY_LINKED    -> redundant, by construction, with the pre-existing
                          `source_assertion.signal_id is not None` idempotent-
                          reuse branch immediately below (R1's own
                          ALREADY_LINKED short-circuit fires unconditionally
                          whenever `signal_id` is already set, before it ever
                          looks at candidates - so this outcome can never
                          coincide with a fresh-creation attempt; no new
                          branching is needed to "handle" it).
    POSSIBLE_EXISTING_SIGNAL_MATCH
                       -> fails closed: raises before any Signal is
                          constructed, added, or flushed, and before
                          `source_assertion.signal_id` is touched. Never
                          auto-selects a candidate, never records
                          MARK_DUPLICATE, never links anything itself -
                          resolving a possible match remains an exclusively
                          human decision, exercised the same way it always
                          has been (a later, separate ReviewerAction).
    CLEAR_TO_CREATE    -> creation proceeds exactly as before. Any
                          `advisory_candidate_signal_ids`/`advisory_reasons`
                          the decision carries travel through on
                          `GovernedSignalCreationResult.reconciliation_decision`
                          for explainability only - never inspected as a
                          gating condition by this function, and never
                          persisted anywhere (no Signal field, no notes
                          field, no hidden write).

STALE-SAFE CREATION (R4C of
docs/architecture/existing-signal-reconciliation-r4-human-resolution-design.md's
own S20 roadmap - see
docs/architecture/existing-signal-reconciliation-r4c-stale-safe-creation-report.md).
The latest ReviewerAction may now be `CONFIRM_DISTINCT_SIGNAL` as well as
`APPROVE_SIGNAL` (`VALID_LATEST_ACTIONS_FOR_CREATION`) - but only
`APPROVE_SIGNAL` is a general creation approval. When the latest action is
`CONFIRM_DISTINCT_SIGNAL` and reconciliation (recomputed fresh, exactly as
for any other call - never "trust the last answer") still returns
`POSSIBLE_EXISTING_SIGNAL_MATCH`, this function builds a fresh
`ReconciliationReviewPlan` for the CURRENT blocking state
(`app.services.existing_signal_reconciliation_review.build_reconciliation_review_plan`,
unmodified R4A) and compares its freshly computed fingerprint
(`compute_reconciliation_fingerprint`, also unmodified R4A) against the
confirmation's own stored `reconciliation_fingerprint`, byte-for-byte. A
match means the human's confirmed-distinct review is still current, and
creation proceeds through the unmodified path below; a mismatch raises
`StaleReconciliationConfirmationError` - the blocking state changed (a
candidate was added/removed, an anchor reason changed) since that
confirmation was recorded, so it can no longer authorize creation. If
reconciliation instead returns `CLEAR_TO_CREATE` while the latest action is
`CONFIRM_DISTINCT_SIGNAL`, the stored confirmation is simply moot - nothing
blocks creation any more, so nothing is compared or consulted, and creation
proceeds exactly as it would for `APPROVE_SIGNAL` (design doc Section 14
step 6). This module never reimplements R4A's canonicalization or hashing
logic, never builds a plan for any outcome other than
`POSSIBLE_EXISTING_SIGNAL_MATCH`, and never records, mutates, or reads any
`ReviewerAction` field beyond `.action` and `.reconciliation_fingerprint` on
the single row `get_latest_reviewer_action()` already returns.

`claims` is an optional, already-structured `tuple[Claim, ...]` this
function never inspects itself beyond passing it straight through to
`build_reconciliation_subject()` - no raw text is re-extracted here, no
source-family-specific extractor is imported, and an empty default (`()`)
keeps every existing caller's behavior unchanged (empty claims simply means
no vendor-name/evidence-date compatibility evidence is available, which can
only ever narrow advisory metadata, never change whether a Signal is
created). The proposed Signal's own `category` and the same year-priority
order R2 already established for reading an *existing* Signal are reused,
verbatim, to enrich the reconciliation subject with the *proposed* Signal's
own structured fields - never inferred from title/notes/source_notes, never
"today," and `manual_year_estimate` is excluded from the priority list for
the same reason R2 excludes it when reading a candidate (design doc S6/S10
- an unverified personal guess must not become structural reconciliation
evidence). See `_resolve_reference_year()`'s own docstring for the exact
priority and how this module guards against it silently drifting from R2's.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app.models import ReviewerAction, Runway, Signal, SourceAssertion
from app.models.signal import DEFAULT_SCORE_BY_CONFIDENCE
from app.services.evidence_claim_semantics import Claim
from app.services.existing_signal_reconciliation import (
    ExistingSignalReconciliationDecision,
    ExistingSignalReconciliationOutcome,
    evaluate_existing_signal_reconciliation,
)
from app.services.existing_signal_reconciliation_candidates import (
    build_reconciliation_subject,
    find_reconciliation_candidates,
)
from app.services.existing_signal_reconciliation_review import (
    build_reconciliation_review_plan,
    compute_reconciliation_fingerprint,
)
from app.services.reviewer_action_persistence import get_latest_reviewer_action

__all__ = [
    "REQUIRED_IDENTITY_DECISION",
    "REQUIRED_INTELLIGENCE_DECISION",
    "REQUIRED_PROMOTION_DECISION",
    "VALID_LATEST_ACTIONS_FOR_CREATION",
    "GovernedSignalCreationResult",
    "ExistingSignalPossibleMatchError",
    "StaleReconciliationConfirmationError",
    "create_signal_from_approved_review",
    "link_source_assertion_to_duplicate_signal",
]

REQUIRED_IDENTITY_DECISION = "ATTACH_CONFIRMED"
REQUIRED_INTELLIGENCE_DECISION = "REVIEW_REQUIRED"
REQUIRED_PROMOTION_DECISION = "HUMAN_REVIEW_REQUIRED"

# R4C (docs/architecture/existing-signal-reconciliation-r4c-stale-safe-creation-report.md,
# Section 14 of docs/architecture/existing-signal-reconciliation-r4-human-resolution-design.md):
# the only two latest-ReviewerAction values that may reach reconciliation
# evaluation at all. CONFIRM_DISTINCT_SIGNAL is not a general creation
# approval - it additionally requires, below, that reconciliation currently
# returns POSSIBLE_EXISTING_SIGNAL_MATCH with a freshly recomputed
# fingerprint matching the one it was recorded with (see
# StaleReconciliationConfirmationError). Every other latest action still
# fails closed here exactly as before R4C.
VALID_LATEST_ACTIONS_FOR_CREATION = ("APPROVE_SIGNAL", "CONFIRM_DISTINCT_SIGNAL")

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
    False on an idempotent repeat that reused the existing one.

    `reconciliation_decision` is the full R1 `ExistingSignalReconciliationDecision`
    for this call - result-only, explainability metadata, never persisted
    anywhere. Populated on every successful call from
    `create_signal_from_approved_review()` (`CLEAR_TO_CREATE`, possibly
    carrying non-blocking `advisory_candidate_signal_ids`/`advisory_reasons`,
    or the trivial `ALREADY_LINKED` reached on an idempotent repeat); left
    `None` for `link_source_assertion_to_duplicate_signal()`, which is a
    separate, already-human-resolved path the reconciliation guard does not
    run for (Task 18 of the R3 mission: a human has already recorded
    MARK_DUPLICATE by the time that function is called - there is nothing
    left for a pre-creation guard to evaluate)."""

    signal: Signal
    created: bool
    source_assertion_id: int
    reconciliation_decision: Optional[ExistingSignalReconciliationDecision] = None


class ExistingSignalPossibleMatchError(ValueError):
    """Raised by `create_signal_from_approved_review()` when reconciliation
    finds a genuine structural identity anchor (design doc S5/S6) connecting
    the proposed evidence to one or more existing Signals - creation fails
    closed, before any Signal is constructed, added, or flushed, and before
    `source_assertion.signal_id` is touched.

    Subclasses `ValueError` (matching this module's own existing
    fail-closed-via-ValueError convention for every other governance-gate
    failure, so any caller already catching `ValueError` still catches this)
    but additionally carries the full `ExistingSignalReconciliationDecision`
    as a structured `.decision` attribute - `candidate_signal_ids` and
    `reasons` are directly inspectable without a raw DB query or string
    parsing of the exception message, which a bare `ValueError` could not
    offer. This is the one new exception type this slice introduces,
    deliberately narrow in scope: every other failure path in this module
    still raises a plain `ValueError`, unchanged."""

    def __init__(self, decision: ExistingSignalReconciliationDecision) -> None:
        self.decision = decision
        super().__init__(
            "create_signal_from_approved_review requires human reconciliation before a new "
            f"Signal may be created: candidate_signal_ids={decision.candidate_signal_ids!r}, "
            f"reasons={decision.reasons!r}"
        )


class StaleReconciliationConfirmationError(ValueError):
    """Raised by `create_signal_from_approved_review()` (R4C,
    docs/architecture/existing-signal-reconciliation-r4c-stale-safe-creation-report.md,
    Section 14 of docs/architecture/existing-signal-reconciliation-r4-human-
    resolution-design.md) when the latest ReviewerAction is
    CONFIRM_DISTINCT_SIGNAL but reconciliation still returns
    POSSIBLE_EXISTING_SIGNAL_MATCH and the freshly recomputed
    `compute_reconciliation_fingerprint()` value no longer matches the
    fingerprint that confirmation was recorded with - the blocking
    reconciliation state changed since a human reviewed and confirmed
    distinctness (a candidate was added or removed, an anchor reason
    changed, or the stored fingerprint was never genuinely derived from
    this SourceAssertion's own reconciliation state at all, e.g. copied
    from another assertion's confirmation).

    Deliberately distinct from `ExistingSignalPossibleMatchError` so a
    caller/human can tell "you never reviewed this" apart from "you
    reviewed this, but the world changed since then" - a meaningfully
    different, more actionable message (design doc's own Section 14
    reasoning). Exposes only opaque fingerprint strings and the same
    `ExistingSignalReconciliationDecision` `ExistingSignalPossibleMatchError`
    already exposes (candidate_signal_ids/reasons only - no title, no
    financial value, no raw evidence text, matching R1's own absent-field
    discipline). Never auto-fixes and never records a ReviewerAction itself
    - resolving staleness requires a new, separate, future human review
    (out of this module's scope, same as every other ReviewerAction write in
    this pipeline)."""

    def __init__(
        self,
        decision: ExistingSignalReconciliationDecision,
        stale_confirmation: ReviewerAction,
        *,
        current_fingerprint: str,
    ) -> None:
        self.current_reconciliation_decision = decision
        self.stored_fingerprint = stale_confirmation.reconciliation_fingerprint
        self.current_fingerprint = current_fingerprint
        super().__init__(
            "create_signal_from_approved_review: the latest CONFIRM_DISTINCT_SIGNAL "
            "confirmation's stored reconciliation_fingerprint no longer matches the freshly "
            "recomputed blocking reconciliation state - a new human review is required "
            f"(stored_fingerprint={self.stored_fingerprint!r}, current_fingerprint={self.current_fingerprint!r}, "
            f"current_candidate_signal_ids={decision.candidate_signal_ids!r})"
        )


# The same year-field priority order app.services.existing_signal_reconciliation_candidates
# already established for reading an EXISTING Signal candidate
# (`_YEAR_FIELD_PRIORITY`/`_signal_reference_year` there), reapplied here to
# the PROPOSED Signal's own structured year/date arguments so both sides of
# a reconciliation comparison use one consistent, documented convention.
# Deliberately reimplemented rather than imported: this is a five-line field-
# precedence convention, not reconciliation decision logic (design doc S3's
# "do not duplicate anchor/compatibility/latest-link/candidate-discovery/
# disconfirming logic" is about *those*, not this), and R1/R2 remain
# unmodified per this slice's own explicit instruction not to touch them
# without an independently demonstrated defect. `TestReferenceYearPriorityConsistency`
# in the test suite guards against the two ever silently drifting apart by
# comparing this function's output directly against R2's own
# `_signal_reference_year()` for equivalent field values on a real ORM Signal.
# `manual_year_estimate` is excluded for the identical reason R2 excludes it:
# a private, unverified personal guess must never become structural
# reconciliation evidence.
def _resolve_reference_year(
    *,
    target_year: Optional[int],
    planning_year: Optional[int],
    procurement_year: Optional[int],
    construction_start: Optional[date],
    completion_date: Optional[date],
) -> Optional[int]:
    for value in (target_year, planning_year, procurement_year):
        if value is not None:
            return value
    if construction_start is not None:
        return construction_start.year
    if completion_date is not None:
        return completion_date.year
    return None


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


def _check_governance_gates(session: Session, source_assertion: SourceAssertion) -> ReviewerAction:
    """Unchanged governance-decision checks, plus the latest-ReviewerAction
    check (R4C-widened, see VALID_LATEST_ACTIONS_FOR_CREATION). Returns the
    latest ReviewerAction itself so the caller can distinguish an
    APPROVE_SIGNAL from a CONFIRM_DISTINCT_SIGNAL latest action - this
    function only validates that the latest action is *one of* the two
    allowed values; it never inspects reconciliation_fingerprint or runs
    reconciliation itself (that happens only after this function returns,
    using fresh state - see create_signal_from_approved_review())."""
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
    if latest.action not in VALID_LATEST_ACTIONS_FOR_CREATION:
        raise ValueError(
            f"latest ReviewerAction is {latest.action!r}, not one of {VALID_LATEST_ACTIONS_FOR_CREATION!r} - "
            "a historical approval superseded by a later action is not sufficient"
        )
    return latest


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
    claims: "tuple[Claim, ...]" = (),
) -> GovernedSignalCreationResult:
    """Validates every governance gate and human-selected field, evaluates
    the existing-Signal reconciliation guard, then either creates exactly
    one new internal Signal (published=False) or, on an idempotent repeat,
    reuses the one already linked via source_assertion.signal_id. Never
    commits; calls session.flush() so any constraint violation surfaces
    immediately. Never mutates ReviewerAction.

    `claims` is optional, already-structured evidence (see module docstring)
    used only to enrich reconciliation's own compatibility metadata - never
    inspected for any other purpose by this function.

    Raises `ExistingSignalPossibleMatchError` (see its own docstring) before
    touching any Signal or `source_assertion.signal_id` if reconciliation
    finds a genuine identity anchor to an existing Signal.
    """
    _validate_human_selected_fields(title=title, category=category, confidence=confidence, status=status)
    latest_reviewer_action = _check_governance_gates(session, source_assertion)

    if runway_id is not None:
        runway = session.get(Runway, runway_id)
        if runway is None or runway.airport_id != source_assertion.airport_id:
            raise ValueError("runway_id, if given, must belong to the same airport as source_assertion")

    reference_year = _resolve_reference_year(
        target_year=target_year, planning_year=planning_year, procurement_year=procurement_year,
        construction_start=construction_start, completion_date=completion_date,
    )
    reconciliation_subject = build_reconciliation_subject(
        source_assertion, claims, category=category, reference_year=reference_year,
    )
    reconciliation_candidates = find_reconciliation_candidates(session, source_assertion)
    reconciliation_decision = evaluate_existing_signal_reconciliation(
        reconciliation_subject, reconciliation_candidates,
    )
    if reconciliation_decision.outcome == ExistingSignalReconciliationOutcome.POSSIBLE_EXISTING_SIGNAL_MATCH:
        # Fails closed before any Signal is constructed, added, or flushed,
        # and before source_assertion.signal_id is touched. Unreachable when
        # source_assertion.signal_id is already set - R1's own ALREADY_LINKED
        # short-circuit fires first in that case, unconditionally, before any
        # candidate is even inspected (module docstring's own "RECONCILIATION
        # GATE" section) - so this branch never conflicts with the existing
        # idempotent-reuse branch immediately below.
        #
        # R4C: a CONFIRM_DISTINCT_SIGNAL latest action may resolve this block
        # only if its stored fingerprint still matches a freshly rebuilt plan
        # for the CURRENT blocking state - R1/R2 are never told "trust the
        # last answer," they already recomputed unconditionally above (design
        # doc Section 14 step 5). The plan/fingerprint canonicalization logic
        # itself is never reimplemented here - build_reconciliation_review_plan()
        # and compute_reconciliation_fingerprint() (R4A) are the only
        # authority for both.
        if latest_reviewer_action.action == "CONFIRM_DISTINCT_SIGNAL":
            fresh_plan = build_reconciliation_review_plan(
                source_assertion_id=source_assertion.id,
                subject=reconciliation_subject,
                decision=reconciliation_decision,
            )
            fresh_fingerprint = compute_reconciliation_fingerprint(fresh_plan)
            if fresh_fingerprint != latest_reviewer_action.reconciliation_fingerprint:
                raise StaleReconciliationConfirmationError(
                    reconciliation_decision, latest_reviewer_action, current_fingerprint=fresh_fingerprint,
                )
            # Matched: this confirmation is current. Fall through to the
            # existing, unmodified creation path below - exactly the same
            # path an APPROVE_SIGNAL + CLEAR_TO_CREATE call already takes.
        else:
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

    return GovernedSignalCreationResult(
        signal=signal, created=True, source_assertion_id=source_assertion.id,
        reconciliation_decision=reconciliation_decision,
    )


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
