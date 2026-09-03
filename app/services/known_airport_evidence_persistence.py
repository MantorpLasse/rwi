"""Known-Airport evidence persistence (RWI Mission #26D, following the
tooling-gap finding in Mission #26C).

    Source (create/reuse)
        -> SourceAssertion, airport_id = an explicit, already-resolved
           Airport, assertion_type = an explicit allowlisted value
           (create/reuse)
        -> STOP

Mission #26C discovered that Mission #25J1's stage-only persistence path
(app.services.stage_only_evidence_persistence) hardcodes both
`airport_id=None` and `assertion_type="project_construction"` - correct
and intentional for IDENTITY-UNRESOLVED discovery evidence, but unable to
honestly express "this preserved fragment is about Airport X, whose
identity is not in question at all" (e.g. coordinate evidence for an
Airport that already has a governed name/IATA/ICAO). This module is the
deliberately separate sibling for that second case. It does NOT replace,
weaken, or broaden app.services.stage_only_evidence_persistence in any
way - that module is untouched by this mission and its own tests still
pass unchanged (see tests/test_stage_only_evidence_persistence.py).

WHEN TO USE WHICH:
  - Evidence about an Airport whose identity is NOT yet established, or
    is being proposed/evaluated: app.services.stage_only_evidence_persistence
    (airport_id always NULL, assertion_type always "project_construction").
  - Evidence about an Airport a human has ALREADY explicitly named by its
    real, existing Airport.id - no name/IATA/ICAO lookup, no fuzzy
    matching, no candidate evaluation of any kind: THIS module.

This module creates ONLY Source and SourceAssertion rows - never an
Airport, Runway, Installation, or Signal row; never EvidenceBag, UAC, or
ReviewerAction; never calls the identity guard or the UAC3 discovery-
identity orchestrator. Enforced by AST inspection in
tests/test_known_airport_evidence_persistence_architectural_safety.py,
mirroring the exact discipline
tests/test_stage_only_evidence_persistence_architectural_safety.py
already established.

GENERALITY (Mission #26D Part D): this is NOT a coordinate-specific
service. The semantic capability is "preserve governed source evidence
about an already-known Airport" - coordinates are only the first
acceptance use case (Roland Garros, Mission #26D Part O). A future caller
could stage other airport-inventory-shaped evidence about a known Airport
through the exact same function, with a different (still allowlisted)
assertion_type.

ASSERTION TYPE CONTRACT (Mission #26D Part F, "smallest safe API"): the
caller supplies `assertion_type` explicitly rather than this module
hardcoding one value, but it is checked against `ALLOWED_ASSERTION_TYPES`
- a narrow, explicit allowlist, not the full
app.models.source_assertion.ASSERTION_TYPES CHECK-constraint universe.
V1 contains exactly one value, "airport_inventory" (Mission #26B's own
approved semantic type for airport reference-data evidence, e.g.
coordinates - see docs/architecture/rwi-known-airport-ambiguity...
no, see Mission #26B's report). Extending the allowlist to a new value is
a one-line, separately-reviewable change - never "any CHECK-constraint-
legal string", which would let a caller silently smuggle
"project_construction"/"historical" through this seam and blur the two
services' distinct meanings (Mission #26D's own explicit "do not overload"
warning, echoed from Mission #26C Part M).

FIELD SEMANTICS for a SourceAssertion this module creates:
  airport_id = the caller's explicit, pre-validated, already-existing
      Airport.id - never inferred, never fuzzy-matched.
  runway_id = the caller's explicit Runway.id if supplied (validated to
      exist, same discipline as airport_id) else NULL. The Mission #26D
      acceptance case always passes NULL.
  unknown_airport_candidate_id = always NULL - mutually exclusive with
      airport_id at the DB layer (see the CheckConstraint on
      SourceAssertion) and semantically wrong here regardless: this
      module's entire premise is that identity is NOT in question.
  assertion_type = the caller's explicit, allowlist-checked value.
  evidence_quality = "unverified_candidate" - the same default every
      other Selection-KEEP-originated SourceAssertion in this pipeline
      uses (Mission #25I's own approved contract). Naming an Airport does
      not, by itself, make preserved text "verified" - that judgment
      belongs to RWI's later, separate claim-review process.
  review_state = "unreviewed" - same meaning as everywhere else in this
      pipeline: "has RWI's later claim/evidence-reconciliation process
      examined this row" (never "did a human choose this fragment at
      Selection time" - that's KEEP, a separate, already-happened step).
  identity_guard_decision / identity_guard_reason = NULL. Naming a known
      Airport here is NOT the same act as IdentityGuard's own candidate-
      evaluation - that field answers a different question ("did the
      automated guard evaluate ambiguous candidates and pick one") that
      never arose for this row at all; leaving it NULL is honest, not a
      bypass, matching stage-only's own reasoning verbatim.
  intelligence_review_decision / intelligence_review_reason = NULL.
  promotion_policy_decision / promotion_policy_reason = NULL.

This SourceAssertion means exactly: "this preserved source says this
about this known Airport." It never means "RWI has accepted this as the
Airport's current truth" - Airport.latitude/.longitude (or any other
live Airport column) are never written by this module, under any
circumstance.

IDEMPOTENCY / CONFLICT (Mission #26D Part J): Source reuse mirrors
app.services.stage_only_evidence_persistence's own convention exactly -
external_id = f"discovery:{document_identity}", so the SAME document
resolves to the SAME Source row across every persistence path in this
pipeline. SourceAssertion reuse is looked up by the same pre-existing
DB-level UniqueConstraint tuple (source_id, artifact_identity,
source_locator, raw_fragment_hash) stage-only already uses - an EXACT
replay (same fragment, same airport_id, same assertion_type) reuses the
existing row, never a duplicate. That unique key is intentionally
airport-agnostic (it identifies WHICH preserved fragment, not what a
caller currently believes about it), so if an existing row is found under
that same key but its `airport_id` or `assertion_type` does not match
what THIS call requested, silently reusing it would misrepresent
provenance (Mission #26D Part J's own explicit "must NOT silently reuse
an assertion belonging to another Airport"). Both plan_ and apply_ detect
this and refuse - plan_ surfaces it in the preview
(`KnownAirportEvidenceConflictError` message included on the row rather
than raised, so a dry-run preview never itself raises), apply_ raises
`KnownAirportEvidenceConflictError` before any write.

Never commits and never imports app.database.SessionLocal - mutates the
caller-supplied Session and flushes only enough to obtain row ids; the
caller owns the transaction boundary entirely, matching every other
persistence service in this pipeline (Mission #26D Part K).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Airport, Runway, Source, SourceAssertion
from app.services.discovery_evidence_persistence import DiscoverySourceMetadata

__all__ = [
    "ALLOWED_ASSERTION_TYPES",
    "AssertionTypeNotAllowedError",
    "KnownAirportEvidenceConflictError",
    "UnknownAirportIdError",
    "UnknownRunwayIdError",
    "PlannedKnownAirportEvidence",
    "KnownAirportPersistenceResult",
    "plan_known_airport_evidence_persistence",
    "apply_known_airport_evidence_persistence",
]

# Mission #26D Part F: smallest safe API - an explicit allowlist, never the
# full app.models.source_assertion.ASSERTION_TYPES CHECK-constraint
# universe. "airport_inventory" is Mission #26B's own approved semantic
# type for airport reference-data evidence (coordinates being the first
# acceptance case). Extending this tuple is a deliberate, one-line,
# separately-reviewable future change - not exercised by this mission.
ALLOWED_ASSERTION_TYPES = ("airport_inventory",)


class AssertionTypeNotAllowedError(ValueError):
    """Raised when the caller-supplied assertion_type is not in
    ALLOWED_ASSERTION_TYPES. Fails closed - never silently coerces to the
    default or to any CHECK-constraint-legal value outside the allowlist."""


class UnknownAirportIdError(ValueError):
    """Raised when the caller-supplied airport_id does not name a real,
    already-existing Airport row. Fails closed - never creates one."""


class UnknownRunwayIdError(ValueError):
    """Raised when the caller-supplied runway_id does not name a real,
    already-existing Runway row. Fails closed - never creates one."""


class KnownAirportEvidenceConflictError(ValueError):
    """Raised (by apply_) or reported (by plan_, non-raising) when an
    existing SourceAssertion already occupies this exact content-identity
    key (source_id, artifact_identity, source_locator, raw_fragment_hash)
    but is attached to a different airport_id or has a different
    assertion_type than this call requested. Never silently reused - see
    module docstring's IDEMPOTENCY / CONFLICT section."""


