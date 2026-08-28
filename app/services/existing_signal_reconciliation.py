"""Pure existing-Signal reconciliation decision core
(docs/architecture/existing-signal-reconciliation-guard-design.md, reviewed and
hardened version; R1 of that document's own S16 roadmap - see
docs/architecture/existing-signal-reconciliation-r1-core-report.md).

    ExistingSignalReconciliationSubject (new evidence, DB-free snapshot)
        + tuple[ReconciliationCandidateSignal, ...] (existing Signals, DB-free
          snapshots - candidate discovery is a future, separate adapter, R2)
        -> evaluate_existing_signal_reconciliation()
        -> ExistingSignalReconciliationDecision
        -> STOP (no Signal write, no ReviewerAction write, no candidate
           discovery, no governed-creation integration - all future, separate
           slices; this module answers exactly one question: "does existing
           structured evidence already anchor this new evidence to a specific
           existing Signal, or not?")

Answers exactly one question, immediately upstream of the (not-yet-built)
governed-creation integration point: "before a new Signal is created from this
evidence, is there structural, identity-bearing proof that an existing Signal
already represents the same underlying project?" It never asks "create,
update, link, or publish a Signal" - no `Signal`, `SourceAssertion`, or
`ReviewerAction` model is imported anywhere in this module, and none is
needed. This module knows nothing about a specific source family, airport
authority, vendor, or country - every comparison operates only on the
already-structured fields the caller supplies, never on raw document text,
never on a database, never on wall-clock time.

CENTRAL, HARDENED RULE (design doc S5/S18, Invariant 21): a candidate can
gate creation (`POSSIBLE_EXISTING_SIGNAL_MATCH`) ONLY via at least one
STRUCTURAL IDENTITY ANCHOR - a shared canonical runway, a shared physical
installation identity, a shared source document already backing that
candidate, or (real-DB blast-radius review, legacy blind-spot fix) a
candidate's own direct source reference when that reference is both
unsupported by any linked assertion yet AND already established, exactly,
to be exclusive to this one candidate (`ReconciliationCandidateSignal.
is_uniquely_sourced` - see its own docstring). Compatibility evidence
(airport, category, vendor name, temporal proximity, reference year) can
accumulate without limit and still never gate creation by itself; it is
carried instead as non-blocking advisory metadata on `CLEAR_TO_CREATE`. This
is not a numeric threshold relaxed or tightened by a constant - no count of
compatibility axes, however large, ever substitutes for one anchor. The
design doc's own review checkpoint (S18) found and removed exactly this kind
of disguised score ("2+ independent supporting categories"); this module is
written so that mistake cannot quietly return - see
TestNoCompatibilityCountSubstitutesForAnchor in the test suite.

STRUCTURAL SAFETY OVERRIDE: a populated, differing `runway_id` on both sides,
or a populated, differing `airport_id` on both sides, disconfirms a candidate
entirely - it is dropped from consideration (neither an anchor nor advisory
metadata), never merely down-weighted. Two Signals cannot legitimately be the
same physical project if they are structurally proven to sit on different
airports or different canonical runways; this module does not need "less
evidence" to reach that conclusion, since it is not scoring evidence at all.

Financial amounts and any form of raw free text (titles, notes, extracted
narrative) are structurally absent from every dataclass in this module - not
merely ignored by the algorithm, but impossible to pass in at all. See design
doc S6's own `UNSAFE_FOR_RECONCILIATION` classification of both axes, and the
design doc's own worked real-airport example (S8) of two independently-true
dollar figures that happen to share a numeral by pure coincidence - exactly
the kind of trap that motivated excluding money entirely rather than trying
to filter it safely.

Deliberately, this module never names a specific source family, acquisition
provider, airport authority, or vendor anywhere in its own source - not in
logic, and not even in commentary - so that property can be verified by
simple inspection rather than trusted by convention alone (see the test
suite's own provider-firewall checks).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional

__all__ = [
    "ExistingSignalReconciliationOutcome",
    "ExistingSignalReconciliationSubject",
    "ReconciliationCandidateSignal",
    "ExistingSignalReconciliationDecision",
    "evaluate_existing_signal_reconciliation",
]


class ExistingSignalReconciliationOutcome(str, Enum):
    """The reviewed design's three-outcome vocabulary
    (existing-signal-reconciliation-guard-design.md S5/S18) - unchanged by
    this implementation. str+Enum matches the existing typed-outcome
    precedent (AttachmentOutcome, SignalCandidateOutcome,
    PromotionPolicyOutcome) rather than the plain-string-constant convention
    used by ReviewerAction's persisted vocabulary - nothing here is persisted
    by this module, so the typed-Enum precedent applies."""

    CLEAR_TO_CREATE = "CLEAR_TO_CREATE"
    POSSIBLE_EXISTING_SIGNAL_MATCH = "POSSIBLE_EXISTING_SIGNAL_MATCH"
    ALREADY_LINKED = "ALREADY_LINKED"


@dataclass(frozen=True)
class ExistingSignalReconciliationSubject:
    """A DB-free, ORM-free snapshot of the new governed evidence under
    consideration for Signal creation - the reconciliation-relevant subset of
    what a future R2 candidate-discovery adapter would derive from one
    SourceAssertion (and its already-extracted, already-structured claims).
    Every field here is a structural reconciliation fact this module's
    taxonomy (design doc S6) actually classifies as ANCHOR or COMPATIBILITY
    evidence - no speculative field the current architecture cannot supply
    (design doc S6/S10; e.g. no explicit project-identifier field, since none
    exists anywhere in the schema today).

    `existing_signal_id`: mirrors `SourceAssertion.signal_id` - when set, this
    evidence is already linked, and evaluate_existing_signal_reconciliation()
    short-circuits to ALREADY_LINKED before looking at `candidates` at all
    (design doc S5/S11 - "candidate list irrelevant").

    Identity-anchor-bearing fields: `runway_id` (canonical, resolved -
    never free-text runway wording), `physical_installation_ids` (the
    `physical_installation_id` values from this evidence's own
    `InstallationAssertionLink` rows with outcome SAME_PHYSICAL_INSTALLATION -
    design doc S6's corrected transitive anchor path), `source_id` and
    `artifact_identity` (this evidence's own document identity, compared
    against a candidate's already-recorded supporting provenance).

    Compatibility-only fields: `airport_id` (also used as a hard
    disqualifying check when it structurally conflicts with a candidate's -
    never itself an anchor), `category`, `vendor_names` (structured party
    names already extracted from a relationship claim - never raw text
    scanned by this module), `evidence_date` (this evidence's own
    chronological position, for "does the candidate's evidence postdate
    this?" reasoning), `reference_year` (a bare year already present on the
    evidence, e.g. a target/planning year - weaker than `evidence_date`).

    Deliberately absent: any title, notes, or raw-text field (design doc
    S6 - title/free-text resemblance is UNSAFE_FOR_RECONCILIATION); any
    financial/dollar-amount field (design doc S6/S8 - a worked real-airport
    example there shows amount equality must be structurally impossible to
    use, not merely unused); any confidence/score/weight field (design doc
    S15 Invariant 20)."""

    existing_signal_id: Optional[int] = None
    airport_id: Optional[int] = None
    runway_id: Optional[int] = None
    physical_installation_ids: "tuple[int, ...]" = ()
    source_id: Optional[int] = None
    artifact_identity: Optional[str] = None
    category: Optional[str] = None
    vendor_names: "tuple[str, ...]" = ()
    evidence_date: Optional[date] = None
    reference_year: Optional[int] = None


@dataclass(frozen=True)
class ReconciliationCandidateSignal:
    """A DB-free, ORM-free snapshot of one existing Signal being evaluated as
    a possible reconciliation candidate - the reconciliation-relevant subset
    of what a future R2 candidate-discovery adapter would derive from one
    `Signal` row plus its `supporting_source_assertions` (design doc S11).

    `signal_id` is an opaque identifier - this module never interprets it
    beyond equality/sorting. `physical_installation_ids`,
    `supporting_source_ids`, and `supporting_artifact_identities` are the
    AGGREGATE of every `supporting_source_assertion` already linked to this
    Signal (design doc S6/S11's own "join through InstallationAssertionLink
    on each supporting_source_assertion" instruction) - a future adapter
    computes this aggregation; this module only ever tests set membership
    against it, never re-derives it from anything raw.

    `source_id`/`is_uniquely_sourced` (legacy blind-spot fix, real-DB blast-
    radius review): `source_id` is the candidate's OWN direct provenance
    reference - distinct from `supporting_source_ids`, which is derived
    entirely from already-linked supporting assertions and is empty for a
    candidate with none. `is_uniquely_sourced` is a fact the caller must
    compute and supply, never inferred here: whether this exact `source_id`
    is not shared by any OTHER candidate/Signal at all (a live, exact,
    non-fuzzy count the pure core cannot itself perform, since it never
    touches a database) - required because a single reference document can
    legitimately back more than one distinct real-world event, so direct
    `source_id` equality is only trustworthy as an anchor when it is also
    known to be exclusive to this one candidate.

    Same absent-field discipline as `ExistingSignalReconciliationSubject`:
    no title, no raw notes, no financial fields, no confidence/score."""

    signal_id: int
    airport_id: Optional[int] = None
    runway_id: Optional[int] = None
    physical_installation_ids: "tuple[int, ...]" = ()
    supporting_source_ids: "tuple[int, ...]" = ()
    supporting_artifact_identities: "tuple[str, ...]" = ()
    category: Optional[str] = None
    confirmed_vendor: Optional[str] = None
    likely_supplier: Optional[str] = None
    evidence_date: Optional[date] = None
    reference_year: Optional[int] = None
    source_id: Optional[int] = None
    is_uniquely_sourced: bool = False


@dataclass(frozen=True)
class ExistingSignalReconciliationDecision:
    """An evaluation result - never an ORM object, never persisted by this
    module (design doc S14). `candidate_signal_ids`/`reasons` are populated
    only for POSSIBLE_EXISTING_SIGNAL_MATCH and name only anchor-backed
    candidates; `advisory_candidate_signal_ids`/`advisory_reasons` are
    populated only for CLEAR_TO_CREATE and name only compatibility-only
    candidates - the two pairs are never populated on the same decision,
    matching the design doc's own "mutually exclusive by outcome" reading of
    S14. `signal_id` is populated only for ALREADY_LINKED. Every id tuple is
    sorted ascending for deterministic ordering (S22); reasons follow the
    same id order they were derived from."""

    outcome: ExistingSignalReconciliationOutcome
    candidate_signal_ids: "tuple[int, ...]" = ()
    reasons: "tuple[str, ...]" = ()
    signal_id: Optional[int] = None
    advisory_candidate_signal_ids: "tuple[int, ...]" = ()
    advisory_reasons: "tuple[str, ...]" = ()


def _norm(value: "str | None") -> "str | None":
    if value is None:
        return None
    stripped = value.strip()
    return stripped.casefold() if stripped else None


def _airport_conflict(subject: ExistingSignalReconciliationSubject, candidate: ReconciliationCandidateSignal) -> bool:
    return subject.airport_id is not None and candidate.airport_id is not None and subject.airport_id != candidate.airport_id


def _runway_conflict(subject: ExistingSignalReconciliationSubject, candidate: ReconciliationCandidateSignal) -> bool:
    return subject.runway_id is not None and candidate.runway_id is not None and subject.runway_id != candidate.runway_id


def _anchor_reasons(
    subject: ExistingSignalReconciliationSubject, candidate: ReconciliationCandidateSignal,
) -> "tuple[str, ...]":
    """[ANCHOR]-tier axes only (design doc S6). Each reason names the exact
    structural fact that agreed - never a score, never a count."""
    reasons: "list[str]" = []
    sid = candidate.signal_id

    if subject.runway_id is not None and candidate.runway_id is not None and subject.runway_id == candidate.runway_id:
        reasons.append(f"signal {sid}: identity_anchor:runway_id (runway_id={subject.runway_id})")

    shared_installations = sorted(set(subject.physical_installation_ids) & set(candidate.physical_installation_ids))
    for physical_installation_id in shared_installations:
        reasons.append(
            f"signal {sid}: identity_anchor:physical_installation_identity "
            f"(physical_installation_id={physical_installation_id})"
        )

    if subject.source_id is not None and subject.source_id in candidate.supporting_source_ids:
        reasons.append(f"signal {sid}: identity_anchor:provenance (source_id={subject.source_id})")

    if subject.artifact_identity is not None and subject.artifact_identity in candidate.supporting_artifact_identities:
        reasons.append(f"signal {sid}: identity_anchor:provenance (artifact_identity={subject.artifact_identity!r})")

    # Legacy blind-spot fix: a candidate with NO supporting SourceAssertion
    # at all (so the two anchors immediately above can never fire for it)
    # may still be anchored by its own direct source_id - but ONLY when the
    # caller has already established, exactly, that this source_id is not
    # shared by any other candidate/Signal (`is_uniquely_sourced`). This is
    # deliberately narrower than the two anchors above: it never fires for a
    # candidate that already has supporting-assertion provenance (that case
    # is already covered, and existing behavior for it must not change), and
    # it never fires for a source_id known to legitimately back more than
    # one distinct event. Still a genuine structural identity fact - never a
    # compatibility count - so Invariant 21 is unaffected.
    if (
        subject.source_id is not None
        and candidate.source_id is not None
        and subject.source_id == candidate.source_id
        and not candidate.supporting_source_ids
        and not candidate.supporting_artifact_identities
        and candidate.is_uniquely_sourced
    ):
        reasons.append(f"signal {sid}: identity_anchor:direct_unique_source (source_id={subject.source_id})")

    return tuple(reasons)


def _compatibility_reasons(
    subject: ExistingSignalReconciliationSubject, candidate: ReconciliationCandidateSignal,
) -> "tuple[str, ...]":
    """[COMPATIBILITY]-tier axes only (design doc S6) - never capable of
    gating POSSIBLE_EXISTING_SIGNAL_MATCH by themselves or in combination,
    regardless of how many of these agree (Invariant 21).

    Deliberately does NOT emit a standalone "same airport" reason: design doc
    S6 classifies airport agreement as a near-universal PRECONDITION for even
    considering a candidate (already true of every candidate a real R2
    adapter would return, since it scopes by airport_id), not a
    distinguishing signal in its own right - design doc S9 case G expects a
    bare-airport-only match to carry NO advisory metadata at all, not a
    single-item "compatibility:airport" note. Airport agreement is still
    used above (`_airport_conflict`) as a hard disqualifying check when it
    structurally *disagrees*."""
    reasons: "list[str]" = []
    sid = candidate.signal_id

    if _norm(subject.category) is not None and _norm(subject.category) == _norm(candidate.category):
        reasons.append(f"signal {sid}: compatibility:category (category={subject.category!r})")

    candidate_vendor_names = {
        v for v in (_norm(candidate.confirmed_vendor), _norm(candidate.likely_supplier)) if v is not None
    }
    matched_vendors = sorted(
        {raw for raw in subject.vendor_names if _norm(raw) in candidate_vendor_names}, key=_norm,
    )
    for vendor_name in matched_vendors:
        reasons.append(f"signal {sid}: compatibility:vendor (vendor={vendor_name!r})")

    if subject.evidence_date is not None and candidate.evidence_date is not None:
        if candidate.evidence_date >= subject.evidence_date:
            reasons.append(
                f"signal {sid}: compatibility:temporal "
                f"(candidate_evidence_date={candidate.evidence_date.isoformat()} postdates "
                f"subject_evidence_date={subject.evidence_date.isoformat()})"
            )

    if subject.reference_year is not None and subject.reference_year == candidate.reference_year:
        reasons.append(f"signal {sid}: compatibility:year (year={subject.reference_year})")

    return tuple(reasons)


def evaluate_existing_signal_reconciliation(
    subject: ExistingSignalReconciliationSubject,
    candidates: "tuple[ReconciliationCandidateSignal, ...]",
) -> ExistingSignalReconciliationDecision:
    """Pure, deterministic: the same (subject, candidates) pair always
    produces the same decision, regardless of call order or repetition
    (design doc S15 Invariant 20's determinism reading, S22).

    Step 1 (design doc S5/S11): if `subject.existing_signal_id` is set,
    return ALREADY_LINKED immediately - `candidates` is never inspected.

    Step 2: for each remaining candidate, independently (Invariant 22 - never
    pooled across candidates) check the hard structural disqualifiers
    (differing populated airport_id, differing populated runway_id) and drop
    disqualified candidates entirely - not even as advisory metadata
    (Invariant 23 - disconfirming evidence never promotes a compatibility
    axis to anchor status, and here it does the opposite: it removes the
    candidate from consideration altogether).

    Step 3: classify the remaining candidates' [ANCHOR] and [COMPATIBILITY]
    evidence independently. If any candidate has at least one anchor reason,
    the decision is POSSIBLE_EXISTING_SIGNAL_MATCH, naming every anchor-
    backed candidate (never just one - Invariant 7/22) with only their anchor
    reasons; compatibility-only candidates are not mentioned in this outcome
    at all (design doc S14 - the two reason lists are never populated
    together). Otherwise, if any candidate has at least one compatibility
    reason, the decision is CLEAR_TO_CREATE carrying that as non-blocking
    advisory metadata. Otherwise, CLEAR_TO_CREATE with no metadata at all.
    """
    if subject.existing_signal_id is not None:
        return ExistingSignalReconciliationDecision(
            outcome=ExistingSignalReconciliationOutcome.ALREADY_LINKED,
            signal_id=subject.existing_signal_id,
        )

    anchor_reasons_by_id: "dict[int, set[str]]" = {}
    advisory_reasons_by_id: "dict[int, set[str]]" = {}

    for candidate in candidates:
        if _airport_conflict(subject, candidate) or _runway_conflict(subject, candidate):
            continue

        anchor_reasons = _anchor_reasons(subject, candidate)
        if anchor_reasons:
            # `|=` (union), never `=` (overwrite): a caller-supplied candidate
            # tuple could legally contain more than one row for the same
            # signal_id (e.g. a not-yet-deduplicated R2 adapter result) - a
            # plain overwrite would silently drop one row's anchor reasons
            # depending on iteration order, making the decision's own
            # `reasons` non-deterministic across equivalent input orderings
            # (found during the R1 review checkpoint - see the R1 report's
            # own "corrections made during review" section). Merging keeps
            # the outcome AND the full reason set independent of row order.
            anchor_reasons_by_id.setdefault(candidate.signal_id, set()).update(anchor_reasons)
            continue

        compatibility_reasons = _compatibility_reasons(subject, candidate)
        if compatibility_reasons:
            advisory_reasons_by_id.setdefault(candidate.signal_id, set()).update(compatibility_reasons)

    if anchor_reasons_by_id:
        ordered_ids = tuple(sorted(anchor_reasons_by_id))
        # sorted(...) per id, not raw set iteration: set order depends on
        # Python's per-process string hash randomization, so relying on it
        # would make `reasons` non-deterministic across process runs even
        # for the exact same logical input (task's own determinism
        # requirement - "byte/equality-identical decisions").
        reasons = tuple(reason for signal_id in ordered_ids for reason in sorted(anchor_reasons_by_id[signal_id]))
        return ExistingSignalReconciliationDecision(
            outcome=ExistingSignalReconciliationOutcome.POSSIBLE_EXISTING_SIGNAL_MATCH,
            candidate_signal_ids=ordered_ids,
            reasons=reasons,
        )

    if advisory_reasons_by_id:
        ordered_ids = tuple(sorted(advisory_reasons_by_id))
        advisory_reasons = tuple(
            reason for signal_id in ordered_ids for reason in sorted(advisory_reasons_by_id[signal_id])
        )
        return ExistingSignalReconciliationDecision(
            outcome=ExistingSignalReconciliationOutcome.CLEAR_TO_CREATE,
            advisory_candidate_signal_ids=ordered_ids,
            advisory_reasons=advisory_reasons,
        )

    return ExistingSignalReconciliationDecision(outcome=ExistingSignalReconciliationOutcome.CLEAR_TO_CREATE)
