"""UAC3 — integrates the existing discovery candidate-selection path with
the governed unknown-airport-candidate path (UAC1/UAC2B)
(docs/architecture/rwi-uac3-unknown-airport-discovery-integration-report.md,
Slice 3 of docs/architecture/rwi-governed-new-airport-discovery-design.md).

    CandidateFragment + known CandidateAirport list
        -> resolve_or_persist_discovery_identity()
        -> exactly ONE of:
             KNOWN_CANONICAL_ATTACHMENT   (existing path, unchanged)
             AMBIGUOUS_KNOWN_IDENTITY     (existing path, unchanged)
             UNKNOWN_AIRPORT_CANDIDATE    (NEW - this slice)
             UNRESOLVED_IDENTITY          (existing path, unchanged)
        -> STOP (no Airport, Runway, RunwayEnd, Installation, or Signal is
           ever created here; MATCH_EXISTING_AIRPORT/CREATE_NEW_AIRPORT
           resolution, the human review CLI, and candidate-selection
           orchestration for any specific acquisition producer are all
           separate, future, not-yet-built work)

DECIDES EXACTLY ONE QUESTION: "can this evidence safely attach to a known
canonical Airport - and if not, does it carry enough of its OWN
deterministic identity information to form a governed, non-canonical
candidate?" It never decides that a new real airport definitely exists,
that no existing airport could ever match, canonical runway identity, or
whether a Signal should ever be created from this evidence - all of
those remain entirely out of scope, exactly as they already are for the
existing known-airport path this module composes rather than replaces.

WHY THIS ORCHESTRATION CANNOT SIMPLY CALL persist_discovery_fragment()
UNCONDITIONALLY AND THEN persist_candidate_linked_source_assertion():
both functions key their own idempotent find-or-reuse behavior on the
IDENTICAL fragment-identity tuple (source_id, artifact_identity,
source_locator, raw_fragment_hash) against the same SourceAssertion
uniqueness constraint. Calling persist_discovery_fragment() first would
unconditionally create a SourceAssertion with airport_id=NULL,
unknown_airport_candidate_id=NULL for the "no known match" case; a
subsequent persist_candidate_linked_source_assertion() call for the
SAME fragment would then find that ALREADY-EXISTING row and correctly
return it UNCHANGED (that function's own, deliberate "never rewrite an
already-resolved SourceAssertion" rule - the identical discipline
persist_discovery_fragment() itself already applies) - meaning
unknown_airport_candidate_id would never actually get set. This module
avoids that trap by evaluating the deterministic guard decision itself
FIRST (reusing the exact same pure evaluate_attachment_for_candidates()
call persist_discovery_fragment() makes internally - never
reimplemented), and calling exactly ONE of the two persistence functions
based on that decision, never both for the same fragment.

CANDIDATE-INPUT EXTRACTION CONTRACT: this module never re-reads raw
text, never makes a second extraction/LLM call, and never applies a
hidden heuristic parser to split already-extracted values into finer
categories extraction does not itself distinguish.
CandidateFragment.airport_identifiers is undifferentiated by code type
(IATA vs ICAO vs FAA-LID all mixed in one set) - this module does not
guess which type a given string is, and does not populate any of
UnknownAirportCandidate's three separately-typed claimed-code fields
from it. CandidateFragment.locations is similarly undifferentiated by
geographic granularity (city vs state/region vs country all mixed in
one set) - this module does not guess a value's granularity, and does
not populate raw_city/raw_state_region/raw_country from it. Only
raw_name (from CandidateFragment.airport_names) and
raw_runway_designation (from runway_ends/runway_pairs, audit-only, never
part of the fingerprint) are populated - see
_extract_unknown_airport_candidate_seed()'s own docstring for the exact,
deterministic, required-field formability rule this implies. A future
extraction-layer enhancement that structurally distinguishes code type
or location granularity at extraction time (not by heuristic
post-processing here) could populate the remaining fields correctly;
this module deliberately does not attempt it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from sqlalchemy.orm import Session

from app.services.discovery_candidate_fragment import CandidateFragment, candidate_fragment_to_evidence_bag
from app.services.discovery_evidence_persistence import (
    DiscoverySourceMetadata,
    persist_candidate_linked_source_assertion,
    persist_discovery_fragment,
)
from app.services.evidence_attachment_guard import (
    AttachmentOutcome,
    CandidateAirport,
    evaluate_attachment_for_candidates,
)
from app.services.unknown_airport_candidate_persistence import find_or_create_unknown_airport_candidate

__all__ = [
    "DiscoveryIdentityOutcome",
    "DiscoveryIdentityResolutionResult",
    "resolve_or_persist_discovery_identity",
]

# Guard outcomes that mean "a known Airport candidate was safely attached" -
# the existing path, entirely unchanged by this module.
_KNOWN_MATCH_OUTCOMES = (AttachmentOutcome.ATTACH_CONFIRMED, AttachmentOutcome.ATTACH_PROVISIONAL)

# The guard outcome meaning "more than one known candidate independently
# qualified" (evaluate_attachment_for_candidates() own, only source of
# REVIEW_REQUIRED). Per the design's own locked policy: ambiguity among
# KNOWN candidates is an ordinary human identity-review case, never a
# reason to fabricate a "new airport" candidate merely because matching
# was ambiguous - the true airport is presumably ALREADY one of the
# plausible known candidates.
_AMBIGUOUS_KNOWN_OUTCOME = AttachmentOutcome.REVIEW_REQUIRED

# Guard outcomes meaning "no known candidate accepted this evidence" -
# REJECT_CROSS_AIRPORT (evidence positively conflicts with at least one
# supplied candidate) and INSUFFICIENT_IDENTITY (no positive or
# contradicting evidence found) are treated identically for ROUTING
# purposes here: both mean "not the candidates I was given," and the
# only further question this module asks is whether the FRAGMENT'S OWN
# extracted identity is sufficient to form a governed candidate,
# independent of which specific known candidates were rejected or simply
# not matched. The two outcomes remain distinguishable in
# DiscoveryIdentityResolutionResult.attachment_outcome and in whichever
# SourceAssertion.identity_guard_decision value the underlying guard
# call actually produced - this module does not collapse that
# information, only their ROUTING consequence.
_NO_KNOWN_MATCH_OUTCOMES = (AttachmentOutcome.REJECT_CROSS_AIRPORT, AttachmentOutcome.INSUFFICIENT_IDENTITY)


class DiscoveryIdentityOutcome(str, Enum):
    """The four mutually exclusive routing outcomes this module can
    produce - a narrow, explicit enum rather than an overloaded boolean,
    matching the vocabulary conventions already established elsewhere in
    this pipeline (AttachmentOutcome, PromotionPolicyOutcome)."""

    KNOWN_CANONICAL_ATTACHMENT = "KNOWN_CANONICAL_ATTACHMENT"
    UNKNOWN_AIRPORT_CANDIDATE = "UNKNOWN_AIRPORT_CANDIDATE"
    AMBIGUOUS_KNOWN_IDENTITY = "AMBIGUOUS_KNOWN_IDENTITY"
    UNRESOLVED_IDENTITY = "UNRESOLVED_IDENTITY"


@dataclass(frozen=True)
class DiscoveryIdentityResolutionResult:
    """Deterministic, ORM-free summary of what
    resolve_or_persist_discovery_identity() did - never exposes ORM
    instances directly, matching DiscoveryPersistenceResult's own
    convention.

    `attachment_outcome` is the underlying guard AttachmentOutcome this
    routing decision was made from - always populated, for audit/
    debugging; never re-interpreted by any caller as a fifth outcome.
    `unknown_airport_candidate_created` is None whenever no candidate was
    involved (every outcome except UNKNOWN_AIRPORT_CANDIDATE); True/False
    (created vs exact-fingerprint reuse) only for that one outcome.
    """

    outcome: DiscoveryIdentityOutcome
    reason: str
    attachment_outcome: AttachmentOutcome
    source_id: int
    source_created: bool
    source_assertion_id: int
    source_assertion_created: bool
    attached_airport_id: Optional[int] = None
    unknown_airport_candidate_id: Optional[int] = None
    unknown_airport_candidate_created: Optional[bool] = None


def _extract_unknown_airport_candidate_seed(fragment: CandidateFragment) -> "Optional[dict]":
    """Deterministic, required-field candidate-formability rule - no
    confidence scoring, no heuristic parsing (module docstring). Returns
    None (not formable - remain UNRESOLVED_IDENTITY) unless EXACTLY ONE
    claimed airport name is present in the fragment:

    - ZERO names: "weak identity claim" (design mission §15) - e.g. "the
      airport will install EMAS" with no reliable name/code/location.
      Never manufacture a candidate from nothing.
    - EXACTLY ONE name: sufficient, PROVIDED it contains at least one
      alphabetic character (any Unicode letter, not just ASCII/English -
      `str.isalpha()` is script-agnostic) - a purely punctuation/numeric/
      symbol string (e.g. "---", "123", "***") cannot be an airport name
      in any language and is rejected here (UAC3 critical-review
      correction: found and closed during adversarial review, reproduced
      directly against a live probe). This is a deterministic, structural
      character-class check, not a confidence score or an NLP judgment -
      it says nothing about whether a name is a REAL or SPECIFIC airport
      name (a bare, generic "Airport" still forms a candidate; that is
      unaffected and deliberate - see the review report's own
      "candidate-formability verdict" for why a semantic-quality/
      genericness filter is explicitly NOT added: it would require an
      inevitably incomplete, English-biased blocklist, which the human
      review layer this candidate feeds is the correct, already-governed
      place to reject, not this deterministic gate).
    - TWO OR MORE names: "multi-airport document" safety (design mission
      §16) - a single fragment ambiguously naming multiple airports must
      fail closed, never guess which name is "the" candidate, never
      blend two identities into one candidate row.

    raw_runway_designation is populated opportunistically (best-effort
    join of runway_ends|runway_pairs, mirroring
    discovery_evidence_persistence._join_or_none()'s own convention) -
    safe to be lossy/joined because it is audit-only and explicitly
    excluded from the candidate fingerprint (UAC1's own
    compute_candidate_fingerprint() docstring), unlike raw_name which
    gates formability and feeds the fingerprint directly.
    """
    if len(fragment.airport_names) != 1:
        return None
    (raw_name,) = fragment.airport_names
    if not raw_name or not raw_name.strip():
        return None
    if not any(character.isalpha() for character in raw_name):
        return None

    runway_claims = fragment.runway_ends | fragment.runway_pairs
    raw_runway_designation = ", ".join(sorted(runway_claims)) if runway_claims else None

    return {
        "raw_name": raw_name,
        "raw_runway_designation": raw_runway_designation,
        "evidence_source_locator": fragment.source_locator,
        "evidence_artifact_identity": fragment.artifact_identity,
    }


def resolve_or_persist_discovery_identity(
    session: Session,
    source_metadata: DiscoverySourceMetadata,
    fragment: CandidateFragment,
    candidate_airports: "list[CandidateAirport]",
) -> DiscoveryIdentityResolutionResult:
    """The single UAC3 orchestration entry point. Never commits (every
    function it calls flushes only); the caller owns the transaction
    boundary, matching every other persistence service in this pipeline.
    Composes, never duplicates, the logic of three already-committed,
    already-reviewed services:

    1. app.services.evidence_attachment_guard.evaluate_attachment_for_candidates()
       (unmodified) - decides, once, which of the four outcomes applies.
    2. app.services.discovery_evidence_persistence.persist_discovery_fragment()
       (unmodified) - the existing known-airport/ambiguous/unresolved
       path, called for three of the four outcomes exactly as it already
       runs today.
    3. app.services.unknown_airport_candidate_persistence.find_or_create_unknown_airport_candidate()
       + app.services.discovery_evidence_persistence.persist_candidate_linked_source_assertion()
       (both unmodified) - called together, ONLY for the fourth outcome
       (no known match + a formable candidate seed), and never alongside
       a call to persist_discovery_fragment() for the same fragment (see
       module docstring for why calling both would silently fail to set
       unknown_airport_candidate_id).

    Idempotent per fragment identity, inherited entirely from the two
    persistence functions' own existing dedup: replaying the identical
    fragment through this function a second time reuses the existing
    Source and SourceAssertion rows unchanged (created=False on both),
    and reuses rather than duplicates an existing UnknownAirportCandidate
    by exact fingerprint. A SourceAssertion, once persisted with a given
    identity resolution, is never later rewritten to a different
    resolution by a second call through this function - the same
    append-only-evidence discipline persist_discovery_fragment() and
    persist_candidate_linked_source_assertion() already independently
    establish, not a new rule this module invents.
    """
    evidence = candidate_fragment_to_evidence_bag(fragment)
    decisions = (
        evaluate_attachment_for_candidates(evidence, list(candidate_airports)) if candidate_airports else {}
    )

    if decisions:
        best_outcome = min(
            (d.outcome for d in decisions.values()),
            key=lambda o: (
                0 if o in _KNOWN_MATCH_OUTCOMES
                else 1 if o == _AMBIGUOUS_KNOWN_OUTCOME
                else 2 if o == AttachmentOutcome.REJECT_CROSS_AIRPORT
                else 3
            ),
        )
    else:
        best_outcome = AttachmentOutcome.INSUFFICIENT_IDENTITY

    if best_outcome in _KNOWN_MATCH_OUTCOMES or best_outcome == _AMBIGUOUS_KNOWN_OUTCOME:
        persisted = persist_discovery_fragment(session, source_metadata, fragment, candidate_airports)
        outcome = (
            DiscoveryIdentityOutcome.KNOWN_CANONICAL_ATTACHMENT
            if best_outcome in _KNOWN_MATCH_OUTCOMES
            else DiscoveryIdentityOutcome.AMBIGUOUS_KNOWN_IDENTITY
        )
        return DiscoveryIdentityResolutionResult(
            outcome=outcome,
            reason=persisted.reason,
            attachment_outcome=persisted.outcome,
            source_id=persisted.source_id,
            source_created=persisted.source_created,
            source_assertion_id=persisted.source_assertion_id,
            source_assertion_created=persisted.source_assertion_created,
            attached_airport_id=persisted.attached_airport_id,
        )

    seed = _extract_unknown_airport_candidate_seed(fragment)
    if seed is None:
        persisted = persist_discovery_fragment(session, source_metadata, fragment, candidate_airports)
        return DiscoveryIdentityResolutionResult(
            outcome=DiscoveryIdentityOutcome.UNRESOLVED_IDENTITY,
            reason=(
                f"{persisted.reason} Additionally, the evidence fragment does not carry exactly one "
                "claimed airport name, so no governed unknown-airport candidate was formed either "
                "(zero names = insufficient identity; more than one = an ambiguous multi-airport "
                "fragment, never blended into one candidate)."
            ),
            attachment_outcome=persisted.outcome,
            source_id=persisted.source_id,
            source_created=persisted.source_created,
            source_assertion_id=persisted.source_assertion_id,
            source_assertion_created=persisted.source_assertion_created,
        )

    candidate_result = find_or_create_unknown_airport_candidate(session, **seed)
    linked = persist_candidate_linked_source_assertion(
        session, source_metadata, fragment, unknown_airport_candidate_id=candidate_result.candidate.id,
    )
    return DiscoveryIdentityResolutionResult(
        outcome=DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE,
        reason=(
            f"No known Airport candidate matched ({best_outcome.value}); evidence carries exactly one "
            f"claimed airport name ({seed['raw_name']!r}), sufficient to form a governed, non-canonical "
            f"unknown-airport candidate pending human resolution."
        ),
        attachment_outcome=best_outcome,
        source_id=linked.source_id,
        source_created=linked.source_created,
        source_assertion_id=linked.source_assertion_id,
        source_assertion_created=linked.source_assertion_created,
        unknown_airport_candidate_id=candidate_result.candidate.id,
        unknown_airport_candidate_created=candidate_result.created,
    )