def _sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _find_existing_source(session: Session, metadata: DiscoverySourceMetadata) -> "Source | None":
    external_id = f"discovery:{metadata.document_identity}"
    return session.scalar(select(Source).where(Source.external_id == external_id))


def _find_existing_assertion(
    session: Session, source_id: int, *, artifact_identity: str, source_locator: str, raw_fragment_hash: str
) -> "SourceAssertion | None":
    return session.scalar(
        select(SourceAssertion).where(
            SourceAssertion.source_id == source_id,
            SourceAssertion.artifact_identity == artifact_identity,
            SourceAssertion.source_locator == source_locator,
            SourceAssertion.raw_fragment_hash == raw_fragment_hash,
        )
    )


def _validate_assertion_type(assertion_type: str) -> None:
    if assertion_type not in ALLOWED_ASSERTION_TYPES:
        raise AssertionTypeNotAllowedError(
            f"assertion_type={assertion_type!r} is not in this module's allowlist {ALLOWED_ASSERTION_TYPES!r} - "
            "see ALLOWED_ASSERTION_TYPES / module docstring."
        )


def _conflict_detail(existing: SourceAssertion, *, airport_id: int, assertion_type: str) -> "str | None":
    if existing.airport_id != airport_id:
        return (
            f"Existing SourceAssertion id={existing.id} for this exact fragment is attached to "
            f"airport_id={existing.airport_id!r}, not the requested airport_id={airport_id!r}."
        )
    if existing.assertion_type != assertion_type:
        return (
            f"Existing SourceAssertion id={existing.id} for this exact fragment has "
            f"assertion_type={existing.assertion_type!r}, not the requested assertion_type={assertion_type!r}."
        )
    return None


