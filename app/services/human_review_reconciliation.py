"""Human review reconciliation awareness — read-only, deliberately separate
from app.services.human_review_queue (docs/architecture/existing-signal-
reconciliation-r4-human-resolution-design.md S15; R4D of that document's own
S20 roadmap — see docs/architecture/existing-signal-reconciliation-r4d-review-
queue-report.md).

    HumanReviewItem (app.services.human_review_queue, generic, cheap)
        -> list_reconciliation_review_items()
        -> tuple[ReconciliationReviewItem, ...] — one per eligible item,
           each carrying a FRESHLY recomputed R1/R2 reconciliation decision
           and, when blocking, the R4A plan/fingerprint
        -> STOP (no ReviewerAction, no Signal, no SourceAssertion mutation —
           this module answers exactly one question: "what does
           reconciliation currently say about this item, and what would a
           human reviewing it need to see?")

DELIBERATELY A SEPARATE MODULE FROM app.services.human_review_queue, exactly
mirroring app.services.human_review_claim_enrichment's own already-
established precedent of being "a deliberately separate concern, composed by
the caller" (design doc S15's own words) rather than folded into the base
queue. The design doc explicitly rejects widening `ReviewWorkflowState` or
`list_human_review_items()`'s own cheap read path: computing reconciliation
requires actually running R1/R2 (a real, bounded set of extra DB reads per
eligible row, unlike every other field the base queue already reads, which
are all already-persisted columns) — folding that cost into the base queue
would change its performance character for every caller, most of whom do
not need reconciliation awareness at all. `app/services/human_review_queue.py`
is not modified by this module and is never imported for write purposes —
only its already-committed, unmodified `HumanReviewItem`, `ReviewWorkflowState`,
and `list_review_workflow_items()` are reused, never duplicated.

REUSES R1/R2/R4A VERBATIM, NEVER REIMPLEMENTS THEM: `build_reconciliation_subject()`
and `find_reconciliation_candidates()` (R2), `evaluate_existing_signal_reconciliation()`
(R1), `build_reconciliation_review_plan()` and `compute_reconciliation_fingerprint()`
(R4A) are called exactly as `create_signal_from_approved_review()` (R3/R4C)
itself calls them — no anchor rule, compatibility rule, disconfirming rule,
latest-installation-link semantics, candidate-discovery SQL, canonicalization,
or hashing logic is reimplemented here. There is no local `hashlib`, no local
`json`, and no scoring/ranking of any kind anywhere in this module.

HUMAN-SELECTED-CONTEXT BOUNDARY: `category` and `reference_year` are
human-selected creation-time inputs to `create_signal_from_approved_review()`
— nothing on a governed `SourceAssertion` row (or its `claims`) represents
them before a human actually chooses them at creation time. This module
therefore always calls `build_reconciliation_subject(assertion, (), category=None,
reference_year=None)` — an empty `claims` tuple and no category/year context,
never fabricated, never inferred from `Claim.category`, free text, or
anything else. Because `category`/`vendor_names`/`evidence_date`/`reference_year`
are all COMPATIBILITY-tier fields in R1's own taxonomy (never anchor-tier —
see `app.services.existing_signal_reconciliation`'s own module docstring),
omitting them can only ever suppress non-blocking advisory metadata, never
manufacture a false anchor/block that `create_signal_from_approved_review()`
would not also independently find given the same governed, structural
evidence (`runway_id`, `physical_installation_ids`, `source_id`,
`artifact_identity`, `airport_id` — all already-persisted, structural
`SourceAssertion` fields, none of them human-selected creation-time
choices). A direct, mechanical consequence of this boundary: this module's
own reconciliation calls can never produce non-empty COMPATIBILITY/advisory
metadata (every compatibility-tier input is always empty/None) — so no
advisory field is exposed on `ReconciliationReviewItem` at all; adding one
would only ever be an always-empty placeholder, not real information (see
the R4D report's own "human-selected-context limitation" section for the
full reasoning).

ELIGIBILITY: reconciliation is computed only for rows where
`create_signal_from_approved_review()` (R4C) would itself even attempt it —
`source_assertion.signal_id` still `None` (once linked, R1's own
`ALREADY_LINKED` short-circuit — and this module's own identical
precondition — make reconciliation moot; `list_review_workflow_items()`'s
own `RESOLVED_SIGNAL_CREATED`/`RESOLVED_DUPLICATE` states already say
everything that needs saying) and the latest `ReviewerAction` is one of
`{None, "APPROVE_SIGNAL", "CONFIRM_DISTINCT_SIGNAL"}` — the exact
`VALID_LATEST_ACTIONS_FOR_CREATION` set R4C itself gates creation on, plus
"never reviewed yet." A row whose latest action is `DEFER`,
`NEEDS_MORE_EVIDENCE`, `REJECT_SIGNAL`, or `MARK_DUPLICATE` is excluded
entirely — reconciliation is never computed for it, and it never appears in
this module's output; `list_review_workflow_items()` remains the complete,
unfiltered picture for those states, unchanged.

LATEST ACTION ONLY, NO CHAIN-WALKING: this module calls
`app.services.reviewer_action_persistence.get_latest_reviewer_action()`
exactly once per eligible row — the same function, same
`(created_at DESC, id DESC)` ordering, R3/R4C already use. A historically
matching `CONFIRM_DISTINCT_SIGNAL` superseded by a later action never
controls anything here; only the current latest row does.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from sqlalchemy.orm import Session

from app.models import SourceAssertion
from app.services.existing_signal_reconciliation import (
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
from app.services.human_review_queue import HumanReviewItem, list_review_workflow_items
from app.services.reviewer_action_persistence import get_latest_reviewer_action

__all__ = [
    "ReconciliationReviewState",
    "ReconciliationReviewItem",
    "list_reconciliation_review_items",
]

# Exact names from the reviewed design doc (S15) — not invented here. A
# stale confirmation is deliberately NOT a third state: it behaves like
# RECONCILIATION_REVIEW_REQUIRED (a human still has a decision to make) but
# carries an extra, non-blocking `reconciliation_warnings` entry instead of
# growing the state vocabulary (mirroring HumanReviewItem.invariant_warnings).
class ReconciliationReviewState(str, Enum):
    RECONCILIATION_REVIEW_REQUIRED = "RECONCILIATION_REVIEW_REQUIRED"
    DISTINCT_CONFIRMED_PENDING_SIGNAL = "DISTINCT_CONFIRMED_PENDING_SIGNAL"


# The latest-action values reconciliation is meaningful for — exactly
# R4C's own VALID_LATEST_ACTIONS_FOR_CREATION, plus "never reviewed yet"
# (None). Reproduced literally rather than imported: importing
# governed_signal_creation here for one tuple constant would pull a write
# module's entire import surface into a read-only presentation module for
# no benefit — the two are already independently guarded against drift by
# TestLatestActionOnly::test_eligibility_actions_match_r4c_valid_latest_actions_exactly
# in the test suite, which compares this tuple directly against
# governed_signal_creation.VALID_LATEST_ACTIONS_FOR_CREATION.
_RECONCILIATION_ELIGIBLE_ACTIONS = (None, "APPROVE_SIGNAL", "CONFIRM_DISTINCT_SIGNAL")


@dataclass(frozen=True)
class ReconciliationReviewItem:
    """One `HumanReviewItem`, enriched — read-only, never persisted — with a
    freshly recomputed R1/R2 reconciliation decision and, when blocking, the
    R4A review plan's fingerprint. Composition, not inheritance or field
    duplication: `item` carries the complete, unmodified base 9D snapshot;
    every field below is additive.

    `reconciliation_outcome`: `"CLEAR_TO_CREATE"` or
    `"POSSIBLE_EXISTING_SIGNAL_MATCH"` — `ExistingSignalReconciliationOutcome`'s
    own `.value` strings, verbatim (`ALREADY_LINKED` is structurally
    unreachable here — see the module docstring's "ELIGIBILITY" section).

    `reconciliation_candidate_signal_ids`/`reconciliation_anchor_reasons`:
    populated only when `reconciliation_outcome ==
    "POSSIBLE_EXISTING_SIGNAL_MATCH"` — copied verbatim from R1's own
    `ExistingSignalReconciliationDecision.candidate_signal_ids`/`.reasons`
    (never `.advisory_*`, which this module never reads — see the module
    docstring's own human-selected-context-boundary section for why advisory
    metadata is never populated here).

    `reconciliation_fingerprint`: the R4A `compute_reconciliation_fingerprint()`
    value for the CURRENT blocking plan — populated only when
    `reconciliation_outcome == "POSSIBLE_EXISTING_SIGNAL_MATCH"`.

    `stored_reconciliation_fingerprint`: the latest `ReviewerAction`'s own
    `reconciliation_fingerprint` — populated only when the latest action is
    `CONFIRM_DISTINCT_SIGNAL` (regardless of whether it is still current).

    `reconciliation_review_state`: `None` when nothing currently blocks
    (`CLEAR_TO_CREATE` — any prior confirmation is moot, design doc S14 step
    6, not consulted) or one of `ReconciliationReviewState`'s two values
    when it does.

    `reconciliation_warnings`: read-only, narrow, never-repaired annotations
    — currently just the stale-confirmation note (design doc S15's own
    illustrative wording, adapted — see the module docstring). A
    `CONFIRM_DISTINCT_SIGNAL` row with a `NULL` stored fingerprint was
    evaluated as a candidate warning case and found provably unreachable —
    `ReviewerAction`'s own DB CHECK constraint
    (`ck_reviewer_actions_fingerprint_required`, R4B) enforces `NOT NULL`
    for exactly this action for every writer, including a direct ORM bypass
    of `record_reviewer_action()`'s own Python-level validation (verified by
    a dedicated test) — so no code path here ever needs to warn about it.
    Mirrors `HumanReviewItem.invariant_warnings` in spirit; kept as a
    separate tuple rather than appended to that one, since this module never
    mutates or extends the base `HumanReviewItem` it wraps."""

    item: HumanReviewItem
    reconciliation_outcome: str
    reconciliation_candidate_signal_ids: "tuple[int, ...]" = ()
    reconciliation_anchor_reasons: "tuple[str, ...]" = ()
    reconciliation_fingerprint: "str | None" = None
    stored_reconciliation_fingerprint: "str | None" = None
    reconciliation_review_state: "str | None" = None
    reconciliation_warnings: "tuple[str, ...]" = ()


def _reconciliation_applicable(item: HumanReviewItem) -> bool:
    return item.linked_signal_id is None and item.latest_reviewer_action in _RECONCILIATION_ELIGIBLE_ACTIONS


def _evaluate_item_reconciliation(session: Session, item: HumanReviewItem) -> "ReconciliationReviewItem | None":
    """Returns None if the row disappeared between the base queue read and
    this function's own re-fetch (an append-only pipeline makes this
    unreachable in practice, but this module fails visible-and-excluded
    rather than crashing on a read-only race it cannot itself resolve)."""
    assertion = session.get(SourceAssertion, item.source_assertion_id)
    if assertion is None:
        return None

    latest_action_row = get_latest_reviewer_action(session, item.source_assertion_id)
    # NOTE (evaluated, per the R4D mission's own "invariant warnings" ask):
    # a CONFIRM_DISTINCT_SIGNAL row with a NULL reconciliation_fingerprint
    # was considered as a possible invariant-warning case, but is provably
    # unreachable - app.models.reviewer_action.ReviewerAction's own DB CHECK
    # constraint (ck_reviewer_actions_fingerprint_required) enforces
    # NOT NULL for exactly this action at the database level, for every
    # writer (record_reviewer_action() and any direct ORM bypass alike).
    # No warning is generated for it; see the R4D report's own "invariant
    # warnings" section for this conclusion and how it was verified.
    stored_fingerprint = (
        latest_action_row.reconciliation_fingerprint
        if latest_action_row is not None and latest_action_row.action == "CONFIRM_DISTINCT_SIGNAL"
        else None
    )
    warnings: "list[str]" = []

    # Human-selected-context boundary (module docstring): category/reference_year
    # are never fabricated at queue time.
    subject = build_reconciliation_subject(assertion, (), category=None, reference_year=None)
    candidates = find_reconciliation_candidates(session, assertion)
    decision = evaluate_existing_signal_reconciliation(subject, candidates)

    if decision.outcome == ExistingSignalReconciliationOutcome.CLEAR_TO_CREATE:
        # Design doc S14 step 6: nothing currently blocks, so any prior
        # confirmation is moot and not consulted - reconciliation_review_state
        # stays None, matching the base item's own workflow state.
        return ReconciliationReviewItem(
            item=item,
            reconciliation_outcome=decision.outcome.value,
            stored_reconciliation_fingerprint=stored_fingerprint,
            reconciliation_warnings=tuple(warnings),
        )

    # decision.outcome == POSSIBLE_EXISTING_SIGNAL_MATCH (ALREADY_LINKED is
    # unreachable - excluded by _reconciliation_applicable()'s own
    # signal_id-None precondition, mirroring R1's own short-circuit).
    plan = build_reconciliation_review_plan(
        source_assertion_id=item.source_assertion_id, subject=subject, decision=decision,
    )
    fresh_fingerprint = compute_reconciliation_fingerprint(plan)

    if stored_fingerprint is not None and stored_fingerprint == fresh_fingerprint:
        review_state = ReconciliationReviewState.DISTINCT_CONFIRMED_PENDING_SIGNAL
    else:
        review_state = ReconciliationReviewState.RECONCILIATION_REVIEW_REQUIRED
        if stored_fingerprint is not None:
            # Genuinely stale, not "never reviewed" - design doc S15's own
            # illustrative wording, adapted: R4B/R4A deliberately persist
            # only the fingerprint, never the originally-reviewed candidate
            # set (R4B's own "compact immutable reference" choice - see its
            # report), so the PREVIOUS candidate_signal_ids cannot honestly
            # be shown here, only that the stored value no longer matches.
            warnings.append(
                "STALE_RECONCILIATION_CONFIRMATION: the latest CONFIRM_DISTINCT_SIGNAL "
                "confirmation's stored fingerprint no longer matches the current blocking "
                f"reconciliation state (current candidate_signal_ids={decision.candidate_signal_ids!r}) "
                "- a new human review is required."
            )

    return ReconciliationReviewItem(
        item=item,
        reconciliation_outcome=decision.outcome.value,
        reconciliation_candidate_signal_ids=decision.candidate_signal_ids,
        reconciliation_anchor_reasons=decision.reasons,
        reconciliation_fingerprint=fresh_fingerprint,
        stored_reconciliation_fingerprint=stored_fingerprint,
        reconciliation_review_state=review_state.value,
        reconciliation_warnings=tuple(warnings),
    )


def list_reconciliation_review_items(
    session: Session, *, limit: "int | None" = None,
) -> "tuple[ReconciliationReviewItem, ...]":
    """Every governed SourceAssertion where fresh reconciliation is
    meaningful to compute right now (see the module docstring's
    "ELIGIBILITY" section) — each enriched with a freshly recomputed R1/R2
    decision and, when blocking, the R4A plan's fingerprint. Rows already
    resolved (`MARK_DUPLICATE`, a linked Signal, `REJECT_SIGNAL`, `DEFER`,
    `NEEDS_MORE_EVIDENCE`) are excluded entirely — reconciliation is never
    computed for them; use `app.services.human_review_queue.list_review_workflow_items()`
    for the complete, unfiltered picture of every state.

    Read-only: every session call here (this module's own `session.get()`,
    plus `find_reconciliation_candidates()`'s and `get_latest_reviewer_action()`'s
    own SELECT-only queries) is a read. No `session.add()`, no
    `session.flush()`, no `session.commit()`, no attribute assignment on any
    ORM-mapped instance, anywhere in this module.

    QUERY COST (bounded, not batched across items - see the R4D report's own
    "query behavior" section): each eligible row costs one `session.get()`,
    one `get_latest_reviewer_action()` query, and `find_reconciliation_candidates()`'s
    own already-established three-batched-query shape (R2) - the same
    per-subject cost `create_signal_from_approved_review()` itself already
    pays for one row. For the realistic queue sizes this pipeline has always
    assumed (single digits to low dozens, per `human_review_queue.py`'s own
    documented trade-off), this is acceptable and untested-regression-free;
    it is NOT batched across the whole eligible set, unlike the base queue's
    own `_latest_actions_by_assertion()`, because doing so would require
    either reimplementing R2's own candidate-discovery batching logic here
    (forbidden - R1/R2 remain the only truth sources) or changing R2 itself
    to accept a batch of subjects, out of this slice's scope.

    `limit`, if given, bounds the result AFTER eligibility filtering and
    reconciliation evaluation - not the raw base-queue SQL query - exactly
    matching `list_human_review_items()`'s own established discipline (the
    Slice 8 "limit consumed by non-matching rows before filtering" defect
    this mirrors the fix for).
    """
    base_items = list_review_workflow_items(session)
    eligible = [item for item in base_items if _reconciliation_applicable(item)]
    evaluated = tuple(
        result
        for result in (_evaluate_item_reconciliation(session, item) for item in eligible)
        if result is not None
    )
    if limit is not None:
        evaluated = evaluated[:limit]
    return evaluated
