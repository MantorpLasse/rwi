"""Intelligence-review persistence bridge
(docs/architecture/intelligence-review-persistence-slice4-report.md,
Slice 4 of docs/architecture/evidence-to-signal-semantics-design.md).

    SourceAssertion (already governed, already has identity_guard_decision)
        + tuple[Claim, ...] (already extracted by a source-specific adapter,
          e.g. app.acquisition.mac_granicus_claims.extract_mac_claims -
          this module never calls one itself)
        -> persist_intelligence_review()
        -> SignalCandidateDecision persisted onto the SAME row
           (.intelligence_review_decision / .intelligence_review_reason)
        -> STOP (no Signal, no review-queue UI, no promotion - all future,
           separately-approved slices)

The smallest possible bridge between the already-committed, unmodified
Slice 3 core (app.services.signal_candidate_evaluation) and the two
additive SourceAssertion columns this slice adds. Deliberately does NOT
import any source-specific extractor (no app.acquisition.mac_granicus_claims
import here) - the caller obtains `claims` however is appropriate for that
row's own source family and passes the already-built tuple in, exactly the
same separation Slice 3 itself keeps from Slice 2's MAC-specific adapter.

This module performs NO web search, NO fetch, NO parsing, NO AI
extraction, and creates/modifies NO Signal, NO Airport/Runway/RunwayEnd,
NO Installation, NO PhysicalInstallationIdentity - only ever writes to the
two intelligence-review columns of the one SourceAssertion row it is
given. It never commits and never imports app.database.SessionLocal - it
mutates the caller-supplied Session and flushes only to surface any
constraint violation immediately; the caller owns the transaction
boundary entirely (mirrors app.services.discovery_evidence_persistence's
own "no hidden commits" discipline exactly).

IDENTITY GATE, fail-closed: the identity decision fed into
evaluate_signal_candidate() is read directly from
`source_assertion.identity_guard_decision`. Any value other than the
literal string "ATTACH_CONFIRMED" - including "ATTACH_PROVISIONAL",
"REVIEW_REQUIRED", "REJECT_CROSS_AIRPORT", "INSUFFICIENT_IDENTITY", None
(a row from a pathway that never ran the guard at all, e.g. NASR/
USAspending), or any unrecognized string - is mapped defensively to
AttachmentOutcome.INSUFFICIENT_IDENTITY before evaluation, which
`evaluate_signal_candidate()` (unmodified) already turns into
IDENTITY_NOT_CONFIRMED. This module adds no new identity logic of its
own; it only ever forwards the already-governed decision.

IDEMPOTENCY: `claims` and `source_assertion.identity_guard_decision`
together fully determine the decision (evaluate_signal_candidate is pure
and deterministic - Slice 3, unmodified). This service always recomputes
and (re-)writes both columns rather than skipping when they are already
set, so a repeated call with the same inputs is idempotent in effect
(same value written twice) while a later call - e.g. after the source
family's extractor was legitimately improved - keeps the persisted
judgment in sync with the current deterministic evaluation, exactly as
this slice's own instruction requires ("decision/reason must reflect the
current deterministic evaluation exactly"). It never creates a second
SourceAssertion row and never touches any field but these two.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import SourceAssertion
from app.services.evidence_attachment_guard import AttachmentOutcome
from app.services.evidence_claim_semantics import Claim
from app.services.signal_candidate_evaluation import (
    SignalCandidateContext,
    SignalCandidateDecision,
    SignalCandidateOutcome,
    evaluate_signal_candidate,
)

__all__ = ["IntelligenceReviewResult", "persist_intelligence_review"]


@dataclass(frozen=True)
class IntelligenceReviewResult:
    """Deterministic, ORM-free summary of what persist_intelligence_review()
    did. `decision` is the exact SignalCandidateDecision that was persisted
    - a plain, already-immutable dataclass (never an ORM instance), safe to
    inspect further (`.material_claims`, `.warnings`) without touching the
    database again."""

    source_assertion_id: int
    decision: SignalCandidateDecision

    @property
    def outcome(self) -> SignalCandidateOutcome:
        return self.decision.outcome

    @property
    def reason(self) -> str:
        return self.decision.reason


def _identity_decision_from_assertion(source_assertion: SourceAssertion) -> AttachmentOutcome:
    """Fail-closed mapping from the persisted, free-text
    identity_guard_decision column to a real AttachmentOutcome. Never
    raises: None, an unrecognized string, or anything other than the
    literal ATTACH_CONFIRMED value all resolve to INSUFFICIENT_IDENTITY -
    the conservative "identity unknown/unconfirmed" member -
    which evaluate_signal_candidate() (unmodified) already turns into
    IDENTITY_NOT_CONFIRMED."""
    raw = source_assertion.identity_guard_decision
    if not raw:
        return AttachmentOutcome.INSUFFICIENT_IDENTITY
    try:
        return AttachmentOutcome(raw)
    except ValueError:
        return AttachmentOutcome.INSUFFICIENT_IDENTITY


def persist_intelligence_review(
    session: Session, source_assertion: SourceAssertion, claims: "tuple[Claim, ...]",
) -> IntelligenceReviewResult:
    """Evaluates `claims` against `source_assertion`'s own already-governed
    identity decision and persists the result onto the same row. Never
    commits (task's own explicit "caller owns transaction" requirement);
    calls `session.flush()` only so a constraint violation surfaces
    immediately, matching app.services.discovery_evidence_persistence's
    own established discipline.
    """
    context = SignalCandidateContext(identity_decision=_identity_decision_from_assertion(source_assertion))
    decision = evaluate_signal_candidate(claims, context)

    source_assertion.intelligence_review_decision = decision.outcome.value
    source_assertion.intelligence_review_reason = decision.reason
    session.flush()

    return IntelligenceReviewResult(source_assertion_id=source_assertion.id, decision=decision)
