"""Update & Report V1 — optional, explicit governed apply step (Part 8).

    ChangeCandidateResult (already classified, already shown to a human -
        see app.services.update_report_change_detection)
        + reviewer, reason (a SEPARATE, explicit operator action - never
          triggered by report generation itself)
        -> apply_field_change_candidate()
        -> app.services.signal_amendment.amend_signal()
        -> SignalAmendmentResult (Signal mutated + durable amendment audit
           history, in the same governed write seam every other Signal
           correction already goes through)
        -> STOP

NO NEW WRITE PATH IS INVENTED HERE. Only `amend_signal()` is called - never
a direct `setattr`/`session.add()` on a `Signal`, and never a bypass of
`app.services.signal_amendment_history.record_signal_amendment()` (which
`amend_signal()` already calls internally).

WHY NEW_SIGNAL_CANDIDATE AND CORROBORATION_ONLY HAVE NO APPLY PATH HERE
(mission Part 8's own explicit instruction: "do NOT invent a new write path
... otherwise report 'creation not yet wired in V1' and stop"):

  - NEW_SIGNAL_CANDIDATE's only existing governed creation path,
    app.services.governed_signal_creation.create_signal_from_approved_review(),
    requires the SAME SourceAssertion to already carry a fully-governed
    identity/intelligence/promotion-policy decision AND a matching
    ReviewerAction (APPROVE_SIGNAL/CONFIRM_DISTINCT_SIGNAL) - governance
    state this mission's own candidate inputs (staged evidence, explicit
    CLI ids) are not guaranteed to have. Wiring a call here that silently
    depends on unstated upstream state is exactly the kind of implicit
    coupling this codebase's own review discipline flags; the honest,
    narrow answer for V1 is: creation is not wired into this apply step -
    an operator who wants to create a new Signal from a NEW_SIGNAL_CANDIDATE
    uses the existing, separate governed-creation review workflow directly.
  - CORROBORATION_ONLY's existing path,
    app.services.governed_signal_creation.link_source_assertion_to_duplicate_signal(),
    has the identical prerequisite (a MARK_DUPLICATE ReviewerAction already
    recorded on the SourceAssertion) - same reasoning, same V1 exclusion.

Both are reported as MATERIAL CHANGES / CORROBORATION ONLY with an explicit
recommended action for a human to carry out through the existing, separate
tool - never silently dropped, never faked through a new path.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.services.signal_amendment import SignalAmendmentError, amend_signal
from app.services.update_report_vocabulary import MaterialChangeClassification

if TYPE_CHECKING:  # pragma: no cover
    from app.services.signal_amendment import SignalAmendmentResult
    from app.services.update_report_change_detection import ChangeCandidateResult

__all__ = [
    "UpdateReportApplyError",
    "APPLICABLE_CLASSIFICATIONS",
    "apply_field_change_candidate",
]


class UpdateReportApplyError(ValueError):
    """Raised for every refusal this module makes - a plain ValueError
    subclass, matching app.services.signal_amendment.SignalAmendmentError's
    own convention."""


APPLICABLE_CLASSIFICATIONS: "frozenset[MaterialChangeClassification]" = frozenset(
    {MaterialChangeClassification.FIELD_CHANGE_CANDIDATE, MaterialChangeClassification.CONTRADICTION_OR_CORRECTION}
)


def apply_field_change_candidate(
    session: Session, candidate: "ChangeCandidateResult", *, reviewer: str, reason: str,
) -> "SignalAmendmentResult":
    """The ONLY write this module performs: one `amend_signal()` call for an
    already-classified FIELD_CHANGE_CANDIDATE or CONTRADICTION_OR_CORRECTION
    candidate whose identity was already safely resolved to exactly one
    Signal. Refuses (raises `UpdateReportApplyError`) for every other
    classification, an unresolved identity, or an empty proposed-change set
    - fails closed rather than guessing what the caller meant. This is a
    genuinely separate call from `classify_candidate()`/report generation -
    nothing in this package ever calls this function automatically."""
    if candidate.classification not in APPLICABLE_CLASSIFICATIONS:
        raise UpdateReportApplyError(
            f"classification {candidate.classification.value!r} is not applicable for a governed amend_signal() "
            f"write - only {sorted(c.value for c in APPLICABLE_CLASSIFICATIONS)!r} are"
        )
    if not candidate.identity_resolved or candidate.existing_signal_id is None:
        raise UpdateReportApplyError("candidate identity was not safely resolved to one existing Signal - refusing")
    if not candidate.proposed_changes:
        raise UpdateReportApplyError("candidate carries no proposed field changes - nothing to apply")

    try:
        return amend_signal(
            session,
            signal_id=candidate.existing_signal_id,
            changes=dict(candidate.proposed_changes),
            reason=reason,
            reviewer=reviewer,
            source_assertion_id=candidate.source_assertion_id,
        )
    except SignalAmendmentError as exc:
        raise UpdateReportApplyError(f"amend_signal() refused this candidate: {exc}") from exc
