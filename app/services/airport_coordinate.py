"""Governed persistence and consumption for Airport coordinate history
(RWI Mission #26G, following the architecture recon/approval in Mission
#26F; see app.models.airport_coordinate for the persisted row shape and
the full design rationale).

    already-governed, known-Airport SourceAssertion
        (assertion_type="airport_inventory", Mission #26D/#26E's seam)
        -> check_airport_coordinate_acceptance_eligibility() (evidence-
           quality gates: assertion binding, literal containment,
           coordinate validity, current-head/live-projection consistency)
        -> accept_airport_coordinate()
        -> one AirportCoordinate row, append-only (the governance/
           provenance record)
        -> THEN, atomically, a strictly-gated write onto
           Airport.latitude/.longitude (the existing, unmodified
           current-value projection app.static_export.build's own
           _airport_location_view() already reads directly - no
           static-export change of any kind is required or made by this
           module).

WHY Airport.latitude/.longitude REMAIN DIRECTLY AUTHORITATIVE (mirrors
app.services.airport_identifier's own identical reasoning for
iata_code/icao_code/faa_code): this table is a governance/provenance
record - the Airport columns themselves stay the single, simple,
already-proven read path used by every consumer, unchanged in shape. This
module only ever WRITES to them, atomically, under a strict gate.

CURRENT-HEAD SEMANTICS (Mission #26F Part H, refined): see
get_current_airport_coordinate()'s own docstring - never merely "the
latest row."

REPLACEMENT, NOT WITHDRAWAL-THEN-REPLACEMENT (HQ's explicit #26G
clarification): an ordinary coordinate correction is represented by
exactly ONE new ADMITTED row, with supersedes_coordinate_id naming the
row it replaces. The replaced row is NEVER edited and NEVER given a
companion REJECTED/RETIRED row merely because it was superseded - it
remains, forever, exactly as it was created, its own `status` still
"ADMITTED" (that field records what was decided when THAT row was
created, not "is this still current" - see the model's own docstring).

WITHDRAWAL DEFERRED (Mission #26G Part E's explicit "do not overbuild"
instruction): this module implements ONLY the acceptance
(first-write-or-replacement) path. A standalone withdrawal-without-
replacement service (producing a REJECTED/RETIRED row with no
accompanying new ADMITTED value) is NOT implemented here - the
AIRPORT_COORDINATE_STATUSES vocabulary supports it for a future,
separately-designed mission, but no function in this module ever
constructs a REJECTED/RETIRED row.

EVIDENCE, NEVER JUDGMENT (mirrors app.services.airport_identifier's
central property): evidence_excerpt must be a literal substring of the
cited SourceAssertion's own preserved raw_relevant_text. datum and
coordinate_semantic_type are NEVER inferred - populated only when the
caller explicitly supplies them (and even then, callers are expected to
have verified the excerpt literally states them; this module does not
yet mechanically re-verify those two fields' own containment, mirroring
Mission #26F Part Q's "does not manufacture certainty" instruction rather
than adding unproven mechanical rigor beyond what's asked).

source_id is ALWAYS DERIVED from source_assertion.source_id - never an
independently-supplied parameter - removing an entire class of
"contradictory source/source_assertion pair" risk structurally (Mission
#26F Part I / #26G Part I).

CONCURRENCY (Mission #26F Part R / #26G Parts Q-R): the caller MUST
explicitly state `expected_current_coordinate_id` - None for a believed
first-write, or the believed-current row's id for a believed replacement.
Immediately before any write, this module re-derives the ACTUAL current
head fresh (mirrors app.services.airport_identifier's own "re-check
immediately before write" concurrency guard) and fails closed
(StaleCurrentCoordinateError) on any mismatch - this is the SAME check
for both the "someone else already accepted since I looked" case and the
"I named the wrong Airport's row" case; no separate cross-Airport-
specific code is needed because a row from a different Airport can never
equal this Airport's own current head id.

LEGACY-COORDINATE SAFETY (Mission #26G Part T, achieved as a natural
consequence of the SAME general concurrency check, not a special case):
for a believed first-write (expected_current_coordinate_id=None), this
module also verifies Airport.latitude/.longitude are BOTH still NULL
immediately before write. An Airport whose live coordinates are already
populated but has zero AirportCoordinate rows (e.g. one of the 70 legacy
FAA/Tableau-backed Airports, Mission #26B) is NOT silently treated as "no
history exists yet, safe to overwrite" - UnexplainedLiveCoordinateError
fails closed instead. No legacy backfill of any kind happens here or is
authorized here.

IDEMPOTENCY (Mission #26G Part U): if the current head's own
(source_assertion_id, latitude, longitude) already exactly matches what
is being submitted, this is a true replay - the existing row is reused,
no duplicate is created. If the current head's identity matches but
Airport.latitude/.longitude have diverged from it, this module fails
closed (LiveProjectionDivergenceError) rather than silently repairing the
divergence.

Never commits and never imports app.database.SessionLocal - mutates the
caller-supplied Session and flushes only, so any constraint violation
surfaces immediately; the caller owns the transaction boundary entirely,
matching app.services.airport_identifier and every other persistence
service in this pipeline.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Airport, AirportCoordinate, SourceAssertion
from app.services.manual_identity_evidence import excerpt_contains_value

__all__ = [
    "REQUIRED_ASSERTION_TYPE",
    "AirportNotFoundError",
    "SourceAssertionNotFoundError",
    "SourceAssertionAirportMismatchError",
    "InvalidAssertionTypeForCoordinateError",
    "EmptyEvidenceExcerptError",
    "ExcerptNotInPreservedEvidenceError",
    "EmptyAnalystError",
    "MissingCoordinateError",
    "NonFiniteCoordinateError",
    "CoordinateOutOfRangeError",
    "StaleCurrentCoordinateError",
    "UnexplainedLiveCoordinateError",
    "LiveProjectionDivergenceError",
    "AirportCoordinateEligibility",
    "AirportCoordinateAcceptanceResult",
    "get_current_airport_coordinate",
    "check_airport_coordinate_acceptance_eligibility",
    "accept_airport_coordinate",
]

# Mission #26G Part I: the only assertion_type this acceptance path ever
# accepts, matching Mission #26B's own approved coordinate-evidence
# semantic type exactly. Never "project_construction"/"historical".
REQUIRED_ASSERTION_TYPE = "airport_inventory"

_LATITUDE_RANGE = (-90.0, 90.0)
_LONGITUDE_RANGE = (-180.0, 180.0)


class AirportNotFoundError(ValueError):
    def __init__(self, airport_id: int) -> None:
        self.airport_id = airport_id
        super().__init__(f"airport_id={airport_id!r} does not reference an existing Airport")


class SourceAssertionNotFoundError(ValueError):
    def __init__(self, source_assertion_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        super().__init__(f"source_assertion_id={source_assertion_id!r} does not reference an existing SourceAssertion")


class SourceAssertionAirportMismatchError(ValueError):
    def __init__(self, source_assertion_id: int, *, expected_airport_id: int, actual_airport_id: "int | None") -> None:
        self.source_assertion_id = source_assertion_id
        self.expected_airport_id = expected_airport_id
        self.actual_airport_id = actual_airport_id
        super().__init__(
            f"SourceAssertion {source_assertion_id}'s own airport_id={actual_airport_id!r} does not equal the "
            f"requested airport_id={expected_airport_id!r} - coordinate evidence must belong to the same Airport "
            "it is being cited for."
        )


class InvalidAssertionTypeForCoordinateError(ValueError):
    def __init__(self, source_assertion_id: int, *, actual_assertion_type: str) -> None:
        self.source_assertion_id = source_assertion_id
        self.actual_assertion_type = actual_assertion_type
        super().__init__(
            f"SourceAssertion {source_assertion_id}'s assertion_type={actual_assertion_type!r} is not "
            f"{REQUIRED_ASSERTION_TYPE!r} - coordinate acceptance requires known-Airport inventory evidence."
        )


class EmptyEvidenceExcerptError(ValueError):
    def __init__(self) -> None:
        super().__init__("evidence_excerpt is required and cannot be empty")


class ExcerptNotInPreservedEvidenceError(ValueError):
    def __init__(self, source_assertion_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        super().__init__(
            f"evidence_excerpt does not occur literally within SourceAssertion {source_assertion_id}'s own "
            "preserved raw_relevant_text (or that field is empty) - paraphrased or unrelated text is never accepted."
        )


class EmptyAnalystError(ValueError):
    def __init__(self) -> None:
        super().__init__("analyst is required and cannot be empty")


class MissingCoordinateError(ValueError):
    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(f"{field} is required and cannot be None")


class NonFiniteCoordinateError(ValueError):
    def __init__(self, field: str, value: object) -> None:
        self.field = field
        self.value = value
        super().__init__(f"{field}={value!r} is not a finite number (NaN/Infinity are never accepted)")


class CoordinateOutOfRangeError(ValueError):
    def __init__(self, field: str, value: float, *, low: float, high: float) -> None:
        self.field = field
        self.value = value
        self.low = low
        self.high = high
        super().__init__(f"{field}={value!r} is out of the allowed range [{low}, {high}]")


class StaleCurrentCoordinateError(ValueError):
    """Raised when the caller's expected_current_coordinate_id does not
    match the actually-current AirportCoordinate row for this Airport,
    re-derived fresh immediately before write - covers a concurrent
    acceptance landing first, a wrong/stale id, AND a cross-Airport id
    (which can never equal this Airport's own current head) with one
    check, structurally."""

    def __init__(self, airport_id: int, *, expected: "int | None", actual: "int | None") -> None:
        self.airport_id = airport_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Airport {airport_id}'s actual current AirportCoordinate id={actual!r} does not match "
            f"expected_current_coordinate_id={expected!r} - refusing to write; re-preview and retry."
        )


class UnexplainedLiveCoordinateError(ValueError):
    """Raised for a believed first-write (expected_current_coordinate_id
    is None) when Airport.latitude/.longitude are already populated with
    no corresponding AirportCoordinate history - e.g. a legacy FAA/
    Tableau-backed Airport (Mission #26B). Never silently overwritten;
    never silently adopted as if it were already-governed history."""

    def __init__(self, airport_id: int, *, latitude: "float | None", longitude: "float | None") -> None:
        self.airport_id = airport_id
        self.latitude = latitude
        self.longitude = longitude
        super().__init__(
            f"Airport {airport_id} has no AirportCoordinate history but Airport.latitude/.longitude are already "
            f"populated ({latitude!r}, {longitude!r}) - refusing to treat an unexplained legacy value as a safe "
            "first-write target. See the (separate, future) legacy backfill path."
        )


class LiveProjectionDivergenceError(ValueError):
    """Raised when the current AirportCoordinate head exists but
    Airport.latitude/.longitude no longer match it - a data-integrity
    inconsistency this module never silently repairs."""

    def __init__(
        self, airport_id: int, *, current_coordinate_id: int,
        history_latitude: float, history_longitude: float,
        live_latitude: "float | None", live_longitude: "float | None",
    ) -> None:
        self.airport_id = airport_id
        self.current_coordinate_id = current_coordinate_id
        super().__init__(
            f"Airport {airport_id}'s live (latitude={live_latitude!r}, longitude={live_longitude!r}) does not "
            f"match its current AirportCoordinate {current_coordinate_id}'s own "
            f"(latitude={history_latitude!r}, longitude={history_longitude!r}) - refusing to write over an "
            "unreconciled divergence."
        )


@dataclass(frozen=True)
class AirportCoordinateEligibility:
    """Read-only preview result. Never mutates the session."""

    airport_id: int
    source_assertion_id: int
    source_id: int
    latitude: float
    longitude: float
    current_coordinate_id: "int | None"
    would_be_idempotent_replay: bool


@dataclass(frozen=True)
class AirportCoordinateAcceptanceResult:
    """Deterministic, ORM-free summary of what accept_airport_coordinate()
    did - never exposes ORM instances directly."""

    coordinate_id: int
    coordinate_created: bool
    airport_id: int
    source_id: int
    source_assertion_id: int
    latitude: float
    longitude: float
    status: str
    supersedes_coordinate_id: "int | None"
    airport_columns_written: bool


def _get_latest_coordinate_row(session: Session, *, airport_id: int) -> "AirportCoordinate | None":
    rows = session.scalars(
        select(AirportCoordinate)
        .where(AirportCoordinate.airport_id == airport_id)
        .order_by(AirportCoordinate.created_at.asc(), AirportCoordinate.id.asc())
    ).all()
    return rows[-1] if rows else None


def get_current_airport_coordinate(session: Session, airport_id: int) -> "AirportCoordinate | None":
    """Read-only. The effective/current AirportCoordinate for airport_id,
    or None. NEVER merely 'the latest row' (Mission #26F Part H): a
    latest row whose status is REJECTED/RETIRED means the Airport
    currently has NO accepted coordinate - a withdrawal genuinely
    un-accepts rather than falling back to an earlier ADMITTED row."""
    latest = _get_latest_coordinate_row(session, airport_id=airport_id)
    if latest is None or latest.status != "ADMITTED":
        return None
    return latest


def _validate_coordinate(value: object, *, field: str, low: float, high: float) -> float:
    if value is None:
        raise MissingCoordinateError(field)
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise NonFiniteCoordinateError(field, value) from None
    if not math.isfinite(numeric):
        raise NonFiniteCoordinateError(field, value)
    if not (low <= numeric <= high):
        raise CoordinateOutOfRangeError(field, numeric, low=low, high=high)
    return numeric


def check_airport_coordinate_acceptance_eligibility(
    session: Session,
    *,
    airport_id: int,
    source_assertion_id: int,
    latitude: float,
    longitude: float,
    evidence_excerpt: str,
    analyst: str,
    expected_current_coordinate_id: "int | None",
) -> AirportCoordinateEligibility:
    """The full evidence-quality + concurrency gate, shared by BOTH the
    read-only preview and accept_airport_coordinate()'s own write path
    (one implementation, never duplicated - mirrors
    app.services.airport_identifier's identical reuse pattern). Raises the
    first violated precondition; never partially reports.

    Preconditions, checked in this exact order:

    1. Airport exists -> AirportNotFoundError
    2. SourceAssertion exists -> SourceAssertionNotFoundError
    3. SourceAssertion.airport_id == airport_id -> SourceAssertionAirportMismatchError
    4. SourceAssertion.assertion_type == "airport_inventory" -> InvalidAssertionTypeForCoordinateError
    5. evidence_excerpt non-empty -> EmptyEvidenceExcerptError
    6. evidence_excerpt literally occurs in source_assertion.raw_relevant_text
       -> ExcerptNotInPreservedEvidenceError
    7. analyst non-empty -> EmptyAnalystError
    8. latitude/longitude present, finite, in range -> MissingCoordinateError /
       NonFiniteCoordinateError / CoordinateOutOfRangeError
    9. expected_current_coordinate_id matches the actual current head
       -> StaleCurrentCoordinateError
    10. for a believed first-write, Airport.latitude/.longitude are both
        still NULL -> UnexplainedLiveCoordinateError
    11. for a believed replacement, Airport.latitude/.longitude still
        match the current head -> LiveProjectionDivergenceError
    """
    airport = session.get(Airport, airport_id)
    if airport is None:
        raise AirportNotFoundError(airport_id)

    source_assertion = session.get(SourceAssertion, source_assertion_id)
    if source_assertion is None:
        raise SourceAssertionNotFoundError(source_assertion_id)

    if source_assertion.airport_id != airport_id:
        raise SourceAssertionAirportMismatchError(
            source_assertion_id, expected_airport_id=airport_id, actual_airport_id=source_assertion.airport_id,
        )
    if source_assertion.assertion_type != REQUIRED_ASSERTION_TYPE:
        raise InvalidAssertionTypeForCoordinateError(
            source_assertion_id, actual_assertion_type=source_assertion.assertion_type,
        )

    if not evidence_excerpt or not evidence_excerpt.strip():
        raise EmptyEvidenceExcerptError()
    if not source_assertion.raw_relevant_text or not excerpt_contains_value(
        source_assertion.raw_relevant_text, evidence_excerpt
    ):
        raise ExcerptNotInPreservedEvidenceError(source_assertion_id)

    if not analyst or not analyst.strip():
        raise EmptyAnalystError()

    validated_latitude = _validate_coordinate(latitude, field="latitude", low=_LATITUDE_RANGE[0], high=_LATITUDE_RANGE[1])
    validated_longitude = _validate_coordinate(longitude, field="longitude", low=_LONGITUDE_RANGE[0], high=_LONGITUDE_RANGE[1])

    current = get_current_airport_coordinate(session, airport_id)
    actual_current_id = current.id if current is not None else None
    if expected_current_coordinate_id != actual_current_id:
        raise StaleCurrentCoordinateError(
            airport_id, expected=expected_current_coordinate_id, actual=actual_current_id,
        )

    if current is None:
        if airport.latitude is not None or airport.longitude is not None:
            raise UnexplainedLiveCoordinateError(
                airport_id, latitude=airport.latitude, longitude=airport.longitude,
            )
        would_be_idempotent_replay = False
    else:
        if airport.latitude != current.latitude or airport.longitude != current.longitude:
            raise LiveProjectionDivergenceError(
                airport_id, current_coordinate_id=current.id,
                history_latitude=current.latitude, history_longitude=current.longitude,
                live_latitude=airport.latitude, live_longitude=airport.longitude,
            )
        would_be_idempotent_replay = (
            current.source_assertion_id == source_assertion_id
            and current.latitude == validated_latitude
            and current.longitude == validated_longitude
        )

    return AirportCoordinateEligibility(
        airport_id=airport_id,
        source_assertion_id=source_assertion_id,
        source_id=source_assertion.source_id,
        latitude=validated_latitude,
        longitude=validated_longitude,
        current_coordinate_id=actual_current_id,
        would_be_idempotent_replay=would_be_idempotent_replay,
    )


def accept_airport_coordinate(
    session: Session,
    *,
    airport_id: int,
    source_assertion_id: int,
    latitude: float,
    longitude: float,
    evidence_excerpt: str,
    analyst: str,
    expected_current_coordinate_id: "int | None",
    coordinate_semantic_type: "str | None" = None,
    datum: "str | None" = None,
) -> AirportCoordinateAcceptanceResult:
    """Validates every precondition (via
    check_airport_coordinate_acceptance_eligibility(), reused not
    duplicated), THEN - atomically, in the same transaction - performs
    the strictly-gated write onto Airport.latitude/.longitude.

    1. re-check full eligibility (computed fresh, never trusting a
       separately-supplied, possibly-stale preview)
    2. if would_be_idempotent_replay: return the existing current row
       unchanged, create nothing (Mission #26G Part U)
    3. otherwise, persist the immutable AirportCoordinate row
       (status="ADMITTED", supersedes_coordinate_id=expected_current_coordinate_id)
    4. flush
    5. re-check the concurrency/legacy/divergence gates AGAIN, immediately
       before writing Airport (Mission #26F Part R's own explicit
       "re-check immediately before write" guard - a real risk within one
       Session across a check-then-write gap)
    6. write Airport.latitude/.longitude together, atomically
    7. flush again

    Never commits - the caller commits, so a caller-triggered rollback
    undoes both the history row and the column write together, never one
    without the other (Mission #26G Part S/AE.59).
    """
    eligibility = check_airport_coordinate_acceptance_eligibility(
        session, airport_id=airport_id, source_assertion_id=source_assertion_id,
        latitude=latitude, longitude=longitude, evidence_excerpt=evidence_excerpt, analyst=analyst,
        expected_current_coordinate_id=expected_current_coordinate_id,
    )

    if eligibility.would_be_idempotent_replay:
        current = get_current_airport_coordinate(session, airport_id)
        assert current is not None  # would_be_idempotent_replay implies a current row exists
        return AirportCoordinateAcceptanceResult(
            coordinate_id=current.id, coordinate_created=False, airport_id=airport_id,
            source_id=current.source_id, source_assertion_id=current.source_assertion_id,
            latitude=current.latitude, longitude=current.longitude, status=current.status,
            supersedes_coordinate_id=current.supersedes_coordinate_id, airport_columns_written=False,
        )

    row = AirportCoordinate(
        airport_id=airport_id,
        source_id=eligibility.source_id,
        source_assertion_id=source_assertion_id,
        latitude=eligibility.latitude,
        longitude=eligibility.longitude,
        coordinate_semantic_type=coordinate_semantic_type,
        datum=datum,
        evidence_excerpt=evidence_excerpt,
        analyst=analyst,
        status="ADMITTED",
        supersedes_coordinate_id=expected_current_coordinate_id,
    )
    session.add(row)
    session.flush()

    # Re-check under the SAME transaction, immediately before the write -
    # mirrors app.services.airport_identifier's own explicit concurrency
    # guard, never trusting the eligibility check performed moments
    # earlier.
    airport = session.get(Airport, airport_id)
    session.refresh(airport)
    if expected_current_coordinate_id is None:
        if airport.latitude is not None or airport.longitude is not None:
            raise UnexplainedLiveCoordinateError(
                airport_id, latitude=airport.latitude, longitude=airport.longitude,
            )
    else:
        # The row we are about to supersede must still be exactly what we
        # observed - re-fetch it fresh rather than trusting the eligibility
        # pass's own snapshot.
        superseded = session.get(AirportCoordinate, expected_current_coordinate_id)
        if superseded is None or superseded.airport_id != airport_id or superseded.status != "ADMITTED":
            actual_current = get_current_airport_coordinate(session, airport_id)
            raise StaleCurrentCoordinateError(
                airport_id, expected=expected_current_coordinate_id,
                actual=actual_current.id if actual_current is not None else None,
            )
        if airport.latitude != superseded.latitude or airport.longitude != superseded.longitude:
            raise LiveProjectionDivergenceError(
                airport_id, current_coordinate_id=superseded.id,
                history_latitude=superseded.latitude, history_longitude=superseded.longitude,
                live_latitude=airport.latitude, live_longitude=airport.longitude,
            )

    airport.latitude = eligibility.latitude
    airport.longitude = eligibility.longitude
    session.add(airport)
    session.flush()

    return AirportCoordinateAcceptanceResult(
        coordinate_id=row.id, coordinate_created=True, airport_id=airport_id,
        source_id=eligibility.source_id, source_assertion_id=source_assertion_id,
        latitude=eligibility.latitude, longitude=eligibility.longitude, status="ADMITTED",
        supersedes_coordinate_id=expected_current_coordinate_id, airport_columns_written=True,
    )
