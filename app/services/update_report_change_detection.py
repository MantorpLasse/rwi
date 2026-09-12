"""Update & Report V1 — change-candidate detection (Parts 2-7).

    Session + CandidateEvidenceInput (one already-existing SourceAssertion,
    plus an OPTIONAL, explicit, human-supplied proposed field change - this
    module never invents one from raw text; no generic text -> field-value
    extractor exists anywhere in this codebase today, see the mission's own
    recon)
        -> classify_candidate()
        -> ChangeCandidateResult (non-persisted; MaterialChangeClassification
           + full human-review context)
        -> STOP (no write of any kind - see app.services.update_report_apply
           for the separate, explicit, optional write step)

IDENTITY RESOLUTION is delegated entirely to the existing, already-reviewed
comparison core - never duplicated here:

    app.services.existing_signal_reconciliation_candidates.build_reconciliation_subject()
    app.services.existing_signal_reconciliation_candidates.find_reconciliation_candidates()
    app.services.existing_signal_reconciliation.evaluate_existing_signal_reconciliation()

`claims=()` is passed to `build_reconciliation_subject()` deliberately - this
module has no claim-extraction pipeline to draw from (extraction is a
source-family-specific concern under app.acquisition, out of scope for this
mission), so vendor/temporal advisory metadata is simply absent rather than
fabricated. This narrows what CLEAR_TO_CREATE's advisory metadata can ever
say here, but never widens what counts as an anchor - Invariant 21
(existing_signal_reconciliation's own "no compatibility count ever
substitutes for a structural anchor") is unaffected.

WHY A FIELD-CHANGE PROPOSAL IS ALWAYS OPERATOR-SUPPLIED, NEVER AUTO-EXTRACTED:
this codebase's only raw-text -> typed-value parsing lives in
app/acquisition/*.py, each one a source-family-specific extractor (FAA AIP,
USAspending, MAC Granicus, ...) - there is no generic "read this evidence
text and produce a Signal field diff" function, and building one here would
be exactly the kind of inference the mission explicitly forbids ("No
inference from ... supplier mention alone", "Do not guess"). A
`CandidateEvidenceInput.proposed_changes` therefore represents a human who
has already read the evidence and decided on an explicit new value - the
same act `correct_bgm_signal6_target_year.py` and its four siblings already
perform by hand; this module's job is to validate and classify that
decision, never to originate it.

PRESERVED INVARIANTS (verified, never re-derived here - restated on every
result's own `reasoning` for a human reader, per the mission's own "explicit
uncertainty/unsupported-assumptions" requirement):
  - same airport != same project (evaluate_existing_signal_reconciliation's
    own structural-anchor gate)
  - same runway != same runway end (PhysicalInstallationIdentity is the only
    canonical identity; this module never reads runway_end)
  - FH-D4 disposition: NO TRANSITIVE INFERENCE EVER (this module never
    imports app.services.signal_disposition_resolution/fh_d4_disposition_resolution)
  - grant amount != project total; funding evidence != project detail;
    evidence strength != supplier win probability (this module never
    aggregates or infers a financial field - a financial field only ever
    changes via an explicit `proposed_changes` entry, one Signal at a time)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.signal import Signal
from app.models.source_assertion import SourceAssertion
from app.services.existing_signal_reconciliation import (
    ExistingSignalReconciliationOutcome,
    evaluate_existing_signal_reconciliation,
)
from app.services.existing_signal_reconciliation_candidates import (
    build_reconciliation_subject,
    find_reconciliation_candidates,
)
from app.services.signal_amendment import ALLOWED_AMENDMENT_FIELDS
from app.services.update_report_vocabulary import MaterialChangeClassification, ReportPriority

__all__ = [
    "UpdateReportChangeDetectionError",
    "CandidateEvidenceInput",
    "ChangeCandidateResult",
    "HIGH_PRIORITY_AMENDMENT_FIELDS",
    "classify_candidate",
]


class UpdateReportChangeDetectionError(ValueError):
    """Raised only for a caller-shaped programming error (e.g. an unknown
    `source_assertion_id`) - never for an evidentiary ambiguity, which is
    always a NEEDS_MORE_EVIDENCE classification result instead, not an
    exception (this loop must keep producing a report even when one
    candidate's input is malformed)."""


# Award/contract activity, construction start, completion - Part 9's own
# HIGH examples that map onto a concrete ALLOWED_AMENDMENT_FIELDS entry.
# Every other allowed field is MEDIUM (timeline/phase/funding/runway-end/
# supplier detail, per Part 9).
HIGH_PRIORITY_AMENDMENT_FIELDS: "frozenset[str]" = frozenset(
    {"confirmed_vendor", "construction_start", "completion_date"}
)

PRESERVED_INVARIANT_NOTES: "tuple[str, ...]" = (
    "same airport is not treated as same project (structural anchor required)",
    "same runway is not treated as same runway end",
    "no FH-D4 transitive disposition inference performed here",
    "no financial field inferred or aggregated - a grant amount is never treated as a project total",
)


@dataclass(frozen=True)
class CandidateEvidenceInput:
    """One candidate piece of already-existing evidence to evaluate against
    current Signals - never a new persisted entity (Part 2/4). All of
    `proposed_changes`/`correction_flag`/`proposed_new_signal` represent an
    explicit, already-made human judgment about this evidence (see module
    docstring) - this module validates and classifies that judgment, it
    never originates one.

    `proposed_changes`: optional, explicit field -> new-value mapping
        (values already typed as the target Signal column expects, e.g. an
        `int` for `target_year`, a `date` for `completion_date`) that a
        human has determined this evidence explicitly supports.
    `correction_flag`: the human has determined this evidence corrects or
        contradicts an existing established Signal, not merely refines it
        (Part 7's CONTRADICTION_OR_CORRECTION / Part 9's HIGH-priority
        "contradiction/correction" case) - never inferred from the shape of
        the values themselves.
    `proposed_new_signal`: the human has determined this evidence, once
        safely identity-resolved as CLEAR_TO_CREATE, should become a new
        Signal candidate - never inferred; see
        app.services.update_report_apply's own module docstring for why
        creation itself is not wired into this mission's apply step.
    """

    source_assertion_id: int
    proposed_changes: "Optional[dict[str, Any]]" = None
    correction_flag: bool = False
    proposed_new_signal: bool = False


@dataclass(frozen=True)
class ChangeCandidateResult:
    """A non-persisted, computed representation of one proposed intelligence
    change (Part 4). Recomputed on demand every run - no DB table, no
    migration, matching app.services.human_review_queue's own
    HumanReviewItem/StagedEvidenceItem precedent."""

    airport_id: "Optional[int]"
    airport_display_name: "Optional[str]"
    source_assertion_id: int
    source_title: "Optional[str]"
    source_url: "Optional[str]"
    source_reliability_level: "Optional[str]"
    supporting_evidence_excerpt: "Optional[str]"

    existing_signal_id: "Optional[int]"
    existing_signal_snapshot: "Optional[dict[str, Any]]"

    classification: MaterialChangeClassification
    reconciliation_outcome: "Optional[str]"
    identity_resolved: bool

    proposed_changes: "dict[str, Any]"
    old_values: "dict[str, Any]"

    requires_human_review: bool
    report_priority: ReportPriority

    unresolved_identity_notes: "tuple[str, ...]" = ()
    reasoning: "tuple[str, ...]" = field(default_factory=lambda: PRESERVED_INVARIANT_NOTES)


def _signal_snapshot(signal: Signal) -> "dict[str, Any]":
    snapshot = {"title": signal.title, "category": signal.category, "status": signal.status}
    for amendment_field in sorted(ALLOWED_AMENDMENT_FIELDS):
        snapshot[amendment_field] = getattr(signal, amendment_field)
    return snapshot


def _priority_for_fields(changed_fields: "set[str]") -> ReportPriority:
    if changed_fields & HIGH_PRIORITY_AMENDMENT_FIELDS:
        return ReportPriority.HIGH
    return ReportPriority.MEDIUM


def _validate_and_diff_proposed_changes(
    signal: Signal, proposed_changes: "dict[str, Any]",
) -> "tuple[Optional[dict[str, Any]], dict[str, Any], dict[str, Any]]":
    """Returns (error_note_or_None, old_values, new_values) - `new_values`
    only ever contains fields whose proposed value genuinely differs from
    the Signal's current value (a real diff, never a restated no-op),
    exactly mirroring app.services.signal_amendment.amend_signal()'s own
    diff discipline, computed independently here since this module must
    never call amend_signal() itself (zero writes during detection)."""
    for field_name in proposed_changes:
        if field_name not in ALLOWED_AMENDMENT_FIELDS:
            return (
                f"proposed field {field_name!r} is not in the governed amendment field set "
                f"{sorted(ALLOWED_AMENDMENT_FIELDS)!r} - refusing to propose it",
                {},
                {},
            )
    old_values: "dict[str, Any]" = {}
    new_values: "dict[str, Any]" = {}
    for field_name, new_value in proposed_changes.items():
        old_value = getattr(signal, field_name)
        if old_value != new_value:
            old_values[field_name] = old_value
            new_values[field_name] = new_value
    return None, old_values, new_values


def classify_candidate(
    session: Session, evidence_input: CandidateEvidenceInput, *, is_duplicate_in_batch: bool = False,
) -> ChangeCandidateResult:
    """Classifies one CandidateEvidenceInput. Read-only: every session call
    made directly or transitively (via existing_signal_reconciliation_candidates)
    is a SELECT; nothing is added, flushed, or committed.

    `is_duplicate_in_batch`: the caller (see app.services.update_report_engine)
    has already seen this exact `source_assertion_id` earlier in the same
    report run - short-circuits to DUPLICATE immediately, before any
    reconciliation lookup, since re-presenting the identical evidence twice
    in one run is never new intelligence regardless of what reconciliation
    would say about it."""
    source_assertion = session.get(SourceAssertion, evidence_input.source_assertion_id)
    if source_assertion is None:
        raise UpdateReportChangeDetectionError(
            f"SourceAssertion {evidence_input.source_assertion_id!r} does not exist"
        )

    airport = source_assertion.airport
    source = source_assertion.source
    base_kwargs = dict(
        airport_id=source_assertion.airport_id,
        airport_display_name=airport.name if airport is not None else None,
        source_assertion_id=source_assertion.id,
        source_title=source.title if source is not None else None,
        source_url=source.url if source is not None else None,
        source_reliability_level=source.reliability_level if source is not None else None,
        supporting_evidence_excerpt=source_assertion.raw_relevant_text,
    )

    if is_duplicate_in_batch:
        return ChangeCandidateResult(
            **base_kwargs,
            existing_signal_id=source_assertion.signal_id,
            existing_signal_snapshot=None,
            classification=MaterialChangeClassification.DUPLICATE,
            reconciliation_outcome=None,
            identity_resolved=source_assertion.signal_id is not None,
            proposed_changes={},
            old_values={},
            requires_human_review=False,
            report_priority=ReportPriority.NONE,
            unresolved_identity_notes=(),
            reasoning=PRESERVED_INVARIANT_NOTES
            + (f"SourceAssertion {source_assertion.id} already appeared earlier in this same report run",),
        )

    subject = build_reconciliation_subject(source_assertion, claims=())
    candidates = find_reconciliation_candidates(session, source_assertion)
    decision = evaluate_existing_signal_reconciliation(subject, candidates)

    proposed_changes = evidence_input.proposed_changes or {}

    if decision.outcome == ExistingSignalReconciliationOutcome.ALREADY_LINKED:
        existing_signal_id = decision.signal_id
        existing_signal = session.get(Signal, existing_signal_id) if existing_signal_id is not None else None
        existing_signal_snapshot = _signal_snapshot(existing_signal) if existing_signal is not None else None

        if not proposed_changes:
            return ChangeCandidateResult(
                **base_kwargs,
                existing_signal_id=existing_signal_id,
                existing_signal_snapshot=existing_signal_snapshot,
                classification=MaterialChangeClassification.DUPLICATE,
                reconciliation_outcome=decision.outcome.value,
                identity_resolved=True,
                proposed_changes={},
                old_values={},
                requires_human_review=False,
                report_priority=ReportPriority.NONE,
                unresolved_identity_notes=(),
                reasoning=PRESERVED_INVARIANT_NOTES
                + (f"SourceAssertion {source_assertion.id} is already linked to Signal {existing_signal_id}",),
            )

        return _classify_proposed_change_against_signal(
            base_kwargs, existing_signal, evidence_input, proposed_changes, decision,
        )

    if decision.outcome == ExistingSignalReconciliationOutcome.POSSIBLE_EXISTING_SIGNAL_MATCH:
        if len(decision.candidate_signal_ids) != 1:
            return ChangeCandidateResult(
                **base_kwargs,
                existing_signal_id=None,
                existing_signal_snapshot=None,
                classification=MaterialChangeClassification.NEEDS_MORE_EVIDENCE,
                reconciliation_outcome=decision.outcome.value,
                identity_resolved=False,
                proposed_changes={},
                old_values={},
                requires_human_review=True,
                report_priority=ReportPriority.MEDIUM,
                unresolved_identity_notes=(
                    f"reconciliation found {len(decision.candidate_signal_ids)} anchor-backed candidate "
                    f"Signal(s) {list(decision.candidate_signal_ids)} - identity cannot be safely resolved "
                    "to one Signal, no guess made",
                )
                + decision.reasons,
                reasoning=PRESERVED_INVARIANT_NOTES,
            )

        existing_signal_id = decision.candidate_signal_ids[0]
        existing_signal = session.get(Signal, existing_signal_id)

        if not proposed_changes:
            return ChangeCandidateResult(
                **base_kwargs,
                existing_signal_id=existing_signal_id,
                existing_signal_snapshot=_signal_snapshot(existing_signal) if existing_signal is not None else None,
                classification=MaterialChangeClassification.CORROBORATION_ONLY,
                reconciliation_outcome=decision.outcome.value,
                identity_resolved=True,
                proposed_changes={},
                old_values={},
                requires_human_review=False,
                report_priority=ReportPriority.LOW,
                unresolved_identity_notes=(),
                reasoning=PRESERVED_INVARIANT_NOTES + decision.reasons,
            )

        return _classify_proposed_change_against_signal(
            base_kwargs, existing_signal, evidence_input, proposed_changes, decision,
        )

    # CLEAR_TO_CREATE
    if evidence_input.proposed_new_signal:
        return ChangeCandidateResult(
            **base_kwargs,
            existing_signal_id=None,
            existing_signal_snapshot=None,
            classification=MaterialChangeClassification.NEW_SIGNAL_CANDIDATE,
            reconciliation_outcome=decision.outcome.value,
            identity_resolved=True,
            proposed_changes=dict(proposed_changes),
            old_values={},
            requires_human_review=True,
            report_priority=ReportPriority.HIGH,
            unresolved_identity_notes=(),
            reasoning=PRESERVED_INVARIANT_NOTES
            + (
                "no existing Signal anchor found (CLEAR_TO_CREATE) - operator has explicitly asserted this "
                "represents a new Signal candidate; see app.services.update_report_apply for why signal "
                "creation itself is not wired into this mission's apply step",
            )
            + decision.advisory_reasons,
        )

    if proposed_changes:
        return ChangeCandidateResult(
            **base_kwargs,
            existing_signal_id=None,
            existing_signal_snapshot=None,
            classification=MaterialChangeClassification.NEEDS_MORE_EVIDENCE,
            reconciliation_outcome=decision.outcome.value,
            identity_resolved=False,
            proposed_changes={},
            old_values={},
            requires_human_review=True,
            report_priority=ReportPriority.MEDIUM,
            unresolved_identity_notes=(
                "proposed field changes were supplied, but no existing Signal anchor was found for this "
                "evidence (CLEAR_TO_CREATE) - there is no identified Signal to apply them to; confirm "
                "whether this evidence is actually a new Signal candidate instead",
            ),
            reasoning=PRESERVED_INVARIANT_NOTES,
        )

    return ChangeCandidateResult(
        **base_kwargs,
        existing_signal_id=None,
        existing_signal_snapshot=None,
        classification=MaterialChangeClassification.NEEDS_MORE_EVIDENCE,
        reconciliation_outcome=decision.outcome.value,
        identity_resolved=False,
        proposed_changes={},
        old_values={},
        requires_human_review=True,
        report_priority=ReportPriority.MEDIUM,
        unresolved_identity_notes=(
            "no existing Signal anchor found for this evidence (CLEAR_TO_CREATE) - confirm whether this "
            "represents a new Signal candidate; not guessed automatically",
        ),
        reasoning=PRESERVED_INVARIANT_NOTES + decision.advisory_reasons,
    )


def _classify_proposed_change_against_signal(
    base_kwargs: "dict[str, Any]",
    existing_signal: "Optional[Signal]",
    evidence_input: CandidateEvidenceInput,
    proposed_changes: "dict[str, Any]",
    decision,
) -> ChangeCandidateResult:
    existing_signal_id = existing_signal.id if existing_signal is not None else None
    if existing_signal is None:
        return ChangeCandidateResult(
            **base_kwargs,
            existing_signal_id=existing_signal_id,
            existing_signal_snapshot=None,
            classification=MaterialChangeClassification.NEEDS_MORE_EVIDENCE,
            reconciliation_outcome=decision.outcome.value,
            identity_resolved=False,
            proposed_changes={},
            old_values={},
            requires_human_review=True,
            report_priority=ReportPriority.MEDIUM,
            unresolved_identity_notes=(f"reconciled Signal id {existing_signal_id} could not be loaded",),
            reasoning=PRESERVED_INVARIANT_NOTES,
        )

    error_note, old_values, new_values = _validate_and_diff_proposed_changes(existing_signal, proposed_changes)
    if error_note is not None:
        return ChangeCandidateResult(
            **base_kwargs,
            existing_signal_id=existing_signal_id,
            existing_signal_snapshot=_signal_snapshot(existing_signal),
            classification=MaterialChangeClassification.NEEDS_MORE_EVIDENCE,
            reconciliation_outcome=decision.outcome.value,
            identity_resolved=True,
            proposed_changes={},
            old_values={},
            requires_human_review=True,
            report_priority=ReportPriority.MEDIUM,
            unresolved_identity_notes=(error_note,),
            reasoning=PRESERVED_INVARIANT_NOTES,
        )

    if not new_values:
        return ChangeCandidateResult(
            **base_kwargs,
            existing_signal_id=existing_signal_id,
            existing_signal_snapshot=_signal_snapshot(existing_signal),
            classification=MaterialChangeClassification.NO_MATERIAL_CHANGE,
            reconciliation_outcome=decision.outcome.value,
            identity_resolved=True,
            proposed_changes={},
            old_values={},
            requires_human_review=False,
            report_priority=ReportPriority.NONE,
            unresolved_identity_notes=(),
            reasoning=PRESERVED_INVARIANT_NOTES
            + ("every proposed value already matches the current Signal value - nothing changed",),
        )

    classification = (
        MaterialChangeClassification.CONTRADICTION_OR_CORRECTION
        if evidence_input.correction_flag
        else MaterialChangeClassification.FIELD_CHANGE_CANDIDATE
    )
    priority = ReportPriority.HIGH if evidence_input.correction_flag else _priority_for_fields(set(new_values))

    return ChangeCandidateResult(
        **base_kwargs,
        existing_signal_id=existing_signal_id,
        existing_signal_snapshot=_signal_snapshot(existing_signal),
        classification=classification,
        reconciliation_outcome=decision.outcome.value,
        identity_resolved=True,
        proposed_changes=new_values,
        old_values=old_values,
        requires_human_review=True,
        report_priority=priority,
        unresolved_identity_notes=(),
        reasoning=PRESERVED_INVARIANT_NOTES + decision.reasons,
    )
