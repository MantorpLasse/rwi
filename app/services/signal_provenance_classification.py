"""Read-only Signal provenance classifier (RWI HQ "Trust Preconditions for
Update & Report V1" mission, Part 2 — following the recon in
"RWI System Design Recon — Update & Report V1 + Trust/Operational
Readiness Checkpoint").

    Signal (already persisted, any creation path)
        -> classify_signal_provenance()
        -> SignalProvenanceClassification
        -> STOP (no write, no new column, no persisted classification -
           recomputed fresh every call from real, already-persisted
           relationships)

Answers exactly one question: "how did this Signal come to exist?" — never
"is this Signal trustworthy?" LEGACY DOES NOT MEAN BAD: the recon that
authorized this module found that every non-governed Signal in the current
production database still carries a real, citable `Source` row (an
official-tagged USAspending grant record, an FAA CIP/ALP/master-plan
document, an FAA Tableau incident export, etc.) — the gap for those rows is
process (no recorded `SourceAssertion`/`ReviewerAction` review event), not
evidence. This module's job is to make that distinction visible, not to
brand anything "untrusted."

CLASSIFICATION IS DERIVED ONLY FROM REAL, ALREADY-PERSISTED RELATIONSHIPS —
never from `Signal.title`/`Signal.notes`/`Signal.source_notes` text, and
never from a new column or a new table:

  - `SourceAssertion.signal_id` pointing at this Signal: this FK is set
    ONLY by `app.services.governed_signal_creation.create_signal_from_approved_review()`
    or `.link_source_assertion_to_duplicate_signal()` (see that module's own
    docstring) — no other code path in this repository ever sets it. Its
    presence is therefore a real, structural proof of the governed
    promotion path, not a heuristic.
  - `Signal.category == "replacement_after_incident"` together with the
    ABSENCE of any linked `SourceAssertion`: this is exactly, and only,
    the shape `app.models.incident._create_replacement_signal()` produces
    (see that function's own docstring) — no other creation path uses this
    category.
  - `Signal.source_id` (a real `Source` FK) present, with neither of the
    above: the shape every one-off `scripts/add_*`/`correct_*`/`update_*`/
    `import_faa_construction_report.py` creation script leaves behind — a
    real citation, but no structured evidence/review record.
  - None of the above (no linked SourceAssertion, not the incident
    category, no Source at all): genuinely ambiguous — this module fails
    safe to UNKNOWN rather than guessing. A hand-built Signal with no
    `source_id` set at all (e.g. a bare manual test fixture, or a future
    creation path this classifier doesn't yet know about) falls here.

`IMPORTED_OR_OTHER` (suggested by the mission brief as illustrative) was
deliberately NOT added as a fifth bucket: every currently-observed
non-governed, non-incident-rule Signal in production already carries a
real `Source`, and the only way to split that group further today would be
string-matching `Source.source_type` (e.g. "faa_tableau" vs. "usaspending_grant")
— exactly the kind of unstable, implicit fingerprinting this mission's own
Part 2 instructions warn against ("known creation-path fingerprints only if
stable and explicit"). `LEGACY_SCRIPT_OR_MANUAL` covers that whole group
honestly today; a future mission can split it further if a real, structural
need appears.

This module performs NO database write, NO schema change, and adds NO new
persisted field anywhere — `SELECT` only, via the `Session` the caller
already holds. Safe to call repeatedly; the answer is always recomputed
fresh, never cached or stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.signal import Signal
from app.models.source_assertion import SourceAssertion
from app.services.reviewer_action_persistence import get_latest_reviewer_action

__all__ = [
    "SignalProvenanceGeneration",
    "SignalProvenanceClassification",
    "classify_signal_provenance",
]


class SignalProvenanceGeneration(str, Enum):
    """Descriptive, not evaluative — see module docstring. Matches this
    codebase's existing `str, Enum` convention for a small, closed,
    persisted-nowhere vocabulary (e.g. `app.services.human_review_queue
    .ReviewWorkflowState`)."""

    MODERN_GOVERNED = "MODERN_GOVERNED"
    INCIDENT_RULE = "INCIDENT_RULE"
    LEGACY_SCRIPT_OR_MANUAL = "LEGACY_SCRIPT_OR_MANUAL"
    UNKNOWN = "UNKNOWN"


_DESCRIPTION = {
    SignalProvenanceGeneration.MODERN_GOVERNED: "modern governed promotion path",
    SignalProvenanceGeneration.INCIDENT_RULE: "incident rule-created",
    SignalProvenanceGeneration.LEGACY_SCRIPT_OR_MANUAL: "legacy/manual path",
    SignalProvenanceGeneration.UNKNOWN: "unknown creation path (no linked SourceAssertion, not the "
    "incident-rule shape, and no Source citation)",
}


@dataclass(frozen=True)
class SignalProvenanceClassification:
    """Pure, read-only result of `classify_signal_provenance()`. Never
    persisted anywhere — explainability metadata only, mirroring
    `app.services.signal_publication.PublicationEligibilityDecision`'s own
    "result-only" discipline. `evidence` lists the concrete, real
    relationships this classification was derived from, in the same
    "why did this happen" spirit as `SearchQuery.template_id`/
    `HostnameRanking.reason` elsewhere in this codebase — never a
    trust/quality verdict."""

    signal_id: int
    generation: SignalProvenanceGeneration
    description: str
    evidence: "tuple[str, ...]"


def classify_signal_provenance(session: Session, signal: Signal) -> SignalProvenanceClassification:
    """Read-only: `SELECT` only, never `add`/`flush`/`commit`. Queries
    `SourceAssertion` directly by `signal_id` (mirrors
    `evaluate_publication_eligibility()`'s own precedent) rather than
    trusting `signal.supporting_source_assertions`'s possibly-stale
    in-session relationship collection.
    """
    linked_assertions = (
        session.execute(select(SourceAssertion).where(SourceAssertion.signal_id == signal.id).order_by(SourceAssertion.id))
        .scalars()
        .all()
    )

    if linked_assertions:
        reviewer_actions: "list[str]" = []
        for assertion in linked_assertions:
            latest = get_latest_reviewer_action(session, assertion.id)
            if latest is not None:
                reviewer_actions.append(f"SourceAssertion #{assertion.id}: latest ReviewerAction {latest.action!r}")
        evidence = (
            f"{len(linked_assertions)} linked SourceAssertion(s) via SourceAssertion.signal_id "
            "(set only by the governed create_signal_from_approved_review()/"
            "link_source_assertion_to_duplicate_signal() paths)",
            *reviewer_actions,
        )
        return SignalProvenanceClassification(
            signal_id=signal.id,
            generation=SignalProvenanceGeneration.MODERN_GOVERNED,
            description=_DESCRIPTION[SignalProvenanceGeneration.MODERN_GOVERNED],
            evidence=evidence,
        )

    if signal.category == "replacement_after_incident":
        evidence = (
            "category == 'replacement_after_incident' with no linked SourceAssertion — exactly "
            "the shape app.models.incident._create_replacement_signal() produces automatically "
            "on every Incident insert",
        )
        return SignalProvenanceClassification(
            signal_id=signal.id,
            generation=SignalProvenanceGeneration.INCIDENT_RULE,
            description=_DESCRIPTION[SignalProvenanceGeneration.INCIDENT_RULE],
            evidence=evidence,
        )

    if signal.source_id is not None:
        evidence = (
            f"Source #{signal.source_id} is cited (Signal.source_id set), but no SourceAssertion "
            "links back to this Signal and it is not the incident-rule category — the shape left "
            "by a one-off creation/correction script",
        )
        return SignalProvenanceClassification(
            signal_id=signal.id,
            generation=SignalProvenanceGeneration.LEGACY_SCRIPT_OR_MANUAL,
            description=_DESCRIPTION[SignalProvenanceGeneration.LEGACY_SCRIPT_OR_MANUAL],
            evidence=evidence,
        )

    evidence = (
        "no linked SourceAssertion, not the incident-rule category, and no Source citation "
        "(Signal.source_id is NULL) — insufficient real, persisted relationships to classify "
        "further; fails safe rather than guessing",
    )
    return SignalProvenanceClassification(
        signal_id=signal.id,
        generation=SignalProvenanceGeneration.UNKNOWN,
        description=_DESCRIPTION[SignalProvenanceGeneration.UNKNOWN],
        evidence=evidence,
    )
