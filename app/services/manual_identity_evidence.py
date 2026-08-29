"""Governed persistence for a human analyst's LITERAL transcription of
identity evidence, feeding the EXISTING, UNMODIFIED EvidenceBag ->
IdentityGuard -> EB5 pipeline for a SourceAssertion that has never been
identity-governed at all (docs/architecture, "RWI - New Source Family
Manual Identity Evidence - Architecture Design" mission, the locked design
this module implements; see app.models.manual_identity_evidence for the
persisted row shape).

    SourceAssertion (airport_id already set, identity_guard_decision IS
        NULL, no SourceAssertionEvidenceBag, no ManualIdentityEvidence yet,
        signal_id IS NULL)
        -> record_manual_identity_evidence()
        -> one ManualIdentityEvidence row, append-only (the audit record of
           WHAT the analyst transcribed and FROM WHERE)
        -> manual_identity_evidence_to_candidate_fragment() (pure adapter,
           this module)
        -> app.services.discovery_candidate_fragment.candidate_fragment_to_evidence_bag()
           (existing, unmodified)
        -> one SourceAssertionEvidenceBag snapshot (existing EB1 shape,
           reused via app.services.discovery_evidence_persistence's own
           sole writer helper - see PERSISTENCE REUSE below)
        -> app.services.evidence_attachment_guard.evaluate_attachment_for_candidates()
           (existing, unmodified) run against exactly ONE candidate: the
           assertion's own current canonical Airport (never a
           caller-supplied one - see CANDIDATE DERIVATION below)
        -> SourceAssertion.identity_guard_decision/identity_guard_reason
           set ONCE (a strictly-validated NULL -> value transition,
           never a general mutable identity-decision API), exactly
           mirroring app.services.discovery_evidence_persistence.persist_discovery_fragment()'s
           own discovery-time write shape
        -> STOP. No Signal, no Airport mutation, no Source mutation. A
           later, separately-authorized step reads the result via
           app.services.effective_identity_guard_decision (EB5, unmodified).

THE HUMAN SUPPLIES EVIDENCE, NEVER A DECISION (this module's single most
important property): there is no parameter here for an identity outcome,
an "attach" choice, a confidence score, or an override of any kind.
evaluate_attachment_for_candidates() - the real, unmodified guard - is the
ONLY thing that ever computes an AttachmentOutcome from the transcribed
evidence.

GENERIC, NOT SOURCE-FAMILY-SPECIFIC: this module has no concept of Korea,
Sacheon, "international," or "news" sources. `_PARSER_IDENTIFIER` below is
deliberately the generic `manual_identity_evidence_v1` - a real, ad hoc
value (`manual-korea-research-v1`) was used directly on real SourceAssertion
rows by an earlier, separate ingestion mission; that value is NOT this
mechanism's own architectural identity and never appears here.

CANDIDATE DERIVATION (Phase 6/11's own explicit constraint): the candidate
Airport evaluated against is derived SOLELY from
`source_assertion.airport_id` - never from a caller-supplied airport_id.
There is no `airport_id` parameter anywhere on
`record_manual_identity_evidence()`.

SOURCE BINDING (Phase 6): the caller must supply `source_id`, and it must
equal `source_assertion.source_id` exactly - `SourceMismatchError`
otherwise. This is deliberately a required, explicit parameter (never
silently derived from the assertion alone) so a caller cannot accidentally
attach evidence transcribed from one Source to a SourceAssertion belonging
to a different one; mirrors
app.services.source_assertion_legacy_identity_attestation's own
`matched_airport_id` must-equal-current-value precondition shape exactly.

LITERAL-TRANSCRIPTION ENFORCEMENT (Phase 3/7): only ONE extraction mode
exists this slice, `HUMAN_TRANSCRIPTION` - info not explicitly present in
`evidence_excerpt` may never be supplied, even when the analyst personally
knows it to be true from background knowledge. Mechanically enforced,
conservatively, by `_MECHANICALLY_VALIDATED_RAW_FIELDS`: every non-None raw
field (name/country/city/identifier code) must occur as a literal
substring of `evidence_excerpt` after a narrow, safe normalization
(casefold + collapsed whitespace) - never fuzzy/NLP matching. A value that
cannot be verified this way is refused (`RawFieldNotInExcerptError`), never
silently accepted on trust.

PERSISTENCE REUSE, NOT REIMPLEMENTATION (Phase 10): this module imports and
reuses `app.services.discovery_evidence_persistence._attach_evidence_bag_snapshot()`
- the ONE function that module's own docstring names as "the ONLY code
path in this module permitted to construct a SourceAssertionEvidenceBag
row" (serialization, hashing, and schema_version ownership) - rather than
hand-rolling a second, parallel snapshot-writer. Neither
`persist_discovery_fragment()` (always creates a brand-new SourceAssertion)
nor `persist_candidate_linked_source_assertion()` (always creates a
brand-new SourceAssertion linked to an UnknownAirportCandidate) can operate
against an ALREADY-existing, already-airport-attached SourceAssertion -
this module's own `record_manual_identity_evidence()` is exactly the
smallest new persistence operation needed to fill that one gap, calling
every other existing step (`candidate_fragment_to_evidence_bag()`,
`evaluate_attachment_for_candidates()`, `candidate_airport_from_airport_like()`)
completely unmodified.

AUDIT-CHAIN RECONSTRUCTION (Phase 13): given one `source_assertion_id`, a
reviewer reconstructs the full chain with three independent, plain queries
- no circular or redundant schema is needed (see
app.models.manual_identity_evidence's own "WHY NO evidence_bag_snapshot_id"
note):

    1. `SELECT * FROM manual_identity_evidences WHERE source_assertion_id = X`
       -> who transcribed (analyst), when (created_at), from which Source
       (source_id), the exact excerpt (evidence_excerpt), the exact
       transcribed values (raw_airport_name/raw_country/raw_city/
       raw_identifier_code), extraction_mode, normalization_version.
    2. `SELECT * FROM source_assertion_evidence_bags WHERE source_assertion_id = X`
       -> which EvidenceBag resulted (evidence_bag_json/evidence_bag_hash).
    3. `SourceAssertion.identity_guard_decision`/`identity_guard_reason`
       (on the assertion itself) -> which IdentityGuard outcome resulted -
       and, downstream, `app.services.effective_identity_guard_decision.resolve_effective_identity_guard_decision()`
       for the current EB5-governed effective decision.

CORRECTION/SUPERSESSION: deliberately NOT implemented this slice (Phase 14)
- a mistaken transcription is never edited (the model is immutable) and
never superseded (no supersedes_manual_identity_evidence_id column exists);
a duplicate/rerun attempt for an assertion that already has a
ManualIdentityEvidence row, or an EvidenceBag, is refused outright by
`check_manual_identity_evidence_eligibility()`. This is deliberate: "that
is preferable to implementing incomplete correction semantics" (mission's
own words) - a future, separately-designed correction workflow is out of
scope here.

Never commits and never imports app.database.SessionLocal - mutates the
caller-supplied Session and flushes only, so any constraint violation
surfaces immediately; the caller owns the transaction boundary entirely,
matching every other persistence service in this pipeline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Airport, Source, SourceAssertion
from app.models.manual_identity_evidence import (
    MANUAL_IDENTITY_EVIDENCE_EXTRACTION_MODES,
    MANUAL_IDENTITY_EVIDENCE_NORMALIZATION_VERSION,
    ManualIdentityEvidence,
)
from app.models.source_assertion_evidence_bag import SourceAssertionEvidenceBag
from app.services.discovery_candidate_fragment import CandidateFragment, candidate_fragment_to_evidence_bag
from app.services.discovery_evidence_persistence import _attach_evidence_bag_snapshot
from app.services.evidence_attachment_guard import (
    AttachmentOutcome,
    candidate_airport_from_airport_like,
    evaluate_attachment_for_candidates,
)
from app.services.resolved_candidate_evidence_reevaluation import SourceAssertionNotFoundError

__all__ = [
    "SourceAssertionNotFoundError",
    "SourceNotFoundError",
    "SourceMismatchError",
    "MissingCanonicalAirportError",
    "UnresolvedUnknownAirportCandidateError",
    "ModernIdentityGuardAlreadyGovernedError",
    "ExistingEvidenceBagError",
    "ExistingManualIdentityEvidenceError",
    "SignalAlreadyLinkedError",
    "TargetAirportNotFoundError",
    "EmptyEvidenceExcerptError",
    "RawFieldNotInExcerptError",
    "ManualIdentityEvidenceResult",
    "normalize_for_containment_check",
    "excerpt_contains_value",
    "check_manual_identity_evidence_eligibility",
    "manual_identity_evidence_to_candidate_fragment",
    "record_manual_identity_evidence",
]

# Deliberately generic (module docstring "GENERIC, NOT SOURCE-FAMILY-
# SPECIFIC") - never a per-source-family value. The parser_identifier
# names THIS MECHANISM, not the source family or language of whatever
# SourceAssertion it happens to be used for.
_PARSER_IDENTIFIER = "manual_identity_evidence_v1"


class SourceNotFoundError(ValueError):
    """Raised when the caller-supplied `source_id` does not reference an
    existing Source row."""

    def __init__(self, source_id: int) -> None:
        self.source_id = source_id
        super().__init__(f"source_id={source_id!r} does not reference an existing Source")


class SourceMismatchError(ValueError):
    """Raised when the caller-supplied `source_id` does not equal the
    SourceAssertion's own current `source_id` - prevents attaching evidence
    transcribed from one Source to a SourceAssertion that actually belongs
    to a different one (Phase 6 source-binding requirement)."""

    def __init__(self, source_assertion_id: int, *, expected_source_id: int, supplied_source_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        self.expected_source_id = expected_source_id
        self.supplied_source_id = supplied_source_id
        super().__init__(
            f"supplied source_id={supplied_source_id!r} does not equal SourceAssertion "
            f"{source_assertion_id}'s own source_id={expected_source_id!r} - manual identity evidence "
            "must be inseparably tied to the exact Source it was transcribed from."
        )


class MissingCanonicalAirportError(ValueError):
    """Raised when `source_assertion.airport_id` is NULL - this mechanism
    only ever derives its candidate Airport from an already-set airport_id;
    an unresolved assertion belongs to the unknown-airport discovery
    pipeline (ERG/UAC), never here."""

    def __init__(self, source_assertion_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        super().__init__(
            f"SourceAssertion {source_assertion_id} has no airport_id - this mechanism only ever "
            "evaluates evidence against an already-set canonical Airport."
        )


class UnresolvedUnknownAirportCandidateError(ValueError):
    """Raised when `source_assertion.unknown_airport_candidate_id` is set -
    a candidate-linked assertion has no canonical Airport identity yet for
    this mechanism to evaluate evidence against (mirrors
    MissingCanonicalAirportError's own reasoning; only reachable in
    practice via direct DB bypass, since SourceAssertion's own DB-level
    CHECK constraint already forbids both being set at once)."""

    def __init__(self, source_assertion_id: int, *, unknown_airport_candidate_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        self.unknown_airport_candidate_id = unknown_airport_candidate_id
        super().__init__(
            f"SourceAssertion {source_assertion_id} is still linked to UnknownAirportCandidate "
            f"{unknown_airport_candidate_id} - it has no canonical Airport for this mechanism to "
            "evaluate evidence against yet."
        )


class ModernIdentityGuardAlreadyGovernedError(ValueError):
    """Raised when `source_assertion.identity_guard_decision` is already
    set - this row has already been identity-governed (however it turned
    out); this mechanism exists only to supply the FIRST identity evidence
    for a row that has never been governed at all."""

    def __init__(self, source_assertion_id: int, *, decision: str) -> None:
        self.source_assertion_id = source_assertion_id
        self.decision = decision
        super().__init__(
            f"SourceAssertion {source_assertion_id} already has identity_guard_decision={decision!r} - "
            "it has already been identity-governed; this mechanism only supplies a FIRST decision."
        )


class ExistingEvidenceBagError(ValueError):
    """Raised when a SourceAssertionEvidenceBag already exists for this
    assertion - a real EvidenceBag can only ever be produced once per
    assertion (EB1's own 1:1 UniqueConstraint); this mechanism never
    duplicates or bypasses that."""

    def __init__(self, source_assertion_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        super().__init__(
            f"SourceAssertion {source_assertion_id} already has a SourceAssertionEvidenceBag snapshot."
        )


class ExistingManualIdentityEvidenceError(ValueError):
    """Raised when a ManualIdentityEvidence row already exists for this
    assertion - a duplicate/rerun attempt is refused outright rather than
    creating a second, competing transcription (Phase 14: no supersession
    this slice, so a genuine correction is out of scope, not silently
    permitted)."""

    def __init__(self, source_assertion_id: int, *, existing_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        self.existing_id = existing_id
        super().__init__(
            f"SourceAssertion {source_assertion_id} already has ManualIdentityEvidence "
            f"#{existing_id} - this slice implements no correction/supersession mechanism; refusing "
            "to record a second, competing transcription."
        )


class SignalAlreadyLinkedError(ValueError):
    """Raised when `source_assertion.signal_id` is already set - a row that
    already produced a Signal despite never having been identity-governed
    is a data-integrity anomaly outside this mechanism's scope."""

    def __init__(self, source_assertion_id: int, *, signal_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        self.signal_id = signal_id
        super().__init__(
            f"SourceAssertion {source_assertion_id} already has signal_id={signal_id!r} set - "
            "this mechanism never supplies first-time identity evidence for a row already linked "
            "to a Signal."
        )


class TargetAirportNotFoundError(ValueError):
    """Raised when `source_assertion.airport_id` does not reference an
    existing Airport row - only reachable via a malformed/foreign-key-
    disabled database, matching every other identical precedent in this
    pipeline (e.g. legacy attestation's own TargetAirportNotFoundError)."""

    def __init__(self, airport_id: int) -> None:
        self.airport_id = airport_id
        super().__init__(f"airport_id={airport_id!r} does not reference an existing Airport")


class EmptyEvidenceExcerptError(ValueError):
    """Raised when `evidence_excerpt` is empty or whitespace-only - a
    human cannot honestly transcribe from nothing."""

    def __init__(self) -> None:
        super().__init__("evidence_excerpt is required and cannot be empty")


class RawFieldNotInExcerptError(ValueError):
    """Raised when a supplied raw_* field's value cannot be mechanically
    verified as a literal (case/whitespace-normalized) substring of
    `evidence_excerpt` - fail-closed rather than trusting an unverifiable
    claim (Phase 7's own explicit instruction: "when a value can't be
    mechanically validated safely, fail closed rather than add clever
    inference")."""

    def __init__(self, field_name: str, value: str) -> None:
        self.field_name = field_name
        self.value = value
        super().__init__(
            f"{field_name}={value!r} does not occur literally within evidence_excerpt - a "
            "transcribed value must be mechanically verifiable against the exact evidence text; "
            "hidden inference from background knowledge is never permitted."
        )


@dataclass(frozen=True)
class ManualIdentityEvidenceResult:
    """Deterministic, ORM-free summary of what
    record_manual_identity_evidence() did - never exposes ORM instances
    directly, matching this pipeline's own established convention."""

    manual_identity_evidence_id: int
    source_assertion_id: int
    source_id: int
    evidence_bag_snapshot_id: int
    identity_guard_decision: str
    identity_guard_reason: str


_WHITESPACE_RE = re.compile(r"\s+")


def normalize_for_containment_check(value: str) -> str:
    """Pure, deterministic, mechanically-safe normalization used for EVERY
    literal-containment check in this module (excerpt and every candidate
    raw_* value alike, so the two sides can never be compared under
    different rules): casefold (safer than .lower() for non-ASCII text,
    e.g. Korean, without attempting any language-specific transliteration)
    plus collapsing runs of whitespace to one space. Deliberately NOT
    fuzzy/NLP matching of any kind (Phase 7's own explicit boundary)."""
    return _WHITESPACE_RE.sub(" ", value.strip().casefold())


def excerpt_contains_value(excerpt: str, value: str) -> bool:
    """True iff `value` occurs as a literal substring of `excerpt` after
    `normalize_for_containment_check()` is applied to both sides
    identically."""
    return normalize_for_containment_check(value) in normalize_for_containment_check(excerpt)


def check_manual_identity_evidence_eligibility(
    session: Session, source_assertion: SourceAssertion, *, source_id: int,
) -> None:
    """The single source of truth for "is this assertion even eligible for
    this mechanism at all" - both record_manual_identity_evidence() and any
    read-only CLI inspect call must call this exact function, so they can
    never disagree about eligibility (mirrors
    check_legacy_attestation_eligibility()'s own identical discipline).
    Raises the first violated precondition; never partially reports.
    Fail-closed: every precondition below is a REQUIREMENT, never merely
    advisory."""
    source = session.get(Source, source_id)
    if source is None:
        raise SourceNotFoundError(source_id)
    if source_id != source_assertion.source_id:
        raise SourceMismatchError(
            source_assertion.id, expected_source_id=source_assertion.source_id, supplied_source_id=source_id,
        )
    if source_assertion.unknown_airport_candidate_id is not None:
        raise UnresolvedUnknownAirportCandidateError(
            source_assertion.id, unknown_airport_candidate_id=source_assertion.unknown_airport_candidate_id,
        )
    if source_assertion.airport_id is None:
        raise MissingCanonicalAirportError(source_assertion.id)
    # Checked BEFORE the more generic ModernIdentityGuardAlreadyGovernedError
    # below: once this mechanism has recorded evidence for an assertion, a
    # rerun would otherwise surface only the generic "already governed"
    # message (also true, since this mechanism itself sets
    # identity_guard_decision - see record_manual_identity_evidence()) -
    # this ordering gives a duplicate/rerun attempt the more specific,
    # more actionable error naming exactly which ManualIdentityEvidence row
    # already exists.
    existing_manual_evidence_id = session.scalar(
        select(ManualIdentityEvidence.id).where(
            ManualIdentityEvidence.source_assertion_id == source_assertion.id
        )
    )
    if existing_manual_evidence_id is not None:
        raise ExistingManualIdentityEvidenceError(source_assertion.id, existing_id=existing_manual_evidence_id)
    if source_assertion.identity_guard_decision is not None:
        raise ModernIdentityGuardAlreadyGovernedError(
            source_assertion.id, decision=source_assertion.identity_guard_decision,
        )
    has_evidence_bag = session.scalar(
        select(SourceAssertionEvidenceBag.id).where(
            SourceAssertionEvidenceBag.source_assertion_id == source_assertion.id
        )
    )
    if has_evidence_bag is not None:
        raise ExistingEvidenceBagError(source_assertion.id)
    if source_assertion.signal_id is not None:
        raise SignalAlreadyLinkedError(source_assertion.id, signal_id=source_assertion.signal_id)
    airport = session.get(Airport, source_assertion.airport_id)
    if airport is None:
        raise TargetAirportNotFoundError(source_assertion.airport_id)


def _validate_literal_transcription(
    *,
    evidence_excerpt: str,
    raw_airport_name: "str | None",
    raw_country: "str | None",
    raw_city: "str | None",
    raw_identifier_code: "str | None",
) -> None:
    """Phase 7 literal-evidence safety: excerpt non-empty; every supplied
    raw_* field must mechanically occur within it. Never accepts a value on
    trust merely because it "sounds right" - fails closed on anything that
    cannot be verified this exact way."""
    if not evidence_excerpt or not evidence_excerpt.strip():
        raise EmptyEvidenceExcerptError()

    raw_values = {
        "raw_airport_name": raw_airport_name,
        "raw_country": raw_country,
        "raw_city": raw_city,
        "raw_identifier_code": raw_identifier_code,
    }
    for field_name, value in raw_values.items():
        if value is None:
            continue
        if not value.strip():
            raise ValueError(f"{field_name}, if supplied, cannot be empty/whitespace-only")
        if not excerpt_contains_value(evidence_excerpt, value):
            raise RawFieldNotInExcerptError(field_name, value)


def manual_identity_evidence_to_candidate_fragment(evidence: ManualIdentityEvidence) -> CandidateFragment:
    """Pure adapter: ManualIdentityEvidence (an already-PERSISTED row -
    never separate caller parameters that could diverge from what was
    actually persisted, per Phase 8/9) -> CandidateFragment. Maps ONLY
    literal/transcribed fields; performs no I/O, no inference, no lookup.

    Field mapping:
    - artifact_identity: the assertion's own artifact_identity (falls back
      to a synthetic, still-deterministic identity derived from this row's
      own id when the assertion carries none - required by CandidateFragment,
      never left ambiguous).
    - source_locator: the assertion's own source_locator (same fallback
      reasoning) - reused from the existing SourceAssertion relationship
      rather than duplicated onto this table (see
      app.models.manual_identity_evidence's own field-design note).
    - raw_text: evidence.evidence_excerpt verbatim - the exact literal
      text the analyst transcribed from, never a summary.
    - airport_names / airport_identifiers / locations: the transcribed
      raw_airport_name / raw_identifier_code / {raw_country, raw_city} -
      each only when non-None, one-to-one, never enriched or expanded.
    - parser_identifier: the generic `manual_identity_evidence_v1` (module
      docstring) - explicitly NOT a source-family-specific value.
    - extracted_at: this row's own created_at - when the evidence was
      actually recorded, never a later processing time.
    """
    assertion = evidence.source_assertion
    artifact_identity = assertion.artifact_identity or f"manual_identity_evidence:{evidence.id}"
    source_locator = assertion.source_locator or f"manual_identity_evidence:{evidence.id}"

    airport_names = frozenset({evidence.raw_airport_name}) if evidence.raw_airport_name else frozenset()
    airport_identifiers = frozenset({evidence.raw_identifier_code}) if evidence.raw_identifier_code else frozenset()
    locations = frozenset(
        value for value in (evidence.raw_country, evidence.raw_city) if value
    )

    return CandidateFragment(
        artifact_identity=artifact_identity,
        source_locator=source_locator,
        raw_text=evidence.evidence_excerpt,
        airport_identifiers=airport_identifiers,
        airport_names=airport_names,
        locations=locations,
        parser_identifier=_PARSER_IDENTIFIER,
        extracted_at=evidence.created_at,
    )


def record_manual_identity_evidence(
    session: Session,
    *,
    source_assertion_id: int,
    source_id: int,
    evidence_excerpt: str,
    analyst: str,
    raw_airport_name: "str | None" = None,
    raw_country: "str | None" = None,
    raw_city: "str | None" = None,
    raw_identifier_code: "str | None" = None,
    extraction_mode: str = "HUMAN_TRANSCRIPTION",
) -> ManualIdentityEvidenceResult:
    """Validates every precondition, persists exactly one immutable
    ManualIdentityEvidence row, THEN builds a CandidateFragment from that
    persisted row, feeds it through the existing, unmodified EvidenceBag ->
    IdentityGuard pipeline against exactly the assertion's own canonical
    Airport, and records the resulting FIRST raw
    identity_guard_decision/identity_guard_reason on the SourceAssertion
    itself (a one-time NULL -> value transition). Never commits; calls
    session.flush() at each write step so any constraint violation
    surfaces immediately. See module docstring for the full flow.

    Preconditions, checked in this exact order, all inside one
    `session.no_autoflush` block (mirrors every other governed-write
    service in this pipeline):

    1. source_assertion exists -> SourceAssertionNotFoundError
    2-9. check_manual_identity_evidence_eligibility() (Source exists and
         matches, no unresolved UnknownAirportCandidate link, canonical
         airport_id set, not already identity-governed, no existing
         EvidenceBag, no existing ManualIdentityEvidence, no linked Signal,
         target Airport exists)
    10. analyst non-empty -> ValueError
    11. extraction_mode is a real vocabulary member -> ValueError
    12. literal-transcription validation (excerpt non-empty; every
        supplied raw_* field mechanically occurs within the excerpt)
    """
    with session.no_autoflush:
        source_assertion = session.get(SourceAssertion, source_assertion_id)
        if source_assertion is None:
            raise SourceAssertionNotFoundError(source_assertion_id)

        check_manual_identity_evidence_eligibility(session, source_assertion, source_id=source_id)

        if not analyst or not analyst.strip():
            raise ValueError("analyst is required")
        if extraction_mode not in MANUAL_IDENTITY_EVIDENCE_EXTRACTION_MODES:
            raise ValueError(
                f"extraction_mode must be one of {MANUAL_IDENTITY_EVIDENCE_EXTRACTION_MODES!r}, "
                f"got {extraction_mode!r}"
            )

        _validate_literal_transcription(
            evidence_excerpt=evidence_excerpt,
            raw_airport_name=raw_airport_name,
            raw_country=raw_country,
            raw_city=raw_city,
            raw_identifier_code=raw_identifier_code,
        )

        evidence = ManualIdentityEvidence(
            source_assertion_id=source_assertion.id,
            source_id=source_id,
            evidence_excerpt=evidence_excerpt,
            raw_airport_name=raw_airport_name,
            raw_country=raw_country,
            raw_city=raw_city,
            raw_identifier_code=raw_identifier_code,
            analyst=analyst,
            extraction_mode=extraction_mode,
            normalization_version=MANUAL_IDENTITY_EVIDENCE_NORMALIZATION_VERSION,
        )
        session.add(evidence)
        session.flush()

        # Built FROM THE PERSISTED RECORD - never from the separate
        # caller-supplied parameters above, which could in principle
        # diverge from what actually landed in the database (Phase 8/9).
        fragment = manual_identity_evidence_to_candidate_fragment(evidence)
        evidence_bag = candidate_fragment_to_evidence_bag(fragment)

        snapshot = _attach_evidence_bag_snapshot(
            session, source_assertion_id=source_assertion.id, evidence_bag=evidence_bag,
        )

        # Candidate derived SOLELY from the assertion's own canonical
        # airport_id - never a caller-supplied one (Phase 6/11). Eligibility
        # already proved this airport exists.
        #
        # Local import (not top-level): app.services.airport_alias itself
        # imports normalize_for_containment_check/excerpt_contains_value
        # FROM this module, so a top-level import here would be circular.
        # Deferred to call time, by which point both modules are fully
        # initialized. get_admitted_airport_aliases() is a pure read (no
        # mutation, no flush) that derives the CURRENTLY admitted alias
        # set for this Airport from the append-only AirportAlias history
        # (docs/architecture, "RWI - Governed Canonical Airport Aliases -
        # Cross-Script Identity Design" mission) - the ONLY change this
        # mission makes to this function; falls back to an empty set on a
        # database that has never migrated that table, so this remains
        # completely backward compatible.
        from app.services.airport_alias import get_admitted_airport_aliases

        airport = session.get(Airport, source_assertion.airport_id)
        candidate = candidate_airport_from_airport_like(
            airport, aliases=get_admitted_airport_aliases(session, airport.id),
        )
        decisions = evaluate_attachment_for_candidates(evidence_bag, [candidate])
        decision = decisions[candidate.id]

        # Strictly-validated one-time NULL -> value transition - eligibility
        # above already proved identity_guard_decision IS NULL; re-asserted
        # here as a defensive guard against this exact write path ever
        # being reused as a general mutable identity-decision API.
        if source_assertion.identity_guard_decision is not None:
            raise ModernIdentityGuardAlreadyGovernedError(
                source_assertion.id, decision=source_assertion.identity_guard_decision,
            )
        source_assertion.identity_guard_decision = decision.outcome.value
        source_assertion.identity_guard_reason = decision.reason
        session.flush()

        return ManualIdentityEvidenceResult(
            manual_identity_evidence_id=evidence.id,
            source_assertion_id=source_assertion.id,
            source_id=source_id,
            evidence_bag_snapshot_id=snapshot.id,
            identity_guard_decision=decision.outcome.value,
            identity_guard_reason=decision.reason,
        )
