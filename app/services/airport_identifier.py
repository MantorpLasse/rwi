"""Governed persistence, impact-preview, and consumption for first-class
Airport identifiers (docs/architecture, "RWI - Governed Canonical Airport
Identifiers - Architecture Design" mission, the locked design this module
implements; see app.models.airport_identifier for the persisted row
shape).

    independent, already-preserved SourceAssertion excerpt, literally
    stating BOTH a code value and its type (e.g. "XYZ(IATA)")
        -> check_airport_identifier_admission_eligibility() (evidence-
           quality gates: literal containment, type evidence, independent
           identity anchor, source reliability, current-column conflict,
           cross-Airport typed collision, anti-circularity)
        -> record_airport_identifier()
        -> one AirportIdentifier row, append-only (the governance/audit
           record)
        -> THEN, atomically, a strictly-gated, one-time NULL -> value
           write onto EXACTLY the matching typed Airport column
           (IATA -> Airport.iata_code, ICAO -> Airport.icao_code,
           FAA -> Airport.faa_code)
        -> the EXISTING, UNMODIFIED
           app.services.evidence_attachment_guard.candidate_airport_from_airport_like()
           already reads these three columns directly - NO IdentityGuard
           change of any kind is required or made by this module.

WHY Airport.iata_code/icao_code/faa_code REMAIN DIRECTLY AUTHORITATIVE
(design mission's own Option A, not a first-class-table-as-source-of-
truth design): this table is a governance/audit record only - it is never
itself read by candidate_airport_from_airport_like() or any other live
identity-evaluation code path. This keeps the guard's own read path
exactly as simple as it already is, and requires zero IdentityGuard
changes - see app.models.airport_identifier's own docstring for the full
reasoning.

EVIDENCE, NEVER JUDGMENT, AND TYPE IS NEVER INFERRED (this module's two
central properties): `evidence_class` has exactly one member this slice,
`AUTHORITATIVE_DIRECT`. `identifier_type` is never guessed from code
length or shape - the preserved excerpt itself must literally contain a
"type evidence token" (a literal substring the caller points at, e.g.
"XYZ(IATA)") that itself literally contains both the identifier value and
the type name - see check_airport_identifier_admission_eligibility()'s
own docstring for the exact mechanical rule. This is a narrow,
deterministic containment check, never an NLP/pattern-inference engine.

ANTI-CIRCULARITY (mandatory, mirrors app.services.airport_alias's own
proven pattern exactly, generalized from aliases to typed identifiers):
the Source used to GOVERN an identifier must never gain identity
confidence from that same identifier fact. `_simulate_identifier_impact()`
(shared, pure, used by BOTH the mandatory read-only preview and the
write-time anti-circularity gate - one implementation, never duplicated)
compares every SourceAssertion sharing the target Airport's own
airport_id, with and without the proposed code present on the candidate.
If any assertion whose outcome would change shares the admitting
evidence's own source_id, admission is refused - this structurally,
generically catches self-confirmation (the admitting SourceAssertion using
itself) and same-Source sibling-assertion laundering alike, with no
country- or airport-specific code anywhere.

NO REPLACEMENT OF POPULATED COLUMNS THIS SLICE (design mission's own
explicit "prefer safe incompleteness over broad correction machinery"
instruction): ordinary ADMITTED admission requires the target Airport
column to be currently NULL - even a proposed value IDENTICAL to an
already-populated value is refused, never silently accepted as if it were
newly governed. This mechanism is not a retroactive provenance wrapper
around legacy values. REJECTED/RETIRED rows may still be recorded as
governed reversals of a PRIOR AirportIdentifier admission (mirroring
AirportAlias's own reversal-safety pattern exactly), but this slice
implements no mechanism to replace an already-populated Airport column
with a different governed value - a future, separately-designed
correction workflow is explicitly out of scope here.

NO TRANSLATION. NO TRANSLITERATION. NO FUZZY MATCHING. Every literal
containment check below reuses
app.services.manual_identity_evidence.normalize_for_containment_check()/
excerpt_contains_value() VERBATIM - this module introduces NO second
normalization implementation, matching app.services.airport_alias's own
identical discipline.

CROSS-AIRPORT TYPED COLLISION (Phase 10's own explicit instruction): a
proposed code is checked against the SAME typed column across ALL
Airports (e.g. IATA XYZ already current on Airport X blocks admitting
IATA XYZ to Airport Y) - never compared across different types (an IATA
"XYZ" is never compared against an FAA "XYZ").

Never commits and never imports app.database.SessionLocal - mutates the
caller-supplied Session and flushes only, so any constraint violation
surfaces immediately; the caller owns the transaction boundary entirely,
matching every other persistence service in this pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Airport, Runway, Source, SourceAssertion
from app.models.airport_identifier import (
    AIRPORT_IDENTIFIER_EVIDENCE_CLASSES,
    AIRPORT_IDENTIFIER_STATUSES,
    AIRPORT_IDENTIFIER_TYPES,
    AirportIdentifier,
)
from app.models.source_assertion_evidence_bag import SourceAssertionEvidenceBag
from app.services.airport_alias import get_admitted_airport_aliases
from app.services.evidence_attachment_guard import (
    AttachmentOutcome,
    CandidateAirport,
    candidate_airport_from_airport_like,
    evaluate_attachment,
)
from app.services.evidence_bag_serialization import deserialize_evidence_bag
from app.services.manual_identity_evidence import excerpt_contains_value, normalize_for_containment_check
from app.services.resolved_candidate_evidence_reevaluation import SourceAssertionNotFoundError

__all__ = [
    "SourceAssertionNotFoundError",
    "AirportNotFoundError",
    "SourceNotFoundError",
    "SourceAssertionSourceMismatchError",
    "SourceAssertionAirportMismatchError",
    "InvalidIdentifierTypeError",
    "EmptyIdentifierValueError",
    "EmptyEvidenceExcerptError",
    "EmptyAnalystError",
    "IdentifierNotInExcerptError",
    "TypeEvidenceNotInExcerptError",
    "TypeEvidenceIncompleteError",
    "ExcerptNotInPreservedEvidenceError",
    "NoIdentityAnchorError",
    "InsufficientSourceReliabilityError",
    "CurrentColumnPopulatedError",
    "CrossAirportTypedCollisionError",
    "CircularIdentifierEvidenceError",
    "ConflictingIdentifierStatusRequiresSupersessionError",
    "TargetColumnChangedDuringWriteError",
    "IdentifierImpactRow",
    "IdentifierImpactPreview",
    "AirportIdentifierAdmissionResult",
    "TYPE_TO_AIRPORT_COLUMN",
    "preview_airport_identifier_admission_impact",
    "check_airport_identifier_admission_eligibility",
    "record_airport_identifier",
]

_REQUIRED_RELIABILITY_LEVEL = "official"
_ADMITTED = "ADMITTED"
_WITHDRAWN_STATUSES = ("REJECTED", "RETIRED")

# The ONE explicit, generic type -> column mapping this module ever uses.
# No inference from code length or shape; an identifier_type outside this
# mapping fails closed (ValueError in every caller that validates it).
TYPE_TO_AIRPORT_COLUMN = {
    "IATA": "iata_code",
    "ICAO": "icao_code",
    "FAA": "faa_code",
}


def _normalize_identifier_value(value: str) -> str:
    """Conservative, canonical persisted-value normalization (Phase 4):
    strip whitespace, uppercase - matching the existing Airport code
    convention already used throughout this codebase (iata_code/icao_code/
    faa_code are always stored uppercase, e.g. "XYZ", "KSFO").
    Deliberately a SEPARATE function from
    app.services.manual_identity_evidence.normalize_for_containment_check()
    (casefold + whitespace-collapse, used only for LITERAL CONTAINMENT
    comparison, never for computing a persisted value) - conflating the
    two would let containment-check normalization silently become
    "evidence," exactly what this module must never do. No other
    normalization is applied - no accent-folding, no punctuation removal,
    nothing that could manufacture a match the literal text does not
    support."""
    return value.strip().upper()


class AirportNotFoundError(ValueError):
    def __init__(self, airport_id: int) -> None:
        self.airport_id = airport_id
        super().__init__(f"airport_id={airport_id!r} does not reference an existing Airport")


class SourceNotFoundError(ValueError):
    def __init__(self, source_id: int) -> None:
        self.source_id = source_id
        super().__init__(f"source_id={source_id!r} does not reference an existing Source")


class SourceAssertionSourceMismatchError(ValueError):
    def __init__(self, source_assertion_id: int, *, expected_source_id: int, supplied_source_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        self.expected_source_id = expected_source_id
        self.supplied_source_id = supplied_source_id
        super().__init__(
            f"supplied source_id={supplied_source_id!r} does not equal SourceAssertion "
            f"{source_assertion_id}'s own source_id={expected_source_id!r}."
        )


class SourceAssertionAirportMismatchError(ValueError):
    def __init__(self, source_assertion_id: int, *, expected_airport_id: "int | None", supplied_airport_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        self.expected_airport_id = expected_airport_id
        self.supplied_airport_id = supplied_airport_id
        super().__init__(
            f"supplied airport_id={supplied_airport_id!r} does not equal SourceAssertion "
            f"{source_assertion_id}'s own airport_id={expected_airport_id!r}."
        )


class InvalidIdentifierTypeError(ValueError):
    def __init__(self, identifier_type: str) -> None:
        self.identifier_type = identifier_type
        super().__init__(f"identifier_type must be one of {AIRPORT_IDENTIFIER_TYPES!r}, got {identifier_type!r}")


class EmptyIdentifierValueError(ValueError):
    def __init__(self) -> None:
        super().__init__("identifier_value is required and cannot be empty")


class EmptyEvidenceExcerptError(ValueError):
    def __init__(self) -> None:
        super().__init__("evidence_excerpt is required and cannot be empty")


class EmptyAnalystError(ValueError):
    def __init__(self) -> None:
        super().__init__("analyst is required and cannot be empty")


class IdentifierNotInExcerptError(ValueError):
    def __init__(self, identifier_value: str) -> None:
        self.identifier_value = identifier_value
        super().__init__(
            f"identifier_value={identifier_value!r} does not occur literally within evidence_excerpt."
        )


class TypeEvidenceNotInExcerptError(ValueError):
    def __init__(self, type_evidence_token: str) -> None:
        self.type_evidence_token = type_evidence_token
        super().__init__(
            f"type_evidence_token={type_evidence_token!r} does not occur literally within evidence_excerpt - "
            "the analyst must point at the exact literal text that states the identifier's type."
        )


class TypeEvidenceIncompleteError(ValueError):
    """Raised when the caller-supplied type_evidence_token does not
    itself literally contain BOTH the identifier value and the type name
    - e.g. a token of "XYZ" alone (no "IATA" anywhere in it) can never
    mechanically prove the type, no matter how the analyst characterizes
    it. Fail-closed, never inferred from length/shape (Phase 8's own
    explicit instruction)."""

    def __init__(self, type_evidence_token: str, *, identifier_value: str, identifier_type: str) -> None:
        self.type_evidence_token = type_evidence_token
        self.identifier_value = identifier_value
        self.identifier_type = identifier_type
        super().__init__(
            f"type_evidence_token={type_evidence_token!r} does not itself literally contain both "
            f"identifier_value={identifier_value!r} and identifier_type={identifier_type!r} - type cannot be "
            "mechanically demonstrated; refusing to infer it."
        )


class ExcerptNotInPreservedEvidenceError(ValueError):
    def __init__(self, source_assertion_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        super().__init__(
            f"evidence_excerpt does not occur literally within SourceAssertion {source_assertion_id}'s own "
            "preserved raw_relevant_text (or that field is empty)."
        )


class NoIdentityAnchorError(ValueError):
    def __init__(self, airport_id: int) -> None:
        self.airport_id = airport_id
        super().__init__(
            f"evidence_excerpt contains the proposed identifier but no independent identity anchor "
            f"(Airport {airport_id}'s own current canonical name, an effective ADMITTED alias, or an "
            "already-known canonical identifier) - the proposed identifier can never anchor itself."
        )


class InsufficientSourceReliabilityError(ValueError):
    def __init__(self, source_id: int, *, reliability_level: "str | None") -> None:
        self.source_id = source_id
        self.reliability_level = reliability_level
        super().__init__(
            f"Source {source_id}'s reliability_level={reliability_level!r} does not meet the required "
            f"{_REQUIRED_RELIABILITY_LEVEL!r} tier for AUTHORITATIVE_DIRECT identifier admission."
        )


class CurrentColumnPopulatedError(ValueError):
    """Raised when the target Airport column already has a value - see
    module docstring "NO REPLACEMENT OF POPULATED COLUMNS THIS SLICE."
    Refused even when the proposed value is identical to the existing
    one."""

    def __init__(self, airport_id: int, *, identifier_type: str, current_value: str) -> None:
        self.airport_id = airport_id
        self.identifier_type = identifier_type
        self.current_value = current_value
        super().__init__(
            f"Airport {airport_id}'s {identifier_type} column already has value {current_value!r} - this "
            "mechanism never replaces an already-populated canonical column."
        )


class CrossAirportTypedCollisionError(ValueError):
    def __init__(self, *, identifier_type: str, identifier_value: str, conflicting_airport_id: int) -> None:
        self.identifier_type = identifier_type
        self.identifier_value = identifier_value
        self.conflicting_airport_id = conflicting_airport_id
        super().__init__(
            f"{identifier_type}={identifier_value!r} is already the current value on Airport "
            f"{conflicting_airport_id} - the same typed identifier cannot be admitted for a different Airport."
        )


class CircularIdentifierEvidenceError(ValueError):
    def __init__(
        self, airport_id: int, *, identifier_type: str, identifier_value: str,
        conflicting_source_assertion_ids: "tuple[int, ...]",
    ) -> None:
        self.airport_id = airport_id
        self.identifier_type = identifier_type
        self.identifier_value = identifier_value
        self.conflicting_source_assertion_ids = conflicting_source_assertion_ids
        super().__init__(
            f"admitting {identifier_type}={identifier_value!r} for Airport {airport_id} would change the "
            f"outcome of SourceAssertion(s) {conflicting_source_assertion_ids!r}, which share the admitting "
            "evidence's own source_id - the evidence source must be independent of any assertion its own "
            "admission would confirm."
        )


class ConflictingIdentifierStatusRequiresSupersessionError(ValueError):
    def __init__(
        self, airport_id: int, *, identifier_type: str, identifier_value: str,
        latest_identifier_id: "int | None", latest_status: "str | None", new_status: str,
    ) -> None:
        self.airport_id = airport_id
        self.identifier_type = identifier_type
        self.identifier_value = identifier_value
        self.latest_identifier_id = latest_identifier_id
        self.latest_status = latest_status
        self.new_status = new_status
        super().__init__(
            f"status={new_status!r} for {identifier_type}={identifier_value!r} on Airport {airport_id} "
            f"requires supersedes_identifier_id={latest_identifier_id!r}, naming the current latest row "
            f"(status={latest_status!r}) explicitly."
        )


class TargetColumnChangedDuringWriteError(ValueError):
    """Raised inside the write transaction (Phase 15's own explicit
    concurrency guard) when the target Airport column is no longer NULL
    at write time, despite having been NULL at eligibility-check time -
    e.g. a concurrent admission for the same Airport/type landed first.
    Fail-closed; never silently overwritten."""

    def __init__(self, airport_id: int, *, identifier_type: str, current_value: str) -> None:
        self.airport_id = airport_id
        self.identifier_type = identifier_type
        self.current_value = current_value
        super().__init__(
            f"Airport {airport_id}'s {identifier_type} column changed to {current_value!r} between "
            "eligibility check and write - refusing to overwrite."
        )


@dataclass(frozen=True)
class IdentifierImpactRow:
    source_assertion_id: int
    source_id: int
    has_snapshot: bool
    current_outcome: "str | None"
    hypothetical_outcome: "str | None"
    changed: bool


@dataclass(frozen=True)
class IdentifierImpactPreview:
    airport_id: int
    identifier_type: str
    proposed_value: str
    rows: "tuple[IdentifierImpactRow, ...]"

    @property
    def changed_source_assertion_ids(self) -> "tuple[int, ...]":
        return tuple(row.source_assertion_id for row in self.rows if row.changed)


@dataclass(frozen=True)
class AirportIdentifierAdmissionResult:
    identifier_id: int
    airport_id: int
    identifier_type: str
    identifier_value: str
    status: str
    source_id: int
    source_assertion_id: int
    superseded_identifier_id: "int | None"
    column_written: bool


def _load_airport_with_topology(session: Session, airport_id: int) -> "Airport | None":
    return session.scalar(
        select(Airport)
        .where(Airport.id == airport_id)
        .options(selectinload(Airport.runways).selectinload(Runway.runway_ends))
    )


def _candidate_for_airport(
    airport: Airport, *, aliases: "frozenset[str]", extra_identifier: "str | None" = None,
) -> CandidateAirport:
    """Mirrors app.services.airport_alias._candidate_for_airport() exactly
    in shape, generalized: the "extra" value is merged into `identifiers`
    (the proposed code) instead of `aliases`. Existing real Airport
    columns are read via candidate_airport_from_airport_like() unmodified
    - this is the entire reason no new "get_admitted_airport_identifiers()"
    read helper is needed the way get_admitted_airport_aliases() is for
    aliases (see module docstring "WHY Airport.iata_code/... REMAIN
    DIRECTLY AUTHORITATIVE")."""
    candidate = candidate_airport_from_airport_like(airport, aliases=aliases)
    if extra_identifier is None:
        return candidate
    return CandidateAirport(
        id=candidate.id,
        name=candidate.name,
        identifiers=frozenset(candidate.identifiers | {extra_identifier}),
        aliases=candidate.aliases,
        city_location=candidate.city_location,
        canonical_runway_ends=candidate.canonical_runway_ends,
        canonical_runway_pairs=candidate.canonical_runway_pairs,
        known_issuers=candidate.known_issuers,
    )


def _simulate_identifier_impact(
    session: Session, *, airport: Airport, identifier_type: str, proposed_value: str,
) -> "tuple[IdentifierImpactRow, ...]":
    """Shared, pure simulation core for BOTH
    preview_airport_identifier_admission_impact() and the anti-circularity
    gate inside check_airport_identifier_admission_eligibility() - one
    implementation, never duplicated (mirrors
    app.services.airport_alias._simulate_alias_impact() exactly).
    Persists nothing; never flushes; never commits."""
    current_aliases = get_admitted_airport_aliases(session, airport.id)
    candidate_without = _candidate_for_airport(airport, aliases=current_aliases)
    candidate_with = _candidate_for_airport(airport, aliases=current_aliases, extra_identifier=proposed_value)

    assertions = session.scalars(
        select(SourceAssertion).where(SourceAssertion.airport_id == airport.id)
    ).all()

    rows: "list[IdentifierImpactRow]" = []
    for assertion in assertions:
        snapshot = session.scalar(
            select(SourceAssertionEvidenceBag).where(
                SourceAssertionEvidenceBag.source_assertion_id == assertion.id
            )
        )
        if snapshot is None:
            rows.append(IdentifierImpactRow(
                source_assertion_id=assertion.id, source_id=assertion.source_id, has_snapshot=False,
                current_outcome=None, hypothetical_outcome=None, changed=False,
            ))
            continue
        evidence = deserialize_evidence_bag(snapshot.evidence_bag_json)
        current_decision = evaluate_attachment(candidate_without, evidence)
        hypothetical_decision = evaluate_attachment(candidate_with, evidence)
        rows.append(IdentifierImpactRow(
            source_assertion_id=assertion.id, source_id=assertion.source_id, has_snapshot=True,
            current_outcome=current_decision.outcome.value,
            hypothetical_outcome=hypothetical_decision.outcome.value,
            changed=current_decision.outcome != hypothetical_decision.outcome,
        ))
    return tuple(rows)


def preview_airport_identifier_admission_impact(
    session: Session, *, airport_id: int, identifier_type: str, proposed_value: str,
) -> IdentifierImpactPreview:
    """Read-only. Never persists, flushes, or commits anything."""
    if identifier_type not in AIRPORT_IDENTIFIER_TYPES:
        raise InvalidIdentifierTypeError(identifier_type)
    if not proposed_value or not proposed_value.strip():
        raise EmptyIdentifierValueError()
    with session.no_autoflush:
        airport = _load_airport_with_topology(session, airport_id)
        if airport is None:
            raise AirportNotFoundError(airport_id)
        rows = _simulate_identifier_impact(
            session, airport=airport, identifier_type=identifier_type, proposed_value=proposed_value,
        )
    return IdentifierImpactPreview(
        airport_id=airport_id, identifier_type=identifier_type, proposed_value=proposed_value, rows=rows,
    )


def _identity_anchors(session: Session, airport: Airport) -> "list[str]":
    """Eligible anchors (Phase 7): the Airport's own current canonical
    name, every currently-effective ADMITTED alias (never retired/
    rejected), and any already-known canonical identifier - never the
    proposed identifier itself."""
    anchors = [airport.name]
    anchors.extend(get_admitted_airport_aliases(session, airport.id))
    anchors.extend(
        value for value in (airport.iata_code, airport.icao_code, airport.faa_code) if value
    )
    return anchors


def check_airport_identifier_admission_eligibility(
    session: Session,
    *,
    airport: Airport,
    source: Source,
    source_assertion: SourceAssertion,
    identifier_type: str,
    identifier_value: str,
    evidence_excerpt: str,
    type_evidence_token: str,
    evidence_class: str,
) -> IdentifierImpactPreview:
    """The full evidence-quality gate for a NEW ADMITTED identifier (never
    applied to a REJECTED/RETIRED withdrawal - see record_airport_identifier()).
    Raises the first violated precondition; never partially reports.
    Returns the freshly-computed IdentifierImpactPreview so the write path
    reuses this exact computation rather than trusting separately-supplied,
    possibly-stale output.

    Preconditions, checked in this exact order:

    1. source.id == source_assertion.source_id -> SourceAssertionSourceMismatchError
    2. airport.id == source_assertion.airport_id -> SourceAssertionAirportMismatchError
    3. identifier_type is a real vocabulary member -> InvalidIdentifierTypeError
    4. identifier_value non-empty -> EmptyIdentifierValueError
    5. evidence_excerpt non-empty -> EmptyEvidenceExcerptError
    6. evidence_class is a real vocabulary member -> ValueError
    7. identifier_value literally occurs in evidence_excerpt -> IdentifierNotInExcerptError
    8. type_evidence_token literally occurs in evidence_excerpt -> TypeEvidenceNotInExcerptError
    9. type_evidence_token itself literally contains BOTH identifier_value
       and identifier_type -> TypeEvidenceIncompleteError (Phase 8 - type
       is proven mechanically, never inferred)
    10. evidence_excerpt literally occurs in source_assertion.raw_relevant_text
        -> ExcerptNotInPreservedEvidenceError
    11. evidence_excerpt also literally contains an independent identity
        anchor (Phase 7) -> NoIdentityAnchorError
    12. source.reliability_level == "official" -> InsufficientSourceReliabilityError
    13. target Airport column (TYPE_TO_AIRPORT_COLUMN[identifier_type])
        is currently NULL -> CurrentColumnPopulatedError
    14. no OTHER Airport currently has this exact typed identifier
        -> CrossAirportTypedCollisionError
    15. anti-circularity: no SourceAssertion whose outcome would change
        shares source_assertion's own source_id -> CircularIdentifierEvidenceError
    """
    if source.id != source_assertion.source_id:
        raise SourceAssertionSourceMismatchError(
            source_assertion.id, expected_source_id=source_assertion.source_id, supplied_source_id=source.id,
        )
    if airport.id != source_assertion.airport_id:
        raise SourceAssertionAirportMismatchError(
            source_assertion.id, expected_airport_id=source_assertion.airport_id, supplied_airport_id=airport.id,
        )
    if identifier_type not in AIRPORT_IDENTIFIER_TYPES:
        raise InvalidIdentifierTypeError(identifier_type)
    if not identifier_value or not identifier_value.strip():
        raise EmptyIdentifierValueError()
    if not evidence_excerpt or not evidence_excerpt.strip():
        raise EmptyEvidenceExcerptError()
    if evidence_class not in AIRPORT_IDENTIFIER_EVIDENCE_CLASSES:
        raise ValueError(
            f"evidence_class must be one of {AIRPORT_IDENTIFIER_EVIDENCE_CLASSES!r}, got {evidence_class!r}"
        )
    if not excerpt_contains_value(evidence_excerpt, identifier_value):
        raise IdentifierNotInExcerptError(identifier_value)
    if not type_evidence_token or not excerpt_contains_value(evidence_excerpt, type_evidence_token):
        raise TypeEvidenceNotInExcerptError(type_evidence_token)
    if not (
        excerpt_contains_value(type_evidence_token, identifier_value)
        and excerpt_contains_value(type_evidence_token, identifier_type)
    ):
        raise TypeEvidenceIncompleteError(
            type_evidence_token, identifier_value=identifier_value, identifier_type=identifier_type,
        )
    if not source_assertion.raw_relevant_text or not excerpt_contains_value(source_assertion.raw_relevant_text, evidence_excerpt):
        raise ExcerptNotInPreservedEvidenceError(source_assertion.id)

    anchors = _identity_anchors(session, airport)
    if not any(excerpt_contains_value(evidence_excerpt, anchor) for anchor in anchors):
        raise NoIdentityAnchorError(airport.id)

    if (source.reliability_level or "") != _REQUIRED_RELIABILITY_LEVEL:
        raise InsufficientSourceReliabilityError(source.id, reliability_level=source.reliability_level)

    column_name = TYPE_TO_AIRPORT_COLUMN[identifier_type]
    current_value = getattr(airport, column_name)
    if current_value is not None:
        raise CurrentColumnPopulatedError(airport.id, identifier_type=identifier_type, current_value=current_value)

    normalized_proposed = _normalize_identifier_value(identifier_value)
    with session.no_autoflush:
        other_airports = session.scalars(
            select(Airport).where(Airport.id != airport.id)
        ).all()
    for other in other_airports:
        other_value = getattr(other, column_name)
        if other_value is not None and _normalize_identifier_value(other_value) == normalized_proposed:
            raise CrossAirportTypedCollisionError(
                identifier_type=identifier_type, identifier_value=identifier_value, conflicting_airport_id=other.id,
            )

    with session.no_autoflush:
        rows = _simulate_identifier_impact(
            session, airport=airport, identifier_type=identifier_type, proposed_value=identifier_value,
        )
    conflicting = tuple(
        row.source_assertion_id for row in rows if row.changed and row.source_id == source_assertion.source_id
    )
    if conflicting:
        raise CircularIdentifierEvidenceError(
            airport.id, identifier_type=identifier_type, identifier_value=identifier_value,
            conflicting_source_assertion_ids=conflicting,
        )

    return IdentifierImpactPreview(
        airport_id=airport.id, identifier_type=identifier_type, proposed_value=identifier_value, rows=rows,
    )


def _get_latest_identifier_row(
    session: Session, *, airport_id: int, identifier_type: str,
) -> "AirportIdentifier | None":
    """Read-only. "Current" is always recency (created_at, id) among rows
    for the same (airport_id, identifier_type) - never derived by walking
    supersedes_identifier_id."""
    rows = session.scalars(
        select(AirportIdentifier)
        .where(AirportIdentifier.airport_id == airport_id, AirportIdentifier.identifier_type == identifier_type)
        .order_by(AirportIdentifier.created_at.asc(), AirportIdentifier.id.asc())
    ).all()
    return rows[-1] if rows else None


def record_airport_identifier(
    session: Session,
    *,
    airport_id: int,
    source_id: int,
    source_assertion_id: int,
    identifier_type: str,
    identifier_value: str,
    evidence_excerpt: str,
    analyst: str,
    type_evidence_token: "str | None" = None,
    evidence_class: str = "AUTHORITATIVE_DIRECT",
    status: str = _ADMITTED,
    supersedes_identifier_id: "int | None" = None,
) -> AirportIdentifierAdmissionResult:
    """Validates every precondition, persists exactly one immutable
    AirportIdentifier row, THEN - atomically, in the same transaction -
    performs the strictly-gated one-time NULL -> value write onto exactly
    the matching typed Airport column (Phase 14's own required sequence):

    1. re-check eligibility (status=ADMITTED path only)
    2. persist the immutable AirportIdentifier evidence/governance row
    3. flush
    4. re-check the target Airport column is STILL NULL (Phase 15's own
       concurrency guard - a real risk within one Session across a
       preview-then-write gap, even single-threaded: something else
       could have populated it in between) -> TargetColumnChangedDuringWriteError
    5. write the normalized value onto exactly that one column
    6. flush again

    Never commits - the caller commits, so this remains atomic with
    whatever the caller does before/after; a caller-triggered rollback
    undoes both the audit row and the column write together, never one
    without the other.

    For status in ("REJECTED", "RETIRED") (always a withdrawal of a prior
    ADMITTED row, never a first write, and never itself writing to the
    Airport column - the column is only ever written by the original
    ADMITTED path): only the light checks apply (Source/SourceAssertion/
    Airport binding, non-empty fields) plus reversal-safety
    (supersedes_identifier_id must name the current latest ADMITTED row).
    """
    with session.no_autoflush:
        airport = session.get(Airport, airport_id)
        if airport is None:
            raise AirportNotFoundError(airport_id)
        source = session.get(Source, source_id)
        if source is None:
            raise SourceNotFoundError(source_id)
        source_assertion = session.get(SourceAssertion, source_assertion_id)
        if source_assertion is None:
            raise SourceAssertionNotFoundError(source_assertion_id)

        if status not in AIRPORT_IDENTIFIER_STATUSES:
            raise ValueError(f"status must be one of {AIRPORT_IDENTIFIER_STATUSES!r}, got {status!r}")
        if not analyst or not analyst.strip():
            raise EmptyAnalystError()

        latest = _get_latest_identifier_row(session, airport_id=airport_id, identifier_type=identifier_type)

        column_written = False
        if status == _ADMITTED:
            check_airport_identifier_admission_eligibility(
                session, airport=airport, source=source, source_assertion=source_assertion,
                identifier_type=identifier_type, identifier_value=identifier_value,
                evidence_excerpt=evidence_excerpt, type_evidence_token=type_evidence_token or "",
                evidence_class=evidence_class,
            )
            if latest is not None and latest.status == _ADMITTED:
                raise CurrentColumnPopulatedError(
                    airport_id, identifier_type=identifier_type,
                    current_value=getattr(airport, TYPE_TO_AIRPORT_COLUMN[identifier_type]) or latest.identifier_value,
                )
            if latest is not None and latest.status in _WITHDRAWN_STATUSES:
                if supersedes_identifier_id != latest.id:
                    raise ConflictingIdentifierStatusRequiresSupersessionError(
                        airport_id, identifier_type=identifier_type, identifier_value=identifier_value,
                        latest_identifier_id=latest.id, latest_status=latest.status, new_status=status,
                    )
            elif supersedes_identifier_id is not None:
                raise ValueError("supersedes_identifier_id must be omitted for a first-time admission")
        else:
            if not identifier_value or not identifier_value.strip():
                raise EmptyIdentifierValueError()
            if not evidence_excerpt or not evidence_excerpt.strip():
                raise EmptyEvidenceExcerptError()
            if source.id != source_assertion.source_id:
                raise SourceAssertionSourceMismatchError(
                    source_assertion.id, expected_source_id=source_assertion.source_id, supplied_source_id=source.id,
                )
            if latest is None or latest.status != _ADMITTED or supersedes_identifier_id != latest.id:
                raise ConflictingIdentifierStatusRequiresSupersessionError(
                    airport_id, identifier_type=identifier_type, identifier_value=identifier_value,
                    latest_identifier_id=latest.id if latest is not None else None,
                    latest_status=latest.status if latest is not None else None, new_status=status,
                )

        normalized_value = _normalize_identifier_value(identifier_value)

        row = AirportIdentifier(
            airport_id=airport_id,
            identifier_type=identifier_type,
            identifier_value=normalized_value,
            source_id=source_id,
            source_assertion_id=source_assertion_id,
            evidence_excerpt=evidence_excerpt,
            analyst=analyst,
            evidence_class=evidence_class if status == _ADMITTED else (latest.evidence_class if latest else evidence_class),
            status=status,
            supersedes_identifier_id=supersedes_identifier_id,
        )
        session.add(row)
        session.flush()

        if status == _ADMITTED:
            # Re-check under the SAME transaction, immediately before the
            # write - Phase 15's own explicit concurrency guard, never
            # trusting the eligibility check performed moments earlier.
            session.refresh(airport)
            column_name = TYPE_TO_AIRPORT_COLUMN[identifier_type]
            current_value = getattr(airport, column_name)
            if current_value is not None:
                raise TargetColumnChangedDuringWriteError(
                    airport_id, identifier_type=identifier_type, current_value=current_value,
                )
            setattr(airport, column_name, normalized_value)
            session.add(airport)
            session.flush()
            column_written = True

        return AirportIdentifierAdmissionResult(
            identifier_id=row.id, airport_id=airport_id, identifier_type=identifier_type,
            identifier_value=normalized_value, status=status, source_id=source_id,
            source_assertion_id=source_assertion_id, superseded_identifier_id=supersedes_identifier_id,
            column_written=column_written,
        )
