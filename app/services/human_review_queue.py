"""Human review queue — read-only, workflow-aware governed-evidence query
(docs/architecture/human-review-workflow-awareness-slice9d-report.md, Slice
9D of docs/architecture/reviewer-action-human-signal-promotion-slice9-design.md;
built on Slice 8's own docs/architecture/human-review-queue-slice8-report.md).

    SourceAssertion (already governed: identity + intelligence review +
    promotion policy, all already persisted by Slices 1/4/7)
        + latest ReviewerAction, if any (Slice 9B) + signal_id, if any
          (Slice 9C)
        -> list_human_review_items()
        -> tuple[HumanReviewItem, ...] restricted to items still needing a
           human DECISION
        -> STOP (no reviewer actions, no Signal write - a future,
           separately-authorized slice)

Answers exactly one question: "which already-governed SourceAssertion rows
need a human's judgment RIGHT NOW, and what does a reviewer need to see to
make that judgment?" - not "which rows were ever promotion_policy_decision
== HUMAN_REVIEW_REQUIRED" (Slice 8's own, now-superseded question). It never
asks "create/update a Signal," never performs a reviewer action, and never
writes anything - `session.scalars()`/`session.execute()`/`session.query()`
(SELECT only) are the only session calls this module makes. No
`session.add()`, no `session.flush()`, no `session.commit()` anywhere.

SLICE 9D WORKFLOW-AWARENESS: real SourceAssertion #222 exposed the exact
defect this slice fixes. #222 was resolved by a human as MARK_DUPLICATE
(linked to existing Signal #67), yet Slice 8's own queue kept listing it
forever, because it only ever read promotion_policy_decision - an
append-only, historical classification that never changes once computed -
and had no concept of ReviewerAction (Slice 9B) or SourceAssertion.signal_id
(Slice 9C) at all. `derive_workflow_state()` below closes that gap by
deriving a small, purely-computed (never persisted) classification from the
latest ReviewerAction plus signal_id, reusing
app.services.reviewer_action_persistence.get_latest_reviewer_action()'s own
ordering semantics rather than duplicating them.

GENERIC, NOT SOURCE-SPECIFIC: this module has no knowledge of MAC/Granicus
or any other source family - it reads only fields every governed
SourceAssertion row already carries (identity/intelligence/promotion
decision-and-reason pairs, raw evidence, Source/Airport metadata, reviewer
history, Signal linkage). It does NOT import app.acquisition.mac_granicus_claims
or any other source-specific extractor - claim re-derivation, where
available, is a deliberately separate concern
(app.services.human_review_claim_enrichment), composed by the CLI, never
required by this module to function.

BASE FILTER (unchanged from Slice 8): `SourceAssertion.promotion_policy_decision
== PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED.value` exactly. No fuzzy
interpretation - AUTO_ELIGIBLE, DO_NOT_PROMOTE, NULL, and any unrecognized
value are all excluded identically. NULL is never treated as "needs review"
- an unevaluated row is not a queue item, it is simply not yet evaluated
(design doc's own "history never deleted, never silently promoted" posture).

INVARIANT WARNINGS, NEVER SILENT NORMALIZATION: promotion policy is only
ever computed for evidence whose identity is ATTACH_CONFIRMED and whose
intelligence review is REVIEW_REQUIRED (app.services.promotion_policy_evaluation's
own outcome mapping) - so a HUMAN_REVIEW_REQUIRED row should always carry
`identity_guard_decision == "ATTACH_CONFIRMED"` and `intelligence_review_decision
== "REVIEW_REQUIRED"`. Slice 9D adds a second class of invariant check: a
latest ReviewerAction whose own fields disagree with SourceAssertion.signal_id
in a way that should never happen given the write paths already committed
(e.g. MARK_DUPLICATE's own duplicate_of_signal_id disagreeing with the
actual signal_id link, or a REJECT_SIGNAL/DEFER/NEEDS_MORE_EVIDENCE action
coexisting with a signal_id that should never have been set for those
outcomes). Both classes are surfaced as explicit `HumanReviewItem.invariant_warnings`
entries - never corrected, never hidden, never modified. (An
"APPROVE_SIGNAL linked to an unrelated/drifted Signal" cannot be checked
here - there is no second, independent source of truth for which Signal
*should* be linked; that drift check already lives at Slice 9C's own
creation-time compatibility-signature comparison.)

SOURCE AUTHORITY: `HumanReviewItem.source_reliability_level_raw` carries
`Source.reliability_level` verbatim, explicitly named and documented as the
existing coarse field - never relabeled as, or treated as equivalent to,
`app.services.promotion_policy_evaluation.SourceAuthorityTier` (design doc
S12/S15's own already-documented gap; this module does not attempt to close
it, only to avoid conflating the two).

SOURCE-FAMILY DISPATCH FIELDS (docs/architecture/rwi-usaspending-legacy-
claims-extractor-design.md): `source_type`/`source_published_date` carry
`Source.source_type`/`Source.published_date` verbatim - generic, already-
persisted Source metadata, not a new concept. They exist so
app.services.human_review_claim_enrichment can dispatch to a source-family-
specific claims adapter using (source_type, parser_identifier) rather than
parser_identifier alone (needed because a shared legacy backfill
parser_identifier can span multiple, structurally different source_type
families), and so an adapter can anchor a claim's TemporalContext.as_of_date
to the cited document's own persisted date without re-parsing it from raw
text. This module still has no knowledge of MAC/Granicus, USAspending, or
any other source-specific extractor - it only exposes one more already-
joined, source-agnostic Source field, exactly like source_title/
source_publisher already are.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import SourceAssertion
from app.models.reviewer_action import ReviewerAction
from app.services.known_airport_funding_lightweight_path_guard import funding_namespace_for
from app.services.promotion_policy_evaluation import PromotionPolicyOutcome
from app.services.signal_candidate_evaluation import SignalCandidateOutcome

__all__ = [
    "HumanReviewItem",
    "ReviewWorkflowState",
    "list_human_review_items",
    "list_review_workflow_items",
    "StagedEvidenceItem",
    "GOVERNANCE_STAGE_STAGED_UNREVIEWED",
    "STAGED_ATTENTION_WORKFLOW_STATE_VALUES",
    "list_staged_evidence_items",
    "list_staged_evidence_needing_attention_items",
]

_EXPECTED_IDENTITY_DECISION = "ATTACH_CONFIRMED"
_EXPECTED_INTELLIGENCE_DECISION = SignalCandidateOutcome.REVIEW_REQUIRED.value
_QUEUE_DECISION = PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED.value


class ReviewWorkflowState(str, Enum):
    """A read-only, purely-derived classification of "what state is this
    governed SourceAssertion's human review actually in" - never persisted
    anywhere, recomputed on every read from (promotion_policy_decision
    already being HUMAN_REVIEW_REQUIRED, latest ReviewerAction, signal_id).
    Deliberately the smallest vocabulary that distinguishes every reviewer
    action outcome plus the "never reviewed yet" case - not a workflow
    engine, no transitions, no persistence, no scheduler."""

    ACTIVE_REVIEW = "ACTIVE_REVIEW"
    DEFERRED = "DEFERRED"
    NEEDS_MORE_EVIDENCE = "NEEDS_MORE_EVIDENCE"
    APPROVED_PENDING_SIGNAL = "APPROVED_PENDING_SIGNAL"
    RESOLVED_REJECTED = "RESOLVED_REJECTED"
    RESOLVED_DUPLICATE = "RESOLVED_DUPLICATE"
    RESOLVED_SIGNAL_CREATED = "RESOLVED_SIGNAL_CREATED"


# The states that mean "a human still has a decision to make right now."
# NEEDS_MORE_EVIDENCE is included deliberately: this slice adds no
# acquisition trigger and no scheduler, so excluding it would make an item
# vanish from all visibility the moment a reviewer asks for more evidence,
# with no committed path back into view. DEFERRED is excluded deliberately
# (task's own recommendation) - a human already chose to set it aside;
# resurfacing it automatically would need a scheduler, explicitly out of
# scope. APPROVED_PENDING_SIGNAL is excluded per this slice's own explicit
# instruction: the human's decision is already made (APPROVE_SIGNAL); what
# remains is a separate governed-creation operation, not a second review.
_ACTIVE_QUEUE_STATES = (ReviewWorkflowState.ACTIVE_REVIEW, ReviewWorkflowState.NEEDS_MORE_EVIDENCE)
_ACTIVE_QUEUE_STATE_VALUES = frozenset(state.value for state in _ACTIVE_QUEUE_STATES)

# Actions whose own semantics require signal_id to still be NULL - a human
# who rejected, deferred, or asked for more evidence never authorized any
# Signal linkage; if signal_id is set anyway, that is a real, surfaced
# invariant violation, never silently ignored.
_ACTIONS_EXPECTING_NULL_SIGNAL_ID = ("DEFER", "NEEDS_MORE_EVIDENCE", "REJECT_SIGNAL")


@dataclass(frozen=True)
class HumanReviewItem:
    """One queue entry - an immutable, ORM-free snapshot of one governed
    SourceAssertion row's already-persisted fields, plus its purely-derived
    reviewer-workflow state. Never a live ORM instance (safe to
    hold/compare/serialize after the session closes)."""

    source_assertion_id: int
    airport_id: "int | None"
    airport_name: "str | None"
    airport_code: "str | None"

    source_id: int
    source_type: "str | None"
    source_title: "str | None"
    source_publisher: "str | None"
    source_url: "str | None"
    source_document_reference: "str | None"
    source_reliability_level_raw: "str | None"
    source_published_date: "date | None"

    artifact_identity: "str | None"
    source_locator: "str | None"
    raw_fragment_hash: "str | None"
    raw_relevant_text: "str | None"
    parser_identifier: "str | None"

    identity_guard_decision: "str | None"
    identity_guard_reason: "str | None"
    intelligence_review_decision: "str | None"
    intelligence_review_reason: "str | None"
    promotion_policy_decision: str
    promotion_policy_reason: str

    # Slice 9D additions - all purely derived/read-only, never persisted.
    latest_reviewer_action: "str | None"
    linked_signal_id: "int | None"
    review_workflow_state: str

    invariant_warnings: "tuple[str, ...]" = ()


def _airport_code(airport) -> "str | None":
    if airport is None:
        return None
    return airport.iata_code or airport.icao_code or airport.faa_code


def derive_workflow_state(
    latest_action: Optional[ReviewerAction], signal_id: Optional[int],
) -> ReviewWorkflowState:
    """Pure function, no I/O: the one place this module's workflow
    vocabulary (ReviewWorkflowState) is computed. Assumes the caller already
    confirmed promotion_policy_decision == HUMAN_REVIEW_REQUIRED (this
    function does not re-check that - it only classifies what to do given a
    row already in that base population)."""
    if latest_action is None:
        return ReviewWorkflowState.ACTIVE_REVIEW
    if latest_action.action == "DEFER":
        return ReviewWorkflowState.DEFERRED
    if latest_action.action == "NEEDS_MORE_EVIDENCE":
        return ReviewWorkflowState.NEEDS_MORE_EVIDENCE
    if latest_action.action == "REJECT_SIGNAL":
        return ReviewWorkflowState.RESOLVED_REJECTED
    if latest_action.action == "MARK_DUPLICATE":
        return ReviewWorkflowState.RESOLVED_DUPLICATE
    if latest_action.action == "APPROVE_SIGNAL":
        return (
            ReviewWorkflowState.RESOLVED_SIGNAL_CREATED
            if signal_id is not None
            else ReviewWorkflowState.APPROVED_PENDING_SIGNAL
        )
    # Unreachable given ReviewerAction's own DB CHECK constraint (exactly
    # these five action values) - if it is ever reached anyway (e.g. a
    # future action value added to the vocabulary without updating this
    # function), fail toward showing the item to a human rather than
    # silently hiding it as resolved.
    return ReviewWorkflowState.ACTIVE_REVIEW


def _reviewer_action_invariant_warnings(
    latest_action: Optional[ReviewerAction], signal_id: Optional[int],
) -> "tuple[str, ...]":
    if latest_action is None:
        return ()
    warnings: "list[str]" = []
    if latest_action.action == "MARK_DUPLICATE":
        if signal_id != latest_action.duplicate_of_signal_id:
            warnings.append(
                f"INVARIANT_VIOLATION: latest ReviewerAction is MARK_DUPLICATE "
                f"(duplicate_of_signal_id={latest_action.duplicate_of_signal_id!r}) but "
                f"source_assertion.signal_id={signal_id!r} - target and link disagree."
            )
    elif latest_action.action in _ACTIONS_EXPECTING_NULL_SIGNAL_ID:
        if signal_id is not None:
            warnings.append(
                f"INVARIANT_VIOLATION: latest ReviewerAction is {latest_action.action} but "
                f"source_assertion.signal_id={signal_id!r} is already set - these should be "
                "mutually exclusive."
            )
    return tuple(warnings)


def _invariant_warnings(
    assertion: SourceAssertion, latest_action: Optional[ReviewerAction],
) -> "tuple[str, ...]":
    warnings: "list[str]" = []
    if assertion.identity_guard_decision != _EXPECTED_IDENTITY_DECISION:
        warnings.append(
            f"INVARIANT_VIOLATION: promotion_policy_decision={_QUEUE_DECISION!r} but "
            f"identity_guard_decision={assertion.identity_guard_decision!r} (expected "
            f"{_EXPECTED_IDENTITY_DECISION!r}) - identity/intelligence/promotion review may "
            f"have been evaluated at different times against different evidence."
        )
    if assertion.intelligence_review_decision != _EXPECTED_INTELLIGENCE_DECISION:
        warnings.append(
            f"INVARIANT_VIOLATION: promotion_policy_decision={_QUEUE_DECISION!r} but "
            f"intelligence_review_decision={assertion.intelligence_review_decision!r} (expected "
            f"{_EXPECTED_INTELLIGENCE_DECISION!r})."
        )
    warnings.extend(_reviewer_action_invariant_warnings(latest_action, assertion.signal_id))
    return tuple(warnings)


def _latest_actions_by_assertion(
    session: Session, source_assertion_ids: Sequence[int],
) -> "dict[int, ReviewerAction]":
    """Batched equivalent of calling
    app.services.reviewer_action_persistence.get_latest_reviewer_action()
    once per id - avoids N+1 by fetching every candidate row for the whole
    id set in one query, ordered by the identical (created_at desc, id desc)
    tiebreak that function uses, then keeping only the first (= latest) row
    per source_assertion_id. Never mutates reviewer ordering semantics -
    tested directly against get_latest_reviewer_action() for equivalence."""
    if not source_assertion_ids:
        return {}
    rows = (
        session.query(ReviewerAction)
        .filter(ReviewerAction.source_assertion_id.in_(source_assertion_ids))
        .order_by(
            ReviewerAction.source_assertion_id,
            ReviewerAction.created_at.desc(),
            ReviewerAction.id.desc(),
        )
        .all()
    )
    latest: "dict[int, ReviewerAction]" = {}
    for row in rows:
        latest.setdefault(row.source_assertion_id, row)  # first per group, per the ORDER BY above, is the latest
    return latest


def _to_item(assertion: SourceAssertion, latest_action: Optional[ReviewerAction]) -> HumanReviewItem:
    airport = assertion.airport
    source = assertion.source
    workflow_state = derive_workflow_state(latest_action, assertion.signal_id)
    return HumanReviewItem(
        source_assertion_id=assertion.id,
        airport_id=assertion.airport_id,
        airport_name=airport.name if airport else None,
        airport_code=_airport_code(airport),
        source_id=assertion.source_id,
        source_type=source.source_type if source else None,
        source_title=source.title if source else None,
        source_publisher=source.publisher if source else None,
        source_url=source.url if source else None,
        source_document_reference=source.document_reference if source else None,
        source_reliability_level_raw=source.reliability_level if source else None,
        source_published_date=source.published_date if source else None,
        artifact_identity=assertion.artifact_identity,
        source_locator=assertion.source_locator,
        raw_fragment_hash=assertion.raw_fragment_hash,
        raw_relevant_text=assertion.raw_relevant_text,
        parser_identifier=assertion.parser_identifier,
        identity_guard_decision=assertion.identity_guard_decision,
        identity_guard_reason=assertion.identity_guard_reason,
        intelligence_review_decision=assertion.intelligence_review_decision,
        intelligence_review_reason=assertion.intelligence_review_reason,
        promotion_policy_decision=assertion.promotion_policy_decision,
        promotion_policy_reason=assertion.promotion_policy_reason,
        latest_reviewer_action=latest_action.action if latest_action else None,
        linked_signal_id=assertion.signal_id,
        review_workflow_state=workflow_state.value,
        invariant_warnings=_invariant_warnings(assertion, latest_action),
    )


def list_review_workflow_items(session: Session, *, limit: "int | None" = None) -> "tuple[HumanReviewItem, ...]":
    """Every SourceAssertion with promotion_policy_decision ==
    HUMAN_REVIEW_REQUIRED, regardless of derived workflow state - the full
    picture (active, deferred, needs-more-evidence, approved-pending,
    resolved-rejected, resolved-duplicate, resolved-signal-created), each
    row annotated with its own `review_workflow_state`. This is both Slice
    8's original raw-policy listing (preserved, not removed - see
    list_human_review_items() below for the now-narrower default) and the
    audit/debugging "all workflow states" view; kept as one function rather
    than two, since the underlying query and row-shaping are identical and
    only the filtering differs. Read-only: SELECT only, never
    add/flush/commit. Ordered by `created_at` descending, `id` descending
    tiebreak. `limit`, if given, bounds the result directly in SQL."""
    stmt = (
        select(SourceAssertion)
        .where(SourceAssertion.promotion_policy_decision == _QUEUE_DECISION)
        .options(selectinload(SourceAssertion.airport), selectinload(SourceAssertion.source))
        .order_by(SourceAssertion.created_at.desc(), SourceAssertion.id.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = session.scalars(stmt).all()
    latest_by_id = _latest_actions_by_assertion(session, [row.id for row in rows])
    return tuple(_to_item(row, latest_by_id.get(row.id)) for row in rows)


def list_human_review_items(session: Session, *, limit: "int | None" = None) -> "tuple[HumanReviewItem, ...]":
    """The default human-decision queue. SEMANTIC REFINEMENT (Slice 9D):
    previously (Slice 8) this returned every row with promotion_policy_decision
    == HUMAN_REVIEW_REQUIRED, full stop - an append-only classification that
    never changes once computed, so a row stayed listed forever even after a
    human fully resolved it via a later ReviewerAction. It now returns only
    rows whose derived `review_workflow_state` is ACTIVE_REVIEW or
    NEEDS_MORE_EVIDENCE - i.e. rows that still need a human DECISION right
    now. DEFERRED, APPROVED_PENDING_SIGNAL, and every RESOLVED_* state are
    excluded - see list_review_workflow_items() for the complete,
    unfiltered picture (e.g. for audit, or a future "all/resolved" CLI
    view).

    Because workflow state depends on ReviewerAction history (not knowable
    from SQL alone without the same batched lookup list_review_workflow_items()
    already performs), `limit` here bounds the ACTIVE result after
    filtering, not the raw SQL query - unlike Slice 8's original version,
    which could apply `LIMIT` directly in SQL because it never needed to
    look past promotion_policy_decision. Real-world row counts (single
    digits to low dozens) make this an acceptable, correctness-preserving
    trade-off; ordering itself is still fully deterministic (`created_at`
    desc, `id` desc)."""
    items = list_review_workflow_items(session)
    active = tuple(item for item in items if item.review_workflow_state in _ACTIVE_QUEUE_STATE_VALUES)
    if limit is not None:
        active = active[:limit]
    return active


# ---------------------------------------------------------------------------
# Staged evidence lane (RWI HQ "Commander Review Queue - Staged Evidence
# Lane", following the recon in "Commander Review Queue Coverage").
#
# EVERYTHING ABOVE THIS POINT IS UNCHANGED. list_review_workflow_items() and
# list_human_review_items() still answer exactly the same question they
# always have - "which already-fully-governed SourceAssertion needs a human
# promotion decision right now" - via the exact same, untouched
# promotion_policy_decision == HUMAN_REVIEW_REQUIRED predicate. This section
# adds a SEPARATE, PARALLEL read path for a genuinely different population:
# preserved candidate evidence that was never routed through IdentityGuard /
# intelligence review / promotion policy at all (app.services.
# stage_only_evidence_persistence and app.services.known_airport_evidence_persistence
# both hardcode identity_guard_decision/intelligence_review_decision/
# promotion_policy_decision to NULL at construction, by design - see those
# modules' own docstrings). NULL is not "pending governance evaluation" for
# this population; it is "governance evaluation does not apply to this
# evidence class at all." Conflating the two into one predicate (e.g. "OR
# promotion_policy_decision IS NULL" on the existing queue) would silently
# blur two structurally different meanings of NULL under one list - this
# module keeps them as two entirely separate query functions instead, so
# neither can accidentally widen the other.
# ---------------------------------------------------------------------------

# Presentation-only label (never persisted, never added to any model/enum) -
# the ONLY value list_staged_evidence_items() ever produces. Deliberately
# NOT a ReviewWorkflowState member: a staged row has not entered that
# workflow's population at all, so borrowing its vocabulary (even a new
# member on that same enum) would misleadingly suggest it eventually will,
# on the same track, once "reviewed" in whatever sense that enum implies.
GOVERNANCE_STAGE_STAGED_UNREVIEWED = "STAGED_UNREVIEWED"


@dataclass(frozen=True)
class StagedEvidenceItem:
    """One staged-evidence queue entry - an immutable, ORM-free snapshot,
    structurally parallel to HumanReviewItem but for a different
    population. `identity_guard_decision`/`intelligence_review_decision`/
    `promotion_policy_decision`/`linked_signal_id` are always None for
    every row this module's own predicate can return (enforced by that
    predicate, not merely expected) - carried here anyway, explicitly, so
    a caller/renderer states "not evaluated" from a real field rather than
    inventing that text from nothing. `governance_stage` is always
    GOVERNANCE_STAGE_STAGED_UNREVIEWED - see that constant's own docstring
    for why it is not a ReviewWorkflowState member.

    RWI HQ "Staged Evidence Attention States" additions (all read-time
    derived, never persisted, never affecting which rows this module's
    own structural predicate returns):

    `review_workflow_state` reuses ReviewWorkflowState's own existing,
    unmodified vocabulary and derive_workflow_state() function - the SAME
    engine the heavy governed queue already uses - computed from
    (latest_reviewer_action, signal_id) exactly like that queue's own
    HumanReviewItem.review_workflow_state. This answers "what is the
    Commander's current attention state for this staged row," entirely
    separate from `governance_stage` (which only ever answers "did this
    row pass through the heavy identity/intelligence/promotion pipeline" -
    always No, for this whole population, by design).

    `latest_reviewer_action*` fields mirror HumanReviewItem's own
    `latest_reviewer_action` field, extended with the reviewer/reason/
    created_at a Commander needs to see WITHOUT a second lookup - the
    append-only ReviewerAction history itself is never summarized or
    altered; a superseded action remains in the database, simply not
    reflected here (this module's own "latest wins" convention, unchanged).

    `funding_provenance` is a pure namespace LOOKUP
    (app.services.known_airport_funding_lightweight_path_guard.funding_namespace_for) -
    "faa_aip"/"usaspending" if recognized, else None - NEVER an
    eligibility decision. A None value here does not mean "ineligible for
    a reason this module determined" - it means only "this Source's
    external_id does not carry a namespace this module recognizes";
    app.services.known_airport_funding_lightweight_path_guard.check_lightweight_funding_path_eligibility()
    remains the one and only eligibility authority anywhere in this
    pipeline."""

    source_assertion_id: int
    airport_id: "int | None"
    airport_name: "str | None"
    airport_code: "str | None"

    source_id: int
    source_type: "str | None"
    source_title: "str | None"
    source_publisher: "str | None"
    source_url: "str | None"
    source_document_reference: "str | None"
    source_reliability_level_raw: "str | None"
    source_published_date: "date | None"

    artifact_identity: "str | None"
    source_locator: "str | None"
    raw_fragment_hash: "str | None"
    raw_relevant_text: "str | None"
    parser_identifier: "str | None"

    assertion_type: str
    evidence_quality: "str | None"
    review_state: "str | None"

    identity_guard_decision: "str | None"
    intelligence_review_decision: "str | None"
    promotion_policy_decision: "str | None"
    linked_signal_id: "int | None"

    governance_stage: str = GOVERNANCE_STAGE_STAGED_UNREVIEWED

    review_workflow_state: str = ReviewWorkflowState.ACTIVE_REVIEW.value
    funding_provenance: "str | None" = None
    latest_reviewer_action_id: "int | None" = None
    latest_reviewer_action: "str | None" = None
    latest_reviewer_action_reason: "str | None" = None
    latest_reviewer_action_reviewer: "str | None" = None
    latest_reviewer_action_created_at: "datetime | None" = None


def _staged_evidence_predicate():
    """The narrowest predicate that matches exactly the persisted shape
    app.services.stage_only_evidence_persistence and
    app.services.known_airport_evidence_persistence actually write - never
    a bare `promotion_policy_decision IS NULL` (that alone would also catch
    any other row that happens to have an unevaluated/malformed governance
    state for an unrelated reason). Every condition below is a real,
    verified invariant of those two persistence paths, not a guess:

      identity_guard_decision IS NULL        - never set by either path
      intelligence_review_decision IS NULL   - never set by either path
      promotion_policy_decision IS NULL      - never set by either path
      evidence_quality == "unverified_candidate" - hardcoded by both paths
      review_state == "unreviewed"           - hardcoded by both paths
      signal_id IS NULL                      - excludes a row already
          promoted via the lightweight known-airport-funding review path
          (app.services.known_airport_funding_reviewer_action /
          known_airport_funding_signal_creation) - once a Signal exists,
          the row already had Commander attention and is no longer
          "awaiting" it, even though promotion_policy_decision itself is
          still NULL (that field is never populated by the lightweight
          path either - see the real, evidenced case of a
          formerly-staged row that IS excluded by this signal_id check
          alone, in tests/test_human_review_queue.py).
      unknown_airport_candidate_id IS NULL   - excludes any row already
          routed through UAC candidate formation - that shape implies
          SOME identity-resolution work already happened, which is a
          meaningfully different situation from evidence that has never
          been examined at all, even though its own
          identity_guard_decision may also be NULL at this exact moment.

    A row satisfying every one of these is, by construction, indistinguishable
    from a genuine stage-only or known-airport-staged SourceAssertion - this
    predicate identifies the SHAPE those two persistence paths produce, not
    an arbitrary "everything else" catch-all.
    """
    return (
        SourceAssertion.identity_guard_decision.is_(None),
        SourceAssertion.intelligence_review_decision.is_(None),
        SourceAssertion.promotion_policy_decision.is_(None),
        SourceAssertion.evidence_quality == "unverified_candidate",
        SourceAssertion.review_state == "unreviewed",
        SourceAssertion.signal_id.is_(None),
        SourceAssertion.unknown_airport_candidate_id.is_(None),
    )


def _to_staged_item(assertion: SourceAssertion, latest_action: "Optional[ReviewerAction]") -> StagedEvidenceItem:
    airport = assertion.airport
    source = assertion.source
    workflow_state = derive_workflow_state(latest_action, assertion.signal_id)
    return StagedEvidenceItem(
        source_assertion_id=assertion.id,
        airport_id=assertion.airport_id,
        airport_name=airport.name if airport else None,
        airport_code=_airport_code(airport),
        source_id=assertion.source_id,
        source_type=source.source_type if source else None,
        source_title=source.title if source else None,
        source_publisher=source.publisher if source else None,
        source_url=source.url if source else None,
        source_document_reference=source.document_reference if source else None,
        source_reliability_level_raw=source.reliability_level if source else None,
        source_published_date=source.published_date if source else None,
        artifact_identity=assertion.artifact_identity,
        source_locator=assertion.source_locator,
        raw_fragment_hash=assertion.raw_fragment_hash,
        raw_relevant_text=assertion.raw_relevant_text,
        parser_identifier=assertion.parser_identifier,
        assertion_type=assertion.assertion_type,
        evidence_quality=assertion.evidence_quality,
        review_state=assertion.review_state,
        identity_guard_decision=assertion.identity_guard_decision,
        intelligence_review_decision=assertion.intelligence_review_decision,
        promotion_policy_decision=assertion.promotion_policy_decision,
        linked_signal_id=assertion.signal_id,
        review_workflow_state=workflow_state.value,
        funding_provenance=funding_namespace_for(source.external_id if source else None),
        latest_reviewer_action_id=latest_action.id if latest_action else None,
        latest_reviewer_action=latest_action.action if latest_action else None,
        latest_reviewer_action_reason=latest_action.reason if latest_action else None,
        latest_reviewer_action_reviewer=latest_action.reviewer if latest_action else None,
        latest_reviewer_action_created_at=latest_action.created_at if latest_action else None,
    )


def list_staged_evidence_items(session: Session, *, limit: "int | None" = None) -> "tuple[StagedEvidenceItem, ...]":
    """Read-only: SELECT only, never add/flush/commit/delete/update. Never
    mutates any SourceAssertion field, never creates a Signal. DOES read
    ReviewerAction, as of RWI HQ "Staged Evidence Attention States" -
    solely to compute the read-time-derived `review_workflow_state`/
    `latest_reviewer_action*` fields; nothing about which rows this
    function RETURNS changes because of this (the structural predicate,
    `_staged_evidence_predicate()`, is completely unmodified - a row with
    ReviewerAction history is neither added nor removed from this result
    by that history alone). Ordered by `created_at` descending, `id`
    descending tiebreak - identical convention to
    list_review_workflow_items(). `limit`, if given, bounds the query
    directly in SQL - the structural predicate itself is still fully
    expressible in SQL with no further Python-side FILTERING needed
    afterward (the ReviewerAction batch-lookup below only enriches rows
    already selected, it never excludes one), so a direct SQL LIMIT
    remains correctness-preserving here."""
    stmt = (
        select(SourceAssertion)
        .where(*_staged_evidence_predicate())
        .options(selectinload(SourceAssertion.airport), selectinload(SourceAssertion.source))
        .order_by(SourceAssertion.created_at.desc(), SourceAssertion.id.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = session.scalars(stmt).all()
    latest_by_id = _latest_actions_by_assertion(session, [row.id for row in rows])
    return tuple(_to_staged_item(row, latest_by_id.get(row.id)) for row in rows)


# The Commander-attention subset of the staged population (RWI HQ "Staged
# Evidence Attention States", Question 2 - "what staged evidence needs
# Commander attention now" - as distinct from list_staged_evidence_items()'s
# own Question 1, "what evidence is still structurally staged"). Reuses
# ReviewWorkflowState's own existing vocabulary verbatim - RESOLVED_REJECTED
# and RESOLVED_DUPLICATE are deliberately excluded: a human has already
# acted and no further Commander decision is currently expected, though the
# evidence itself remains fully visible under list_staged_evidence_items()
# (Question 1) - never hidden. DEFERRED/RESOLVED_SIGNAL_CREATED are not
# reachable for this population today (DEFER is outside
# LIGHTWEIGHT_FUNDING_REVIEWER_ACTIONS; a signal_id-set row is already
# excluded by _staged_evidence_predicate() itself) but are excluded here
# too, for the same reason they would be excluded if they ever did occur.
_STAGED_ATTENTION_STATES = (
    ReviewWorkflowState.ACTIVE_REVIEW,
    ReviewWorkflowState.NEEDS_MORE_EVIDENCE,
    ReviewWorkflowState.APPROVED_PENDING_SIGNAL,
)
STAGED_ATTENTION_WORKFLOW_STATE_VALUES = frozenset(state.value for state in _STAGED_ATTENTION_STATES)


def list_staged_evidence_needing_attention_items(
    session: Session, *, limit: "int | None" = None,
) -> "tuple[StagedEvidenceItem, ...]":
    """Question 2: staged evidence whose CURRENT derived
    `review_workflow_state` is one of STAGED_ATTENTION_WORKFLOW_STATE_VALUES.
    Mirrors list_human_review_items()'s own "fetch everything, filter by
    derived state, then limit in Python" discipline exactly (never applying
    `limit` to the raw SQL query first) - the same review-checkpoint fix
    that function's own docstring documents applies identically here: an
    SQL-level limit could otherwise consume only non-attention rows and
    leave zero for this filter to find, even when attention rows exist
    further down. Read-only; never mutates anything."""
    items = list_staged_evidence_items(session)
    attention = tuple(item for item in items if item.review_workflow_state in STAGED_ATTENTION_WORKFLOW_STATE_VALUES)
    if limit is not None:
        attention = attention[:limit]
    return attention
