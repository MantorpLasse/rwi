"""Promotion-policy persistence bridge
(docs/architecture/promotion-policy-persistence-slice7-report.md, Slice 7 of
docs/architecture/evidence-to-signal-semantics-design.md).

    SourceAssertion (already governed, already has identity_guard_decision)
        + tuple[Claim, ...] (already extracted by a source-specific adapter -
          this module never calls one itself)
        + PromotionPolicyContext (caller-supplied, source-authority tier etc.)
        -> persist_promotion_policy()
        -> PromotionPolicyDecision persisted onto the SAME row
           (.promotion_policy_decision / .promotion_policy_reason)
        -> STOP (no Signal, no review-queue UI, no automatic promotion - all
           future, separately-authorized slices)

The smallest possible bridge between the already-committed, unmodified
Slice 6 core (app.services.promotion_policy_evaluation) and the two additive
SourceAssertion columns this slice adds.

COMPOSES THE COMMITTED CORES, NEVER DUPLICATES THEM: this module calls
`evaluate_signal_candidate()` (Slice 3, unmodified) and
`evaluate_promotion_policy()` (Slice 6, unmodified) directly - it contains no
materiality logic and no promotion-eligibility logic of its own. The one
small piece of shared logic it needs - mapping a SourceAssertion to the
identity decision that should currently govern it, fail-closed - is reused
verbatim from
`app.services.intelligence_review_persistence._identity_decision_from_assertion()`
rather than re-implemented a second time (a single source of truth for that
mapping; if it is ever changed, both slices stay in sync automatically). As
of EB5 (docs/architecture/rwi-eb5-downstream-identity-consumption-report.md),
that shared helper itself forwards the EFFECTIVE identity decision - the
latest governed IdentityGuardEvaluation (EB4) when one validly exists,
otherwise the original historical `identity_guard_decision` column exactly
as before - so this module automatically stays in sync with EB5 too,
without needing its own copy of that precedence logic.

DELIBERATELY DOES NOT CALL `persist_intelligence_review()`: that function
always (re-)writes `intelligence_review_decision`/`intelligence_review_reason`
as part of its own contract (Slice 4's own idempotent-recompute discipline) -
calling it here would mean this module also mutates fields outside its own
declared scope every time it runs, which this slice's own explicit
instruction forbids ("do not overwrite... intelligence review fields").
Instead, `evaluate_signal_candidate()` is called directly, in-memory only,
purely to obtain the `SignalCandidateDecision` `evaluate_promotion_policy()`
needs - this is a pure recomputation with no database write of its own, and
never touches the two intelligence-review columns. A consequence,
deliberately: `persist_promotion_policy()` does NOT require
`persist_intelligence_review()` to have been called first - it depends only
on `source_assertion.identity_guard_decision` (identical to what Slice 4
itself depends on) and the caller-supplied `claims`, never on Slice 4's own
persisted output. Calling both in sequence, in the same transaction, is how
a caller builds the full three-field audit trail on one row (task S10) - but
they remain two independently-callable, independently-correct functions.

This module performs NO web search, NO fetch, NO parsing, NO AI extraction,
and creates/modifies NO Signal, NO Airport/Runway/RunwayEnd, NO Installation,
NO PhysicalInstallationIdentity, NO InstallationAssertionLink, NO Incident -
only ever writes to the two promotion-policy columns of the one
SourceAssertion row it is given. It never commits and never imports
app.database.SessionLocal - it mutates the caller-supplied Session and
flushes only to surface any constraint violation immediately; the caller
owns the transaction boundary entirely (mirrors
app.services.intelligence_review_persistence's own "no hidden commits"
discipline exactly).

SOURCE AUTHORITY REMAINS EXPLICIT, NEVER INFERRED: PromotionPolicyContext's
own `source_authority_tier` must be supplied by the caller - this module
never reads `Source.reliability_level` (it does not even import `Source`),
and never treats `"official"` or any other existing value as implying Tier 1
(design doc S12/S15's own finding: that field cannot deterministically
represent the tiers this policy needs). Not calling `evaluate_promotion_policy()`
with a fabricated tier is this module's own responsibility just as much as
Slice 6's.

AUTO_ELIGIBLE IS NEVER EXECUTABLE AUTHORITY HERE: persisting
`promotion_policy_decision = "AUTO_ELIGIBLE"` is exactly and only that - a
column value. No Signal is ever created, updated, or published by this
function, regardless of which outcome is persisted.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import SourceAssertion
from app.services.evidence_claim_semantics import Claim
from app.services.intelligence_review_persistence import _identity_decision_from_assertion
from app.services.promotion_policy_evaluation import (
    PromotionPolicyContext,
    PromotionPolicyDecision,
    PromotionPolicyOutcome,
    evaluate_promotion_policy,
)
from app.services.signal_candidate_evaluation import (
    SignalCandidateContext,
    SignalCandidateDecision,
    evaluate_signal_candidate,
)

__all__ = ["PromotionPolicyPersistenceResult", "persist_promotion_policy"]


@dataclass(frozen=True)
class PromotionPolicyPersistenceResult:
    """Deterministic, ORM-free summary of what persist_promotion_policy() did.
    `signal_candidate` is the (freshly recomputed, never persisted by this
    module) SignalCandidateDecision `decision` was derived from - exposed so
    a caller/test can inspect the full reasoning chain without a second
    database round-trip. `decision` is the exact PromotionPolicyDecision that
    was persisted - a plain, already-immutable dataclass (never an ORM
    instance)."""

    source_assertion_id: int
    signal_candidate: SignalCandidateDecision
    decision: PromotionPolicyDecision

    @property
    def outcome(self) -> PromotionPolicyOutcome:
        return self.decision.outcome

    @property
    def reason(self) -> str:
        return self.decision.reason


def persist_promotion_policy(
    session: Session, source_assertion: SourceAssertion, claims: "tuple[Claim, ...]", context: PromotionPolicyContext,
) -> PromotionPolicyPersistenceResult:
    """Evaluates `claims` against `source_assertion`'s own already-governed
    identity decision and the caller-supplied `context`, then persists the
    resulting PromotionPolicyDecision onto the same row. Never commits;
    calls `session.flush()` only so a constraint violation surfaces
    immediately. Never writes intelligence_review_decision/
    intelligence_review_reason - see module docstring.
    """
    identity_decision = _identity_decision_from_assertion(session, source_assertion)
    signal_candidate = evaluate_signal_candidate(claims, SignalCandidateContext(identity_decision=identity_decision))
    decision = evaluate_promotion_policy(signal_candidate, claims, context)

    source_assertion.promotion_policy_decision = decision.outcome.value
    source_assertion.promotion_policy_reason = decision.reason
    session.flush()

    return PromotionPolicyPersistenceResult(
        source_assertion_id=source_assertion.id, signal_candidate=signal_candidate, decision=decision,
    )