@dataclass(frozen=True)
class PlannedKnownAirportEvidence:
    """Read-only preview of what apply_ WOULD do for one KEPT fragment -
    never mutates the session."""

    document_identity: str
    source_locator: str
    raw_fragment_hash: str
    raw_text: str
    airport_id: int
    assertion_type: str
    source_would_be_created: bool
    source_id_if_existing: "int | None"
    source_assertion_would_be_created: bool
    source_assertion_id_if_existing: "int | None"
    conflict: "str | None"


@dataclass(frozen=True)
class KnownAirportPersistenceResult:
    """Deterministic, ORM-free summary of what apply_ did."""

    source_id: int
    source_created: bool
    source_assertion_id: int
    source_assertion_created: bool
    airport_id: int
    assertion_type: str


def plan_known_airport_evidence_persistence(
    session: Session,
    metadata: DiscoverySourceMetadata,
    *,
    airport_id: int,
    assertion_type: str,
    source_locator: str,
    raw_text: str,
    runway_id: "int | None" = None,
) -> PlannedKnownAirportEvidence:
    """Read-only. Performs ONLY SELECT queries - never session.add()/
    flush()/commit(). Raises UnknownAirportIdError/UnknownRunwayIdError
    immediately (these are caller-input-shape errors, not plan-vs-apply
    concerns) but never raises for a same-key/different-attachment
    conflict - that is reported via the returned `conflict` field so a
    dry-run preview never itself raises."""
    _validate_assertion_type(assertion_type)
    if session.get(Airport, airport_id) is None:
        raise UnknownAirportIdError(f"No Airport exists for id={airport_id!r}.")
    if runway_id is not None and session.get(Runway, runway_id) is None:
        raise UnknownRunwayIdError(f"No Runway exists for id={runway_id!r}.")

    raw_fragment_hash = _sha256_of_text(raw_text)
    existing_source = _find_existing_source(session, metadata)
    source_id_if_existing = existing_source.id if existing_source is not None else None

    source_assertion_id_if_existing = None
    conflict = None
    if existing_source is not None:
        existing_assertion = _find_existing_assertion(
            session,
            existing_source.id,
            artifact_identity=metadata.document_identity,
            source_locator=source_locator,
            raw_fragment_hash=raw_fragment_hash,
        )
        if existing_assertion is not None:
            conflict = _conflict_detail(existing_assertion, airport_id=airport_id, assertion_type=assertion_type)
            if conflict is None:
                source_assertion_id_if_existing = existing_assertion.id

    return PlannedKnownAirportEvidence(
        document_identity=metadata.document_identity,
        source_locator=source_locator,
        raw_fragment_hash=raw_fragment_hash,
        raw_text=raw_text,
        airport_id=airport_id,
        assertion_type=assertion_type,
        source_would_be_created=existing_source is None,
        source_id_if_existing=source_id_if_existing,
        source_assertion_would_be_created=conflict is None and source_assertion_id_if_existing is None,
        source_assertion_id_if_existing=source_assertion_id_if_existing,
        conflict=conflict,
    )


