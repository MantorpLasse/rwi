"""Pure reconciliation-review-plan and fingerprint core for human resolution of
blocking reconciliation decisions
(docs/architecture/existing-signal-reconciliation-r4-human-resolution-design.md
S6/S7; R4A of that document's own S20 roadmap - see
docs/architecture/existing-signal-reconciliation-r4a-review-plan-report.md).

    ExistingSignalReconciliationSubject + ExistingSignalReconciliationDecision
    (both from app.services.existing_signal_reconciliation, R1, read but never
    modified here)
        -> build_reconciliation_review_plan()
        -> ReconciliationReviewPlan (DB-free, immutable, canonical)
        -> compute_reconciliation_fingerprint()
        -> str (a 64-character lowercase hexadecimal SHA-256 digest)
        -> STOP (no human-decision record is read or written here, no
           persistence, no new decision vocabulary added, no governed-creation
           integration - all future, separate slices, R4B/R4C respectively)

Answers exactly one question: "what is the canonical, machine-comparable
representation of the exact blocking reconciliation state a human is about to
review (or already reviewed)?" It never asks "is this a genuine match" (that
is R1's question, already answered before this module is ever called) and
never asks "has a human resolved this" (that is a future, separate slice's
question - this module imports nothing related to recording or reading a
human decision, and needs nothing from that layer to do its own job).

DELIBERATELY A SEPARATE MODULE FROM R1 (`app.services.existing_signal_reconciliation`),
not added to it, for a reason this task's own fresh inspection confirmed
rather than merely inherited from the design doc: R1 has been independently
reviewed and hardened multiple times as a single, narrow, already-closed
contract ("does existing structured evidence anchor this new evidence to an
existing Signal, or not?"), and every prior review checkpoint in this
project's history has used "R1's own file is unchanged" as a direct,
zero-ambiguity signal that reconciliation semantics themselves have not
shifted. This module answers a genuinely different question (what did a
human review, and how do we know if that review is still current?) that has
nothing to do with anchor/compatibility/disconfirming logic - mixing the two
concerns into one file would make that "R1 unchanged" signal meaningless for
future reviewers without changing what either module actually does. This
mirrors the same reasoning that already put R2's read-only DB adapter in its
own file, `existing_signal_reconciliation_candidates.py`, rather than folding
it into R1.

PURITY: no SQLAlchemy, no ORM model, no database access, no filesystem
access, no network access, no `app.acquisition` import, no persistence, no
mutation of any input, no internally-generated timestamp, and no
random/UUID value anywhere in this module or its output - the same input
always produces the same `ReconciliationReviewPlan` and the same fingerprint,
forever, in any process, on any machine.

CANONICALIZATION IS THE ENTIRE POINT: every set-like field (candidate ids,
anchor reasons, physical-installation ids) is deduplicated and sorted before
it ever reaches the hash - both when the plan itself is built and,
defensively, again inside the fingerprint function itself, so a fingerprint
computed from a plan built any other way (not through
`build_reconciliation_review_plan()`) still canonicalizes correctly rather
than silently trusting the caller. Two logically identical review situations,
however their candidates/reasons happened to be ordered or duplicated when
they arrived, always produce byte-identical serialized payloads and identical
fingerprints (design doc S7, S16).

FIELDS THIS MODULE NEVER TOUCHES, STRUCTURALLY: financial amounts, titles,
notes, source_notes, any other free-text/narrative field, confidence/
similarity scores, and provider/source-family identity - none of these are
representable at all on `ReconciliationReviewPlan`, matching the identical
absent-field discipline R1 and R2 already established for their own
dataclasses. This module also never reads `advisory_candidate_signal_ids` or
`advisory_reasons` from the decision it is given - only the blocking
(`candidate_signal_ids`/`reasons`) pair ever reaches the plan, per the design
doc's own explicit S16 conclusion that compatibility-only churn must never
invalidate a human's distinctness confirmation.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.services.existing_signal_reconciliation import (
    ExistingSignalReconciliationDecision,
    ExistingSignalReconciliationOutcome,
    ExistingSignalReconciliationSubject,
)

__all__ = [
    "CURRENT_RECONCILIATION_PLAN_VERSION",
    "ReconciliationReviewPlan",
    "build_reconciliation_review_plan",
    "compute_reconciliation_fingerprint",
]

# Bumped only when the SET of fields feeding the fingerprint, or the meaning
# of any field within it, changes - never for a pure refactor that changes
# nothing about what is being compared (design doc S17). Included inside the
# hashed payload itself (never a separate comparison step) so that an old
# human confirmation can never silently authorize creation under a materially
# different future reconciliation algorithm merely because the underlying
# database facts happen to still coincide.
CURRENT_RECONCILIATION_PLAN_VERSION = 1

# The exact prefix convention R1's own `_anchor_reasons()` already uses for
# every reason string it produces (`f"signal {sid}: identity_anchor:..."`) -
# relied on below to verify every blocking candidate actually has at least
# one reason attributed to it, rather than trusting the two tuples arrived
# already consistent with each other.
_REASON_CANDIDATE_PREFIX_TEMPLATE = "signal {signal_id}: "


@dataclass(frozen=True)
class ReconciliationReviewPlan:
    """The canonical, machine-comparable representation of one blocking
    `POSSIBLE_EXISTING_SIGNAL_MATCH` reconciliation state - what a human
    reviews, and what a future `CONFIRM_DISTINCT_SIGNAL` confirmation is
    bound to (design doc S6). Every field is copied verbatim from R1/R2
    output already produced upstream of this module - nothing here is
    invented, re-derived from raw text, or scored.

    Fields are grouped by what they represent, matching the design doc's own
    field list exactly - no additional identity concept was introduced:

    `reconciliation_plan_version`: see `CURRENT_RECONCILIATION_PLAN_VERSION`.
    `source_assertion_id`: the governed evidence being evaluated - not a
    field `ExistingSignalReconciliationSubject` itself carries (R1's own
    subject has no row-identity field at all), supplied explicitly by the
    caller instead.
    `subject_airport_id`/`subject_runway_id`/`subject_physical_installation_ids`/
    `subject_source_id`/`subject_artifact_identity`: the reviewed subject's
    own structural identity, copied from `ExistingSignalReconciliationSubject`
    - deliberately excludes `category`/`vendor_names`/`evidence_date`/
    `reference_year`, which are compatibility-only on that same dataclass and
    can never contribute to an anchor (design doc S16).
    `candidate_signal_ids`/`anchor_reasons`: the COMPLETE blocking candidate
    set and their structural anchor reasons, copied from
    `ExistingSignalReconciliationDecision.candidate_signal_ids`/`.reasons` -
    never `.advisory_candidate_signal_ids`/`.advisory_reasons`, which this
    module never reads at all.

    Every tuple field is stored deduplicated and sorted by
    `build_reconciliation_review_plan()` - this dataclass does not itself
    enforce that invariant (a caller constructing one directly could pass
    unsorted data), which is exactly why `compute_reconciliation_fingerprint()`
    canonicalizes again, defensively, rather than trusting the plan it is
    handed."""

    reconciliation_plan_version: int
    source_assertion_id: int
    subject_airport_id: "int | None"
    subject_runway_id: "int | None"
    subject_physical_installation_ids: "tuple[int, ...]"
    subject_source_id: "int | None"
    subject_artifact_identity: "str | None"
    candidate_signal_ids: "tuple[int, ...]"
    anchor_reasons: "tuple[str, ...]"


def build_reconciliation_review_plan(
    *,
    source_assertion_id: int,
    subject: ExistingSignalReconciliationSubject,
    decision: ExistingSignalReconciliationDecision,
    reconciliation_plan_version: int = CURRENT_RECONCILIATION_PLAN_VERSION,
) -> ReconciliationReviewPlan:
    """Builds the canonical review plan for one blocking reconciliation
    decision. Fails closed (raises `ValueError`) rather than silently
    manufacturing a plan for anything that is not a genuine, well-formed
    `POSSIBLE_EXISTING_SIGNAL_MATCH`:

    - `decision.outcome` must be exactly `POSSIBLE_EXISTING_SIGNAL_MATCH`.
      `CLEAR_TO_CREATE` and `ALREADY_LINKED` never block creation, so there is
      nothing for a human to review and no plan should ever be built for
      them - a caller that reaches this function with the wrong outcome has
      a bug, and this function does not paper over it with an empty plan.
    - `decision.candidate_signal_ids` must be non-empty - a
      `POSSIBLE_EXISTING_SIGNAL_MATCH` with no blocking candidates would
      itself be malformed (a violation of R1's own contract), never silently
      accepted here.
    - `decision.reasons` must be non-empty, and every id in
      `candidate_signal_ids` must have at least one reason in `reasons`
      carrying that exact candidate's own `"signal {id}: "` prefix (R1's own,
      already-established reason-string convention). This is what keeps
      candidate/reason association meaningful without needing a nested
      per-candidate structure: because every reason string already names its
      own candidate, a flat, sorted tuple of reason strings can never lose
      that association or "pool" one candidate's reasons onto another's - but
      only as long as this invariant holds, so it is checked explicitly
      rather than assumed forever.
    """
    if decision.outcome != ExistingSignalReconciliationOutcome.POSSIBLE_EXISTING_SIGNAL_MATCH:
        raise ValueError(
            "build_reconciliation_review_plan requires a POSSIBLE_EXISTING_SIGNAL_MATCH "
            f"decision, got {decision.outcome!r} - CLEAR_TO_CREATE and ALREADY_LINKED never "
            "block creation and therefore have nothing for a human to review."
        )

    candidate_signal_ids = tuple(sorted(set(decision.candidate_signal_ids)))
    if not candidate_signal_ids:
        raise ValueError(
            "malformed POSSIBLE_EXISTING_SIGNAL_MATCH decision: candidate_signal_ids is empty"
        )

    anchor_reasons = tuple(sorted(set(decision.reasons)))
    if not anchor_reasons:
        raise ValueError(
            "malformed POSSIBLE_EXISTING_SIGNAL_MATCH decision: reasons is empty"
        )

    candidate_prefixes = tuple(
        _REASON_CANDIDATE_PREFIX_TEMPLATE.format(signal_id=signal_id) for signal_id in candidate_signal_ids
    )

    for signal_id, prefix in zip(candidate_signal_ids, candidate_prefixes):
        if not any(reason.startswith(prefix) for reason in anchor_reasons):
            raise ValueError(
                f"malformed POSSIBLE_EXISTING_SIGNAL_MATCH decision: candidate_signal_ids "
                f"names signal {signal_id!r} but no reason in `reasons` is attributed to it "
                f"(expected at least one reason starting with {prefix!r})"
            )

    # The reciprocal check (found during the R4A review checkpoint): every
    # reason must itself be attributed to one of the DECLARED blocking
    # candidates - not merely "every declared candidate has some reason,"
    # which alone would silently let a reason for signal_id 2 into the plan
    # even if candidate_signal_ids == (1,). R1's own real construction can
    # never actually produce this (its `reasons` tuple is built strictly by
    # iterating candidate_signal_ids), but this function does not trust that
    # invariant to hold forever - a plan that hashed evidence for a candidate
    # a human was never shown would be exactly the "phantom staleness
    # dependency" this whole contract exists to prevent.
    for reason in anchor_reasons:
        if not any(reason.startswith(prefix) for prefix in candidate_prefixes):
            raise ValueError(
                f"malformed POSSIBLE_EXISTING_SIGNAL_MATCH decision: reason {reason!r} is not "
                f"attributed to any candidate in candidate_signal_ids={candidate_signal_ids!r}"
            )

    return ReconciliationReviewPlan(
        reconciliation_plan_version=reconciliation_plan_version,
        source_assertion_id=source_assertion_id,
        subject_airport_id=subject.airport_id,
        subject_runway_id=subject.runway_id,
        subject_physical_installation_ids=tuple(sorted(set(subject.physical_installation_ids))),
        subject_source_id=subject.source_id,
        subject_artifact_identity=subject.artifact_identity,
        candidate_signal_ids=candidate_signal_ids,
        anchor_reasons=anchor_reasons,
    )


def compute_reconciliation_fingerprint(plan: ReconciliationReviewPlan) -> str:
    """Deterministic SHA-256 fingerprint of a `ReconciliationReviewPlan`,
    returned as a 64-character lowercase hexadecimal digest (the one
    documented format `hashlib.sha256(...).hexdigest()` already produces -
    no alternate encoding).

    Canonicalizes every set-like field again here, defensively, rather than
    trusting the plan it is handed was built through
    `build_reconciliation_review_plan()` (which already canonicalizes) - so
    this function alone is sufficient to guarantee determinism regardless of
    how its input was constructed. Serializes the fully-canonicalized payload
    via `json.dumps(..., sort_keys=True, separators=(",", ":"))` - never
    `repr()`, `str()` of the dataclass, an object-serialization module, or
    Python's own `hash()`, none of which are guaranteed stable across Python
    versions, processes, or even (for `hash()`, on `str`/bytes keys) the same
    process with hash randomization enabled. Every field is already a plain
    JSON-native type
    (`int`, `str`, `None`, list) - no float, no `Decimal`, no `date`/`datetime`
    ever reaches this function, so no lossy or locale-dependent formatting
    step is needed.
    """
    canonical_payload = {
        "reconciliation_plan_version": plan.reconciliation_plan_version,
        "source_assertion_id": plan.source_assertion_id,
        "subject_airport_id": plan.subject_airport_id,
        "subject_runway_id": plan.subject_runway_id,
        "subject_physical_installation_ids": sorted(set(plan.subject_physical_installation_ids)),
        "subject_source_id": plan.subject_source_id,
        "subject_artifact_identity": plan.subject_artifact_identity,
        "candidate_signal_ids": sorted(set(plan.candidate_signal_ids)),
        "anchor_reasons": sorted(set(plan.anchor_reasons)),
    }
    canonical_json = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
