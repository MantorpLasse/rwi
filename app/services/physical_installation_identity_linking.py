"""Explicit, deterministic linking of a reviewed PhysicalInstallationIdentity
to its canonical RunwayEnd - NOT general automatic reconciliation.

See docs/domain/canonical-runway-runway-end-design.md S7 ("Physical
installation linking"): safe only when airport, normalized designation,
and parent Runway all agree unambiguously against the already-governed
canonical inventory already sitting in runways/runway_ends. Every other
case (missing, ambiguous, cross-airport, conflicting existing link) is
reported and left untouched - never guessed, never silently applied.

This module only ever SELECTs. It never sets runway_end_id itself; the
caller (scripts/link_physical_installation_identities_mdw_cgf_pilot.py)
does that, only after re-running plan_identity_links() immediately before
write and confirming every identity in scope resolved cleanly.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Airport, PhysicalInstallationIdentity, Runway, RunwayEnd
from app.services.runway_identity import AmbiguousRunwayDesignationError, normalize_end

RESOLVED_NEW = "resolved_new"
ALREADY_LINKED_CORRECT = "already_linked_correct"
UNRESOLVED = "unresolved"
AMBIGUOUS = "ambiguous"
CROSS_AIRPORT = "cross_airport"
CONFLICTING_LINK = "conflicting_link"

# Statuses that are safe to include in an apply (a no-op re-confirmation
# counts as safe); anything else aborts the whole batch.
SAFE_STATUSES = frozenset({RESOLVED_NEW, ALREADY_LINKED_CORRECT})


@dataclass(frozen=True)
class IdentityLinkResolution:
    identity_id: int
    airport_id: int
    airport_code: str | None
    stored_designation: str | None
    normalized_designation: str | None
    target_runway_id: int | None
    target_runway_designation: str | None
    target_runway_end_id: int | None
    target_runway_end_designation: str | None
    current_runway_end_id: int | None
    proposed_runway_end_id: int | None
    status: str


def _ends_by_designation_for_airport(session: Session, airport_id: int) -> dict[str, list[RunwayEnd]]:
    """Every canonical RunwayEnd currently on record for this airport,
    keyed by normalized designation. Ends already correctly associate to
    exactly one Runway via the FK; this never crosses into another
    airport's rows because the join is filtered by Runway.airport_id."""
    rows = session.execute(
        select(RunwayEnd).join(Runway, RunwayEnd.runway_id == Runway.id).where(Runway.airport_id == airport_id)
    ).scalars().all()
    lookup: dict[str, list[RunwayEnd]] = {}
    for end in rows:
        try:
            key = normalize_end(end.designation)
        except AmbiguousRunwayDesignationError:
            continue
        lookup.setdefault(key, []).append(end)
    return lookup


def resolve_identity(
    session: Session,
    identity: PhysicalInstallationIdentity,
    *,
    airport_code: str | None = None,
    ends_by_designation: dict[str, list[RunwayEnd]] | None = None,
) -> IdentityLinkResolution:
    """Read-only resolution of one identity against the canonical
    inventory. ends_by_designation may be supplied pre-computed (the usual
    case, for one query per airport rather than per identity); if omitted
    it's computed fresh, scoped to this identity's own airport_id - this is
    the fail-closed default a test can rely on without extra setup."""
    if ends_by_designation is None:
        ends_by_designation = _ends_by_designation_for_airport(session, identity.airport_id)

    base = dict(
        identity_id=identity.id,
        airport_id=identity.airport_id,
        airport_code=airport_code,
        stored_designation=identity.runway_end,
        normalized_designation=None,
        target_runway_id=None,
        target_runway_designation=None,
        target_runway_end_id=None,
        target_runway_end_designation=None,
        current_runway_end_id=identity.runway_end_id,
        proposed_runway_end_id=None,
    )

    if not identity.runway_end:
        return IdentityLinkResolution(**base, status=UNRESOLVED)

    try:
        normalized = normalize_end(identity.runway_end)
    except AmbiguousRunwayDesignationError:
        return IdentityLinkResolution(**base, status=AMBIGUOUS)
    base["normalized_designation"] = normalized

    matches = ends_by_designation.get(normalized, [])
    if len(matches) == 0:
        return IdentityLinkResolution(**base, status=UNRESOLVED)
    if len(matches) > 1:
        return IdentityLinkResolution(**base, status=AMBIGUOUS)

    target_end = matches[0]
    target_runway = session.get(Runway, target_end.runway_id)

    # Defensive, always re-checked even though the query above already
    # scopes by airport - a future refactor that widens the lookup must
    # not silently make cross-airport linking possible.
    if target_runway is None or target_runway.airport_id != identity.airport_id:
        return IdentityLinkResolution(**base, status=CROSS_AIRPORT)

    base["target_runway_id"] = target_runway.id
    base["target_runway_designation"] = target_runway.designation
    base["target_runway_end_id"] = target_end.id
    base["target_runway_end_designation"] = target_end.designation

    if identity.runway_end_id is not None:
        if identity.runway_end_id == target_end.id:
            base["proposed_runway_end_id"] = target_end.id
            return IdentityLinkResolution(**base, status=ALREADY_LINKED_CORRECT)
        return IdentityLinkResolution(**base, status=CONFLICTING_LINK)

    base["proposed_runway_end_id"] = target_end.id
    return IdentityLinkResolution(**base, status=RESOLVED_NEW)


def plan_identity_links(session: Session, airport_codes: tuple[str, ...]) -> list[IdentityLinkResolution]:
    """Read-only. Resolves every existing PhysicalInstallationIdentity row
    belonging to any of airport_codes - not a hardcoded identity-ID list -
    against the current canonical inventory."""
    results: list[IdentityLinkResolution] = []
    for code in airport_codes:
        airport = session.scalar(
            select(Airport).where(
                (Airport.faa_code == code) | (Airport.iata_code == code) | (Airport.icao_code == code)
            )
        )
        if airport is None:
            continue
        ends_by_designation = _ends_by_designation_for_airport(session, airport.id)
        identities = session.scalars(
            select(PhysicalInstallationIdentity).where(PhysicalInstallationIdentity.airport_id == airport.id)
        ).all()
        for identity in identities:
            results.append(
                resolve_identity(session, identity, airport_code=code, ends_by_designation=ends_by_designation)
            )
    return results


def apply_identity_links(session: Session, airport_codes: tuple[str, ...]) -> list[IdentityLinkResolution]:
    """Re-resolves every identity in scope immediately before writing.
    Aborts the ENTIRE operation (raises, writes nothing) if any resolution
    is outside SAFE_STATUSES - no partial apply. Only ever assigns
    runway_end_id; nothing else is touched. Does not commit - the caller
    controls the transaction boundary."""
    resolutions = plan_identity_links(session, airport_codes)
    unsafe = [r for r in resolutions if r.status not in SAFE_STATUSES]
    if unsafe:
        details = ", ".join(f"identity {r.identity_id} ({r.status})" for r in unsafe)
        raise ValueError(f"Aborting: {len(unsafe)} identity link(s) not safe to apply: {details}")

    for resolution in resolutions:
        if resolution.status == RESOLVED_NEW:
            identity = session.get(PhysicalInstallationIdentity, resolution.identity_id)
            identity.runway_end_id = resolution.target_runway_end_id
    return resolutions
