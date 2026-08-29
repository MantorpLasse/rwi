"""Validation and creation of immutable human reviewer-action records
(docs/architecture/reviewer-action-persistence-slice9b-report.md, Slice 9B of
docs/architecture/reviewer-action-human-signal-promotion-slice9-design.md).

    SourceAssertion (already governed: identity_guard_decision,
        intelligence_review_decision, promotion_policy_decision all set)
        -> record_reviewer_action()
        -> one immutable ReviewerAction row appended
        -> STOP (no Signal, no publication, no automatic promotion - all
           future, separately-authorized slices)

This module intentionally contains no Signal-creation, Signal-update, or
Signal-publication behavior whatsoever - it never imports the `Signal`
constructor's write path and never sets any attribute on a Signal instance.
`Signal` is imported only to validate that a `duplicate_of_signal_id` target
actually exists, exactly the same read-only existence check
app.services.physical_installation_reconciliation.record_reconciliation_decision
performs against `PhysicalInstallationIdentity`. APPROVE_SIGNAL records only
that a human authorized a later, separate, not-yet-built governed
Signal-creation operation (Slice 9C) to act - it is not that operation
itself, and this module never calls one.

Modeled directly on
app.services.physical_installation_reconciliation.record_reconciliation_decision,
the exact structural precedent for validating and inserting a human-reviewed,
append-only decision about a SourceAssertion. Never commits and never imports
app.database.SessionLocal - it mutates the caller-supplied Session and
flushes only so a constraint violation (including the immutability event
listeners on ReviewerAction) surfaces immediately; the caller owns the
transaction boundary entirely, mirroring every other persistence service in
this pipeline (intelligence_review_persistence, promotion_policy_persistence,
physical_installation_reconciliation).

APPROVAL GATE, fail-closed: APPROVE_SIGNAL (and, below, CONFIRM_DISTINCT_SIGNAL)
may only be recorded where all three governed decisions already agree:

    EFFECTIVE identity decision   == "ATTACH_CONFIRMED"
    intelligence_review_decision  == "REVIEW_REQUIRED"
    promotion_policy_decision     == "HUMAN_REVIEW_REQUIRED"

RAW-vs-EFFECTIVE IDENTITY ("RWI - Raw-vs-Effective APPROVE_SIGNAL Gate -
Narrow Fix" mission - the known inconsistency this mission closed): the
identity component of this gate reads
`app.services.effective_identity_guard_decision.resolve_effective_identity_guard_decision()`
(EB5) - never `SourceAssertion.identity_guard_decision` (the raw column)
directly - for BOTH APPROVE_SIGNAL and CONFIRM_DISTINCT_SIGNAL, exactly
mirroring the MARK_DUPLICATE gate's own already-established EB5 reuse
below. Before this fix, a genuinely governed row like SA235 (raw
ATTACH_PROVISIONAL, effective ATTACH_CONFIRMED via
CROSS_SOURCE_ALIAS_ATTESTATION) could never legally receive APPROVE_SIGNAL
or CONFIRM_DISTINCT_SIGNAL, even though its identity is exactly as
governedly confirmed as a row whose raw column happens to already read
ATTACH_CONFIRMED - the SAME inconsistency MARK_DUPLICATE's own gate was
built to avoid for legacy-attested rows. `intelligence_review_decision`/
`promotion_policy_decision` remain RAW-column checks, unchanged - EB5 has
no analog for those two decisions and this mission does not invent one.
The raw `identity_guard_decision` column itself is never read, compared,
or mutated by this gate any more - it remains a permanent, immutable,
purely historical fact, exactly as EB5's own module docstring already
requires of every consumer.

`intelligence_review_decision` is checked explicitly even though, given the
current pipeline's own construction, a row can never reach
promotion_policy_decision="HUMAN_REVIEW_REQUIRED" without already having
intelligence_review_decision="REVIEW_REQUIRED" - the same "checked
explicitly rather than assumed" defense-in-depth discipline
app.services.promotion_policy_persistence and
app.services.human_review_queue's own invariant warnings already apply to
this exact chain. AUTO_ELIGIBLE and DO_NOT_PROMOTE are both refused here:
AUTO_ELIGIBLE belongs to a future, separate automation/audit route (Slice
9's own design doc suggested it as also human-approvable, but this slice's
own governing instruction narrows that - see the Slice 9B report's "design
corrections discovered" section for why); DO_NOT_PROMOTE is an absolute
block no reviewer action can override at this boundary. HUMAN_REVIEW_REQUIRED
items do NOT need to first become AUTO_ELIGIBLE to be approved - that
distinction from the design doc is preserved.

MARK_DUPLICATE IDENTITY GATE (rwi-mark-duplicate-upstream-governance-gate
design/review): MARK_DUPLICATE may only be recorded when the SourceAssertion's
EFFECTIVE identity decision - `app.services.effective_identity_guard_decision.
resolve_effective_identity_guard_decision()`, never the raw
`identity_guard_decision` column directly - is `ATTACH_CONFIRMED`. Reusing
EB5 here (rather than duplicating APPROVE_SIGNAL/CONFIRM_DISTINCT_SIGNAL's own
raw-column check above) is deliberate and load-bearing: a legacy-attested row
like SA81 has `identity_guard_decision` permanently NULL by the legacy
attestation mechanism's own design (it never writes that column), yet its
identity is genuinely governed via `LEGACY_HUMAN_ATTESTATION` - checking the
raw column here would make MARK_DUPLICATE permanently unreachable for every
legacy-attested row, the opposite of this gate's purpose. Existing-Signal
reconciliation (R1-R4) is itself structurally identity-blind - it can
correctly compute a genuine `POSSIBLE_EXISTING_SIGNAL_MATCH` for a
SourceAssertion whose own airport attachment was never human-confirmed at
all (a raw legacy import default) - so without this gate, MARK_DUPLICATE
could permanently link an unverified airport attachment to a real, named
Signal before a human ever confirmed the assertion belongs there. Reconciled
against the real historical precedent (SourceAssertion #222 -> Signal #67):
that MARK_DUPLICATE was recorded only after `identity_guard_decision`
already read `ATTACH_CONFIRMED`, satisfying this gate with zero behavior
change.

DELIBERATELY NOT REQUIRED FOR MARK_DUPLICATE: `intelligence_review_decision`
and `promotion_policy_decision`. Those two answer "should THIS evidence,
taken alone, become a NEW Signal" - a question MARK_DUPLICATE never asks (it
creates no Signal and never reads claims at all); it only asks "does this
evidence, whatever its own materiality, refer to an entity that already has
one." Requiring intelligence review first would block a genuine, safe
reconciliation for a reason structurally unrelated to duplicate-recognition.
This action intentionally terminates the new-Signal path before that
question is ever reached.

CONFIRM_DISTINCT_SIGNAL GATE (R4B,
docs/architecture/existing-signal-reconciliation-r4b-reviewer-action-report.md):
this action is a follow-on resolution to an already-approved governed
creation attempt that R3's reconciliation guard blocked, not a replacement
for the original human approval - so it is gated by the exact same three
governed decisions as APPROVE_SIGNAL, above. A SourceAssertion that could
never legally receive APPROVE_SIGNAL (AUTO_ELIGIBLE, DO_NOT_PROMOTE, or a
malformed/NULL governance state) can never receive CONFIRM_DISTINCT_SIGNAL
either. This module also requires a syntactically valid R4A
reconciliation_fingerprint (exactly 64 lowercase hex characters - the
literal shape app.services.existing_signal_reconciliation_review.
compute_reconciliation_fingerprint() produces) for every
CONFIRM_DISTINCT_SIGNAL and forbids one for every other action, and
requires supersedes_action_id to be set (this action must resolve some
prior row in the same SourceAssertion's history - see the review-checkpoint
correction note in the R4B report; a rootless confirmation with no
predecessor is refused rather than left as ambiguous history). None of
this recomputes or validates the fingerprint against a live reconciliation
plan itself - only that a downstream integration (R4C, not yet built) can do:
checking whether this stored fingerprint still matches the *current*
blocking state requires re-running R1/R2/R4A fresh at creation time, which
this persistence-only module deliberately does not do (see "R4B/R4C
boundary" in the R4B report). R4B stores authorization evidence; R4C
decides whether that evidence is still valid.
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Signal, SourceAssertion
from app.models.reviewer_action import REVIEWER_ACTIONS, ReviewerAction
from app.services.effective_identity_guard_decision import resolve_effective_identity_guard_decision
from app.services.evidence_attachment_guard import AttachmentOutcome

__all__ = [
    "REQUIRED_IDENTITY_DECISION_FOR_APPROVAL",
    "REQUIRED_INTELLIGENCE_DECISION_FOR_APPROVAL",
    "REQUIRED_PROMOTION_DECISION_FOR_APPROVAL",
    "REQUIRED_EFFECTIVE_IDENTITY_FOR_MARK_DUPLICATE",
    "get_latest_reviewer_action",
    "record_reviewer_action",
]

REQUIRED_IDENTITY_DECISION_FOR_APPROVAL = "ATTACH_CONFIRMED"
REQUIRED_INTELLIGENCE_DECISION_FOR_APPROVAL = "REVIEW_REQUIRED"
REQUIRED_PROMOTION_DECISION_FOR_APPROVAL = "HUMAN_REVIEW_REQUIRED"
REQUIRED_EFFECTIVE_IDENTITY_FOR_MARK_DUPLICATE = AttachmentOutcome.ATTACH_CONFIRMED

# The literal shape app.services.existing_signal_reconciliation_review.
# compute_reconciliation_fingerprint() produces: hashlib.sha256(...).hexdigest()
# is always exactly 64 lowercase hex characters. Anchored, no whitespace
# allowed anywhere - deliberately stricter than str.strip()+lower() would
# permit, since silently normalizing a malformed value would let a
# non-fingerprint string through disguised as one.
_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_ACTIONS_REQUIRING_APPROVAL_GATE = ("APPROVE_SIGNAL", "CONFIRM_DISTINCT_SIGNAL")


def record_reviewer_action(
    session: Session,
    source_assertion: SourceAssertion,
    *,
    action: str,
    reason: str,
    reviewer: str,
    supersedes_action_id: Optional[int] = None,
    duplicate_of_signal_id: Optional[int] = None,
    reconciliation_fingerprint: Optional[str] = None,
) -> ReviewerAction:
    """Validates and appends exactly one ReviewerAction row. Never commits;
    calls session.flush() only so a constraint violation surfaces
    immediately. Never mutates source_assertion, never mutates or creates a
    Signal.
    """
    if action not in REVIEWER_ACTIONS:
        raise ValueError(f"action must be one of {REVIEWER_ACTIONS!r}, got {action!r}")
    if not reason.strip():
        raise ValueError("reason is required for a reviewer action")
    if not reviewer.strip():
        raise ValueError("reviewer is required for a reviewer action")

    if source_assertion.id is None:
        raise ValueError("source_assertion must already be persisted (has no id)")
    if session.get(SourceAssertion, source_assertion.id) is None:
        raise ValueError("referenced SourceAssertion does not exist")

    if action == "MARK_DUPLICATE":
        if duplicate_of_signal_id is None:
            raise ValueError("MARK_DUPLICATE requires duplicate_of_signal_id")
        if session.get(Signal, duplicate_of_signal_id) is None:
            raise ValueError("referenced Signal (duplicate_of_signal_id) does not exist")
        # See module docstring "MARK_DUPLICATE IDENTITY GATE" - the EFFECTIVE
        # decision (EB5), never the raw column, so a legacy-attested row
        # (raw identity_guard_decision permanently NULL) is correctly
        # treated as governed. Deliberately does NOT also require
        # intelligence_review_decision/promotion_policy_decision - see the
        # same docstring section for why.
        effective = resolve_effective_identity_guard_decision(session, source_assertion_id=source_assertion.id)
        if effective.effective_decision != REQUIRED_EFFECTIVE_IDENTITY_FOR_MARK_DUPLICATE:
            raise ValueError(
                f"MARK_DUPLICATE requires the effective identity decision "
                f"(resolve_effective_identity_guard_decision) to be "
                f"{REQUIRED_EFFECTIVE_IDENTITY_FOR_MARK_DUPLICATE.value!r}, got "
                f"{effective.effective_decision.value!r} (basis={effective.basis.value!r})"
            )
    elif duplicate_of_signal_id is not None:
        raise ValueError("duplicate_of_signal_id is only valid when action == MARK_DUPLICATE")

    if action in _ACTIONS_REQUIRING_APPROVAL_GATE:
        # See module docstring "APPROVAL GATE" - the EFFECTIVE decision
        # (EB5, resolve_effective_identity_guard_decision()), never the raw
        # identity_guard_decision column directly, for the identical
        # reason the MARK_DUPLICATE gate already reuses EB5 above: a raw
        # ATTACH_PROVISIONAL row whose identity has since been genuinely,
        # governedly confirmed (an EB4 re-evaluation, a legacy human
        # attestation, or a CrossSourceAliasAttestation) must not be
        # permanently unreachable here merely because the ORIGINAL,
        # historical machine decision undershot what later governance
        # established. The raw column itself is never read, compared, or
        # mutated by this branch.
        effective = resolve_effective_identity_guard_decision(session, source_assertion_id=source_assertion.id)
        if effective.effective_decision != AttachmentOutcome(REQUIRED_IDENTITY_DECISION_FOR_APPROVAL):
            raise ValueError(
                f"{action} requires the EFFECTIVE identity decision "
                f"(resolve_effective_identity_guard_decision(), not merely the raw "
                f"identity_guard_decision column) to be {REQUIRED_IDENTITY_DECISION_FOR_APPROVAL!r}, got "
                f"{effective.effective_decision.value!r} (basis={effective.basis.value!r})"
            )
        if source_assertion.intelligence_review_decision != REQUIRED_INTELLIGENCE_DECISION_FOR_APPROVAL:
            raise ValueError(
                f"{action} requires intelligence_review_decision == "
                f"{REQUIRED_INTELLIGENCE_DECISION_FOR_APPROVAL!r}, got "
                f"{source_assertion.intelligence_review_decision!r}"
            )
        if source_assertion.promotion_policy_decision != REQUIRED_PROMOTION_DECISION_FOR_APPROVAL:
            raise ValueError(
                f"{action} requires promotion_policy_decision == "
                f"{REQUIRED_PROMOTION_DECISION_FOR_APPROVAL!r}, got "
                f"{source_assertion.promotion_policy_decision!r}"
            )

    if action == "CONFIRM_DISTINCT_SIGNAL":
        if reconciliation_fingerprint is None:
            raise ValueError("CONFIRM_DISTINCT_SIGNAL requires reconciliation_fingerprint")
        if not isinstance(reconciliation_fingerprint, str) or not _FINGERPRINT_PATTERN.fullmatch(
            reconciliation_fingerprint
        ):
            raise ValueError(
                "reconciliation_fingerprint must be exactly 64 lowercase hexadecimal "
                f"characters, got {reconciliation_fingerprint!r}"
            )
        if supersedes_action_id is None:
            # create_signal_from_approved_review() (unmodified by R4B) only ever
            # raises the reconciliation block this action resolves when
            # get_latest_reviewer_action() is already "APPROVE_SIGNAL" - so
            # every legitimate CONFIRM_DISTINCT_SIGNAL necessarily supersedes
            # some prior row (an APPROVE_SIGNAL, or a later DEFER/
            # NEEDS_MORE_EVIDENCE recorded after it - every worked example in
            # the R4 design's own Section 13 shows this). A row with no
            # supersedes_action_id at all would be a rootless confirmation
            # never actually tied to a reviewed block; rejected here rather
            # than left to accumulate as ambiguous history. R4B does not
            # further check *which* action is superseded - that remains a
            # workflow/R4C concern, not something provable from this row alone.
            raise ValueError("CONFIRM_DISTINCT_SIGNAL requires supersedes_action_id")
    elif reconciliation_fingerprint is not None:
        raise ValueError("reconciliation_fingerprint is only valid when action == CONFIRM_DISTINCT_SIGNAL")

    if supersedes_action_id is not None:
        previous = session.get(ReviewerAction, supersedes_action_id)
        if previous is None or previous.source_assertion_id != source_assertion.id:
            raise ValueError("superseded action must exist and concern the same SourceAssertion")

    record = ReviewerAction(
        source_assertion_id=source_assertion.id,
        action=action,
        reason=reason.strip(),
        reviewer=reviewer.strip(),
        supersedes_action_id=supersedes_action_id,
        duplicate_of_signal_id=duplicate_of_signal_id,
        reconciliation_fingerprint=reconciliation_fingerprint,
    )
    session.add(record)
    session.flush()
    return record


def get_latest_reviewer_action(session: Session, source_assertion_id: int) -> Optional[ReviewerAction]:
    """The effective current reviewer action for a SourceAssertion: the most
    recently recorded ReviewerAction row, ordered by created_at then id (the
    same tiebreak discipline app.services.human_review_queue uses for its
    own ordering). "Latest" means "most recently recorded," not "the
    unsuperseded terminal node reached by walking supersedes_action_id" -
    with an append-only log, recency alone already identifies the current
    state regardless of whether a row explicitly named its predecessor, so
    no chain-walking is needed (matches the design doc's own §2.2
    reasoning). Returns None if no action has ever been recorded for this
    SourceAssertion.
    """
    actions = (
        session.query(ReviewerAction)
        .filter(ReviewerAction.source_assertion_id == source_assertion_id)
        .order_by(ReviewerAction.created_at.desc(), ReviewerAction.id.desc())
        .limit(1)
        .all()
    )
    return actions[0] if actions else None