def apply_known_airport_evidence_persistence(
    session: Session,
    metadata: DiscoverySourceMetadata,
    *,
    airport_id: int,
    assertion_type: str,
    source_locator: str,
    raw_text: str,
    runway_id: "int | None" = None,
) -> KnownAirportPersistenceResult:
    """The only write path in this module. Creates/reuses exactly one
    Source row and creates/reuses exactly one SourceAssertion row -
    nothing else. Never calls the identity guard, UAC orchestration, or
    any EvidenceBag construction. Never mutates Airport/Runway/
    Installation/Signal. Caller owns commit(). Raises
    KnownAirportEvidenceConflictError (no write performed) if an existing
    row occupies this exact content-identity key under a different
    airport_id/assertion_type."""
    _validate_assertion_type(assertion_type)
    if session.get(Airport, airport_id) is None:
        raise UnknownAirportIdError(f"No Airport exists for id={airport_id!r}.")
    if runway_id is not None and session.get(Runway, runway_id) is None:
        raise UnknownRunwayIdError(f"No Runway exists for id={runway_id!r}.")

    raw_fragment_hash = _sha256_of_text(raw_text)

    source = _find_existing_source(session, metadata)
    source_created = False
    if source is None:
        source = Source(
            title=metadata.title,
            source_type=metadata.source_type,
            publisher=metadata.publisher,
            url=metadata.url,
            published_date=metadata.published_date,
            reliability_level=metadata.reliability_level,
            external_id=f"discovery:{metadata.document_identity}",
        )
        session.add(source)
        session.flush()
        source_created = True

    assertion = _find_existing_assertion(
        session,
        source.id,
        artifact_identity=metadata.document_identity,
        source_locator=source_locator,
        raw_fragment_hash=raw_fragment_hash,
    )
    if assertion is not None:
        conflict = _conflict_detail(assertion, airport_id=airport_id, assertion_type=assertion_type)
        if conflict is not None:
            raise KnownAirportEvidenceConflictError(conflict)

    assertion_created = False
    if assertion is None:
        assertion = SourceAssertion(
            source_id=source.id,
            airport_id=airport_id,
            runway_id=runway_id,
            unknown_airport_candidate_id=None,
            assertion_type=assertion_type,
            raw_relevant_text=raw_text,
            source_locator=source_locator,
            raw_fragment_hash=raw_fragment_hash,
            artifact_identity=metadata.document_identity,
            evidence_quality="unverified_candidate",
            review_state="unreviewed",
            identity_guard_decision=None,
            identity_guard_reason=None,
            intelligence_review_decision=None,
            intelligence_review_reason=None,
            promotion_policy_decision=None,
            promotion_policy_reason=None,
        )
        session.add(assertion)
        session.flush()
        assertion_created = True

    return KnownAirportPersistenceResult(
        source_id=source.id,
        source_created=source_created,
        source_assertion_id=assertion.id,
        source_assertion_created=assertion_created,
        airport_id=airport_id,
        assertion_type=assertion_type,
    )
